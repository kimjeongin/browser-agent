# Orchestrator

LangGraph Supervisor 그래프로 사용자 의도를 분류하고 Chat Agent 또는 Browser Agent로 라우팅한다.

## 책임

- 마지막 사용자 메시지를 LLM(`llama3.1:8b`)으로 분석해 `chat_agent` 또는 `browser_agent`를 결정한다.
- 결정된 에이전트에 ACP HTTP 요청을 전달하고 응답을 반환한다.
- LangGraph 대화 체크포인트를 PostgreSQL에 저장한다.
- ACP 엔드포인트(`/runs`, `/runs/stream`)를 노출해 Gateway가 호출한다.

## 그래프 구조

```
supervisor ──(browser_agent)──▶ browser_agent ──▶ END
           └──(chat_agent)───▶ chat_agent    ──▶ END
```

`supervisor` 노드는 LLM을 사용해 `{"agent": "chat_agent" | "browser_agent"}` JSON 형식으로 응답을 생성한다. JSON 파싱 실패 시 응답 텍스트에서 키워드를 검색하고, 그것도 실패하면 `chat_agent`를 기본값으로 사용한다.

### 라우팅 기준

| 에이전트 | 처리 대상 |
|---------|---------|
| `browser_agent` | 클릭, 타이핑, URL 탐색, 스크롤, 스크린샷, 폼 작성, DOM 조작 등 실제 브라우저 제어가 필요한 요청 |
| `chat_agent` | 일반 질의응답, 웹 검색, 요약, 번역, 코딩 도움, 수학, 일반 대화 등 그 외 모든 요청 |

## ACP 엔드포인트

### `POST /runs`

동기 에이전트 실행. Gateway의 `POST /sessions/{id}/chat`이 호출한다.

**요청 스키마**

| 필드 | 타입 | 설명 |
|------|------|------|
| `run_id` | string | 실행 식별자 (빈 문자열이면 자동 생성) |
| `thread_id` | string | 대화 스레드 식별자 (session_id와 동일) |
| `input` | object | `{"messages": [{"role": "human", "content": "..."}]}` |

**응답 스키마**

| 필드 | 타입 | 설명 |
|------|------|------|
| `run_id` | string | 실행 식별자 |
| `status` | string | `"completed"` \| `"failed"` |
| `output` | object \| null | 그래프 최종 상태 |
| `error` | string \| null | 오류 메시지 (실패 시) |

### `POST /runs/stream`

스트리밍 에이전트 실행. SSE 이벤트(`token`, `tool_start`, `tool_end`, `done`, `error`)를 반환한다.

### `GET /health`

- **응답**: `{"status": "ok"}`

## 의존 서비스

| 서비스 | 용도 |
|--------|------|
| PostgreSQL `:5432` | LangGraph `AsyncPostgresSaver` 대화 체크포인트 저장 |
| Chat Agent `:8002` | `POST /runs` ACP 호출 |
| Browser Agent `:8003` | `POST /runs` ACP 호출 |
| Ollama `:11434` | 의도 분류 LLM (`llama3.1:8b`), Docker 내부에서 `host.docker.internal:11434` 접근 |

## 환경변수

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `CHAT_AGENT_URL` | string | `http://chat-agent:8002` | Chat Agent ACP 서비스 URL |
| `BROWSER_AGENT_URL` | string | `http://browser-agent:8003` | Browser Agent ACP 서비스 URL |
| `DATABASE_URL` | string | `postgresql+asyncpg://postgres:password@postgres:5432/browser_agent` | PostgreSQL DSN (내부에서 `postgresql://`로 변환해 psycopg에 전달) |
| `OLLAMA_BASE_URL` | string | `http://host.docker.internal:11434` | Ollama 서버 URL |
| `ORCHESTRATOR_MODEL` | string | `llama3.1:8b` | 의도 분류에 사용할 Ollama 모델 |
| `LLM_TEMPERATURE` | float | `0.0` | LLM 온도 |
| `LLM_NUM_CTX` | int | `8192` | 컨텍스트 윈도우 크기 |

## 로컬 실행

```bash
cd services/ochestrator
uv pip install -e ../shared -e .
uvicorn main:app --host 0.0.0.0 --port 8001 --reload
```

Docker로 실행:

```bash
docker compose -f docker-compose.services.yml up --build ochestrator
```

## 구현 주의사항

- `supervisor` 노드는 `streaming=False`로 LLM을 초기화한다. 분류는 단일 JSON 응답만 필요하므로 스트리밍이 불필요하다.
- ACP로 서브 에이전트를 호출할 때 각 호출에 새 `thread_id`(UUID)를 생성한다. Orchestrator의 체크포인터와 서브 에이전트의 체크포인터가 분리되어 있기 때문이다.
- `AsyncPostgresSaver`는 psycopg DSN(`postgresql://`)을 요구한다. `database_url`의 `postgresql+asyncpg://` 접두사를 `_psycopg_connection_string()`으로 변환해 전달한다.
- 서브 에이전트 호출 실패 시 오류를 전파하지 않고 사용자에게 오류 메시지 `AIMessage`를 반환한다.

## 파일 구조

```
services/ochestrator/
├── main.py          # FastAPI 애플리케이션, LangGraph 그래프, ACP 라우터
├── pyproject.toml   # 패키지 메타데이터 및 의존성
└── Dockerfile       # python:3.13-slim + libpq-dev, 포트 8001
```
