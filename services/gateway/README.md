# Gateway

브라우저 확장(Extension)의 공개 진입점. JWT 검증, 세션 관리, Orchestrator 프록시, 브라우저 도구 브로커를 담당한다.

## 책임

- Keycloak JWKS로 Bearer 토큰을 검증한다 (RS256, 60분 TTL 캐시).
- 인메모리 dict에 세션을 저장하고 조회·삭제한다 (TTL 24시간, lazy expiry).
- 채팅 요청을 Orchestrator ACP 엔드포인트로 프록시한다 (동기 및 SSE 스트리밍).
- `asyncio.Queue`로 Browser Agent의 도구 호출을 Extension SSE 채널에 전달한다.
- `asyncio.Future`로 Extension의 도구 실행 결과를 Browser Agent에 반환한다.

## 데이터 흐름 (webMCP-inspired 3-hop)

```
Browser Agent
  │
  │  POST /sessions/{id}/browser-tools/invoke  (blocking, 60s)
  ▼
Gateway (asyncio.Queue에 enqueue)
  │
  │  SSE event: { inv_id, tool_name, params }
  ▼
Extension (GET /sessions/{id}/commands SSE 채널 구독)
  │
  │  DOM 액션 실행
  │
  │  POST /sessions/{id}/browser-tools/result/{inv_id}
  ▼
Gateway (asyncio.Future.set_result()) → Browser Agent 응답 반환
```

## API 엔드포인트

| 인증 | 메서드 | 경로 | 설명 |
|------|--------|------|------|
| 불필요 | `GET` | `/health` | 헬스체크 |
| JWT 필요 | `POST` | `/sessions` | 세션 생성 |
| JWT 필요 | `GET` | `/sessions/{id}` | 세션 조회 |
| JWT 필요 | `DELETE` | `/sessions/{id}` | 세션 비활성화 |
| JWT 필요 | `POST` | `/sessions/{id}/chat` | 채팅 (동기) |
| JWT 필요 | `GET` | `/sessions/{id}/chat/stream` | 채팅 스트리밍 (SSE) |
| 불필요 | `GET` | `/sessions/{id}/commands` | 브라우저 도구 호출 SSE 채널 |
| 불필요 | `POST` | `/sessions/{id}/browser-tools/invoke` | 브라우저 도구 실행 요청 (Browser Agent 호출) |
| 불필요 | `POST` | `/sessions/{id}/browser-tools/result/{inv_id}` | 도구 실행 결과 제출 (Extension 호출) |
| 불필요 | `GET` | `/sessions/{id}/browser-status` | 브라우저 제어 상태 조회 |

### `POST /sessions`

새 세션을 생성한다. `session_id`는 UUID hex 문자열로 자동 생성된다.

- **응답 상태**: `201 Created`

**응답 스키마**

| 필드 | 타입 | 설명 |
|------|------|------|
| `session_id` | string | 세션 식별자 (UUID hex) |
| `user_id` | string | Keycloak `sub` claim |
| `status` | string | `"active"` |
| `browser_controlling` | boolean | 브라우저 제어 활성 여부 |

### `GET /sessions/{session_id}`

세션 메타데이터를 반환한다. JWT `sub` != `session.user_id`이면 `403`을 반환한다.

### `DELETE /sessions/{session_id}`

세션을 비활성 상태로 표시한다 (`status: "inactive"`). 인메모리 레코드는 TTL이 만료될 때까지 유지된다.

### `POST /sessions/{session_id}/chat`

단일 채팅 턴을 Orchestrator에 동기 ACP 요청으로 전달한다. Orchestrator 장애 시 `502 Bad Gateway`를 반환한다.

**요청 스키마**

| 필드 | 타입 | 설명 |
|------|------|------|
| `content` | string | 사용자 메시지 |
| `images` | string[] | base64 이미지 (선택) |

### `GET /sessions/{session_id}/chat/stream`

Orchestrator 응답 토큰을 SSE로 스트리밍한다.

- **쿼리 파라미터**: `content` (string, 필수)

**SSE 이벤트 타입**

| `type` | 설명 |
|--------|------|
| `token` | LLM 생성 토큰 (`content` 필드 포함) |
| `tool_start` | 도구 호출 시작 (`name` 필드 포함) |
| `tool_end` | 도구 호출 완료 (`name` 필드 포함) |
| `done` | 스트림 종료 (`run_id` 필드 포함) |
| `error` | 오류 발생 (`error` 필드 포함) |

### `GET /sessions/{session_id}/commands`

Extension background service worker가 연결을 유지하는 SSE 채널. Browser Agent의 도구 호출 요청이 이 채널을 통해 Extension에 전달된다.

- **킵얼라이브**: `comment: keepalive` 15초마다

**이벤트 데이터 스키마**

| 필드 | 타입 | 설명 |
|------|------|------|
| `inv_id` | string | 호출 식별자 (UUID) |
| `tool_name` | string | `navigate` \| `click` \| `type` \| `scroll` \| `screenshot` \| `click_by_mark_id` \| `extract_content` \| `wait_for_element` \| `get_page_info` \| `get_structured_dom` |
| `params` | object | tool_name별 파라미터 |

### `POST /sessions/{session_id}/browser-tools/invoke`

Browser Agent가 호출하는 엔드포인트. Extension이 결과를 POST할 때까지 최대 60초 blocking 대기한다.

**요청 스키마**

| 필드 | 타입 | 설명 |
|------|------|------|
| `tool_name` | string | 실행할 도구 이름 |
| `params` | object | 도구 파라미터 |

**응답**: Extension이 제출한 도구 실행 결과 (JSON). 60초 초과 시 `504 Gateway Timeout`.

### `POST /sessions/{session_id}/browser-tools/result/{inv_id}`

Extension이 DOM 액션 결과를 제출하는 엔드포인트. `inv_id`에 해당하는 `asyncio.Future`를 resolve한다.

**요청 스키마**

| 필드 | 타입 | 설명 |
|------|------|------|
| `inv_id` | string | 호출 식별자 |
| `success` | boolean | 실행 성공 여부 |
| `result` | any | 실행 결과 (선택) |
| `error` | string \| null | 오류 메시지 (실패 시) |

### `GET /sessions/{session_id}/browser-status`

현재 세션의 브라우저 제어 활성 여부를 반환한다.

**응답**: `{"session_id": "...", "browser_controlling": true | false}`

### `GET /health`

**응답**: `{"status": "ok", "service": "gateway", "active_sessions": <int>}`

## 인메모리 상태

단일 인스턴스에서 asyncio 기반으로 관리된다. 수평 확장 시 Redis Streams로 교체 필요.

| 클래스 | 역할 |
|--------|------|
| `SessionStore` | 세션 dict + TTL lazy expiry. SSE 구독자 수, 브라우저 제어 상태 추적 |
| `InvocationBroker` | `asyncio.Queue` (세션별 도구 호출 큐) + `asyncio.Future` (inv_id별 결과 대기). 120초 경과 stale invocation 주기적 정리 |

## 의존 서비스

| 서비스 | 용도 |
|--------|------|
| Keycloak `:8080` | JWKS 엔드포인트 (`/realms/browser-agent/protocol/openid-connect/certs`) |
| Orchestrator `:8001` | ACP `POST /runs`, `POST /runs/stream` |

## 환경변수

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `DATABASE_URL` | string | `postgresql+asyncpg://postgres:password@postgres:5432/browser_agent` | PostgreSQL DSN (현재 미사용, 향후 확장용) |
| `ORCHESTRATOR_URL` | string | `http://orchestrator:8001` | Orchestrator 서비스 URL |
| `KEYCLOAK_REALM_URL` | string | `http://localhost:8080/realms/browser-agent` | Keycloak Realm URL (`iss` claim 검증에 사용) |
| `KEYCLOAK_JWKS_URL` | string | `""` | JWKS fetch URL. 비어있으면 `{KEYCLOAK_REALM_URL}/protocol/openid-connect/certs`로 자동 구성 |
| `KEYCLOAK_AUDIENCE` | string | `browser-agent-extension` | JWT `aud` claim 검증 값 |
| `SESSION_TTL` | int | `86400` | 세션 인메모리 TTL (초), 기본 24시간 |
| `BROWSER_TOOL_TIMEOUT` | float | `60.0` | 브라우저 도구 응답 대기 최대 시간 (초) |
| `CORS_ORIGINS` | string | `""` | 허용 Origin 목록 (쉼표 구분). 비어있으면 `*` |
| `CHROME_EXTENSION_ID` | string | `""` | 지정 시 `chrome-extension://{id}`를 CORS 허용 Origin에 추가 |
| `ENVIRONMENT` | string | `"production"` | `"development"` 설정 시 `localhost:3000`, `localhost:5173`을 CORS에 추가 |

## 로컬 실행

```bash
cd services/gateway
uv pip install -e ../shared -e .
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Docker로 실행:

```bash
cd infra
docker compose -f docker-compose.services.yml up --build gateway
```

## 구현 주의사항

- JWT 검증은 `app.state.verifier` (`KeycloakJWTVerifier`)에 위임한다. Gateway 자체에서 검증 로직을 구현하지 않는다.
- `DELETE /sessions/{id}`는 인메모리 레코드를 즉시 삭제하지 않고 `status`를 `"inactive"`로 변경한다. TTL은 `SESSION_TTL`로 재설정된다.
- SSE 킵얼라이브: `asyncio.wait_for(queue.get(), timeout=15.0)`이 15초 대기 후 데이터 없으면 `comment: keepalive`를 전송한다.
- `GET /sessions/{id}/commands`는 인증 없이 접근 가능하다. Extension background script가 JWT를 매 SSE 연결 요청마다 안전하게 전달하기 어렵기 때문이다.
- `asyncio.Queue + asyncio.Future` 패턴은 단일 Gateway 프로세스 내에서만 동작한다. 수평 확장이 필요하면 Redis Streams로 교체해야 한다.
- stale invocation 정리 주기: 120초 이상 결과가 오지 않은 invocation을 60초마다 취소한다.

## 파일 구조

```
services/gateway/
├── main.py              # FastAPI 애플리케이션, CORS 설정, 라우터 등록
├── settings.py          # pydantic-settings 기반 환경변수
├── models.py            # API 요청/응답 Pydantic 모델
├── api/
│   ├── sessions.py      # POST/GET/DELETE /sessions
│   ├── chat.py          # POST /chat, GET /chat/stream
│   ├── browser_tools.py # GET /commands, POST /invoke, POST /result
│   └── deps.py          # FastAPI 의존성 (세션 검증 등)
├── core/
│   ├── session_store.py     # SessionStore (인메모리 TTL 스토어)
│   └── invocation_broker.py # InvocationBroker (asyncio.Queue + Future)
├── tests/
│   ├── conftest.py          # 공통 픽스처
│   ├── test_health.py       # 헬스체크 테스트
│   ├── test_browser_tools.py # 브라우저 도구 round-trip 테스트
│   └── test_cleanup.py      # stale invocation 정리 테스트
└── Dockerfile           # python:3.13-slim, 포트 8000
```
