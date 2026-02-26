# 백엔드 엣지 케이스 및 버그 분석

서비스: Gateway, Browser Agent, Orchestrator, Chat Agent, Shared ACP

---

## Gateway (`services/gateway/main.py`)

### 🔴 [GW-1] asyncio.Queue / Future 메모리 누수

**위치**: `_session_queues`, `_pending_invocations` dict
**시나리오**: Extension이 commands SSE 채널을 끊고 다시 연결하지 않은 채 세션이 만료되면, `_session_queues[session_id]`와 `_pending_invocations[inv_id]`가 영구적으로 메모리에 남는다. Browser Agent가 `invoke_browser_tool()`을 호출하면 Future가 생성되지만 아무도 `set_result()` 또는 `set_exception()`을 호출하지 않아 60초 timeout 이후에도 메모리에 잔류한다.

**재현 조건**: 브라우저 도구 실행 중 Extension crash → Gateway 재시작 없이 지속 사용
**영향**: 장시간 운영 시 OOM, 새 호출이 오래된 Future를 덮어쓰지 못함

**권장 수정**:
```python
# cleanup_session() 호출 시 pending invocations도 정리
async def cleanup_session(session_id: str):
    if session_id in _session_queues:
        del _session_queues[session_id]
    # inv_id별로 session_id 역매핑 필요
    for inv_id, future in list(_pending_invocations.items()):
        if inv_id.startswith(session_id):  # 역매핑 구조 변경 필요
            if not future.done():
                future.cancel()
            del _pending_invocations[inv_id]
```

---

### 🔴 [GW-2] `_browser_controlling` 플래그 로직 버그

**위치**: `main.py` 라인 533–536 (추정)
**코드**:
```python
is_controlling = any(
    k.startswith(session_id)
    for k in _pending_invocations.keys()
)
```
**문제**: `_pending_invocations`의 key는 `inv_id` (UUID 형식)이며, `session_id`로 시작하지 않는다. 따라서 이 조건은 항상 `False`를 반환하여 `/sessions/{id}/browser-status` 엔드포인트가 항상 `{"controlling": false}`를 반환한다.

**영향**: Extension 사이드패널의 BrowserControlBanner가 절대 표시되지 않음. 브라우저 제어 중임을 사용자에게 알릴 수 없음.

**권장 수정**:
```python
# inv_id → session_id 역매핑 구조 추가
_invocation_session_map: dict[str, str] = {}  # inv_id → session_id

# invoke 시 등록
_invocation_session_map[inv_id] = session_id

# 확인 시
is_controlling = any(
    _invocation_session_map.get(k) == session_id
    for k in _pending_invocations.keys()
)
```

---

### 🔴 [GW-3] Extension 미연결 상태에서 invoke 블로킹

**위치**: `invoke_browser_tool()` 함수
**시나리오**: Browser Agent가 `POST /sessions/{id}/browser-tools/invoke`를 호출했을 때 Extension이 commands SSE 채널에 연결되어 있지 않으면 Queue에 이벤트가 쌓이지만 소비되지 않는다. 60초 timeout까지 Browser Agent 스레드가 블로킹된다.

**재현 조건**: Extension 미설치 상태, Service Worker 종료(30초 idle), 네트워크 단절
**영향**: Browser Agent 전체 응답이 60초 지연, LangGraph ReAct 루프 내에서 여러 도구 호출 시 누적 지연

**권장 수정**:
```python
async def invoke_browser_tool(session_id, tool_name, params):
    if session_id not in _session_queues:
        raise HTTPException(400, "Extension not connected")
    # ... 기존 로직
```

---

### 🟠 [GW-4] CORS 와일드카드 설정

**위치**: `CORSMiddleware` 설정
**코드**:
```python
allow_origins=["*"]
```
**문제**: 모든 출처에서 API 호출 허용. JWT 인증이 있어도 CORS 우회 가능성 존재. 프로덕션 환경에서 CSRF 위험.

**권장 수정**: Extension ID를 allowlist에 추가
```python
allow_origins=[
    "chrome-extension://YOUR_EXTENSION_ID",
    "http://localhost:3000",  # 개발 환경
]
```

---

### 🟠 [GW-5] JWT 만료 중 장시간 브라우저 도구 실행

**시나리오**:
1. 사용자가 채팅 메시지 전송 (JWT 유효)
2. Browser Agent가 복잡한 웹 태스크 수행 (여러 도구 호출, 3~5분 소요)
3. 중간에 JWT 만료 (기본 access_token TTL 5분)
4. Browser Agent가 Gateway에 `invoke_browser_tool` 호출 시 인증 없이 진행 (internal 호출이므로)
5. Extension의 postToolResult는 만료된 토큰으로 전송 시도 → 401

**영향**: 브라우저 도구 실행 체인 중단, 부분 실행 상태로 남음

**권장 수정**: Extension에서 도구 결과 전송 전 토큰 유효성 확인 및 refresh 시도

---

### 🟠 [GW-6] 동시 invoke 호출에서 Future 덮어쓰기

**시나리오**: LangGraph ReAct 루프에서 병렬 도구 호출이 발생하거나, 이전 호출이 타임아웃 후 새 호출이 동일 `inv_id`를 사용하는 경우 (이론적).
실제로는 `uuid4()`를 사용하므로 충돌 가능성 극히 낮지만, 타임아웃 후 cleanup 없이 `_pending_invocations[inv_id]`가 남아 있을 경우 다음 호출에서 오래된 Future가 참조될 수 있다.

---

### 🟡 [GW-7] Session Queue 최대 크기 미설정

**위치**: `asyncio.Queue()` 생성
**문제**: `maxsize` 파라미터 없이 Queue 생성. Extension이 SSE를 소비하지 않으면 Queue가 무한 증가.

```python
# 현재
_session_queues[session_id] = asyncio.Queue()

# 권장
_session_queues[session_id] = asyncio.Queue(maxsize=100)
```

---

### 🟡 [GW-8] SSE 연결 끊김 시 Queue 미정리

**시나리오**: Extension이 commands SSE 연결을 끊을 때(`GET /sessions/{id}/commands` disconnect) Queue가 정리되지 않으면 다음 Extension 연결이 이전 세션의 오래된 이벤트를 받을 수 있다.

---

### 🟡 [GW-9] `/sessions/{id}/browser-tools/invoke` 인증 미적용

**문제**: 이 엔드포인트는 Browser Agent 서비스만 호출해야 하지만 인증이 없다. 내부 네트워크에서는 괜찮으나 Gateway가 외부에 노출될 경우 누구나 임의 도구를 Extension에 push 가능.

---

## Browser Agent (`services/browser_agent/main.py`)

### 🔴 [BA-1] LangGraph ReAct 무한 루프

**위치**: LangGraph `create_react_agent()` 설정
**문제**: `max_iterations` 또는 `recursion_limit`이 설정되지 않았다. LLM이 동일한 도구를 반복 호출하거나 목표 달성 불가 상황에서 루프를 탈출하지 못한다.

**재현 조건**: 존재하지 않는 버튼 클릭 요청, 로그인 필요 페이지 접근 시도
**영향**: Browser Agent 프로세스 CPU 100%, Gateway 연결 타임아웃

**권장 수정**:
```python
graph = create_react_agent(
    model=llm,
    tools=tools,
    # LangGraph 0.2+
    checkpointer=checkpointer,
)
# 또는 compile 시
graph.step_timeout = 30  # 각 스텝 30초 제한
```

또는 `ainvoke` 시:
```python
config = {"recursion_limit": 25, "configurable": {"thread_id": thread_id}}
```

---

### 🔴 [BA-2] `session_id` 누락 시 도구 호출 실패

**시나리오**: LLM이 `session_id` 파라미터를 tool call에 포함하지 않으면 도구가 `None` 또는 빈 문자열로 Gateway에 요청한다. Gateway는 `session_id`가 없으면 Queue를 찾지 못해 500 에러 반환.

**재현 조건**: 컨텍스트 길이 초과 시 시스템 프롬프트 truncation, 복잡한 멀티턴 대화
**영향**: 브라우저 도구 전체 실패, LangGraph 에러 상태

**권장 수정**: 도구 실행 레이어에서 `session_id` 검증 추가
```python
async def _call_gateway_tool(session_id: str, ...):
    if not session_id:
        return {"error": "session_id is required but was not provided by LLM"}
```

---

### 🟠 [BA-3] httpx 클라이언트 호출마다 생성

**위치**: 각 도구 함수 내부
**문제**: `async with httpx.AsyncClient() as client:` 패턴이 도구 호출마다 새 HTTP 클라이언트를 생성하고 닫는다. TCP 연결 재사용 없음.

**영향**: Gateway 호출마다 TCP handshake 오버헤드. 높은 빈도의 도구 호출 시 성능 저하.

**권장 수정**: 앱 lifespan에서 클라이언트 풀 생성 후 공유
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http_client = httpx.AsyncClient(
        base_url=settings.gateway_url,
        timeout=65.0,
    )
    yield
    await app.state.http_client.aclose()
```

---

### 🟠 [BA-4] 페이지 로드 완료 전 다음 도구 실행

**시나리오**: `navigate` 도구가 URL을 열고 즉시 반환하면, 다음 `click` 또는 `find_element` 도구가 페이지 로드 전에 실행된다. Extension content script가 아직 주입되지 않았거나 DOM이 완성되지 않은 상태.

**재현 조건**: 느린 네트워크, SPA 클라이언트 렌더링 페이지
**영향**: click 도구가 존재하지 않는 요소를 찾아 실패, LLM이 재시도하며 루프

---

### 🟠 [BA-5] DSN 변환 로직 취약성

**위치**: `_psycopg_connection_string()` 함수
**코드**:
```python
return url.replace("postgresql+asyncpg://", "postgresql://", 1)
```
**문제**: URL이 `postgresql+asyncpg://`로 시작하지 않으면 원본 URL이 그대로 반환되어 psycopg가 파싱 에러를 낼 수 있다. 환경변수 오입력 시 silent failure.

**권장 수정**:
```python
def _psycopg_connection_string(url: str) -> str:
    if not url.startswith("postgresql+asyncpg://"):
        raise ValueError(f"Expected postgresql+asyncpg:// DSN, got: {url[:30]}")
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)
```

---

### 🟡 [BA-6] LLM 응답 미검증

**문제**: LLM이 올바르지 않은 도구 호출 JSON을 반환하거나 도구 이름이 등록된 것과 다를 경우 LangGraph가 예외를 발생시키고 전체 실행이 실패한다.

---

### 🟡 [BA-7] 스크린샷 파일 크기 미제한

**시나리오**: 큰 화면 또는 고해상도 스크린샷이 메모리와 네트워크 대역폭을 과도하게 소비한다. LLM 컨텍스트에 이미지를 포함하면 토큰 수가 폭발적으로 증가.

---

## Orchestrator (`services/ochestrator/main.py`)

### 🔴 [OR-1] 서브 에이전트 전체 실패 시 에러 전파 없음

**시나리오**: Chat Agent와 Browser Agent 모두 다운된 경우 Orchestrator가 ACP 호출 실패를 사용자에게 `AIMessage` 오류 메시지로만 전달한다. 클라이언트(Gateway)는 `status: completed`를 받게 되어 에러를 알 수 없다.

**권장 수정**: 서브 에이전트 호출 실패 시 `status: failed` 반환

---

### 🟠 [OR-2] LLM 의도 분류 실패 시 기본값 `chat_agent`

**시나리오**: Ollama가 응답하지 않거나 JSON 파싱이 완전히 실패하면 `chat_agent`로 fallback된다. 브라우저 제어 요청이 채팅으로 잘못 라우팅될 수 있다.

**개선**: 분류 실패 횟수 메트릭 수집, 연속 실패 시 Circuit Breaker

---

### 🟡 [OR-3] `session_id=None` 전파

**시나리오**: Gateway에서 `thread_id`를 포함하지 않고 ACP 호출 시, `session_id`가 `None`으로 서브 에이전트에 전달된다. Browser Agent의 모든 도구가 `None` `session_id`로 Gateway에 요청.

---

### 🟡 [OR-4] 의도 분류 LLM 응답 파싱 취약성

**문제**: LLM이 ```json ... ``` 코드 블록 형식으로 응답하면 JSON 파싱 실패. 현재 파싱 로직이 순수 JSON만 처리.

```python
# 취약한 파싱
json.loads(content)

# 개선: 코드 블록 제거 후 파싱
import re
match = re.search(r'\{.*?\}', content, re.DOTALL)
if match:
    json.loads(match.group())
```

---

## Chat Agent (`services/chat_agent/main.py`)

### 🔴 [CA-1] DuckDuckGo 레이트 리밋으로 인한 도구 실패 루프

**시나리오**: 단기간에 여러 검색 요청 시 DuckDuckGo가 429 또는 202를 반환한다. Chat Agent LangGraph가 검색 실패를 인지하고 재시도하며 루프에 빠진다.

**영향**: Chat Agent 응답 불가, 무한 재시도로 DuckDuckGo IP 블록 심화

**권장 수정**:
- 검색 실패 시 최대 3회 재시도 후 에러 메시지 반환
- Exponential backoff 적용
- 대체 검색 엔진 (SerpAPI, Brave Search) fallback

---

### 🟠 [CA-2] `fetch_webpage` 리다이렉트 루프

**시나리오**: 인증이 필요한 페이지, 쿠키 미설치 페이지 등에서 무한 리다이렉트 발생 시 httpx가 `TooManyRedirects` 예외를 던진다. 적절한 처리가 없으면 Chat Agent crash.

**권장 수정**:
```python
response = await client.get(url, follow_redirects=True, max_redirects=5)
```

---

### 🟠 [CA-3] `max_chars` 截断으로 컨텍스트 손실

**시나리오**: 웹페이지를 `max_chars=5000`으로 자를 때 중요한 내용이 잘릴 수 있다. 특히 긴 기사에서 답변 관련 내용이 후반부에 있는 경우.

---

### 🟡 [CA-4] LangGraph 무한 도구 루프 (Chat Agent)

**Browser Agent와 동일**: `recursion_limit` 미설정으로 DuckDuckGo 검색이 연속 실패하면 루프 가능.

---

## Shared ACP (`services/shared/src/shared/acp/`)

### 🟠 [ACP-1] `astream_events` 타임아웃 미설정

**위치**: `server.py`의 스트리밍 엔드포인트
**문제**: `graph.astream_events()`에 타임아웃이 없다. 에이전트가 응답을 생성하지 못하면 SSE 연결이 영구히 열려 있는다.

---

### 🟡 [ACP-2] SSE 파싱 엣지 케이스

**위치**: `client.py`의 `run_stream()`
**시나리오**: SSE 이벤트 데이터에 개행 문자가 포함되거나 `data:` 접두사 없는 코멘트 라인(`:`으로 시작) 처리 미비.

---

### 🟡 [ACP-3] ACPClient 기본 타임아웃 120초

**문제**: Browser Agent의 도구 실행 체인이 120초를 초과하면 Orchestrator → Browser Agent ACP 연결이 끊긴다. Browser Agent는 계속 실행 중이지만 Orchestrator는 실패로 처리.

**권장**: `run_stream()`은 타임아웃 없이 무제한, `run()`은 명시적 타임아웃 설정

---

## Ollama / LLM 공통

### 🟠 [LLM-1] 컨텍스트 윈도우 오버플로우

**설정**: `LLM_NUM_CTX=8192`
**시나리오**: 멀티턴 대화에서 메시지 히스토리가 길어지고 스크린샷/웹페이지 내용이 컨텍스트에 포함되면 8192 토큰을 초과한다. Ollama는 초과 토큰을 자르며 초기 시스템 프롬프트(`session_id` 포함)가 잘릴 수 있다.

**영향**: [BA-2]와 연동하여 `session_id` 누락 → 도구 실패

---

### 🟡 [LLM-2] Ollama 모델 미설치 시 silent failure

**시나리오**: `qwen2.5:14b` 모델이 설치되지 않은 Ollama에 요청하면 404 에러. LangGraph가 이를 tool error로 처리하지 못하고 전체 실행 실패.

**권장**: 앱 시작 시 Ollama 모델 존재 여부 확인 health check
