# Browser Agent

LangGraph Progress Ledger 에이전트. Gateway 서비스를 통해 브라우저 확장의 DOM 제어 도구를 호출한다.

## 책임

- Orchestrator로부터 ACP 요청을 수신하고 LangGraph Progress Ledger 그래프로 처리한다.
- `GatewayBrowserToolsClient`를 통해 Gateway에 브라우저 도구 호출 요청을 전달한다.
- Gateway → SSE → Extension → DOM 액션 → 결과 반환까지 대기 (blocking, 65s timeout).
- `progress_check` 노드로 루프·오류를 감지하고, `replan` 노드로 전략을 재수립한다.
- 대화 체크포인트를 PostgreSQL에 저장한다.

## 브라우저 도구 호출 흐름 (webMCP-inspired)

```
Browser Agent
  │
  │  POST /sessions/{id}/browser-tools/invoke  (blocking, 65s)
  ▼
Gateway (asyncio.Queue → SSE)
  │
  │  SSE event: { inv_id, tool_name, params }
  ▼
Extension (배경 스크립트 또는 content script에서 DOM 액션 실행)
  │
  │  POST /sessions/{id}/browser-tools/result/{inv_id}
  ▼
Gateway asyncio.Future.set_result() → response returned
```

MCP 서버 중간 계층 없이 Browser Agent가 Gateway에 직접 HTTP 요청을 보낸다.

## 그래프 구조 (Progress Ledger)

```
START ──▶ actor ──(tool calls)──▶ tools ──▶ progress_check ──(making progress)──▶ actor
                └──(no tool calls)──▶ END        └──(stuck/error, stall < 3)──▶ actor
                                                  └──(stall_count >= 3)────────▶ replan ──▶ actor
```

| 노드 | 역할 |
|------|------|
| `actor` | 멀티모달 LLM(`qwen2.5vl:7b`)으로 다음 도구 호출 결정. screenshot artifact를 이미지로 직접 수신 |
| `tools` | LangGraph `ToolNode`. 10개 브라우저 도구 실행 |
| `progress_check` | LLM 호출 없이 휴리스틱으로 루프 감지 (최근 3개 액션이 동일 도구) 및 오류 감지 |
| `replan` | 경량 LLM(`qwen3:8b`)으로 새 전략 생성. 이전 시도 목록을 컨텍스트로 전달 |

`stall_count`가 3 이상이면 `replan` 노드를 거친 후 `actor`로 돌아간다.

## 사용 가능 도구

| 도구 | 설명 |
|------|------|
| `navigate` | URL로 브라우저 탭 탐색 |
| `click` | CSS 선택자로 요소 클릭. `fallback_selectors`, `element_text` 지원 |
| `type` | 입력 필드에 텍스트 입력 (`clear_first` 옵션) |
| `scroll` | 페이지 또는 특정 요소 스크롤 (`up`/`down`/`left`/`right`) |
| `screenshot` | 현재 탭 스크린샷 캡처. 인터랙티브 요소에 번호 배지 표시. `content_and_artifact` 형식으로 반환 |
| `click_by_mark_id` | screenshot에서 표시된 번호 배지로 요소 클릭 (CSS 선택자 추정 불필요) |
| `extract_content` | 페이지 또는 특정 요소 텍스트 추출 |
| `wait_for_element` | 요소가 DOM에 나타날 때까지 대기 |
| `get_page_info` | 현재 페이지 URL, 제목, ready state 조회 |
| `get_structured_dom` | 뷰포트 내 인터랙티브 요소만 압축 표현으로 반환. `extract_content`보다 토큰 효율적 |

> **주의**: 모든 도구에 `session_id` 파라미터가 필수다. 시스템 프롬프트가 LLM에게 매 도구 호출에 현재 `session_id`를 포함하도록 지시한다.

## ACP 엔드포인트

### `POST /runs`

동기 에이전트 실행.

**요청 스키마**

| 필드 | 타입 | 설명 |
|------|------|------|
| `run_id` | string | 실행 식별자 (빈 문자열이면 자동 생성) |
| `thread_id` | string | 대화 스레드 식별자 (= session_id) |
| `input` | object | `{"messages": [{"role": "human", "content": "..."}], "session_id": "..."}` |

**응답 스키마**

| 필드 | 타입 | 설명 |
|------|------|------|
| `run_id` | string | 실행 식별자 |
| `status` | string | `"completed"` \| `"failed"` |
| `output` | object \| null | 그래프 최종 상태 (`{"messages": [...]}`) |
| `error` | string \| null | 오류 메시지 (실패 시) |

### `POST /runs/stream`

스트리밍 에이전트 실행. LangGraph `astream_events` v2 기반으로 SSE 이벤트를 전송한다.

**SSE 이벤트 타입**

| `type` | 설명 |
|--------|------|
| `token` | LLM 생성 토큰 (`content` 필드 포함) |
| `tool_start` | 도구 호출 시작 (`name` 필드 포함) |
| `tool_end` | 도구 호출 완료 (`name` 필드 포함) |
| `done` | 스트림 종료 (`run_id` 필드 포함) |
| `error` | 오류 발생 (`error` 필드 포함) |

### `GET /health`

Gateway 헬스도 함께 확인한다.

**응답**: `{"status": "ok" | "degraded", "service": "browser-agent", "gateway": "ok" | "unavailable"}`

## 의존 서비스

| 서비스 | 용도 |
|--------|------|
| PostgreSQL `:5432` | LangGraph `AsyncPostgresSaver` 대화 체크포인트 저장 |
| Ollama `:11434` | actor LLM (`qwen2.5vl:7b`), planner LLM (`qwen3:8b`). Docker 내부에서 `host.docker.internal:11434` 접근 |
| Gateway `:8000` | 브라우저 도구 브로커 (`POST /sessions/{id}/browser-tools/invoke`) |

## 환경변수

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `DATABASE_URL` | string | `postgresql+asyncpg://postgres:password@postgres:5432/browser_agent` | PostgreSQL DSN (내부에서 `postgresql://`로 변환해 psycopg에 전달) |
| `BROWSER_MODEL` | string | `qwen2.5vl:7b` | actor LLM. 멀티모달 모델 권장 (screenshot 이미지 직접 인식) |
| `PLANNER_MODEL` | string | `qwen3:8b` | planner/replan LLM. 속도 우선 경량 모델 |
| `GATEWAY_URL` | string | `http://gateway:8000` | Gateway 서비스 URL |
| `BROWSER_TOOL_TIMEOUT` | float | `65.0` | 브라우저 도구 응답 대기 최대 시간 (초). Gateway timeout보다 5초 길게 설정 |
| `OLLAMA_BASE_URL` | string | `http://host.docker.internal:11434` | Ollama 서버 URL |
| `LLM_TEMPERATURE` | float | `0.0` | LLM 온도 |
| `LLM_NUM_CTX` | int | `8192` | 컨텍스트 윈도우 크기 |

## 로컬 실행

```bash
cd services/browser_agent
uv pip install -e ../shared -e .
uvicorn main:app --host 0.0.0.0 --port 8003 --reload
```

Docker로 실행:

```bash
cd infra
docker compose -f docker-compose.services.yml up --build browser-agent
```

## 구현 주의사항

- `screenshot` 도구는 `response_format="content_and_artifact"`를 사용한다. LangGraph `ToolNode`가 artifact를 `ToolMessage.artifact`에 저장하고, `actor_node`의 `_enrich_screenshot_messages()`가 이를 멀티모달 `image_url` content로 재구성해 `qwen2.5vl`에 전달한다.
- `_compress_messages()`는 오래된 screenshot artifact를 제거해 컨텍스트 윈도우를 관리한다.
- `progress_check` 노드는 LLM을 사용하지 않는다. 최근 3개 액션이 동일 도구이면 loop로 판단하고, ToolMessage에 "error"/"failed"/"exception"/"timeout" 키워드가 있으면 오류로 판단한다.
- `BROWSER_TOOL_TIMEOUT`을 Gateway의 `BROWSER_TOOL_TIMEOUT`보다 5초 길게 설정하는 이유: Gateway가 먼저 504를 반환해야 Browser Agent에서 의미 있는 오류를 받을 수 있기 때문이다.

## 파일 구조

```
services/browser_agent/
├── main.py              # FastAPI 애플리케이션, lifespan (Gateway 클라이언트 초기화)
├── settings.py          # BrowserAgentSettings (환경변수)
├── pyproject.toml       # 패키지 메타데이터 및 의존성
├── graph/
│   ├── builder.py       # build_browser_graph (Progress Ledger 그래프 컴파일)
│   ├── nodes.py         # actor_node, progress_check_node, replan_node
│   ├── router.py        # route_after_actor, route_after_progress
│   ├── state.py         # AgentState (messages, session_id, stall_count, progress_ledger, action_history)
│   ├── prompts.py       # SYSTEM_PROMPT, REPLAN_SYSTEM_PROMPT
│   └── utils.py         # _compress_messages
├── tools/
│   ├── browser_tools.py  # 10개 브라우저 도구 정의 (LangChain @tool)
│   ├── gateway_client.py # GatewayBrowserToolsClient (httpx, singleton)
│   └── result_formatter.py # 도구 실행 결과 포맷팅
├── tests/
│   ├── test_browser_tools.py  # 도구별 invoke 동작 테스트
│   ├── test_gateway_client.py # GatewayBrowserToolsClient HTTP 테스트
│   └── test_progress_ledger.py # progress_check, replan 로직 테스트
└── Dockerfile           # python:3.13-slim + libpq-dev, 포트 8003
```
