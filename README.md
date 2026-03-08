# Browser Agent

WXT 브라우저 확장 + 멀티 에이전트 AI 백엔드로 구성된 AI 챗봇 어시스턴트. 사용자 입력을 채팅 응답 또는 실제 브라우저 DOM 제어로 처리한다.

---

## 아키텍처 개요

```
Extension (WXT/Chrome) ──Bearer JWT──▶ Gateway :8000
                       ◀──SSE chat stream──
                       ◀──SSE browser commands──

Gateway :8000 ──ACP──▶ Orchestrator :8001
                            ├──ACP──▶ Chat Agent :8002
                            └──ACP──▶ Browser Agent :8003

Browser Agent :8003 ──POST /browser-tools/invoke──▶ Gateway :8000
Gateway :8000 ──asyncio.Queue──▶ SSE /commands──▶ Extension
Extension ──POST /browser-tools/result/{inv_id}──▶ Gateway :8000

── Observability (모든 서비스 공통) ──────────────────────────
Services ──OTLP HTTP:4318──▶ OTel Collector
                                  ├── traces ──▶ Phoenix :6006  (LLM 트레이스)
                                  ├── logs   ──▶ Loki :3100     (시스템 로그)
                                  └── metrics──▶ Prometheus :9090
Grafana :3000 ◀── Loki + Prometheus (로그/메트릭 통합 대시보드)
Grafana 로그 뷰에서 trace_id 클릭 시 Phoenix :6006으로 바로 이동
```

### webMCP-inspired 브라우저 제어 흐름 (3-hop)

1. Browser Agent가 `POST /sessions/{id}/browser-tools/invoke` 호출 (blocking, 60s)
2. Gateway가 `asyncio.Queue`에 tool invocation 이벤트 추가 → SSE로 Extension push
3. Extension이 DOM 액션 실행 후 `POST /sessions/{id}/browser-tools/result/{inv_id}` 호출
4. Gateway `asyncio.Future.set_result()` → Browser Agent 응답 반환

### 컴포넌트 역할

| 컴포넌트 | 포트 | 역할 | 기술 스택 |
|----------|------|------|-----------|
| Extension | — | 사용자 UI, DOM 제어, 탭 그룹 관리 | WXT 0.20, React 19, Tailwind v4, Zustand 5 |
| Gateway | 8000 | 진입점, SSE 허브, JWT 검증, 브라우저 도구 브로커 | FastAPI, python-jose |
| Orchestrator | 8001 | 의도 분류, 에이전트 라우팅 | FastAPI, LangGraph, LangChain |
| Chat Agent | 8002 | 웹 검색, 일반 대화 | FastAPI, LangGraph, DuckDuckGo |
| Browser Agent | 8003 | DOM 제어 (Progress Ledger 그래프) | FastAPI, LangGraph, httpx |
| Keycloak | 8080 | JWT 발급, PKCE | Keycloak 26.5.3 |
| PostgreSQL | 5432 | DB, LangGraph 체크포인트 | pgvector/pgvector:pg16 |
| MinIO | 9000/9001 | 오브젝트 스토리지 | MinIO |
| Phoenix | 6006 | LLM 트레이스 시각화 (OpenInference) | Arize Phoenix 13.10.0 |
| OTel Collector | 4317 (gRPC) / 4318 (HTTP) | OTel 신호 수신 및 라우팅 | opentelemetry-collector-contrib 0.116.0 |
| Prometheus | 9090 | 서비스 메트릭 수집 | prom/prometheus 2.55.1 |
| Loki | 3100 (내부) | 시스템 로그 집계 | grafana/loki 3.3.2 |
| Grafana | 3000 | Loki + Prometheus 통합 시각화 | grafana/grafana 11.4.0 |

---

## 사전 요구사항

- Docker & Docker Compose v2
- Node.js 20+ + pnpm
- Python 3.13+
- [Ollama](https://ollama.ai)
- Chrome (Manifest V3)

Ollama 모델을 미리 pull한다:

```bash
ollama pull qwen3:8b          # Orchestrator (의도 분류), Chat Agent, Browser Agent planner
ollama pull qwen2.5vl:7b      # Browser Agent actor (멀티모달, 스크린샷 직접 인식)
```

---

## 빠른 시작

```bash
# 1. 저장소 클론
git clone <repo-url>
cd browser-agent

# 2. 인프라 시작 (PostgreSQL, MinIO, Keycloak, Phoenix, OTel Collector, Loki, Prometheus, Grafana)
cd infra
docker compose up -d

# Keycloak이 healthy 상태가 될 때까지 대기 (최초 시작 시 약 60초)
# Grafana: http://localhost:3000, Phoenix: http://localhost:6006

# 3. 백엔드 서비스 빌드 및 시작
docker compose -f docker-compose.services.yml up --build

# 4. Extension 의존성 설치 및 빌드
cd ../extension
pnpm install
pnpm build

# 5. Chrome에서 Extension 로드
# chrome://extensions → 개발자 모드 활성화 → "압축 해제된 확장 프로그램 로드"
# → extension/.output/chrome-mv3 선택
```

테스트 계정: `testuser` / `password` (Keycloak realm import 시 자동 생성)

---

## 설정

주요 환경변수. 서비스별 상세 설정은 각 서비스 README를 참조한다.

### 백엔드 서비스 (`infra/docker-compose.services.yml`)

| 변수 | 서비스 | 예시 |
|------|--------|------|
| `DATABASE_URL` | Gateway, Orchestrator, Chat, Browser | `postgresql+asyncpg://postgres:password@postgres:5432/browser_agent` |
| `ORCHESTRATOR_URL` | Gateway | `http://orchestrator:8001` |
| `KEYCLOAK_REALM_URL` | Gateway | `http://localhost:8080/realms/browser-agent` |
| `KEYCLOAK_JWKS_URL` | Gateway | `http://keycloak:8080/realms/browser-agent/protocol/openid-connect/certs` |
| `KEYCLOAK_AUDIENCE` | Gateway | `browser-agent-extension` |
| `OLLAMA_BASE_URL` | Orchestrator, Chat, Browser | `http://host.docker.internal:11434` |
| `ORCHESTRATOR_MODEL` | Orchestrator | `qwen3:8b` |
| `CHAT_MODEL` | Chat Agent | `qwen3:8b` |
| `BROWSER_MODEL` | Browser Agent (actor) | `qwen2.5vl:7b` |
| `PLANNER_MODEL` | Browser Agent (planner) | `qwen3:8b` |
| `GATEWAY_URL` | Browser Agent | `http://gateway:8000` |
| `BROWSER_TOOL_TIMEOUT` | Gateway / Browser Agent | `60` / `65` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | 전체 서비스 | `http://otel-collector:4318` |
| `LOG_LEVEL` | 전체 서비스 | `INFO` |

### Extension (`extension/.env`)

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `WXT_PUBLIC_API_BASE_URL` | `http://localhost:8000` | Gateway URL |
| `WXT_PUBLIC_KEYCLOAK_REALM_URL` | `http://localhost:8080/realms/browser-agent` | Keycloak Realm URL |
| `WXT_PUBLIC_KEYCLOAK_CLIENT_ID` | `browser-agent-extension` | Keycloak Public Client ID |

---

## Observability

모든 서비스가 `setup_telemetry()` 한 번 호출로 traces, logs, metrics를 OTel Collector에 전송한다 (`shared.observability` 모듈).

### 신호 흐름

| 신호 | 경로 | 목적지 |
|------|------|--------|
| Traces | 서비스 → OTLP HTTP:4318 → OTel Collector → Phoenix:6006 | LLM 호출, LangGraph 노드 실행 추적 |
| Logs | 서비스 → OTLP HTTP:4318 → OTel Collector → Loki:3100 | JSON 구조화 로그 집계 |
| Metrics | 서비스 → OTLP HTTP:4318 → OTel Collector → Prometheus:9090 | HTTP 요청 수/지연, CPU/메모리 |

### 대시보드 접속

| 도구 | URL | 기본 계정 |
|------|-----|-----------|
| Grafana | `http://localhost:3000` | `admin` / `admin` |
| Phoenix | `http://localhost:6006` | — |
| Prometheus | `http://localhost:9090` | — |

### 로그 포맷

모든 서비스가 `python-json-logger`로 동일한 JSON 구조를 stdout에 출력한다:

```json
{
  "timestamp": "2026-03-08T12:00:00",
  "level": "INFO",
  "logger": "gateway.api.chat",
  "message": "chat stream started",
  "service": "gateway",
  "hostname": "abc123",
  "location": "chat.py:42",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "span_id": "00f067aa0ba902b7"
}
```

`trace_id`가 있는 로그는 Grafana Loki에서 클릭 시 Phoenix 트레이스 뷰로 바로 이동한다.

### 계측 범위

| 계측 대상 | 라이브러리 | 생성되는 신호 |
|-----------|-----------|---------------|
| FastAPI 엔드포인트 | `opentelemetry-instrumentation-fastapi` | HTTP 요청 span + 요청 수/지연 메트릭 |
| httpx 클라이언트 | `opentelemetry-instrumentation-httpx` | 아웃바운드 HTTP span (W3C `traceparent` 자동 주입) |
| LangChain / LangGraph | `openinference-instrumentation-langchain` | LLM 호출, 노드 실행 span → Phoenix |
| Python 로그 | `opentelemetry-instrumentation-logging` | `trace_id` / `span_id` 자동 주입 |
| 시스템 리소스 | `opentelemetry-instrumentation-system-metrics` | CPU, 메모리, GC 메트릭 |

---

## 개발 가이드

```bash
# Extension 개발 서버 (HMR)
cd extension && pnpm dev

# 개별 서비스 로컬 실행 예시 (gateway)
cd services/gateway
uv pip install -e ../shared -e .
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### 테스트 실행

```bash
# Gateway 테스트 (health, browser tools round-trip, stale cleanup) — 19개
cd services/gateway && uv run pytest

# Orchestrator 테스트 (supervisor routing) — 31개
cd services/orchestrator && uv run pytest

# Chat Agent 테스트 (web_search, fetch_webpage) — 14개
cd services/chat_agent && uv run pytest

# Browser Agent 테스트 (tools, gateway_client, progress_ledger) — 62개
cd services/browser_agent && uv run pytest

# Extension 테스트 (background, content script) — 56개
cd extension && pnpm test
```

---

## 프로젝트 구조

```
browser-agent/
├── extension/                   # WXT 브라우저 확장 (Chrome MV3)
│   ├── entrypoints/
│   │   ├── background.ts        # PKCE 인증, commands SSE 수신, 탭 그룹 관리
│   │   ├── content.ts           # DOM 액션 실행 (click, type, scroll 등)
│   │   └── sidepanel/           # 채팅 UI + 브라우저 제어 상태 배너
│   ├── services/
│   │   └── command-executor.ts  # 도구 호출 디스패처 (navigate/screenshot vs content script)
│   ├── lib/                     # GatewayClient, PKCE 유틸, tab-manager, token-manager
│   └── stores/                  # Zustand 상태 (채팅, 브라우저 제어 상태)
├── services/
│   ├── shared/                  # 공유 패키지 (auth, acp, llm, observability, models)
│   ├── gateway/                 # 진입점 서비스 + 브라우저 도구 브로커
│   │   ├── api/                 # 라우터 (sessions, chat, browser_tools)
│   │   └── core/                # SessionStore, InvocationBroker
│   ├── orchestrator/            # 슈퍼바이저 에이전트 (의도 분류)
│   ├── chat_agent/              # 웹 검색 ReAct 에이전트
│   └── browser_agent/           # Progress Ledger DOM 제어 에이전트
│       ├── graph/               # LangGraph 노드, 라우터, 상태, 프롬프트
│       └── tools/               # 브라우저 도구 정의, GatewayBrowserToolsClient
└── infra/
    ├── docker-compose.yml           # 인프라 서비스 (PostgreSQL, MinIO, Keycloak, Observability)
    ├── docker-compose.services.yml  # 애플리케이션 서비스 전체 스택
    ├── postgres/init.sql            # DB 초기화 (uuid-ossp, pgvector 설치)
    ├── keycloak/                    # Realm 설정 JSON (컨테이너 시작 시 자동 import)
    ├── otel/otel-collector-config.yaml   # OTel Collector 파이프라인 설정
    ├── loki/loki-config.yaml             # Loki 로그 보존 설정
    ├── prometheus/prometheus.yml         # Prometheus 스크랩 대상 설정
    └── grafana/provisioning/            # Grafana 데이터소스 자동 프로비저닝
```

---

## 설계 결정

| 결정 | 이유 |
|------|------|
| SSE (WebSocket 아님) | Chrome Extension Service Worker에서 `EventSource` 미지원. `fetch` + `ReadableStream`으로 직접 구현 |
| asyncio.Queue + asyncio.Future | 브라우저 도구 조율을 단일 Gateway 인스턴스 내에서 처리. Redis Pub/Sub보다 단순하고 지연 없음. 수평 확장 시 Redis Streams로 교체 |
| Gateway 직접 HTTP (MCP 서버 없음) | Browser Agent → Gateway 직접 HTTP 호출. MCP 서버 중간 계층 제거로 3-hop으로 단순화 |
| ACP 프로토콜 (HTTP POST) | 에이전트 간 표준 인터페이스. 각 서비스가 독립 배포/스케일 가능 |
| Progress Ledger 그래프 | 단순 ReAct 대신 actor → progress_check → replan 루프로 루프 감지 및 전략 재수립 가능 |
| `qwen2.5vl:7b` (Browser Agent actor) | 멀티모달 모델로 screenshot 도구 반환 이미지를 LLM이 직접 인식. 별도 OCR 불필요 |
| session_id = thread_id | Browser Agent가 session 단위로 LangGraph 체크포인트를 유지해 멀티턴 대화 지원 |
| Keycloak PKCE | Extension은 `client_secret` 안전 보관 불가 → Public client + PKCE S256 강제 |
| Access Token 메모리 저장 | `localStorage`/`sessionStorage`는 XSS 취약. Service Worker 메모리만 사용 |
| Chrome Tab Groups API | AI 제어 탭을 "AI Assistant" 그룹으로 격리. 사용자가 AI 탭을 시각적으로 구분 가능 |
| psycopg (LangGraph 체크포인터) | `langgraph-checkpoint-postgres`가 asyncpg가 아닌 psycopg DSN을 요구 |
| OTel Collector 중앙 라우터 | 서비스가 Phoenix/Loki/Prometheus에 직접 쓰면 엔드포인트가 서비스 수만큼 분산됨. Collector를 두면 엔드포인트 `OTLP HTTP:4318` 하나로 통일되고, 백엔드 교체 시 서비스 코드를 변경하지 않아도 됨 |
| Phoenix를 LLM 트레이스 전용으로 분리 | OpenInference 계측(`LangChainInstrumentor`)이 생성하는 LLM span은 Prometheus/Loki 형식에 맞지 않음. Phoenix는 LLM span 전용 UI를 제공하므로 traces → Phoenix, logs → Loki로 역할을 분리 |
| Grafana `derivedFields`로 trace_id 링크 | Loki 로그의 `trace_id` 필드를 클릭하면 Phoenix 트레이스 뷰로 바로 이동. 로그 → 트레이스 correlation을 별도 쿼리 없이 제공 |
| JSON 구조화 로그 (`python-json-logger`) | 평문 로그는 Loki에서 파싱 비용이 높고 필드 추출이 불안정함. JSON 포맷으로 `service`, `trace_id`, `level`, `location` 필드를 고정 스키마로 출력 |
