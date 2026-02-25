---
name: wxt-extension-patterns
description: WXT 프레임워크를 사용한 브라우저 확장 개발 패턴 (Manifest V3). Background/Content Script 분리, 메시지 패싱, 스토리지 전략. 브라우저 확장 코드 작성 시 자동으로 로드됩니다.
user-invokable: false
---

# WXT 브라우저 확장 Best Practices (Manifest V3)

## 패키지 관리 및 명령어

```bash
pnpm install
pnpm dev              # Chrome 개발 (HMR)
pnpm dev:firefox      # Firefox 개발
pnpm build            # Chrome 프로덕션 빌드
pnpm build:firefox    # Firefox 프로덕션 빌드
pnpm zip              # 배포용 zip 생성
pnpm compile          # TypeScript 타입 체크 (빌드 없이)
```

---

## 엔트리포인트 구조

```
entrypoints/
├── background.ts          # Service Worker (API 호출, 토큰, 비즈니스 로직)
├── content.ts             # Content Script (DOM 조작, 페이지 주입)
├── sidepanel/             # 사이드패널 UI (주요 UI)
│   ├── main.tsx
│   └── index.html
└── popup/                 # 팝업 UI
    ├── main.tsx
    └── index.html
```

```typescript
// entrypoints/background.ts
export default defineBackground(() => {
  browser.runtime.onInstalled.addListener(() => {
    console.log('Extension installed');
  });

  browser.runtime.onMessage.addListener(handleMessage);
});

// entrypoints/content.ts
export default defineContentScript({
  matches: ['https://*.example.com/*'],
  main() {
    // DOM 조작
    injectUI();
  },
});
```

---

## 메시지 패싱 (타입 안전)

**핵심 규칙**: API 호출은 **Background에서만**. UI는 Background에 메시지를 보내고 결과를 받습니다.

```typescript
// types/messages.ts — 판별 유니온으로 타입 안전성 확보
export type ExtensionMessage =
  | { type: 'GET_AUTH_SESSION' }
  | { type: 'LOGIN' }
  | { type: 'LOGOUT' }
  | { type: 'FETCH_DATA'; payload: { resourceId: string } };

export type ExtensionResponse =
  | { success: true; data: unknown }
  | { success: false; error: string };
```

```typescript
// entrypoints/background.ts — Background에서 메시지 처리
browser.runtime.onMessage.addListener(
  (message: ExtensionMessage, _sender, sendResponse) => {
    handleMessage(message).then(sendResponse);
    return true; // 비동기 응답을 위해 반드시 true 반환
  },
);

async function handleMessage(message: ExtensionMessage): Promise<ExtensionResponse> {
  switch (message.type) {
    case 'GET_AUTH_SESSION':
      return { success: true, data: await getSession() };
    case 'FETCH_DATA':
      return { success: true, data: await fetchData(message.payload.resourceId) };
    default:
      return { success: false, error: 'Unknown message type' };
  }
}
```

```typescript
// shared/messaging.ts — UI에서 Background로 메시지 전송
export async function sendMessage<T = unknown>(
  message: ExtensionMessage,
): Promise<T> {
  const response: ExtensionResponse = await browser.runtime.sendMessage(message);
  if (!response.success) throw new Error(response.error);
  return response.data as T;
}

// UI 사용 예시
const session = await sendMessage<AuthSession>({ type: 'GET_AUTH_SESSION' });
```

---

## 스토리지 전략

| 스토리지 | 용도 | 특징 |
|---|---|---|
| `chrome.storage.local` | 앱 설정, 캐시 | 영구 저장, 용량 제한 있음 |
| `chrome.storage.sync` | 계정 연동 설정 | 기기 간 동기화, 용량 매우 작음 |
| `chrome.storage.session` | 민감한 임시 데이터 | 브라우저 재시작 시 삭제 |
| 메모리 변수 | Access token | 확장 재시작 시 삭제 (가장 안전) |

```typescript
// Background 메모리에만 access token 저장 (가장 안전)
let accessToken: string | null = null;

// Refresh token은 session storage (브라우저 재시작 시 삭제)
async function saveRefreshToken(token: string): Promise<void> {
  await browser.storage.session.set({ refreshToken: token });
}

async function getRefreshToken(): Promise<string | null> {
  const result = await browser.storage.session.get('refreshToken');
  return (result.refreshToken as string) ?? null;
}

// 사용자 설정은 local storage
async function saveSettings(settings: UserSettings): Promise<void> {
  await browser.storage.local.set({ settings });
}
```

---

## Service Worker에서 SSE 처리

Service Worker는 `EventSource`를 지원하지 않으므로 `fetch`를 사용합니다:

```typescript
async function connectToSSE(url: string, token: string): Promise<void> {
  const response = await fetch(url, {
    headers: { Authorization: `Bearer ${token}` },
  });

  if (!response.ok || !response.body) throw new Error('SSE connection failed');

  const reader = response.body.getReader();
  const decoder = new TextDecoder();

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      const lines = decoder.decode(value).split('\n');
      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const data: unknown = JSON.parse(line.slice(6));
          handleSSEEvent(data);
        }
      }
    }
  } finally {
    reader.cancel(); // 연결 해제 시 cleanup
  }
}
```

---

## OAuth2 PKCE (browser.identity API)

브라우저 확장은 `client_secret`을 가질 수 없으므로 반드시 PKCE를 사용합니다:

```typescript
// auth/pkce.ts
async function generatePKCE(): Promise<{ verifier: string; challenge: string }> {
  const verifier = generateRandomString(128);
  const encoder = new TextEncoder();
  const digest = await crypto.subtle.digest('SHA-256', encoder.encode(verifier));
  const challenge = btoa(String.fromCharCode(...new Uint8Array(digest)))
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
  return { verifier, challenge };
}

async function startOAuthFlow(config: OAuthConfig): Promise<TokenResponse> {
  const { verifier, challenge } = await generatePKCE();
  const state = generateRandomString(16);

  // verifier를 임시 저장 (state로 매핑)
  await browser.storage.session.set({ [`pkce_${state}`]: verifier });

  const authUrl = buildAuthUrl(config, { challenge, state });

  // Chrome Identity API로 팝업 처리
  const redirectUrl = await browser.identity.launchWebAuthFlow({
    url: authUrl,
    interactive: true,
  });

  return handleCallback(redirectUrl, state);
}
```

---

## 환경변수 (WXT 규칙)

```bash
# .env
WXT_PUBLIC_API_BASE_URL=http://localhost:8000/api
WXT_PUBLIC_AUTH_SERVER_URL=http://localhost:8080
```

```typescript
// shared/config/env.ts
export const env = {
  apiBaseUrl: import.meta.env.WXT_PUBLIC_API_BASE_URL as string,
  authServerUrl: import.meta.env.WXT_PUBLIC_AUTH_SERVER_URL as string,
} as const;
```

`WXT_PUBLIC_` 접두사가 없는 환경변수는 번들에 포함되지 않습니다.

---

## 권한 선언 (wxt.config.ts)

```typescript
// wxt.config.ts
import { defineConfig } from 'wxt';

export default defineConfig({
  modules: ['@wxt-dev/module-react'],
  manifest: {
    permissions: [
      'storage',      // browser.storage 사용
      'identity',     // OAuth2 플로우
      'sidePanel',    // 사이드패널 UI
    ],
    host_permissions: [
      'https://api.example.com/*',
    ],
  },
});
```

---

## 핵심 원칙

- **API 호출은 Background 전용**: CSP 제한, CORS 우회, 토큰 보안을 위해
- **타입 안전 메시지**: 판별 유니온 타입으로 런타임 오류 방지
- **민감 데이터 저장 순서**: 메모리 > `storage.session` > `storage.local` > `storage.sync`
- **`localStorage`/`sessionStorage` 금지**: Chrome Extension에서 보안 위협
- **Manifest V3 준수**: Service Worker 기반, 지속적인 백그라운드 페이지 없음
- **PKCE 필수**: 브라우저 확장은 Public Client이므로 client_secret 사용 불가
