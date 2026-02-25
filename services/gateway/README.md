# Gateway

브라우저 확장(Extension)의 공개 진입점. JWT 검증, 세션 관리, Orchestrator 프록시, webMCP-inspired 브라우저 도구 브로커를 담당한다.

## 책임

- Keycloak JWKS로 Bearer 토큰을 검증한다 (RS256, 60분 TTL 캐시).
- Redis에 세션을 저장하고 조회·삭제한다 (TTL 24시간).
- 채팅 요청을 Orchestrator ACP 엔드포인트로 프록시한다 (동기 및 SSE 스트리밍).
- Extension의 브라우저 도구 매니페스트를 등록하고 Browser Agent에 노출한다 (webMCP-style).
- Browser Agent의 도구 호출 요청을 SSE로 Extension에 전달하고, 결과를 다시 Browser Agent에 반환한다.

## 데이터 흐름

### 채팅 흐름

```
Extension ──POST /sessions/{id}/chat──▶ Gateway ──ACP──▶ Orchestrator ──▶ 에이전트
Extension ◀──SSE /sessions/{id}/chat/stream─────────────────────────────────────
```

### 브라우저 도구 흐름 (webMCP-inspired, 3홉)

```
Extension BG SW ──POST /sessions/{id}/browser-tools/register──▶ Gateway
                  (도구 매니페스트 등록, 로그인 직후 1회)

Browser Agent ──POST /sessions/{id}/browser-tools/invoke──▶ Gateway
                                                              │ asyncio.Future 생성
                                                              │ asyncio.Queue에 tool_invocation 이벤트 enqueue
                                                              │
Extension BG SW ◀──SSE /sessions/{id}/commands (event: tool_invocation)──
                  BrowserToolRegistry.invoke() 실행
                  │
Extension BG SW ──POST /sessions/{id}/browser-tools/result/{inv_id}──▶ Gateway
                                                                          │ asyncio.Future.set_result()
                                                                          ▼
                                                                    Browser Agent (await 해제, 결과 수신)
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
| 불필요 | `GET` | `/sessions/{id}/commands` | 브라우저 명령 채널 (SSE, Extension 수신) |
| 불필요 | `POST` | `/sessions/{id}/browser-tools/register` | 브라우저 도구 매니페스트 등록 (Extension) |
| 불필요 | `GET` | `/sessions/{id}/browser-tools` | 브라우저 도구 목록 조회 (Browser Agent) |
| 불필요 | `POST` | `/sessions/{id}/browser-tools/invoke` | 브라우저 도구 호출 (Browser Agent, blocking) |
| 불필요 | `POST` | `/sessions/{id}/browser-tools/result/{inv_id}` | 도구 실행 결과 제출 (Extension) |

### `POST /sessions`

새 세션을 생성한다. `session_id`는 UUID hex 문자열로 자동 생성된다.

- **응답 상태**: `201 Created`

**응답 스키마**

| 필드 | 타입 | 설명 |
|------|------|------|
| `session_id` | string | 세션 식별자 (UUID hex) |
| `user_id` | string | Keycloak `sub` claim |
| `status` | string | `"active"` |

### `GET /sessions/{session_id}/commands`

Extension background service worker가 연결을 유지하는 SSE 채널. Browser Agent가 도구를 호출할 때 `tool_invocation` 이벤트를 push한다.

- **킵얼라이브**: `: keepalive` 15초마다

**SSE 이벤트 타입**

| event | 설명 |
|-------|------|
| `tool_invocation` | 도구 호출 요청 |

**tool_invocation 페이로드**

| 필드 | 타입 | 설명 |
|------|------|------|
| `invocation_id` | string | 호출 식별자 (UUID) |
| `tool` | string | 도구 이름 |
| `params` | object | 도구 파라미터 |

### `POST /sessions/{session_id}/browser-tools/register`

Extension이 로그인 직후 도구 매니페스트를 등록한다. Gateway는 `browser_tool_manifests[session_id]`에 저장하고 Browser Agent에 노출한다.

**요청 스키마**

| 필드 | 타입 | 설명 |
|------|------|------|
| `tools` | array | JSON Schema 도구 정의 목록 |

### `GET /sessions/{session_id}/browser-tools`

Browser Agent가 사용 가능한 도구 목록을 조회한다. 등록된 도구가 없으면 빈 배열을 반환한다.

### `POST /sessions/{session_id}/browser-tools/invoke`

Browser Agent가 도구를 호출한다. Extension이 결과를 반환할 때까지 블로킹된다 (기본 30초 타임아웃).

- **503**: Extension이 SSE 채널에 연결되지 않은 경우
- **408**: 타임아웃 (Extension이 30초 내 응답하지 않은 경우)

**요청 스키마**

| 필드 | 타입 | 설명 |
|------|------|------|
| `tool` | string | 도구 이름 |
| `params` | object | 도구 파라미터 |

**응답 스키마**

| 필드 | 타입 | 설명 |
|------|------|------|
| `invocation_id` | string | 호출 식별자 |
| `success` | boolean | 실행 성공 여부 |
| `result` | any | 실행 결과 (선택) |
| `error` | string \| null | 오류 메시지 (실패 시) |

### `POST /sessions/{session_id}/browser-tools/result/{invocation_id}`

Extension이 도구 실행 결과를 제출한다. Gateway는 해당 `asyncio.Future`를 resolve하여 Browser Agent의 await를 해제한다.

- **404**: 해당 `invocation_id`의 pending 요청 없음
- **409**: 이미 결과가 제출된 경우

### `GET /health`

- **응답**: `{"status": "ok", "service": "gateway"}`

## 앱 상태 (In-memory)

| 속성 | 타입 | 설명 |
|------|------|------|
| `session_queues` | `dict[str, asyncio.Queue]` | 세션별 SSE 명령 큐 |
| `pending_invocations` | `dict[str, asyncio.Future]` | 진행 중인 도구 호출 Future |
| `browser_tool_manifests` | `dict[str, list[dict]]` | 세션별 등록된 도구 정의 |

> 단일 Gateway 인스턴스를 가정한다. 수평 확장이 필요한 경우 `asyncio.Queue`/`Future`를 Redis Queue/Future로 교체한다.

## Redis 키 네임스페이스

| 키 패턴 | 타입 | TTL | 용도 |
|---------|------|-----|------|
| `session:{session_id}` | String (JSON) | 24시간 | 세션 상태 저장 |

## 의존 서비스

| 서비스 | 용도 |
|--------|------|
| Redis `:6379` | 세션 저장 |
| Keycloak `:8080` | JWKS 엔드포인트 (`/realms/browser-agent/protocol/openid-connect/certs`) |
| Orchestrator `:8001` | ACP `POST /runs`, `POST /runs/stream` |

## 환경변수

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `REDIS_URL` | string | `redis://redis:6379/0` | Redis 연결 URL |
| `DATABASE_URL` | string | `postgresql+asyncpg://postgres:password@postgres:5432/browser_agent` | PostgreSQL DSN |
| `ORCHESTRATOR_URL` | string | `http://orchestrator:8001` | Orchestrator 서비스 URL |
| `KEYCLOAK_REALM_URL` | string | `http://keycloak:8080/realms/browser-agent` | Keycloak Realm URL |
| `KEYCLOAK_AUDIENCE` | string | `browser-agent-extension` | JWT `aud` claim 검증 값 |
| `SESSION_TTL` | int | `86400` | 세션 Redis TTL (초), 기본 24시간 |
| `TOOL_INVOCATION_TIMEOUT` | float | `30.0` | 도구 호출 타임아웃 (초) |

## 로컬 실행

```bash
cd services/gateway
uv pip install -e ../shared -e .
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Docker로 실행:

```bash
# 프로젝트 루트에서
docker compose -f docker-compose.services.yml up --build gateway
```

## 구현 주의사항

- JWT 검증은 `app.state.verifier` (`KeycloakJWTVerifier`)에 위임한다.
- `DELETE /sessions/{id}`는 Redis 레코드를 즉시 삭제하지 않고 `status`를 `"inactive"`로 변경한다.
- `GET /sessions/{id}/commands` 엔드포인트는 인증 없이 접근 가능하다. Extension이 JWT를 안전하게 전달할 수 없는 내부 컨텍스트이기 때문이다. (browser-tools 엔드포인트들도 동일)
- `asyncio.Future`는 Python 이벤트 루프에 귀속된다. Gateway 재시작 시 pending invocation은 모두 소실된다.

## 파일 구조

```
services/gateway/
├── main.py          # FastAPI 애플리케이션 (전체 로직)
├── pyproject.toml   # 패키지 메타데이터 및 의존성
└── Dockerfile       # python:3.13-slim, 포트 8000
```
