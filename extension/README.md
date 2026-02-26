# browser-agent-extension

AI 채팅 UI와 브라우저 자동화를 제공하는 Chrome Manifest V3 브라우저 확장.

## 책임

- Keycloak PKCE 플로우로 사용자를 인증하고 토큰을 관리한다.
- 사이드패널 채팅 UI를 통해 Gateway SSE 스트림으로 AI 응답을 수신한다.
- `GET /sessions/{id}/commands` SSE 채널을 유지하며 브라우저 도구 호출(tool invocation)을 수신한다.
- 수신한 tool invocation을 content script에 전달해 실제 DOM 액션을 실행한다.
- DOM 액션 결과를 `POST /sessions/{id}/browser-tools/result/{inv_id}`로 Gateway에 전송한다.
- Chrome Tab Groups API로 AI 제어 탭을 "AI Assistant" 그룹으로 격리해 시각적으로 표시한다.
- 브라우저 제어 중일 때 사이드패널에 배너와 실행 중인 도구 단계를 표시한다.

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
| `background.ts` | Service Worker. PKCE 로그인, 토큰 관리, commands SSE 수신, 탭 그룹 관리, DOM 액션 실행 |
| `content.ts` | `<all_urls>` 매칭. click/type/scroll/evaluate_js 등 DOM 액션 실행 |
| `sidepanel/App.tsx` | 채팅 UI. 로그인 화면, 메시지 목록, SSE 스트리밍 입력, 브라우저 제어 상태 배너 |
| `popup/App.tsx` | 사이드패널 열기 버튼 |

## 인증 흐름 (PKCE)

1. `sidepanel/App.tsx`가 `LOGIN` 메시지를 `background.ts`에 전송한다.
2. `background.ts`가 code_verifier(96바이트)와 code_challenge(S256)를 생성하고, `pkce_{state}` 키로 `browser.storage.session`에 verifier를 저장한다.
3. `browser.identity.launchWebAuthFlow()`로 Keycloak 로그인 창을 연다.
4. Keycloak이 `browser.identity.getRedirectURL()`로 authorization code와 state를 전달한다.
5. `background.ts`가 Keycloak token endpoint에 code와 code_verifier를 POST해 토큰을 교환한다.
6. `access_token`은 Service Worker 메모리(`_accessToken`)에, `refresh_token`은 `browser.storage.session`에 저장한다.
7. Gateway에 세션을 생성하고 commands SSE 채널 구독을 시작한다.

> **주의**: `localStorage`와 `sessionStorage`는 Extension에서 보안상 사용 금지. 토큰은 반드시 메모리 또는 `browser.storage.session`에 보관한다.

## Service Worker SSE 주의사항

Service Worker는 `EventSource` API를 지원하지 않는다. `GatewayClient`는 `fetch()` + `ReadableStream`으로 SSE를 직접 구현한다.

```typescript
const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
const reader = res.body.getReader();
// 줄 단위로 파싱하며 'data: ' 접두사 이벤트만 처리
```

## 브라우저 도구 실행 흐름

1. `background.ts`가 `GET /sessions/{id}/commands` SSE 채널을 구독한다 (`GatewayClient.connectCommandsSSE`).
2. 도구 호출 이벤트 수신 시 (`{ inv_id, tool_name, params }`):
   - `navigate`, `screenshot`은 background에서 직접 처리
   - 나머지(`click`, `type`, `scroll` 등)는 `browser.tabs.sendMessage()`로 AI 제어 탭의 `content.ts`에 `EXECUTE_BROWSER_COMMAND` 메시지 전달
3. `content.ts`가 DOM 액션을 실행하고 결과를 반환한다.
4. `background.ts`가 `GatewayClient.postToolResult()`로 `POST /sessions/{id}/browser-tools/result/{inv_id}`에 결과를 전송한다.

## Chrome Tab Groups

AI 제어 탭은 "AI Assistant" (파란색) 탭 그룹에 배치된다:

- 로그인 후 commands SSE 연결 시 AI 전용 탭 생성
- 브라우저 제어 시작 시 해당 탭이 포커스됨
- 사이드패널 "탭 보기" 버튼으로 AI 탭으로 이동 가능

## Gateway API 인터페이스

`GatewayClient` (`lib/api.ts`)가 제공하는 메서드:

| 메서드 | 엔드포인트 | 설명 |
|--------|-----------|------|
| `createSession()` | `POST /sessions` | 새 세션 생성, `session_id` 반환 |
| `getSessionStatus(sessionId)` | `GET /sessions/{id}/browser-status` | 브라우저 제어 상태 조회 |
| `sendChat(sessionId, content)` | `POST /sessions/{id}/chat` | 단일 채팅 요청/응답 |
| `streamChat(sessionId, content)` | `GET /sessions/{id}/chat/stream` | SSE 채팅 스트림 (async generator) |
| `connectCommandsSSE(sessionId, onInvocation)` | `GET /sessions/{id}/commands` | 도구 호출 SSE 구독. cancel 함수 반환 |
| `postToolResult(sessionId, invId, result)` | `POST /sessions/{id}/browser-tools/result/{invId}` | DOM 액션 결과 전송 |

## 사이드패널 브라우저 제어 UI

브라우저 제어 중 표시되는 UI 요소:

- **BrowserControlBanner**: 파란색 배너 + 맥동하는 점. "탭 보기" 버튼으로 AI 탭 포커스
- **ToolStepList**: 최근 5개 도구 실행 단계 표시 (실행 중 / 완료 / 오류 상태)

## 개발 커맨드

```bash
pnpm dev        # 개발 서버 (HMR, Chrome)
pnpm build      # 프로덕션 빌드
pnpm compile    # TypeScript 타입 체크 (noEmit)
pnpm test       # Vitest 테스트 실행
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
│   ├── background.ts        # Service Worker (인증, 토큰 관리, commands SSE, 탭 그룹)
│   ├── content.ts           # DOM 액션 실행기 (click, type, scroll, evaluate_js 등)
│   ├── popup/               # 팝업 UI (사이드패널 열기 버튼)
│   └── sidepanel/           # 채팅 UI (로그인, 메시지, 스트리밍, 브라우저 제어 배너)
├── lib/
│   ├── api.ts               # GatewayClient (HTTP/SSE, postToolResult)
│   ├── auth.ts              # PKCE 유틸리티 (code_verifier, code_challenge 생성)
│   ├── config.ts            # 런타임 환경변수 설정
│   └── messaging.ts         # 타입 안전 메시지 패싱 헬퍼
├── stores/
│   └── chat.ts              # Zustand 채팅 + 브라우저 제어 상태
├── src/__tests__/
│   └── browser-tools.test.ts # SSE 파싱, URL 구성, 메시지 포맷 테스트
├── assets/
│   └── tailwind.css         # Tailwind v4 CSS-first import
└── wxt.config.ts            # WXT + Vite + @tailwindcss/vite 설정
```
