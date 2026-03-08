# infra

`docker-compose.yml`과 `docker-compose.services.yml`로 구성된 로컬 개발 환경 정의. PostgreSQL, MinIO, Keycloak, Observability 스택(Phoenix, OTel Collector, Loki, Prometheus, Grafana)과 모든 애플리케이션 서비스를 포함한다.

---

## 인프라 서비스

| 서비스 | 포트 | 역할 | 이미지 |
|--------|------|------|--------|
| `postgres` | 5432 | 애플리케이션 DB, Keycloak DB, LangGraph 체크포인트 | `pgvector/pgvector:pg16` |
| `minio` | 9000 (API), 9001 (Console) | 스크린샷·파일 오브젝트 스토리지 (S3 호환) | `minio/minio:latest` |
| `keycloak` | 8080 | JWT 발급, PKCE 플로우, JWKS 제공 | `quay.io/keycloak/keycloak:26.5.3` |
| `phoenix` | 6006 | LLM 트레이스 시각화 (OpenInference) | `arizephoenix/phoenix:version-13.10.0` |
| `otel-collector` | 4317 (gRPC), 4318 (HTTP) | OTel 신호 수신 및 라우팅 (traces → Phoenix, logs → Loki, metrics → Prometheus) | `otel/opentelemetry-collector-contrib:0.116.0` |
| `loki` | 3100 (내부) | 시스템 로그 집계 | `grafana/loki:3.3.2` |
| `prometheus` | 9090 | 서비스 메트릭 수집 (OTel Collector scrape) | `prom/prometheus:v2.55.1` |
| `grafana` | 3000 | Loki + Prometheus 통합 대시보드 | `grafana/grafana:11.4.0` |

## 애플리케이션 서비스 (`docker-compose.services.yml`)

| 서비스 | 포트 | 역할 |
|--------|------|------|
| `gateway` | 8000 | API 진입점, SSE 허브, JWT 검증, 브라우저 도구 브로커 |
| `orchestrator` | 8001 (내부 전용) | 의도 분류, 에이전트 라우팅 |
| `chat-agent` | 8002 (내부 전용) | 웹 검색, 일반 대화 |
| `browser-agent` | 8003 (내부 전용) | DOM 제어 에이전트 |

`orchestrator`, `chat-agent`, `browser-agent`는 호스트에 포트를 노출하지 않는다 (`expose`만 사용). Gateway를 통해서만 접근한다.

---

## Compose 파일 구조

### `docker-compose.yml` — 인프라 전용

PostgreSQL, MinIO, Keycloak만 포함한다. 애플리케이션 서비스 없이 인프라만 올리거나 인프라를 별도로 관리할 때 사용한다.

```bash
cd infra
docker compose up -d
```

### `docker-compose.services.yml` — 전체 스택

`include: docker-compose.yml`로 인프라 서비스를 포함한 후, 애플리케이션 서비스(gateway, orchestrator, chat-agent, browser-agent)를 추가로 정의한다. 단일 명령으로 전체 스택을 올릴 때 사용한다.

```bash
cd infra
docker compose -f docker-compose.services.yml up --build
```

---

## Keycloak Realm 자동 import

`docker-compose.yml`의 Keycloak 컨테이너는 `--import-realm` 플래그로 시작한다:

```yaml
command: start-dev --import-realm
volumes:
  - ./keycloak:/opt/keycloak/data/import
```

`infra/keycloak/` 디렉토리의 JSON 파일(`realm-browser-agent.json`)이 컨테이너 시작 시 자동으로 import된다. 이미 realm이 존재하면 import를 건너뛴다.

> **주의**: Keycloak이 healthy 상태가 되기까지 최초 시작 시 약 60초 소요된다. `docker-compose.services.yml`의 `gateway`는 `keycloak: condition: service_healthy`를 의존성으로 지정하므로 Keycloak이 준비되기 전까지 시작하지 않는다.

---

## 초기화 순서

```
postgres (healthy)
    ├── keycloak (postgres healthy 후 시작 → import-realm)
    ├── phoenix  (postgres healthy 후 시작)
    └── orchestrator, chat-agent (postgres healthy 후 시작)
            └── otel-collector (phoenix healthy 후 시작)
                    ├── prometheus (otel-collector started 후 시작)
                    ├── loki (독립 시작)
                    │     └── grafana (loki healthy + prometheus healthy 후 시작)
                    └── gateway (keycloak healthy + otel-collector started 후 시작)
                            └── browser-agent (gateway started + otel-collector started 후 시작)
```

`postgres/init.sql`이 PostgreSQL 최초 초기화 시 실행된다:

```sql
-- keycloak DB 생성 (PostgreSQL 단일 인스턴스 공유)
CREATE DATABASE keycloak;

-- browser_agent DB에 pgvector 확장 설치
\c browser_agent;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;
```

---

## 포트 맵

| 서비스 | 호스트 포트 | 컨테이너 포트 |
|--------|-------------|---------------|
| PostgreSQL | 5432 | 5432 |
| MinIO API | 9000 | 9000 |
| MinIO Console | 9001 | 9001 |
| Keycloak | 8080 | 8080 |
| Phoenix | 6006 | 6006 |
| OTel Collector OTLP gRPC | 4317 | 4317 |
| OTel Collector OTLP HTTP | 4318 | 4318 |
| OTel Collector Prometheus | (노출 안 함) | 8889 |
| OTel Collector Health | (노출 안 함) | 13133 |
| Prometheus | 9090 | 9090 |
| Loki | (노출 안 함) | 3100 |
| Grafana | 3000 | 3000 |
| Gateway | 8000 | 8000 |
| Orchestrator | (노출 안 함) | 8001 |
| Chat Agent | (노출 안 함) | 8002 |
| Browser Agent | (노출 안 함) | 8003 |

---

## 볼륨

| 볼륨 | 서비스 | 용도 |
|------|--------|------|
| `postgres_data` | postgres | PostgreSQL 데이터 영속화 |
| `minio_data` | minio | 오브젝트 스토리지 데이터 영속화 |
| `phoenix_data` | phoenix | LLM 트레이스 영속화 |
| `loki_data` | loki | 로그 청크 영속화 |
| `prometheus_data` | prometheus | 메트릭 시계열 영속화 (보존 기간 15일) |
| `grafana_data` | grafana | 대시보드, 알림 설정 영속화 |

Keycloak은 별도 볼륨 없이 PostgreSQL DB(`keycloak`)에 상태를 저장한다.

---

## 기본 자격증명

| 서비스 | 계정 | 비밀번호 | 접속 주소 |
|--------|------|----------|-----------|
| PostgreSQL | `postgres` | `password` | `localhost:5432` |
| MinIO | `minioadmin` | `minioadmin` | `http://localhost:9001` (Console) |
| Keycloak Admin | `admin` | `admin` | `http://localhost:8080` |
| Grafana | `admin` | `admin` | `http://localhost:3000` |
| Phoenix | — | — | `http://localhost:6006` |
| Prometheus | — | — | `http://localhost:9090` |

> **주의**: 위 자격증명은 로컬 개발 전용이다. 프로덕션 환경에서는 반드시 변경해야 한다. `.env.example`을 복사해 `.env`를 생성하고 모든 비밀번호를 교체한다.

---

## 환경변수 (`.env`)

`.env.example`을 복사해 사용한다:

```bash
cp .env.example .env
```

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `POSTGRES_PASSWORD` | `change_me_in_production` | PostgreSQL 비밀번호 |
| `MINIO_ROOT_PASSWORD` | `change_me_in_production` | MinIO 관리자 비밀번호 |
| `KC_BOOTSTRAP_ADMIN_PASSWORD` | `change_me_in_production` | Keycloak 관리자 비밀번호 |
| `CORS_ORIGINS` | `http://localhost:3000,http://localhost:5173` | Gateway CORS 허용 Origin (쉼표 구분) |
| `OLLAMA_BASE_URL` | `http://host.docker.internal:11434` | 호스트 Ollama 서버 URL |
| `PHOENIX_SECRET` | `changeme-use-strong-secret-in-prod` | Phoenix 인증 시크릿 키 |
| `GRAFANA_ADMIN_PASSWORD` | `admin` | Grafana 관리자 비밀번호 |

---

## Observability 스택

### OTel Collector 파이프라인 (`otel/otel-collector-config.yaml`)

서비스가 `OTLP HTTP:4318`으로 보낸 신호를 세 파이프라인으로 라우팅한다:

| 파이프라인 | 수신 | 내보내기 | 설명 |
|-----------|------|---------|------|
| `traces` | OTLP | Phoenix:6006 (OTLP HTTP) | LangChain/LangGraph LLM 호출 추적 |
| `logs` | OTLP | Loki:3100 | JSON 구조화 로그 집계. `service.name` → `job` 레이블 |
| `metrics` | OTLP | Prometheus scrape :8889 | `browser_agent_` 네임스페이스로 메트릭 노출 |

메모리 한도: 512 MiB (spike 128 MiB). batch 프로세서: 1s timeout, 512 레코드.

### Prometheus 스크랩 설정 (`prometheus/prometheus.yml`)

Prometheus는 OTel Collector의 `:8889/metrics` 엔드포인트를 15초 간격으로 스크랩한다. 모든 서비스 메트릭이 Collector를 통해 단일 스크랩 대상으로 집계된다.

### Grafana 데이터소스 프로비저닝 (`grafana/provisioning/datasources/datasources.yaml`)

컨테이너 시작 시 Prometheus와 Loki가 자동으로 데이터소스로 등록된다.

Loki 데이터소스의 `derivedFields` 설정으로 로그의 `trace_id` 필드를 클릭하면 Phoenix 트레이스 뷰(`http://localhost:6006/trace/<trace_id>`)로 바로 이동한다.
