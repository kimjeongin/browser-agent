# Browser Agent

LangGraph ReAct 에이전트. Gateway를 통해 Extension의 BrowserToolRegistry에 도구 호출을 위임하여 DOM 조작을 수행한다.

## 책임

- Orchestrator로부터 ACP 요청을 수신하고 LangGraph ReAct 그래프로 처리한다.
- 정적으로 정의된 9개 브라우저 도구를 LLM에 바인딩한다.
- 도구 호출 시 Gateway `POST /sessions/{id}/browser-tools/invoke`를 호출한다. Gateway가 SSE를 통해 Extension에 전달하고, Extension이 DOM을 실행한 후 결과를 반환한다.
- 대화 체크포인트를 PostgreSQL에 저장한다.

## 브라우저 도구 호출 흐름

```
Browser Agent (@tool 함수) ──POST /browser-tools/invoke──▶ Gateway :8000
                                                              │ (blocking, ~30s timeout)
                                                              │ asyncio.Future 대기
                                                              ▼
                                                        Extension (SSE 수신)
                                                        BrowserToolRegistry.invoke()
                                                              │ DOM 액션
                                                              ▼
                                                        Extension ──POST result──▶ Gateway
                                                                                      │ Future resolved
                                                                                      ▼
                                                                               Browser Agent (결과 수신)
```

## 사용 가능 도구 (정적 정의)

| 함수 | Gateway 도구명 | 설명 |
|------|---------------|------|
| `navigate` | `navigate` | URL로 탐색 |
| `click_element` | `click` | CSS 셀렉터로 요소 클릭 |
| `type_text` | `type_text` | 텍스트 입력 |
| `scroll_page` | `scroll` | 페이지 스크롤 |
| `take_screenshot` | `take_screenshot` | 스크린샷 캡처 |
| `extract_content` | `extract_content` | 페이지 텍스트 추출 |
| `wait_for_element` | `wait_for_element` | 요소 출현 대기 |
| `evaluate_js` | `evaluate_js` | JavaScript 실행 |
| `get_page_info` | `get_page_info` | 현재 페이지 URL/제목 조회 |

모든 도구는 `session_id`를 첫 번째 파라미터로 요구한다. LLM은 시스템 프롬프트를 통해 모든 도구 호출에 `session_id`를 포함하도록 지시받는다.

## 그래프 구조

```
agent ──(tools_condition: tool call 있음)──▶ tools ──▶ agent
      └──(tools_condition: tool call 없음)──▶ END
```

`agent` 노드 실행 시 메시지 목록 첫 번째 항목이 `SystemMessage`가 아니면 자동으로 삽입한다.

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
| Gateway `:8000` | 브라우저 도구 호출 브로커 (`POST /sessions/{id}/browser-tools/invoke`) |

## 환경변수

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `DATABASE_URL` | string | `postgresql+asyncpg://postgres:password@postgres:5432/browser_agent` | PostgreSQL DSN |
| `BROWSER_MODEL` | string | `qwen2.5:14b` | Ollama 모델 이름 |
| `GATEWAY_URL` | string | `http://gateway:8000` | Gateway 서비스 URL |
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

- `GatewayBrowserToolsClient`는 도구 함수 내부에서 매 호출마다 생성된다. httpx 클라이언트는 단일 요청용 `AsyncClient`를 사용한다.
- 도구 호출 타임아웃은 60초로 설정되어 있다 (Gateway 기본값 30초보다 여유 있게 설정).
- 도구 호출 실패 시 LLM에게 1회 재시도를 지시하는 시스템 프롬프트가 있다.

## 파일 구조

```
services/browser_agent/
├── main.py          # FastAPI 앱, GatewayBrowserToolsClient, @tool 함수들, LangGraph 그래프
├── pyproject.toml   # 패키지 메타데이터 및 의존성 (httpx 포함)
└── Dockerfile       # python:3.13-slim + libpq-dev, 포트 8003
```
