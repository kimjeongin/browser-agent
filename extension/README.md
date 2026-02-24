# browser-agent-extension

AI 채팅 UI와 브라우저 자동화를 제공하는 Chrome Manifest V3 브라우저 확장.

## 책임

- Keycloak PKCE 플로우로 사용자를 인증하고 토큰을 관리한다.
- 사이드패널 채팅 UI를 통해 Gateway SSE 스트림으로 AI 응답을 수신한다.
- `GET /sessions/{id}/commands` SSE 채널을 유지하며 브라우저 명령을 수신한다.
- 수신한 명령을 content script에 전달해 실제 DOM 액션을 실행한다.
- DOM 액션 결과를 `POST /sessions/{id}/command-result`로 Gateway에 전송한다.

## 기술 스택

| 항목 | 버전 |
|------|------|
| WXT | 0.20 |
| React | 19 |
| TypeScript | 5.9 |
| Tailwind CSS | 4.x (CSS-first, `@theme` 기반) |
| Zustand | 5 |

## 진입점

| 파일 | 역할 |
|------|------|
| `background.ts` | Service Worker. PKCE 로그인, 토큰 관리, 명령 SSE 수신 및 content script 중계 |
| `content.ts` | `<all_urls>` 매칭. DOM 액션 실행 (click, type, scroll, evaluate_js 등) |
| `sidepanel/App.tsx` | 채팅 UI. 로그인 화면, 메시지 목록, SSE 스트리밍 입력 |
| `popup/App.tsx` | 사이드패널 열기 버튼 |

## 인증 흐름 (PKCE)

1. `sidepanel/App.tsx`가 `LOGIN` 메시지를 `background.ts`에 전송한다.
2. `background.ts`가 code_verifier(96바이트)와 code_challenge(S256)를 생성하고, `pkce_{state}` 키로 `browser.storage.session`에 verifier를 저장한다.
3. `browser.identity.launchWebAuthFlow()`로 Keycloak 로그인 창을 연다.
4. Keycloak이 `browser.identity.getRedirectURL()`로 authorization code와 state를 전달한다.
5. `background.ts`가 Keycloak token endpoint에 code와 code_verifier를 POST해 토큰을 교환한다.
6. `access_token`은 Service Worker 메모리(`_accessToken`)에, `refresh_token`은 `browser.storage.session`에 저장한다.
7. Gateway에 세션을 생성하고 명령 SSE 채널 구독을 시작한다.

> **주의**: `localStorage`와 `sessionStorage`는 Extension에서 보안상 사용 금지. 토큰은 반드시 메모리 또는 `browser.storage.session`에 보관한다.

## Service Worker SSE 주의사항

Service Worker는 `EventSource` API를 지원하지 않는다. `GatewayClient`는 `fetch()` + `ReadableStream`으로 SSE를 직접 구현한다.

```typescript
const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
const reader = res.body.getReader();
// 줄 단위로 파싱하며 'data: ' 접두사 이벤트만 처리
```

## 브라우저 명령 흐름

1. `background.ts`가 `GET /sessions/{id}/commands` SSE 채널을 구독한다 (`GatewayClient.connectCommandsSSE`).
2. 명령 이벤트 수신 시 `browser.tabs.sendMessage()`로 활성 탭의 `content.ts`에 `EXECUTE_BROWSER_COMMAND` 메시지를 전달한다.
3. `content.ts`가 DOM 액션을 실행하고 `CommandResult`를 반환한다.
4. `background.ts`가 `GatewayClient.postCommandResult()`로 `POST /sessions/{id}/command-result`에 결과를 전송한다.

## Gateway API 인터페이스

`GatewayClient` (`lib/api.ts`)가 제공하는 메서드:

| 메서드 | 엔드포인트 | 설명 |
|--------|-----------|------|
| `createSession()` | `POST /sessions` | 새 세션 생성, `session_id` 반환 |
| `sendChat(sessionId, content)` | `POST /sessions/{id}/chat` | 단일 채팅 요청/응답 |
| `streamChat(sessionId, content)` | `GET /sessions/{id}/chat/stream` | SSE 채팅 스트림 (async generator) |
| `connectCommandsSSE(sessionId, onCommand)` | `GET /sessions/{id}/commands` | 브라우저 명령 SSE 구독. cancel 함수 반환 |
| `postCommandResult(sessionId, result)` | `POST /sessions/{id}/command-result` | DOM 액션 결과 전송 |

## 개발 커맨드

```bash
pnpm dev        # 개발 서버 (HMR, Chrome)
pnpm build      # 프로덕션 빌드
pnpm compile    # TypeScript 타입 체크 (noEmit)
```

빌드 결과물은 `extension/.output/chrome-mv3`에 생성된다. `chrome://extensions` → 개발자 모드 → 이 폴더를 로드해 설치한다.

## 환경변수 (`.env`)

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `WXT_PUBLIC_API_BASE_URL` | `http://localhost:8000` | Gateway 베이스 URL |
| `WXT_PUBLIC_KEYCLOAK_REALM_URL` | `http://localhost:8080/realms/browser-agent` | Keycloak Realm URL |
| `WXT_PUBLIC_KEYCLOAK_CLIENT_ID` | `browser-agent-extension` | Keycloak Public Client ID (PKCE S256 강제) |

`WXT_PUBLIC_` 접두사가 붙은 변수는 빌드 시 번들에 인라인된다.

## 파일 구조

```
extension/
├── entrypoints/
│   ├── background.ts        # Service Worker (인증, 토큰 관리, 명령 SSE)
│   ├── content.ts           # DOM 액션 실행기 (click, type, scroll, evaluate_js 등)
│   ├── popup/               # 팝업 UI (사이드패널 열기 버튼)
│   └── sidepanel/           # 채팅 UI (로그인 화면, 메시지 목록, 스트리밍)
├── lib/
│   ├── api.ts               # GatewayClient (HTTP/SSE)
│   ├── auth.ts              # PKCE 유틸리티 (code_verifier, code_challenge 생성)
│   ├── config.ts            # 런타임 환경변수 설정
│   └── messaging.ts         # 타입 안전 메시지 패싱 헬퍼
├── stores/
│   └── chat.ts              # Zustand 채팅 상태 (messages, sessionId, isLoggedIn)
├── assets/
│   └── tailwind.css         # Tailwind v4 CSS-first import
└── wxt.config.ts            # WXT + Vite + @tailwindcss/vite 설정
```
