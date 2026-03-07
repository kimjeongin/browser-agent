# Chat Agent

LangGraph ReAct 에이전트. 웹 검색과 웹페이지 조회 도구를 사용해 일반 질의응답과 대화를 처리한다.

## 책임

- Orchestrator로부터 ACP 요청을 수신하고 LangGraph ReAct 그래프로 처리한다.
- DuckDuckGo Lite HTML을 직접 파싱해 웹 검색 결과를 반환한다 (API 키 불필요).
- 검색 결과 URL을 추가로 fetch해 페이지 본문을 추출한다.
- 대화 체크포인트를 PostgreSQL에 저장한다.

## 도구 목록

| 도구 | 시그니처 | 설명 |
|------|---------|------|
| `web_search` | `(query: str, max_results: int = 5)` | DuckDuckGo Lite HTML 파싱으로 검색. `title`, `url`, `snippet` 목록 반환 |
| `fetch_webpage` | `(url: str, max_chars: int = 8000)` | URL 페이지를 fetch해 HTML 태그 제거 후 텍스트 반환. `url`, `title`, `content` dict 반환 |

### 검색 구현 방식

`https://lite.duckduckgo.com/lite/?q={query}`를 직접 GET 요청한다. 응답 HTML에서 `<a rel="nofollow">` 링크와 `<td class="result-snippet">` 스니펫을 정규식으로 추출한다. 외부 API 키나 별도 라이브러리가 필요 없다.

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

**응답**: `{"status": "ok"}`

## 의존 서비스

| 서비스 | 용도 |
|--------|------|
| PostgreSQL `:5432` | LangGraph `AsyncPostgresSaver` 대화 체크포인트 저장 |
| Ollama `:11434` | 대화 LLM (`qwen3:8b`). Docker 내부에서 `host.docker.internal:11434` 접근 |

## 환경변수

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `DATABASE_URL` | string | `postgresql+asyncpg://postgres:password@postgres:5432/browser_agent` | PostgreSQL DSN (내부에서 `postgresql://`로 변환해 psycopg에 전달) |
| `CHAT_MODEL` | string | `qwen3:8b` | Ollama 모델 이름 |
| `OLLAMA_BASE_URL` | string | `http://host.docker.internal:11434` | Ollama 서버 URL |
| `LLM_TEMPERATURE` | float | `0.0` | LLM 온도 |
| `LLM_NUM_CTX` | int | `8192` | 컨텍스트 윈도우 크기 |

## 로컬 실행

```bash
cd services/chat_agent
uv pip install -e ../shared -e .
uvicorn main:app --host 0.0.0.0 --port 8002 --reload
```

Docker로 실행:

```bash
cd infra
docker compose -f docker-compose.services.yml up --build chat-agent
```

## 구현 주의사항

- 웹 검색 결과가 0건이면 `[{"title": "No results", "url": "", "snippet": "No results found for: {query}"}]`를 반환한다.
- `fetch_webpage`는 `max_chars` 초과 시 텍스트를 잘라내고 `... [truncated]`를 붙인다.
- HTTP 요청 타임아웃: 전체 15초, 연결 10초.
- 시스템 프롬프트는 메시지 목록에 `SystemMessage`가 없을 때만 삽입한다. Orchestrator가 이미 주입한 경우 중복 삽입을 방지한다.

## 파일 구조

```
services/chat_agent/
├── main.py          # FastAPI 애플리케이션, 도구 정의, LangGraph 그래프, ACP 라우터
├── pyproject.toml   # 패키지 메타데이터 및 의존성
├── tests/
│   ├── conftest.py  # 공통 픽스처
│   └── test_tools.py # web_search, fetch_webpage 도구 테스트
└── Dockerfile       # python:3.13-slim + libpq-dev, 포트 8002
```
