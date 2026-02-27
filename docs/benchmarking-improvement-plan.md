# Browser Agent 벤치마킹 및 개선 계획서

> 작성일: 2026-02-28
> 버전: 1.0
> 상태: 계획 단계

---

## 1. 벤치마킹 요약

### 1.1 경쟁 솔루션 분석 비교표

| 솔루션 | 아키텍처 | 브라우저 접근 방식 | 컨텍스트 효율성 | 정확도 | 핵심 혁신 |
|--------|----------|------------------|----------------|--------|----------|
| **browser-use** | Python + CDP 직접 연결 | Chrome DevTools Protocol (2025 Playwright 대체) | KV 캐시 최적화 (대화 히스토리 → 브라우저 상태 순서) | 89% WebVoyager | 하이브리드 DOM+비전 / 선택적 스크린샷 (~0.8s) / 스텝당 ~3초 |
| **Stagehand v3** | TypeScript + Playwright | DOM 접근성 트리 + 선택적 스크린샷 | Context Builder로 80-90% 토큰 절감 | ~85% | 자기 치유 실행 (액션 캐싱) / 관련 DOM만 추출 |
| **Skyvern 2.0** | Python + Playwright | 시각적 DOM 파싱 + OCR | 검증 에이전트가 불필요한 반복 차단 | 85.85% WebVoyager | Planner-Actor-Validator 3계층 패턴 |
| **Playwright MCP** | Node.js MCP 서버 | 접근성 트리 → 압축 YAML | Wikipedia 124k 토큰 (비최적) | N/A | MCP 표준 / 70+ 도구 |
| **현재 프로젝트** | Python + Chrome Extension | Extension Content Script (3홉 패턴) | 미최적화 (전체 페이지 텍스트 추출) | 미측정 | asyncio.Queue+Future / SSE 기반 |

### 1.2 산업 트렌드 (2025-2026)

- **Generation 3** 패턴: 직접 CDP + 구조화된 컨텍스트 + 자기 치유
- **MCP**: LLM 도구 서버의 사실상 표준으로 수렴
- **Planner-Actor-Validator**: 단순 ReAct 대비 정확도 14% → 60%+ 향상 핵심 요인
- **컨텍스트 압축**: Wikipedia 페이지 기준 124k 토큰(비최적) vs 8.7k 토큰(최적), 14배 차이

---

## 2. 핵심 격차 분석

### 2.1 아키텍처 격차

| 영역 | 현재 프로젝트 | 업계 최선 사례 | 격차 수준 |
|------|-------------|--------------|----------|
| 브라우저 접근 | Extension Content Script (3홉: BA → GW → EXT → 탭) | CDP 직접 연결 (1홉) | 높음 |
| 에이전트 패턴 | 단순 ReAct (LLM → 도구 → LLM) | Planner-Actor-Validator 3계층 | 높음 |
| 컨텍스트 관리 | 전체 메시지 히스토리 유지 | 현재 스냅샷만 유지 + 이전 스텝 압축 | 높음 |
| 스크린샷 전략 | 매 스텝 또는 명시적 요청 시 | 필요 시에만 (평균 ~0.8초 비용) | 중간 |
| DOM 컨텍스트 | 전체 페이지 텍스트 추출 | 관련 접근성 트리 + 선택적 추출 | 높음 |
| 자기 치유 | 없음 | 액션 캐싱 + DOM 변경 감지 재시도 | 높음 |
| 요소 참조 | CSS 셀렉터 (취약) | CDP targetId + frameId + backendNodeId | 중간 |
| 안전 제약 | LLM 추론 의존 | 프로그래밍 방식 강제 | 높음 |

### 2.2 신뢰성 격차

| 이슈 ID | 문제 | 영향 | 긴급도 |
|---------|------|------|--------|
| GW-1 | asyncio.Queue/Future 메모리 누수 | 30분+ 운영 시 OOM | P0 긴급 |
| GW-2 | `_browser_controlling` 플래그 로직 버그 | UI 배너 미표시 | P0 긴급 |
| GW-3 | Extension 미연결 시 60초 블로킹 | 전체 응답 지연 | P0 긴급 |
| EX-1 | SW 재시작 시 액세스 토큰 소실 | 30초 유휴 후 401 | P0 긴급 |
| EX-2 | SSE 자동 재연결 없음 | 네트워크 순단 시 영구 중단 | P0 긴급 |
| BA-1 | LangGraph 무한 루프 위험 | CPU 100%, 응답 없음 | P0 긴급 |
| CT-1 | `evaluate_js` XSS/코드 인젝션 | 심각한 보안 취약점 | P0 보안 |
| CT-2 | `javascript:` URI 미차단 | 보안 취약점 | P0 보안 |

### 2.3 성능 격차

현재 프로젝트의 추정 스텝당 지연:
- Browser Agent LLM 추론: ~5-10초 (Ollama qwen2.5:14b, 로컬)
- 3홉 브라우저 도구 실행: ~1-3초 (SSE 왕복)
- 컨텍스트 오버헤드: 측정 안 됨 (전체 메시지 히스토리)
- **총 추정: 스텝당 7-15초**

browser-use 벤치마크: **스텝당 평균 ~3초**

---

## 3. 개선 계획

---

### P0: 긴급 버그 수정 (즉시, 1-3일)

#### P0-1. asyncio.Queue/Future 메모리 누수 수정 (GW-1)

**문제**: Extension SSE 연결 종료 후 `_pending_invocations`에 완료되지 않은 Future가 남고, TTL 정리 없이 메모리에 누적.

**수정 내용** (`services/gateway/main.py`):

```python
# 주기적 정리 태스크 추가 (lifespan에서 시작)
async def _cleanup_stale_invocations():
    """만료된 invocation 정리 (60초마다 실행)"""
    while True:
        await asyncio.sleep(60)
        now = asyncio.get_running_loop().time()
        stale = [
            inv_id for inv_id, (future, created_at) in _pending_invocations_with_ts.items()
            if (now - created_at) > 120  # 120초 이상 된 것 강제 정리
        ]
        for inv_id in stale:
            future = _pending_invocations.pop(inv_id, None)
            if future and not future.done():
                future.cancel()
            _invocation_to_session.pop(inv_id, None)
        if stale:
            logger.warning("Cleaned up %d stale invocations", len(stale))
```

**수정 파일**: `services/gateway/main.py`
- `_pending_invocations` dict에 생성 타임스탬프 추가 (dict 값을 `Future`에서 `tuple[Future, float]`로 변경)
- lifespan에서 `asyncio.create_task(_cleanup_stale_invocations())` 시작
- lifespan 종료 시 태스크 취소

**기대 효과**: 30분+ 운영 시 메모리 안정화, OOM 리스크 제거

---

#### P0-2. `_browser_controlling` 플래그 버그 수정 (GW-2)

**문제**: `invoke_browser_tool()` finally 블록에서 `pop()` 이후에 역매핑을 확인하므로 항상 False가 설정됨.

```python
# 현재 (버그): pop() 이후 any()가 항상 False
finally:
    _pending_invocations.pop(inv_id, None)
    _invocation_to_session.pop(inv_id, None)
    if not any(v == session_id for v in _invocation_to_session.values()):
        _browser_controlling[session_id] = False  # 항상 실행됨
```

**수정 내용**:
```python
finally:
    _pending_invocations.pop(inv_id, None)
    _invocation_to_session.pop(inv_id, None)
    # pop 이후에 남은 invocation이 있는지 확인
    remaining = sum(1 for v in _invocation_to_session.values() if v == session_id)
    if remaining == 0:
        _browser_controlling[session_id] = False
```

**수정 파일**: `services/gateway/main.py`

**기대 효과**: UI 브라우저 제어 배너가 도구 실행 중 정상 표시됨

---

#### P0-3. Extension 미연결 시 조기 실패 반환 (GW-3)

**문제**: `invoke_browser_tool()`에서 queue가 있어도 Extension SSE 구독자가 없으면 60초 대기.

**수정 내용**: Extension 연결 상태를 별도 플래그로 추적:

```python
# 세션별 SSE 구독자 수 추적
_session_sse_subscribers: dict[str, int] = {}

# browser_command_stream()에서
async def _command_generator():
    _session_sse_subscribers[session_id] = _session_sse_subscribers.get(session_id, 0) + 1
    try:
        # ... 기존 로직
    finally:
        _session_sse_subscribers[session_id] = max(0, _session_sse_subscribers.get(session_id, 1) - 1)

# invoke_browser_tool()에서
if _session_sse_subscribers.get(session_id, 0) == 0:
    raise HTTPException(
        status_code=503,
        detail="Extension is not connected. Please ensure the browser extension is active.",
    )
```

**수정 파일**: `services/gateway/main.py`

**기대 효과**: Extension 미연결 시 즉시 503 반환 (60초 → 0.1초), Browser Agent가 즉시 에러 처리 가능

---

#### P0-4. 보안 취약점 수정 (CT-1, CT-2)

**수정 내용** (`extension/entrypoints/content.ts` 및 `background.ts`):

```typescript
// content.ts - navigate 케이스에 URL 검증 추가
case 'navigate': {
    const url = params.url as string;
    if (!url.startsWith('http://') && !url.startsWith('https://')) {
        return { command_id, success: false, error: 'Only HTTP/HTTPS URLs are allowed.' };
    }
    if (url.startsWith('chrome://') || url.startsWith('about:') || url.startsWith('file://')) {
        return { command_id, success: false, error: 'Cannot navigate to browser internal pages.' };
    }
    // ...
}
```

```typescript
// background.ts - navigateAgentTab()에 동일 검증 추가
async function navigateAgentTab(url: string): Promise<void> {
    if (!url.startsWith('http://') && !url.startsWith('https://')) {
        throw new Error(`Only HTTP/HTTPS URLs are allowed. Received: ${url.slice(0, 50)}`);
    }
    // ...
}
```

**수정 파일**: `extension/entrypoints/content.ts`, `extension/entrypoints/background.ts`

**기대 효과**: 프롬프트 인젝션을 통한 악의적 URL 실행 차단

---

#### P0-5. LangGraph 무한 루프 방지 강화 (BA-1)

**문제**: `services/shared/acp/server.py`의 `recursion_limit=25`가 Browser Agent에 전달되지만, 단일 도구 타임아웃은 별도로 처리 필요.

**수정 내용** (`services/browser_agent/main.py`):
```python
# GatewayBrowserToolsClient.invoke()에서 타임아웃 명시화
try:
    resp = await asyncio.wait_for(
        self._client.post(url, json=payload),
        timeout=self._timeout
    )
except asyncio.TimeoutError:
    raise RuntimeError(f"Browser tool '{tool_name}' timed out after {self._timeout}s")
```

**수정 파일**: `services/browser_agent/main.py`

**기대 효과**: 무한 루프 시 25스텝 후 자동 종료, CPU 폭주 방지

---

### P1: 성능 및 신뢰성 개선 (1-2주)

#### P1-1. DOM 컨텍스트 압축 (Stagehand Context Builder 기반)

**현재 문제**: `browser_extract_content` 도구가 전체 페이지 `innerText`를 LLM에 전달함. 일반 웹페이지 기준 20,000-50,000 토큰 발생.

**구현 계획**:

새 도구 `browser_get_structured_dom` 추가 (`extension/entrypoints/content.ts`):
```typescript
case 'get_structured_dom': {
    const viewport = { top: window.scrollY, bottom: window.scrollY + window.innerHeight };
    const interactable = Array.from(document.querySelectorAll(
        'input, button, a[href], select, textarea, [role="button"], [role="link"], [onclick]'
    )).filter(el => {
        const rect = el.getBoundingClientRect();
        return rect.top < viewport.bottom && rect.bottom > viewport.top;
    }).map((el, idx) => ({
        idx,
        tag: el.tagName.toLowerCase(),
        type: (el as HTMLInputElement).type ?? null,
        id: el.id ?? null,
        name: (el as HTMLInputElement).name ?? null,
        placeholder: (el as HTMLInputElement).placeholder ?? null,
        text: (el as HTMLElement).innerText?.slice(0, 100) ?? null,
        href: (el as HTMLAnchorElement).href ?? null,
        ariaLabel: el.getAttribute('aria-label'),
        selector: el.id ? `#${el.id}` : el.getAttribute('name') ? `[name="${el.getAttribute('name')}"]` : null,
    }));

    result = {
        url: window.location.href,
        title: document.title,
        interactable_count: interactable.length,
        elements: interactable.slice(0, 50),
        page_text_preview: document.body.innerText.slice(0, 2000),
    };
    break;
}
```

`services/browser_agent/main.py`에 `browser_get_structured_dom` 도구 추가 및 시스템 프롬프트에서 `browser_extract_content` 대신 우선 사용 지시.

**수정 파일**:
- `extension/entrypoints/content.ts`
- `services/browser_agent/main.py`

**기대 효과**: 컨텍스트 사용량 70-90% 감소, LLM 추론 정확도 향상

---

#### P1-2. 선택적 스크린샷 전략

**현재 문제**: 시스템 프롬프트가 "After navigation, wait briefly then take a screenshot"를 지시하여 매 navigate 후 스크린샷 발생.

**구현 계획**:

시스템 프롬프트 수정 (`services/browser_agent/main.py`):
```python
# 변경: 스크린샷 사용 최소화
"""
...
1. After navigation, use browser_get_structured_dom to understand the page structure.
   Only use browser_screenshot when: (a) explicitly asked by user, (b) visual verification
   is absolutely necessary, (c) structured DOM fails to capture needed elements.
...
"""
```

스크린샷 압축 (`extension/entrypoints/background.ts`):
```typescript
const dataUrl = await browser.tabs.captureVisibleTab(windowId!, {
    format: 'jpeg',  // PNG → JPEG (크기 60-80% 감소)
    quality: 60,
});
```

**기대 효과**: 스크린샷 빈도 50-70% 감소, LLM 토큰 사용량 대폭 감소

---

#### P1-3. 토큰 자동 갱신 및 SW 재시작 복구 (EX-1)

**현재 문제**: Service Worker 30초 idle 후 재시작 시 `_accessToken`이 null이 되어 모든 API 호출 401.

**구현 계획** (`extension/entrypoints/background.ts`):

```typescript
async function getValidAccessToken(): Promise<string | null> {
    // 1. 메모리에 유효한 토큰이 있으면 반환
    if (_accessToken && _tokenExpiry && Date.now() < _tokenExpiry - 60_000) {
        return _accessToken;
    }

    // 2. refresh_token으로 갱신 시도
    const stored = await browser.storage.session.get('refreshToken');
    if (stored.refreshToken) {
        const success = await refreshTokens(stored.refreshToken as string);
        if (success && _accessToken) return _accessToken;
    }

    return null;
}

// background.ts 시작 시 세션 복구
export default defineBackground(() => {
    (async () => {
        const stored = await browser.storage.local.get('sessionId');
        const refreshStored = await browser.storage.session.get('refreshToken');

        if (stored.sessionId && refreshStored.refreshToken) {
            const ok = await refreshTokens(refreshStored.refreshToken as string);
            if (ok) {
                _sessionId = stored.sessionId as string;
                await startCommandsListener();
                console.log('Session restored after SW restart');
            }
        }
    })();
});
```

**수정 파일**: `extension/entrypoints/background.ts`

**기대 효과**: 30초 idle 후 투명하게 토큰 갱신, 사용자 재로그인 불필요

---

#### P1-4. AI 탭 그룹 누적 방지 (EX-3)

**현재 문제**: 로그인/로그아웃 반복 시마다 "AI Assistant" 탭 그룹이 새로 생성.

**구현 계획** (`extension/entrypoints/background.ts`):

```typescript
async function cleanupOldAITabGroups(): Promise<void> {
    try {
        const existingGroups = await browser.tabGroups.query({ title: 'AI Assistant' });
        for (const group of existingGroups) {
            if (group.id === _agentTabGroupId) continue;
            const tabs = await browser.tabs.query({ groupId: group.id });
            for (const tab of tabs) {
                if (tab.id) await browser.tabs.remove(tab.id);
            }
        }
    } catch (err) {
        console.warn('Failed to cleanup old AI tab groups:', err);
    }
}
// login() 성공 후 또는 getOrCreateAgentTabGroup() 호출 전 실행
```

**수정 파일**: `extension/entrypoints/background.ts`

**기대 효과**: 탭 그룹 누적 없음, 항상 단일 "AI Assistant" 그룹 유지

---

#### P1-5. session_id 검증 강화 (BA-2)

**현재 문제**: LLM이 `session_id` 파라미터를 누락하면 Gateway에서 404 반환, LangGraph 에러 상태.

**구현 계획** (`services/browser_agent/main.py`):

```python
@tool
async def browser_navigate(session_id: str, url: str) -> dict[str, Any]:
    if not session_id or not session_id.strip():
        return {
            "error": "session_id is missing. Always include session_id from the conversation state.",
            "success": False
        }
    return await _get_client().invoke(session_id, "navigate", {"url": url})
```

**수정 파일**: `services/browser_agent/main.py`

**기대 효과**: session_id 누락 시 즉각적이고 명확한 에러, LLM이 재시도 프롬프트 수신

---

#### P1-6. CORS 보안 강화 (GW-4)

**현재 문제**: `allow_origins=["*"]` 와일드카드 설정.

**구현 계획** (`services/gateway/main.py`):

```python
_extension_id = os.getenv("CHROME_EXTENSION_ID", "")
_cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]

if _extension_id:
    _cors_origins.append(f"chrome-extension://{_extension_id}")

if os.getenv("ENVIRONMENT", "production") == "development":
    _cors_origins.extend(["http://localhost:3000", "http://localhost:5173"])

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins if _cors_origins else ["*"],
    allow_credentials=bool(_cors_origins),
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
```

**수정 파일**: `services/gateway/main.py`, `infra/docker-compose.services.yml`

**기대 효과**: 알려진 Extension ID만 API 접근 가능, CSRF 위험 제거

---

### P2: 아키텍처 개선 (2-4주)

#### P2-1. Planner-Actor-Validator 패턴 도입 (Skyvern 2.0 기반)

**현재 문제**: Browser Agent가 단순 ReAct 패턴. 각 액션 후 검증 없이 다음 단계 진행. 실패한 클릭이나 잘못된 셀렉터로 인해 루프 발생.

**구현 계획**: `services/browser_agent/main.py`의 LangGraph를 3노드 구조로 리팩터링

```python
class BrowserAgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    session_id: str
    plan: str | None
    current_action: str | None
    validation_result: str | None
    retry_count: int
    max_retries: int


async def planner_node(state: BrowserAgentState) -> dict:
    """사용자 요청 분석 및 단계별 실행 계획 수립 (llama3.1:8b 사용)"""
    plan_llm = create_ollama_llm("llama3.1:8b", settings)
    response = await plan_llm.ainvoke([
        SystemMessage(content=PLANNER_PROMPT),
        *state["messages"],
    ])
    return {"plan": response.content, "retry_count": 0}


async def actor_node(state: BrowserAgentState) -> dict:
    """계획에 따라 도구 실행 (qwen2.5:14b 사용)"""
    # 기존 LLM + tools 로직 이식


async def validator_node(state: BrowserAgentState) -> dict:
    """액션 성공 여부 검증, 실패 시 Planner에 수정 신호 전달"""
    # get_page_info + browser_get_structured_dom으로 상태 확인


def route_after_validation(state: BrowserAgentState) -> str:
    if state["validation_result"] == "success":
        return "planner"
    elif state["retry_count"] < state["max_retries"]:
        return "actor"
    else:
        return "end"


# 그래프 구조
builder = StateGraph(BrowserAgentState)
builder.add_node("planner", planner_node)
builder.add_node("actor", actor_node)
builder.add_node("validator", validator_node)
builder.set_entry_point("planner")
builder.add_edge("planner", "actor")
builder.add_edge("actor", "validator")
builder.add_conditional_edges("validator", route_after_validation, {
    "planner": "planner",
    "actor": "actor",
    "end": END,
})
```

**수정 파일**: `services/browser_agent/main.py`

**기대 효과**: 액션 성공률 향상 (업계 데이터: 정확도 14% → 60%+ 향상), 무한 루프 방지

---

#### P2-2. 자기 치유 실행 (Self-Healing Execution, Stagehand 기반)

**현재 문제**: CSS 셀렉터 기반 클릭이 DOM 변경 후 실패해도 LLM이 동일 셀렉터 재시도.

**구현 계획**: `extension/entrypoints/content.ts`에 셀렉터 폴백 체인 추가

```typescript
case 'click': {
    const primarySelector = params.selector as string;
    const fallbackSelectors = params.fallback_selectors as string[] ?? [];

    // 1차: 제공된 셀렉터
    let el = document.querySelector(primarySelector) as HTMLElement | null;

    // 2차: 폴백 셀렉터 시도
    if (!el || !isVisible(el)) {
        for (const fallback of fallbackSelectors) {
            el = document.querySelector(fallback) as HTMLElement | null;
            if (el && isVisible(el)) break;
        }
    }

    // 3차: 텍스트 기반 검색
    if (!el && params.element_text) {
        const text = params.element_text as string;
        el = Array.from(document.querySelectorAll('button, a, [role="button"]'))
            .find(e => (e as HTMLElement).innerText?.includes(text)) as HTMLElement | null;
    }

    if (!el) throw new Error(`Element not found: ${primarySelector}`);
    el.click();
    break;
}
```

**수정 파일**: `extension/entrypoints/content.ts`, `services/browser_agent/main.py` (시스템 프롬프트)

**기대 효과**: CSS 셀렉터 실패 시 자동 복구, 재시도 LLM 호출 감소

---

#### P2-3. 컨텍스트 압축 전략

**현재 문제**: LangGraph `add_messages`가 전체 메시지 히스토리를 유지. 스크린샷 포함 시 8192 토큰 초과.

**구현 계획** (`services/browser_agent/main.py`):

```python
def compress_message_history(messages: list[BaseMessage]) -> list[BaseMessage]:
    """
    최근 4개 메시지 유지, 이전 ToolMessage의 스크린샷 base64 제거.
    browser-use 방식: 현재 스냅샷만 유지, 이전 스텝은 압축.
    """
    if len(messages) <= 6:
        return messages

    recent = messages[-4:]
    older = messages[:-4]

    compressed = []
    for msg in older:
        if isinstance(msg, ToolMessage):
            content = msg.content
            if isinstance(content, str) and 'data:image' in content:
                content = '[screenshot removed for context efficiency]'
            compressed.append(ToolMessage(content=content, tool_call_id=msg.tool_call_id))
        else:
            compressed.append(msg)

    return compressed + recent


# call_model 노드에서 압축 적용
async def call_model(state: AgentState) -> dict[str, Any]:
    messages = compress_message_history(state["messages"])
    # ...
```

**수정 파일**: `services/browser_agent/main.py`

**기대 효과**: 컨텍스트 오버플로우 방지, 멀티턴 대화에서 안정적 동작

---

#### P2-4. 병렬 도구 실행 레이스 컨디션 방지 (EX-5)

**현재 문제**: Browser Agent가 병렬 도구 호출 시 content script에 동시에 여러 DOM 조작 메시지 전달.

**구현 계획**:

`extension/entrypoints/content.ts`에 실행 큐 추가:
```typescript
let _executionQueue: Promise<void> = Promise.resolve();

// EXECUTE_BROWSER_COMMAND 핸들러에서
const result = _executionQueue.then(() => executeCommand(message.command!));
_executionQueue = result.then(() => {}).catch(() => {});
result.then(sendResponse);
return true;
```

`services/gateway/main.py`에 세션별 Semaphore 추가:
```python
_session_semaphores: dict[str, asyncio.Semaphore] = {}

async def invoke_browser_tool(session_id: str, ...):
    sem = _session_semaphores.setdefault(session_id, asyncio.Semaphore(1))
    async with sem:
        # ... 기존 로직
```

**수정 파일**: `extension/entrypoints/content.ts`, `services/gateway/main.py`

**기대 효과**: DOM 조작 순서 보장, 폼 입력 오작동 방지

---

### P3: 전략적 개선 (4주+)

#### P3-1. MCP 호환 브라우저 도구 레이어

**배경**: MCP가 LLM 도구 서버의 사실상 표준으로 수렴 중. Gateway 브라우저 도구를 MCP 호환으로 노출하면 Claude Desktop, Cursor 등에서 직접 재사용 가능.

**구현 계획** (`services/gateway/main.py`):

```python
@app.get("/mcp/v1/tools")
async def list_mcp_tools() -> dict:
    return {
        "tools": [
            {
                "name": "browser_navigate",
                "description": "Navigate the AI browser tab to a URL",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"},
                        "url": {"type": "string"}
                    },
                    "required": ["session_id", "url"]
                }
            },
            # ... 나머지 7개 도구
        ]
    }

@app.post("/mcp/v1/tools/{tool_name}/call")
async def call_mcp_tool(tool_name: str, body: dict, user: CurrentUser) -> dict:
    # 기존 invoke_browser_tool 로직 재사용
    pass
```

**기대 효과**: 외부 MCP 클라이언트에서 브라우저 도구 사용 가능, 생태계 확장성

---

#### P3-2. 수평 확장을 위한 Redis Streams 전환

**현재 문제**: `_session_queues`와 `_pending_invocations`가 단일 Gateway 프로세스 메모리에 저장. 수평 확장 불가.

**구현 계획** (`services/gateway/main.py`):

```python
async def invoke_browser_tool_redis(session_id: str, tool_name: str, params: dict) -> dict:
    inv_id = str(uuid.uuid4())
    stream_key = f"commands:{session_id}"
    result_key = f"result:{inv_id}"

    await redis.xadd(stream_key, {
        "inv_id": inv_id,
        "tool_name": tool_name,
        "params": json.dumps(params),
    })

    deadline = asyncio.get_running_loop().time() + 60
    while asyncio.get_running_loop().time() < deadline:
        result_raw = await redis.get(result_key)
        if result_raw:
            await redis.delete(result_key)
            return json.loads(result_raw)
        await asyncio.sleep(0.1)

    raise HTTPException(504, f"Tool '{tool_name}' timed out")
```

**수정 파일**: `services/gateway/main.py`, `infra/docker-compose.services.yml`

**기대 효과**: Gateway 다중 인스턴스 지원, 고가용성 확보

---

#### P3-3. CDP 직접 통신 (장기 로드맵)

**배경**: browser-use가 2025년 Playwright에서 CDP 직접 연결로 마이그레이션하여 스텝당 ~3초 달성. Extension Content Script 기반 접근은 크로스-오리진, Shadow DOM, Service Worker에서 한계 존재.

**단기 현실적 방안**: 하이브리드 모드
- Extension 연결된 경우: 현재 3홉 패턴 유지
- Extension 없는 경우: Playwright 브라우저 인스턴스로 폴백 (별도 서비스)

---

## 4. 구현 로드맵

### Phase 1: P0 긴급 수정 (Week 1)

```
Day 1:
  - P0-1: GW-1 메모리 누수 수정 + 테스트 작성
  - P0-2: GW-2 browser_controlling 플래그 수정 + 테스트

Day 2:
  - P0-3: GW-3 Extension 미연결 조기 실패 + 테스트
  - P0-4: CT-1/CT-2 보안 수정 + 테스트

Day 3:
  - P0-5: BA-1 무한 루프 방지 강화
  - 전체 P0 통합 테스트
  - 기존 34개 테스트 통과 확인
```

**의존성**: 없음 (P0는 독립적 수정)

### Phase 2: P1 성능 신뢰성 (Week 2)

```
Week 2 전반:
  - P1-1: DOM 컨텍스트 압축 (browser_get_structured_dom 도구)
  - P1-3: 토큰 자동 갱신 (background.ts)
  - P1-4: AI 탭 그룹 정리

Week 2 후반:
  - P1-2: 선택적 스크린샷 전략 + 시스템 프롬프트 최적화
  - P1-5: session_id 검증 강화
  - P1-6: CORS 보안 강화
  - P1 통합 테스트
```

**의존성**: P0 완료 후 시작

### Phase 3: P2 아키텍처 개선 (Week 3-4)

```
Week 3:
  - P2-1: Planner-Actor-Validator 패턴 (핵심 구현)
    - Planner 노드 (llama3.1:8b 사용)
    - Actor 노드 (기존 ReAct 로직 이식)
    - Validator 노드 (get_page_info + 구조화된 DOM 비교)
  - P2-3: 컨텍스트 압축 전략

Week 4:
  - P2-2: 자기 치유 실행 (셀렉터 폴백 체인)
  - P2-4: 병렬 실행 레이스 컨디션 방지
  - P2 통합 테스트 + 벤치마크 측정
```

**의존성**: P1-1 (DOM 컨텍스트 압축) 완료 후 P2-1 시작 가능

### Phase 4: P3 전략적 (5주+)

```
Week 5:
  - P3-1: MCP 호환 엔드포인트 (낮은 리스크, 높은 가치)

Week 6-8:
  - P3-2: Redis Streams 전환 (수평 확장)

Week 8+:
  - P3-3: CDP 직접 통신 아키텍처 평가 및 PoC
```

---

## 5. 기대 효과

### 5.1 정량적 기대 효과

| 개선 항목 | 현재 | 개선 후 목표 | 측정 방법 |
|----------|------|------------|---------|
| 메모리 안정성 | OOM 위험 (30분+) | 24시간 운영 안정 | Gateway 메모리 모니터링 |
| UI 배너 정확도 | 0% (항상 미표시) | 100% | 도구 실행 중 배너 표시 여부 |
| Extension 미연결 응답 시간 | 60초 | < 0.5초 | curl 응답 시간 측정 |
| 컨텍스트 토큰 사용량 | ~20,000-50,000 | ~3,000-8,000 | Ollama 토큰 카운터 |
| 스크린샷 빈도 | 매 navigate 후 | 요청 시에만 | 도구 호출 로그 |
| SW 재시작 복구 | 재로그인 필요 | 투명한 자동 복구 | 30초 idle 후 테스트 |
| 브라우저 작업 성공률 | 미측정 (추정 40-60%) | 70-85% | WebVoyager 스타일 벤치마크 |
| 스텝당 평균 실행 시간 | 7-15초 (추정) | 5-8초 | 단계별 타임스탬프 로깅 |

### 5.2 정성적 기대 효과

- **사용자 경험**: 브라우저 제어 배너 표시로 에이전트 동작 가시성 확보 (P0-2)
- **개발자 경험**: 명확한 에러 메시지로 디버깅 시간 단축 (P0-3, P1-5)
- **보안**: 악의적 URL 실행 차단으로 사용자 데이터 보호 (P0-4, P1-6)
- **신뢰성**: 네트워크 순단 후 자동 복구로 사용자 재로그인 불필요 (P1-3)
- **확장성**: MCP 표준 채택으로 Claude Desktop 등 외부 클라이언트 연동 가능 (P3-1)
- **유지보수성**: Planner-Actor-Validator 패턴으로 에이전트 동작 예측 가능성 향상 (P2-1)

### 5.3 리스크 및 완화 방안

| 리스크 | 가능성 | 영향 | 완화 방안 |
|--------|--------|------|---------|
| P2-1 PAV 패턴 도입 시 응답 지연 증가 | 중간 | 중간 | Planner에 경량 모델(llama3.1:8b) 사용, 단순 작업은 기존 ReAct 유지 |
| P1-1 DOM 압축으로 중요 정보 누락 | 낮음 | 높음 | 압축 실패 시 전체 추출로 폴백, 임계값 조정 |
| P3-2 Redis Streams 마이그레이션 중 서비스 중단 | 낮음 | 높음 | Feature flag로 점진적 전환, 기존 코드 유지 후 완전 검증 후 제거 |
| Ollama 로컬 모델 성능 한계 | 높음 | 중간 | 모델별 역할 분리 (llama3.1:8b 분류, qwen2.5:14b 도구 실행), 컨텍스트 최소화 |

---

## 6. 테스트 전략

### 6.1 P0 수정 테스트

**GW-1 메모리 누수 테스트**:
```python
async def test_stale_invocation_cleanup():
    """120초 초과 invocation이 자동 정리됨을 검증"""
    # 1. inv_id 생성, Future 등록 (타임스탬프 포함)
    # 2. 120초 경과 시뮬레이션 (mock loop.time())
    # 3. cleanup 태스크 실행
    # 4. _pending_invocations가 비어있음 확인
```

**GW-2 플래그 버그 테스트**:
```python
async def test_browser_controlling_flag_cleared_after_invoke():
    """invoke 완료 후 controlling 플래그가 False로 변경됨을 검증"""
    # 1. invoke 시작 → controlling=True 확인
    # 2. result 수신 → controlling=False 확인
```

**EX-1 토큰 복구 테스트**:
```typescript
it('should restore access token after SW restart using refresh token', async () => {
    // 1. 리프레시 토큰을 storage.session에 저장
    // 2. 메모리 토큰 없음 시뮬레이션
    // 3. Keycloak 토큰 갱신 모킹
    // 4. 새 액세스 토큰이 반환됨 확인
});
```

### 6.2 벤치마크 테스트

P2 완료 후 표준 브라우저 작업 시나리오 5개로 측정:
1. YouTube 검색 및 영상 클릭
2. Google 검색 후 결과 페이지 내용 추출
3. 폼 작성 (이름, 이메일 입력 후 제출)
4. 여러 페이지 탐색 (링크 클릭, 뒤로 가기)
5. 존재하지 않는 요소 클릭 (에러 복구 시나리오)

각 시나리오를 5회 반복, 성공률 및 평균 소요 시간 측정.

---

## 7. 참고 자료

- [browser-use GitHub](https://github.com/browser-use/browser-use) - CDP 직접 연결, 하이브리드 DOM+Vision
- [Stagehand](https://github.com/browserbase/stagehand) - Context Builder, 자기 치유 실행
- [Skyvern](https://github.com/Skyvern-AI/skyvern) - Planner-Actor-Validator 패턴
- [Playwright MCP](https://github.com/microsoft/playwright-mcp) - MCP 표준 도구 서버
- [Speed Matters: browser-use benchmark](https://browser-use.com/posts/speed-matters)
- [Stagehand v3 Architecture](https://www.browserbase.com/blog/stagehand-v3)
- [Skyvern 2.0 Eval Results](https://www.skyvern.com/blog/skyvern-2-0-state-of-the-art-web-navigation-with-85-8-on-webvoyager-eval/)
- `docs/edge-cases-backend.md` - 백엔드 버그 상세 분석
- `docs/edge-cases-extension.md` - Extension 버그 상세 분석

---

*이 문서는 2026-02-28 기준 코드베이스 분석 및 browser-use, Stagehand v3, Skyvern 2.0, Playwright MCP 벤치마킹을 기반으로 작성되었습니다.*
