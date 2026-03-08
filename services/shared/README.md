# shared

모든 Python 서비스(Gateway, Orchestrator, Chat Agent, Browser Agent)가 공통으로 사용하는 내부 패키지.

## 책임

- Keycloak JWKS JWT 검증 로직과 FastAPI 의존성을 제공한다.
- ACP 서버 라우터와 ACP HTTP 클라이언트를 제공한다.
- ChatOllama 팩토리 함수와 공통 LLM 설정을 제공한다.
- OTel traces/logs/metrics 초기화와 JSON 구조화 로그 포맷을 제공한다.
- 도메인 Pydantic 모델(`Session`)을 정의한다.

## 모듈 목록

| 모듈 | 구성 파일 | 설명 |
|------|---------|------|
| `shared.auth` | `jwt_verifier.py`, `dependencies.py` | Keycloak JWKS JWT 검증, FastAPI 의존성 |
| `shared.acp` | `server.py`, `client.py` | ACP 서버 라우터 팩토리, ACP HTTP 클라이언트 |
| `shared.llm` | `factory.py`, `settings.py` | ChatOllama 팩토리, LLM 공통 설정 |
| `shared.observability` | `observability.py` | OTel traces + logs + metrics 초기화 팩토리 |
| `shared.logging_config` | `logging_config.py` | JSON 구조화 로그 포맷 (`python-json-logger`) |
| `shared.models` | `session.py` | `Session` Pydantic 모델 |

---

### `shared.auth`

#### `KeycloakJWTVerifier` (`jwt_verifier.py`)

Keycloak JWKS 엔드포인트에서 공개키를 가져와 JWT를 오프라인 검증한다.

- JWKS 응답을 `TTLCache`에 60분간 캐시한다 (캐시 크기 1).
- RS256 알고리즘, `aud` claim, `iss` claim을 검증한다.
- 검증 실패 시 `jose.JWTError`를 raise한다.

```python
verifier = KeycloakJWTVerifier(
    realm_url="http://keycloak:8080/realms/browser-agent",
    audience="browser-agent-extension",
)
payload = await verifier.verify(token)
```

#### `get_current_user` (`dependencies.py`)

FastAPI `Depends()`로 사용하는 인증 의존성. `Authorization: Bearer <token>` 헤더를 추출하고 `KeycloakJWTVerifier`로 검증한다. `app.state.verifier`에 `KeycloakJWTVerifier` 인스턴스가 설정되어 있어야 한다. 검증 실패 시 `HTTP 401`을 반환한다.

```python
from shared.auth.dependencies import get_current_user
from typing import Annotated, Any

CurrentUser = Annotated[dict[str, Any], Depends(get_current_user)]

@app.get("/protected")
async def protected(user: CurrentUser):
    return {"user_id": user["sub"]}
```

---

### `shared.acp`

#### `create_acp_router` (`server.py`)

LangGraph 그래프를 ACP 엔드포인트로 노출하는 `APIRouter`를 생성한다.

- `POST /runs`: 동기 실행. `graph.ainvoke()`를 호출하고 `RunResponse`를 반환한다.
- `POST /runs/stream`: 스트리밍 실행. `graph.astream_events(version="v2")`로 `token`, `tool_start`, `tool_end`, `done`, `error` SSE 이벤트를 전송한다.
- `GET /health`: `{"status": "ok"}` 반환.

```python
from shared.acp.server import create_acp_router

router = create_acp_router(lambda request: request.app.state.graph)
app.include_router(router)
```

#### `ACPClient` (`client.py`)

ACP 에이전트 서버를 호출하는 비동기 HTTP 클라이언트.

- `run(thread_id, input)`: `POST /runs` 동기 호출. `RunResponse` dict 반환.
- `run_stream(thread_id, input)`: `POST /runs/stream` 스트리밍 호출. SSE 이벤트 dict를 `AsyncGenerator`로 yield.
- 기본 타임아웃: 120초.

```python
from shared.acp.client import ACPClient

client = ACPClient("http://chat-agent:8002")
result = await client.run(thread_id="abc", input={"messages": [...]})
```

---

### `shared.llm`

#### `create_ollama_llm` (`factory.py`)

`ChatOllama` 인스턴스를 생성한다.

> **주의**: `format="json"`은 절대 설정하지 않는다. `format="json"` 설정 시 LangChain tool calling이 비활성화되어 에이전트가 도구를 호출할 수 없다.

```python
from shared.llm.factory import create_ollama_llm
from shared.llm.settings import LLMSettings

llm = create_ollama_llm(model="qwen3:8b", settings=LLMSettings())
llm_with_tools = llm.bind_tools(tools)
```

#### `LLMSettings` (`settings.py`)

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `OLLAMA_BASE_URL` | string | `http://host.docker.internal:11434` | Ollama 서버 URL |
| `ORCHESTRATOR_MODEL` | string | `qwen3:8b` | Orchestrator 의도 분류 모델 |
| `BROWSER_AGENT_MODEL` | string | `qwen3:14b` | Browser Agent 기본 모델 |
| `CHAT_AGENT_MODEL` | string | `qwen3:8b` | Chat Agent 모델 |
| `VISION_MODEL` | string | `qwen3vl:8b` | 비전 모델 (멀티모달) |
| `LLM_TEMPERATURE` | float | `0.0` | LLM 온도 |
| `LLM_NUM_CTX` | int | `8192` | 컨텍스트 윈도우 크기 |

---

### `shared.models`

#### `Session` (`session.py`)

세션 도메인 모델. Gateway 인메모리 스토어에 JSON 직렬화되어 저장된다.

| 필드 | 타입 | 설명 |
|------|------|------|
| `session_id` | string | 세션 식별자 (UUID hex) |
| `user_id` | string | Keycloak `sub` claim |
| `status` | string | `"active"` \| `"inactive"` |
| `created_at` | datetime | 세션 생성 시각 (UTC) |
| `last_activity` | datetime | 마지막 활동 시각 (UTC) |

---

### `shared.observability`

#### `setup_telemetry` (`observability.py`)

FastAPI 서비스의 OTel traces, logs, metrics를 한 번에 초기화한다. `lifespan` 시작 시 호출하고, lifespan 종료 시 `shutdown_telemetry()`를 호출한다.

```python
from shared.observability import setup_telemetry, shutdown_telemetry
from contextlib import asynccontextmanager
from fastapi import FastAPI

@asynccontextmanager
async def lifespan(app: FastAPI):
    tp, lp = setup_telemetry("my-service", app)
    yield
    shutdown_telemetry(tp, lp)
```

`setup_telemetry`가 초기화하는 항목:

| 신호 | 엔드포인트 | 계측 라이브러리 |
|------|-----------|----------------|
| Traces | `OTEL_EXPORTER_OTLP_ENDPOINT/v1/traces` | `LangChainInstrumentor`, `HTTPXClientInstrumentor` |
| Logs | `OTEL_EXPORTER_OTLP_ENDPOINT/v1/logs` | `LoggingInstrumentor` (trace_id/span_id 자동 주입) |
| Metrics | `OTEL_EXPORTER_OTLP_ENDPOINT/v1/metrics` | `FastAPIInstrumentor`, `SystemMetricsInstrumentor` |

메트릭은 15초마다 내보낸다 (`PeriodicExportingMetricReader`).

`OTEL_EXPORTER_OTLP_ENDPOINT` 기본값: `http://otel-collector:4318`

레거시 Phoenix 직접 연결 포맷(`http://host:port/v1/traces`)도 허용한다. `/v1/traces` 접미사를 자동으로 제거해 base URL로 정규화한다.

#### `shutdown_telemetry` (`observability.py`)

pending span/log/metric을 flush하고 provider를 종료한다.

---

### `shared.logging_config`

#### `configure_logging` (`logging_config.py`)

root logger에 JSON 포맷 핸들러를 설치한다. `setup_telemetry()` 내부에서 자동으로 호출되므로 개별 서비스에서 별도 호출이 불필요하다.

> **주의**: `LoggingInstrumentor().instrument()` 이후에 호출해야 한다. 순서가 역전되면 `trace_id`/`span_id`가 로그에 주입되지 않는다.

출력 JSON 필드:

| 필드 | 설명 |
|------|------|
| `timestamp` | ISO 8601 (`%Y-%m-%dT%H:%M:%S`) |
| `level` | `INFO`, `WARNING`, `ERROR`, ... |
| `logger` | Python 로거 이름 |
| `message` | 로그 메시지 |
| `service` | `setup_telemetry(service_name)` 에 전달한 값 |
| `hostname` | 컨테이너 호스트명 |
| `location` | `파일명:라인번호` |
| `trace_id` | OTel trace ID (트레이스 컨텍스트가 없으면 생략) |
| `span_id` | OTel span ID (트레이스 컨텍스트가 없으면 생략) |

`LOG_LEVEL` 환경변수로 로그 레벨을 제어한다 (기본값: `INFO`).

---

## 설치 방법

각 서비스의 `pyproject.toml`에서 editable 모드로 참조한다.

```toml
# services/<service>/pyproject.toml
[project]
dependencies = [
    "shared",
]

[tool.uv.sources]
shared = { path = "../shared", editable = true }
```

직접 설치:

```bash
uv pip install -e services/shared
```

## 파일 구조

```
services/shared/
├── pyproject.toml
└── src/
    └── shared/
        ├── __init__.py
        ├── observability.py   # setup_telemetry, shutdown_telemetry
        ├── logging_config.py  # configure_logging, _ServiceJsonFormatter
        ├── acp/
        │   ├── __init__.py
        │   ├── client.py      # ACPClient
        │   └── server.py      # create_acp_router, RunRequest
        ├── auth/
        │   ├── __init__.py
        │   ├── dependencies.py  # get_current_user FastAPI 의존성
        │   └── jwt_verifier.py  # KeycloakJWTVerifier
        ├── llm/
        │   ├── __init__.py
        │   ├── factory.py     # create_ollama_llm
        │   └── settings.py    # LLMSettings
        └── models/
            ├── __init__.py
            └── session.py     # Session, SessionCreate
```
