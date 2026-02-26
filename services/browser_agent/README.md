# Browser Agent

LangGraph ReAct 에이전트. Gateway 서비스를 통해 브라우저 확장의 DOM 제어 도구를 호출한다.

## 책임

- Orchestrator로부터 ACP 요청을 수신하고 LangGraph ReAct 그래프로 처리한다.
- `GatewayBrowserToolsClient`를 통해 Gateway에 브라우저 도구 호출 요청을 전달한다.
- Gateway → SSE → Extension → DOM 액션 → 결과 반환까지 대기 (blocking, 65s timeout).
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
Extension BrowserToolRegistry.invoke()
  │
  │  POST /sessions/{id}/browser-tools/result/{inv_id}
  ▼
Gateway asyncio.Future.set_result() → response returned
```

MCP 서버 중간 계층 없이 Browser Agent가 Gateway에 직접 HTTP 요청을 보낸다.

## 사용 가능 도구

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

- **응답**: `{"status": "ok"}`

## 의존 서비스

| 서비스 | 용도 |
|--------|------|
| PostgreSQL `:5432` | LangGraph `AsyncPostgresSaver` 대화 체크포인트 저장 |
| Ollama `:11434` | 브라우저 제어 LLM (`qwen2.5:14b`), Docker 내부에서 `host.docker.internal:11434` 접근 |
| Gateway `:8000` | 브라우저 도구 브로커 (asyncio.Queue → SSE → Extension) |

## 환경변수

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `DATABASE_URL` | string | `postgresql+asyncpg://postgres:password@postgres:5432/browser_agent` | PostgreSQL DSN (내부에서 `postgresql://`로 변환해 psycopg에 전달) |
| `BROWSER_MODEL` | string | `qwen2.5:14b` | Ollama 모델 이름 |
| `GATEWAY_URL` | string | `http://gateway:8000` | Gateway 서비스 URL |
| `BROWSER_TOOL_TIMEOUT` | float | `65.0` | 브라우저 도구 응답 대기 최대 시간 (초) |
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

## 파일 구조

```
services/browser_agent/
├── main.py          # FastAPI 애플리케이션, GatewayBrowserToolsClient, LangGraph 그래프, ACP 라우터
├── pyproject.toml   # 패키지 메타데이터 및 의존성
└── Dockerfile       # python:3.13-slim + libpq-dev, 포트 8003
```
