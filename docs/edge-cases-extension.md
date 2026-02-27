# Extension 엣지 케이스 및 버그 분석

서비스: background.ts, content.ts, sidepanel/App.tsx, lib/api.ts, stores/chat.ts

---

## Service Worker (`entrypoints/background.ts`)

### 🔴 [EX-1] Service Worker 재시작 시 액세스 토큰 소실

**문제**: `_accessToken` 변수가 Service Worker 메모리에만 저장된다. Chrome이 30초 idle 후 Service Worker를 종료하면 액세스 토큰이 사라진다.

**재현 조건**: 30초 이상 채팅 없이 대기 → 새 메시지 입력 → 토큰 없어 401

**현재 처리**: refresh_token은 `browser.storage.session`에 저장되어 있어 토큰 갱신이 이론상 가능하지만, 자동 갱신 로직이 없으면 모든 API 호출이 401 실패 후 재로그인 요구.

**영향**: 사용자가 30초 이상 자리를 비운 후 돌아오면 세션이 깨진 것처럼 보임

**권장 수정**:
```typescript
// background.ts
async function getValidToken(): Promise<string> {
  if (_accessToken && !isTokenExpired(_accessToken)) {
    return _accessToken;
  }
  // refresh_token으로 갱신 시도
  const refreshToken = await browser.storage.session.get('refresh_token');
  if (refreshToken) {
    _accessToken = await refreshAccessToken(refreshToken.refresh_token);
    return _accessToken;
  }
  throw new Error('Authentication required');
}
```

---

### 🔴 [EX-2] Commands SSE 채널 자동 재연결 없음

**위치**: `GatewayClient.connectCommandsSSE()`
**문제**: `fetch()` + `ReadableStream` 기반 SSE는 연결이 끊기면 자동으로 재연결하지 않는다. 브라우저 도구 실행 중 네트워크 순단이 발생하면 commands 채널이 영구히 닫힌다.

**재현 조건**: WiFi 전환, 네트워크 잠시 끊김, Gateway 재시작
**영향**: 이후 모든 브라우저 도구 실행 불가. 재로그인 없이는 복구 불가.

**권장 수정**:
```typescript
async function connectWithRetry(sessionId: string, onInvocation: ..., retries = 0) {
  try {
    const cancel = await gatewayClient.connectCommandsSSE(sessionId, onInvocation);
    return cancel;
  } catch (e) {
    const delay = Math.min(1000 * 2 ** retries, 30000);
    await new Promise(r => setTimeout(r, delay));
    return connectWithRetry(sessionId, onInvocation, retries + 1);
  }
}
```

---

### 🔴 [EX-3] AI 탭 그룹 누적 생성 (메모리/탭 누수)

**시나리오**: 사용자가 로그아웃 후 재로그인 시마다 새 "AI Assistant" 탭 그룹이 생성된다. 기존 그룹의 정리 로직 없이 새 그룹이 계속 추가.

**재현 조건**: 하루에 여러 번 로그인/로그아웃 반복
**영향**: Chrome 탭 그룹 목록이 "AI Assistant" 탭들로 가득 참, 메모리 증가

**권장 수정**:
```typescript
// 로그인 시 기존 AI 그룹 정리
async function cleanupAITabGroups() {
  const groups = await chrome.tabGroups.query({ title: 'AI Assistant' });
  for (const group of groups) {
    const tabs = await chrome.tabs.query({ groupId: group.id });
    for (const tab of tabs) {
      await chrome.tabs.remove(tab.id!);
    }
  }
}
```

---

### 🔴 [EX-4] chrome:// 페이지에서 content script 미주입

**문제**: `content.ts`는 `<all_urls>` 패턴으로 등록되지만 `chrome://`, `chrome-extension://`, `about:` 등 브라우저 내부 페이지에는 content script가 주입되지 않는다. Extension이 해당 탭으로 navigate 후 click/type 도구를 실행하면 응답 없음.

**재현 조건**: Browser Agent가 `navigate("chrome://settings")` 실행
**영향**: 도구 실행 timeout (60초 대기), LangGraph 에러

**권장 수정**: navigate 도구에서 `chrome://` URL 차단
```typescript
if (url.startsWith('chrome://') || url.startsWith('about:')) {
  return { error: 'Cannot navigate to browser internal pages' };
}
```

---

### 🟠 [EX-5] 동시 브라우저 도구 실행 레이스 컨디션

**시나리오**: Browser Agent LangGraph가 병렬 도구 호출(예: `click`과 `type` 동시)을 발생시키면 두 개의 `EXECUTE_BROWSER_COMMAND` 메시지가 content script에 동시 전달된다. DOM 조작 순서가 보장되지 않음.

**영향**: 폼 입력 오작동, 예측 불가능한 DOM 상태

---

### 🟠 [EX-6] Service Worker 재시작 시 AI 탭 ID 소실

**시나리오**: SW 재시작 후 `_aiTabId` 변수가 초기화된다. 기존 AI 탭이 열려 있어도 SW가 인식하지 못해 새 탭을 다시 생성한다.

**권장 수정**: `_aiTabId`를 `browser.storage.session`에 저장
```typescript
async function getOrCreateAiTab(): Promise<number> {
  const stored = await browser.storage.session.get('ai_tab_id');
  if (stored.ai_tab_id) {
    try {
      await chrome.tabs.get(stored.ai_tab_id);
      return stored.ai_tab_id;
    } catch {
      // 탭이 닫혔음, 새로 생성
    }
  }
  const tab = await chrome.tabs.create({ url: 'about:blank' });
  await browser.storage.session.set({ ai_tab_id: tab.id });
  return tab.id!;
}
```

---

### 🟡 [EX-7] PKCE 로그인 중 중복 클릭 레이스

**시나리오**: 사용자가 로그인 버튼을 빠르게 여러 번 클릭하면 여러 PKCE 플로우가 동시에 시작된다. `pkce_{state}` 키가 마지막으로 저장된 state로 덮어쓰여져 이전 state의 code_verifier가 손실된다.

**권장 수정**: 로그인 진행 중 플래그 추가
```typescript
let _loginInProgress = false;

async function handleLogin() {
  if (_loginInProgress) return;
  _loginInProgress = true;
  try {
    // PKCE 플로우
  } finally {
    _loginInProgress = false;
  }
}
```

---

## Content Script (`entrypoints/content.ts`)

### 🔴 [CT-1] `evaluate_js` XSS / 코드 인젝션 위험

**위치**: `evaluate_js` 도구
**코드**:
```typescript
case 'evaluate_js':
  result = await eval(params.code);
```
**문제**: LLM이 생성한 임의의 JavaScript를 `eval()`로 실행한다. LLM이 악의적 사이트에서 추출한 스크립트나 prompt injection으로 유도된 코드를 실행할 수 있다.

**시나리오**:
1. 사용자가 악의적 웹페이지 방문 요청
2. 페이지가 LLM prompt를 조작하는 텍스트 포함
3. LLM이 조작된 JS 코드를 `evaluate_js`로 실행
4. 사용자 쿠키, localStorage 탈취 가능

**영향**: 심각한 보안 취약점, 사용자 데이터 노출

**권장 수정**: `evaluate_js` 도구 제거 또는 허용 코드 목록(allowlist)으로 제한

---

### 🔴 [CT-2] `javascript:` URI navigate 허용

**문제**: navigate 도구가 `javascript:alert(1)` 같은 URI를 차단하지 않는다.

**권장 수정**:
```typescript
if (!url.startsWith('http://') && !url.startsWith('https://')) {
  return { error: 'Only HTTP/HTTPS URLs are allowed' };
}
```

---

### 🟠 [CT-3] 비상호적 요소 클릭 false success 반환

**시나리오**: `click` 도구가 숨겨진 요소, `pointer-events: none` 요소, disabled 요소에 대해 클릭 이벤트를 dispatch했음에도 성공으로 반환한다. 실제 페이지 상태 변화 없음.

**재현 조건**: 버튼이 disabled 상태인 폼, 오버레이로 가려진 요소
**영향**: LLM이 클릭 성공으로 인식하고 다음 단계 진행 → 예상치 못한 결과

**권장 수정**:
```typescript
const element = document.querySelector(selector);
if (!element || element.disabled || !isVisible(element)) {
  return { success: false, reason: 'Element not interactable' };
}
```

---

### 🟠 [CT-4] `type` 도구: 기존 텍스트 미제거

**시나리오**: 이미 텍스트가 입력된 `<input>` 필드에 `type` 도구를 사용하면 기존 텍스트 뒤에 추가된다. 사용자가 "검색어를 'apple'로 변경해줘"라고 요청하면 기존 텍스트 + 'apple'이 된다.

**권장 수정**:
```typescript
element.value = '';  // 기존 텍스트 제거
element.dispatchEvent(new Event('input', { bubbles: true }));
// 이후 새 텍스트 입력
```

---

### 🟡 [CT-5] Shadow DOM 요소 접근 불가

**문제**: `querySelector`는 Shadow DOM 내부 요소를 찾지 못한다. 현대 웹 컴포넌트(LitElement, Stencil.js 등)가 Shadow DOM을 사용하면 click/type 도구가 실패한다.

---

### 🟡 [CT-6] `scroll` 도구: 무한 스크롤 페이지 처리

**시나리오**: 무한 스크롤 페이지에서 `scroll(direction='down', amount=1000)`을 반복하면 LangGraph가 루프를 탈출하지 못할 수 있다.

---

## Gateway Client (`lib/api.ts`)

### 🔴 [API-1] `postToolResult` 실패 시 silent failure

**위치**: `lib/api.ts`의 `postToolResult()`
**문제**: 도구 실행 결과를 Gateway에 전송하는 API 호출이 실패해도 에러를 무시하거나 로그만 출력한다. Browser Agent의 Future는 `set_result()`를 받지 못해 60초 timeout까지 블로킹.

**재현 조건**: 네트워크 순단, Gateway 재시작, JWT 만료

**영향**: 브라우저 도구 체인 전체 중단, Browser Agent 60초 블로킹

**권장 수정**:
```typescript
async postToolResult(sessionId: string, invId: string, result: unknown): Promise<void> {
  let lastError: Error | null = null;
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      await this._fetch(`/sessions/${sessionId}/browser-tools/result/${invId}`, {
        method: 'POST',
        body: JSON.stringify(result),
      });
      return;
    } catch (e) {
      lastError = e as Error;
      await sleep(1000 * (attempt + 1));
    }
  }
  // 3회 실패 시 사용자에게 알림
  notifyUser('브라우저 도구 실행 결과 전송 실패');
  throw lastError;
}
```

---

### 🟠 [API-2] `streamChat` SSE 파싱: 멀티라인 데이터 미처리

**문제**: SSE 스펙에서 `data:` 필드가 여러 줄에 걸칠 수 있지만, 현재 파서가 단일 라인 `data:` 만 처리한다.

---

### 🟡 [API-3] 토큰 만료 시 자동 갱신 없음

**문제**: API 호출이 401을 받으면 갱신 로직 없이 에러를 그대로 전파한다.

**권장 수정**: 401 응답 시 refresh_token으로 갱신 후 재시도

---

## 사이드패널 UI (`entrypoints/sidepanel/App.tsx`)

### 🟠 [UI-1] 사이드패널 닫힘 시 채팅 상태 소실

**문제**: Zustand 스토어가 사이드패널 컴포넌트의 메모리에 의존한다. 사이드패널을 닫고 다시 열면 채팅 히스토리가 사라진다.

**재현 조건**: 채팅 중 사이드패널 X 버튼 클릭 → 팝업에서 사이드패널 다시 열기
**영향**: 이전 대화 내용 전체 소실

**권장 수정**: 채팅 히스토리를 `browser.storage.local`에 저장

---

### 🟠 [UI-2] 스트리밍 중 메시지 크기 제한 없음

**문제**: 매우 긴 LLM 응답(수만 토큰)이 스트리밍되면 DOM 업데이트가 과도하게 발생하여 UI가 프리징될 수 있다.

---

### 🟡 [UI-3] 도구 실행 단계 간 로딩 표시 없음

**시나리오**: Browser Agent가 도구 실행 중이지만 현재 단계와 다음 단계 사이에 시각적 피드백이 없다. 사용자가 "반응이 없다"고 느껴 메시지를 재전송.

---

### 🟡 [UI-4] 오류 메시지 과도한 기술적 표현

**문제**: API 실패 시 `Error: 500 Internal Server Error`같은 기술적 메시지가 사용자에게 그대로 표시된다.

---

### 🟡 [UI-5] 긴 URL이 채팅 버블 레이아웃 깨뜨림

**문제**: 긴 URL이 포함된 메시지에서 CSS word-break 설정 없이 레이아웃 overflow 발생.

---

## Zustand Store (`stores/chat.ts`)

### 🟡 [ST-1] 메시지 배열 무제한 증가

**문제**: 멀티턴 대화에서 메시지가 계속 추가되지만 제거 로직이 없다. 수백 개의 메시지가 쌓이면 React 렌더링 성능 저하.

**권장 수정**: 최근 100개 메시지만 유지하거나 가상화 렌더링 사용

---

### 🟡 [ST-2] `isBrowserControlling` 상태 비동기 업데이트 지연

**시나리오**: Browser Agent가 도구를 연속 실행할 때 `isBrowserControlling` 상태 업데이트가 SSE 이벤트보다 늦게 처리되면 배너가 깜빡거린다.
