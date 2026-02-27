# 인프라 / 운영 엣지 케이스 및 버그 분석

범위: Docker Compose, Keycloak, 보안, 모니터링, 시작 순서, 확장성

---

## Docker Compose 시작 순서

### 🔴 [INF-1] Keycloak Realm Import 레이스 컨디션

**위치**: `docker-compose.yml` Keycloak 서비스
**시나리오**: Keycloak이 처음 시작할 때 realm import가 완료되기 전에 Gateway 서비스가 JWKS 엔드포인트에 접근하면 404 또는 연결 거부 에러가 발생한다.

**재현 조건**: `docker compose up` 최초 실행 (특히 cold start)
**영향**: Gateway JWT 검증기 초기화 실패, 서비스 전체 사용 불가

**현재 상태**: `healthcheck`이 없거나 TCP 포트 open만 확인한다면 Keycloak 초기화 완료 전에 Gateway가 시작될 수 있다.

**권장 수정**:
```yaml
# docker-compose.yml
keycloak:
  healthcheck:
    test: ["CMD", "curl", "-f", "http://localhost:8080/health/ready"]
    interval: 10s
    timeout: 5s
    retries: 10
    start_period: 60s

gateway:
  depends_on:
    keycloak:
      condition: service_healthy
```

---

### 🔴 [INF-2] PostgreSQL 연결 풀 고갈

**시나리오**: LangGraph `AsyncPostgresSaver`가 각 에이전트(Orchestrator, Browser Agent)에서 독립적으로 연결 풀을 생성한다. 동시 세션이 많아지면 PostgreSQL 기본 최대 연결 수(100)를 초과한다.

**계산**:
- Orchestrator: 풀 크기 5 × 인스턴스 수
- Browser Agent: 풀 크기 5 × 인스턴스 수
- Keycloak: 자체 연결 풀 사용

**영향**: `asyncpg.TooManyConnectionsError`, 새 세션 생성 불가

**권장 수정**:
```yaml
# docker-compose.yml PostgreSQL
command: postgres -c max_connections=200 -c shared_buffers=256MB
```
또는 PgBouncer를 앞단에 추가

---

### 🔴 [INF-3] Ollama 모델 미설치 상태로 서비스 시작

**시나리오**: Ollama 서버는 실행되지만 `qwen2.5:14b`, `qwen2.5:7b`, `llama3.1:8b` 모델이 pull되지 않은 상태에서 서비스가 요청을 받으면 Ollama가 404를 반환한다. 에이전트 초기화는 성공하지만 첫 추론 시 실패.

**재현 조건**: 새 개발 환경 셋업, Ollama 재설치 후
**영향**: 모든 에이전트 응답 실패, 에러 메시지가 불분명

**권장 수정**: 시작 스크립트에 모델 pull 추가
```bash
#!/bin/bash
# scripts/setup-ollama.sh
ollama pull llama3.1:8b
ollama pull qwen2.5:7b
ollama pull qwen2.5:14b
```

또는 health check endpoint에서 모델 존재 여부 확인:
```python
@app.get("/health")
async def health():
    try:
        models = await ollama_client.list()
        required = ["qwen2.5:14b"]
        missing = [m for m in required if m not in [x.model for x in models.models]]
        if missing:
            return JSONResponse({"status": "degraded", "missing_models": missing}, 503)
    except Exception as e:
        return JSONResponse({"status": "error", "detail": str(e)}, 503)
    return {"status": "ok"}
```

---

### 🟠 [INF-4] Gateway 롤링 재시작 중 대기 중인 invocation 손실

**시나리오**: Gateway가 재시작되면 메모리에 있던 `_session_queues`와 `_pending_invocations`가 초기화된다. 재시작 중이었던 브라우저 도구 실행이 완전히 손실된다.

**영향**: Browser Agent가 60초 timeout 에러를 받음, Extension은 응답 없이 멈춤

**권장 수정**:
- Graceful shutdown 시 진행 중인 invocation에 에러 신호 전송
- 클라이언트(Browser Agent)에서 재시도 로직 추가

---

## 보안

### 🔴 [SEC-1] 기본 자격증명 노출

**위치**: `docker-compose.yml`, `docker-compose.services.yml`
**문제**: PostgreSQL password, MinIO access key 등이 환경변수 기본값으로 하드코딩되어 있다.

```yaml
POSTGRES_PASSWORD: password          # 변경 필요
MINIO_ROOT_PASSWORD: minioadmin      # 변경 필요
```

**영향**: git 히스토리에 자격증명 노출, 공개 저장소 사용 시 심각한 보안 위협

**권장 수정**: `.env.example` 파일 제공, `.env`는 `.gitignore`에 추가
```bash
# .env (gitignored)
POSTGRES_PASSWORD=<strong-random-password>
MINIO_ROOT_PASSWORD=<strong-random-password>
```

---

### 🟠 [SEC-2] MinIO 퍼블릭 액세스 미제한

**시나리오**: MinIO 버킷에 퍼블릭 정책이 설정된 경우 누구나 스크린샷 파일에 접근 가능.

---

### 🟠 [SEC-3] 내부 서비스 포트 외부 노출

**현재**: 모든 서비스 포트(`8001`, `8002`, `8003`)가 host에 바인딩됨
**문제**: Orchestrator, Chat Agent, Browser Agent는 Gateway를 통해서만 접근해야 하지만, 직접 접근 가능.

**권장 수정**:
```yaml
# docker-compose.services.yml
orchestrator:
  # ports: 삭제 (내부 network만 사용)
  expose:
    - "8001"  # 내부 컨테이너 간 통신만
```

---

### 🟡 [SEC-4] 요청 크기 제한 없음

**문제**: Gateway에 매우 큰 채팅 메시지를 전송하면 메모리 사용량 급증. DoS 가능.

**권장 수정**:
```python
# FastAPI에서
from fastapi import Request
from fastapi.exceptions import RequestValidationError

@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    if request.headers.get("content-length"):
        if int(request.headers["content-length"]) > 1_000_000:  # 1MB
            return JSONResponse({"error": "Request too large"}, 413)
    return await call_next(request)
```

---

## 모니터링 / 관찰성

### 🟠 [MON-1] 분산 트레이싱 없음

**문제**: 사용자 요청이 Gateway → Orchestrator → Browser Agent → Gateway (도구 호출) → Extension을 거치지만 요청을 추적할 `trace_id` / `correlation_id`가 없다. 에러 발생 시 어느 서비스에서 실패했는지 파악 어려움.

**권장 수정**: OpenTelemetry 추가
```python
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

FastAPIInstrumentor.instrument_app(app)
```

---

### 🟠 [MON-2] 헬스체크 미비

**현재**: `/health` 엔드포인트가 `{"status": "ok"}`만 반환
**문제**: DB 연결, Redis 연결, Ollama 연결 상태를 확인하지 않는다. 의존 서비스 장애 시 `ok`를 반환하다가 실제 요청에서 실패.

**권장 수정**: Deep health check 추가
```python
@app.get("/health")
async def health(db=Depends(get_db), redis=Depends(get_redis)):
    checks = {}
    try:
        await db.execute("SELECT 1")
        checks["db"] = "ok"
    except Exception as e:
        checks["db"] = f"error: {e}"

    try:
        await redis.ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {e}"

    status = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    return JSONResponse({"status": status, "checks": checks},
                       status_code=200 if status == "ok" else 503)
```

---

### 🟡 [MON-3] 구조화된 로깅 없음

**문제**: `print()` 또는 `logging.info()` 사용 시 JSON 포맷 없이 텍스트로만 출력된다. 로그 집계 도구(Loki, Datadog)와 통합 어려움.

**권장 수정**: `structlog` 또는 `python-json-logger` 사용

---

### 🟡 [MON-4] 메트릭 노출 없음

**문제**: Prometheus 메트릭 없음. 요청 레이턴시, 에러율, 도구 실행 횟수 등 추적 불가.

---

## 확장성

### 🟠 [SCALE-1] asyncio.Queue + Future 단일 인스턴스 제약

**문제**: `_session_queues`와 `_pending_invocations`가 Gateway 프로세스 메모리에 저장된다. Gateway를 수평 확장(여러 인스턴스)하면 Extension이 한 인스턴스에 연결되고 Browser Agent가 다른 인스턴스에 invoke 요청을 보내면 Queue를 찾지 못한다.

**영향**: Gateway 수평 확장 불가
**권장**: 수평 확장 필요 시 Redis Pub/Sub 또는 Redis Streams로 교체

---

### 🟡 [SCALE-2] Browser Agent 하나의 세션당 하나의 탭

**현재 설계**: Extension이 단일 AI 탭만 관리
**시나리오**: 여러 사용자가 동시에 Browser Agent를 사용하면 각 세션이 독립적인 브라우저 컨텍스트 필요. 단일 Extension 인스턴스로는 여러 사용자의 브라우저 세션을 격리할 수 없다.

**영향**: 동시 멀티유저 브라우저 제어 불가 (현재 1인 사용 가정)

---

## 네트워크 / 타임아웃

### 🟠 [NET-1] 브라우저 도구 timeout 체계 불일치

**타임아웃 체계**:
- Browser Agent → Gateway invoke: **60초**
- Gateway → Extension SSE 이벤트 전달: **즉시** (Queue put)
- Extension 도구 실행: **제한 없음** (DOM 작업이 오래 걸릴 수 있음)
- Extension → Gateway postToolResult: **httpx 기본 타임아웃**

**문제**: Extension 도구 실행이 59초 걸리면 Browser Agent는 timeout. postToolResult가 1초 걸리면 Browser Agent는 이미 timeout 후 에러 처리 완료. 결과 전달이 도착하면 해당 Future는 이미 삭제된 상태.

**권장**:
- 각 단계별 타임아웃 명시화
- Browser Agent invoke timeout을 90초로 늘리거나 동적으로 설정

---

### 🟡 [NET-2] Ollama 연결 타임아웃 미설정

**문제**: `ChatOllama` 클라이언트의 HTTP 타임아웃이 설정되지 않아 Ollama 서버가 응답하지 않으면 영구 대기.

**권장**:
```python
llm = ChatOllama(
    model=model,
    timeout=120,  # 초 단위
)
```

---

## 데이터 / 상태

### 🟠 [DATA-1] LangGraph 체크포인트 무제한 증가

**문제**: PostgreSQL에 저장되는 LangGraph 체크포인트에 만료 정책이 없다. 장기 운영 시 DB 용량 고갈.

**권장**: 오래된 체크포인트 정리 크론 잡 추가
```sql
DELETE FROM checkpoints WHERE created_at < NOW() - INTERVAL '7 days';
```

---

### 🟡 [DATA-2] Redis 세션 TTL 초과 후 Gateway 동작

**시나리오**: Redis에 저장된 세션(TTL 24h)이 만료된 후 사용자가 해당 세션 ID로 API 요청을 보내면 Gateway가 404를 반환한다. Extension이 자동으로 새 세션을 생성하지 않으면 사용자가 재로그인 해야 한다.

---

### 🟡 [DATA-3] MinIO 스크린샷 만료 정책 없음

**문제**: Browser Agent가 캡처한 스크린샷이 MinIO에 영구 저장된다. 버킷 수명 주기 정책 없으면 용량 무제한 증가.

**권장**: S3 Lifecycle Policy로 30일 후 자동 삭제
