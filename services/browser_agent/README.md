# Browser Agent

LangGraph ReAct 에이전트. Browser Relay MCP 서버에서 로드한 브라우저 제어 도구로 DOM 조작을 수행한다.

## 책임

- Orchestrator로부터 ACP 요청을 수신하고 LangGraph ReAct 그래프로 처리한다.
- 앱 시작 시 Browser Relay MCP 서버(`streamable-HTTP`)에 연결하고 전체 생명주기 동안 세션을 유지한다.
- MCP 세션에서 브라우저 제어 도구를 로드해 LLM에 바인딩한다.
- 대화 체크포인트를 PostgreSQL에 저장한다.

## MCP 연결

`langchain-mcp-adapters`의 `load_mcp_tools()`로 MCP 도구를 LangChain 도구로 변환한다. 연결은 앱 lifespan에서 한 번 수행되고 shutdown 시 해제된다. 요청마다 재연결하지 않는다.

```
Browser Agent ──streamable-HTTP──▶ Browser Relay MCP :8010/mcp
                     (앱 시작 시 1회 연결, 전체 생명주기 유지)
```

## 사용 가능 도구 (MCP에서 로드)

Browser Relay MCP가 노출하는 도구 목록이다. 실제 로드된 도구는 시작 로그에서 확인할 수 있다.

| 도구 | 설명 |
|------|------|
| `browser_navigate` | URL로 탐색 |
| `browser_click` | 요소 클릭 |
| `browser_type` | 텍스트 입력 |
| `browser_scroll` | 페이지 스크롤 |
| `browser_screenshot` | 스크린샷 캡처 |
| `browser_extract_content` | 페이지 텍스트 추출 |
| `browser_wait_for_element` | 요소 출현 대기 |
| `browser_evaluate_js` | JavaScript 실행 |
| `get_page_info` | 현재 페이지 URL/제목 조회 |

## 그래프 구조

```
agent ──(tools_condition: tool call 있음)──▶ tools ──▶ agent
      └──(tools_condition: tool call 없음)──▶ END
```

`agent` 노드 실행 시 메시지 목록 첫 번째 항목이 `SystemMessage`가 아니면 자동으로 삽입한다. 시스템 프롬프트는 `session_id`를 모든 도구 호출에 포함하도록 LLM에 지시한다.

MCP 도구가 0개로 로드된 경우 LLM에 도구를 바인딩하지 않고 그래프를 구성한다. 이 상태에서는 브라우저 제어가 불가능하며 시작 로그에 경고가 출력된다.

## ACP 엔드포인트

### `POST /runs`

동기 에이전트 실행.

**요청 스키마**

| 필드 | 타입 | 설명 |
|------|------|------|
| `run_id` | string | 실행 식별자 (빈 문자열이면 자동 생성) |
| `thread_id` | string | 대화 스레드 식별자 |
| `input` | object | `{"messages": [{"role": "human", "content": "..."}]}` |

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

- **응답**: `{"status": "ok"}`

## 의존 서비스

| 서비스 | 용도 |
|--------|------|
| PostgreSQL `:5432` | LangGraph `AsyncPostgresSaver` 대화 체크포인트 저장 |
| Ollama `:11434` | 브라우저 제어 LLM (`qwen2.5:14b`), Docker 내부에서 `host.docker.internal:11434` 접근 |
| Browser Relay MCP `:8010` | 브라우저 제어 도구 제공 (streamable-HTTP `/mcp`) |

## 환경변수

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `DATABASE_URL` | string | `postgresql+asyncpg://postgres:password@postgres:5432/browser_agent` | PostgreSQL DSN (내부에서 `postgresql://`로 변환해 psycopg에 전달) |
| `BROWSER_MODEL` | string | `qwen2.5:14b` | Ollama 모델 이름 |
| `BROWSER_RELAY_MCP_URL` | string | `http://browser-relay:8010/mcp` | Browser Relay MCP 엔드포인트 |
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
docker compose -f docker-compose.services.yml up --build browser-agent
```

## 구현 주의사항

- MCP 연결은 `MCPConnection` 클래스가 관리한다. `streamablehttp_client`와 `ClientSession` 컨텍스트 매니저를 수동으로 진입(enter)하고 종료(exit)한다. `async with` 블록으로 래핑하면 lifespan 범위를 벗어날 수 없기 때문이다.
- MCP 연결 실패 시 FastAPI 앱 자체는 시작되지만 도구 없이 동작한다. 연결 오류를 앱 시작 실패로 처리하지 않는다.
- 도구 호출 실패 시 LLM에게 1회 재시도를 지시하는 시스템 프롬프트가 있다. 재시도 로직은 LLM의 추론에 위임한다.

## 파일 구조

```
services/browser_agent/
├── main.py          # FastAPI 애플리케이션, MCPConnection, LangGraph 그래프, ACP 라우터
├── pyproject.toml   # 패키지 메타데이터 및 의존성
└── Dockerfile       # python:3.13-slim + libpq-dev, 포트 8003
```
