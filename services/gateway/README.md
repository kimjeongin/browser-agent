# Gateway

브라우저 확장(Extension)의 공개 진입점. JWT 검증, 세션 관리, Orchestrator 프록시, 브라우저 명령 SSE 채널을 담당한다.

## 책임

- Keycloak JWKS로 Bearer 토큰을 검증한다 (RS256, 60분 TTL 캐시).
- Redis에 세션을 저장하고 조회·삭제한다 (TTL 24시간).
- 채팅 요청을 Orchestrator ACP 엔드포인트로 프록시한다 (동기 및 SSE 스트리밍).
- `browser_cmd:{session_id}` Redis Pub/Sub 채널을 SSE로 Extension에 전달한다.
- Extension이 제출한 명령 실행 결과를 `browser_result:{command_id}` 채널로 게시한다.

## 데이터 흐름

```
Extension BG SW ──GET /sessions/{id}/commands──▶ Gateway :8000
                                                      │ Redis SUBSCRIBE browser_cmd:{id}
                                                      │
Browser Relay MCP ──Redis PUBLISH browser_cmd:{id}───┘ (SSE push)

Extension Content Script ──POST /sessions/{id}/command-result──▶ Gateway
                                                                      │ Redis PUBLISH browser_result:{cmd_id}
                                                                      ▼
                                                              Browser Relay MCP
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
| 불필요 | `GET` | `/sessions/{id}/commands` | 브라우저 명령 채널 (SSE) |
| 불필요 | `POST` | `/sessions/{id}/command-result` | 명령 실행 결과 제출 |

### `POST /sessions`

새 세션을 생성한다. `session_id`는 UUID hex 문자열로 자동 생성된다.

- **응답 상태**: `201 Created`

**응답 스키마**

| 필드 | 타입 | 설명 |
|------|------|------|
| `session_id` | string | 세션 식별자 (UUID hex) |
| `user_id` | string | Keycloak `sub` claim |
| `status` | string | `"active"` |

### `GET /sessions/{session_id}`

세션 메타데이터를 반환한다. JWT `sub` != `session.user_id`이면 `403`을 반환한다.

### `DELETE /sessions/{session_id}`

세션을 비활성 상태로 표시한다 (`status: "inactive"`). Redis 레코드는 TTL이 만료될 때까지 유지된다.

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

Extension background service worker가 연결을 유지하는 SSE 채널. Browser Relay MCP가 `browser_cmd:{session_id}` 채널에 게시한 명령을 SSE 프레임으로 전달한다.

- **킵얼라이브**: `: keepalive` 15초마다

**BrowserCommand 스키마**

| 필드 | 타입 | 설명 |
|------|------|------|
| `command_id` | string | 명령 식별자 |
| `session_id` | string | 세션 식별자 |
| `action` | string | `navigate` \| `click` \| `type` \| `scroll` \| `screenshot` \| `extract_content` \| `wait_for_element` \| `evaluate_js` \| `get_page_info` |
| `params` | object | action별 파라미터 |

### `POST /sessions/{session_id}/command-result`

Extension이 명령 실행 결과를 제출한다. Gateway는 수신 즉시 `browser_result:{command_id}` 채널에 PUBLISH한다.

**CommandResult 스키마**

| 필드 | 타입 | 설명 |
|------|------|------|
| `command_id` | string | 명령 식별자 |
| `success` | boolean | 실행 성공 여부 |
| `result` | any | 실행 결과 (선택) |
| `error` | string \| null | 오류 메시지 (실패 시) |
| `screenshot` | string \| null | base64 PNG (선택) |

### `GET /health`

- **응답**: `{"status": "ok", "service": "gateway"}`

## Redis 키 네임스페이스

| 키 패턴 | 타입 | TTL | 용도 |
|---------|------|-----|------|
| `session:{session_id}` | String (JSON) | 24시간 | 세션 상태 저장 |
| `browser_cmd:{session_id}` | Pub/Sub 채널 | — | Browser Relay → Extension 명령 전달 |
| `browser_result:{command_id}` | Pub/Sub 채널 | — | Extension → Browser Relay 결과 전달 |

## 의존 서비스

| 서비스 | 용도 |
|--------|------|
| Redis `:6379` | 세션 저장, 브라우저 명령/결과 Pub/Sub |
| PostgreSQL `:5432` | (간접) LangGraph 체크포인터용 (Orchestrator가 직접 사용) |
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

- JWT 검증은 `app.state.verifier` (`KeycloakJWTVerifier`)에 위임한다. Gateway 자체에서 검증 로직을 구현하지 않는다.
- `DELETE /sessions/{id}`는 Redis 레코드를 즉시 삭제하지 않고 `status`를 `"inactive"`로 변경한다. TTL은 `SESSION_TTL`로 재설정된다.
- SSE 킵얼라이브 구현: `pubsub.get_message(timeout=1.0)`이 1초 대기 후 데이터 없으면 추가로 14초 sleep하여 ~15초 주기를 유지한다.
- `GET /sessions/{id}/commands`는 인증 없이 접근 가능하다. Extension content script가 JWT를 안전하게 전달할 수 없는 컨텍스트이기 때문이다.

## 파일 구조

```
services/gateway/
├── main.py          # FastAPI 애플리케이션 (전체 로직)
├── pyproject.toml   # 패키지 메타데이터 및 의존성
└── Dockerfile       # python:3.13-slim, 포트 8000
```
