# infra

`docker-compose.yml`과 `docker-compose.services.yml`로 구성된 로컬 개발 환경 정의. PostgreSQL, Redis, MinIO, Keycloak 인프라 서비스와 모든 애플리케이션 서비스를 포함한다.

---

## 인프라 서비스

| 서비스 | 포트 | 역할 | 이미지 |
|--------|------|------|--------|
| `postgres` | 5432 | 애플리케이션 DB, Keycloak DB, LangGraph 체크포인트 | `pgvector/pgvector:pg16` |
| `redis` | 6379 | 세션 캐시 | `redis:7-alpine` |
| `minio` | 9000 (API), 9001 (Console) | 스크린샷·파일 오브젝트 스토리지 (S3 호환) | `minio/minio:latest` |
| `keycloak` | 8080 | JWT 발급, PKCE 플로우, JWKS 제공 | `quay.io/keycloak/keycloak:26.5.3` |

## 애플리케이션 서비스 (`docker-compose.services.yml`)

| 서비스 | 포트 | 역할 |
|--------|------|------|
| `gateway` | 8000 | API 진입점, SSE 허브, JWT 검증, 브라우저 도구 브로커 |
| `orchestrator` | 8001 | 의도 분류, 에이전트 라우팅 |
| `chat-agent` | 8002 | 웹 검색, 일반 대화 |
| `browser-agent` | 8003 | DOM 제어 에이전트 |

---

## Compose 파일 구조

### `docker-compose.yml` — 인프라 전용

PostgreSQL, Redis, MinIO, Keycloak만 포함한다. 애플리케이션 서비스 없이 인프라만 올리거나 인프라를 별도로 관리할 때 사용한다.

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

> **주의**: Keycloak이 healthy 상태가 되기까지 최초 시작 시 약 60초 소요된다. `docker-compose.services.yml`의 gateway는 `keycloak: condition: service_healthy`를 의존성으로 지정하므로 Keycloak이 준비되기 전까지 시작하지 않는다.

---

## 초기화 순서

```
postgres (healthy)
    ├── keycloak (postgres healthy 후 시작 → import-realm)
    └── 각 서비스 (postgres healthy 후 시작)
            └── gateway (keycloak healthy 후 시작)
redis (healthy)
    └── gateway (redis healthy 후 시작)
browser-agent (gateway healthy 후 시작)
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
| Redis | 6379 | 6379 |
| MinIO API | 9000 | 9000 |
| MinIO Console | 9001 | 9001 |
| Keycloak | 8080 | 8080 |
| Gateway | 8000 | 8000 |
| Orchestrator | 8001 | 8001 |
| Chat Agent | 8002 | 8002 |
| Browser Agent | 8003 | 8003 |

---

## 볼륨

| 볼륨 | 서비스 | 용도 |
|------|--------|------|
| `postgres_data` | postgres | PostgreSQL 데이터 영속화 |
| `minio_data` | minio | 오브젝트 스토리지 데이터 영속화 |

Keycloak은 별도 볼륨 없이 PostgreSQL DB(`keycloak`)에 상태를 저장한다.

---

## 기본 자격증명

| 서비스 | 계정 | 비밀번호 | 접속 주소 |
|--------|------|----------|-----------|
| PostgreSQL | `postgres` | `password` | `localhost:5432` |
| MinIO | `minioadmin` | `minioadmin` | `http://localhost:9001` (Console) |
| Keycloak Admin | `admin` | `admin` | `http://localhost:8080` |

> **주의**: 위 자격증명은 로컬 개발 전용이다. 프로덕션 환경에서는 반드시 변경해야 한다.
