# Code Reviewer Memory - Browser Agent Project

## Critical Recurring Issues

### 1. `evaluate_js` Half-Removal Pattern (2026-02-27)
- `content.ts`에서 `evaluate_js` case가 제거되었으나 `browser_agent/main.py`에 `browser_evaluate_js` LangChain tool이 여전히 존재 → LLM이 여전히 호출 가능, content script는 Unknown action으로 거부함.
- 수정 방향: `browser_agent/main.py`에서 tool과 BROWSER_TOOLS 목록에서도 제거해야 완전 제거.

### 2. hardcoded credentials 패턴 (2026-03-07 재확인)
- `docker-compose.yml`: `POSTGRES_PASSWORD: password`, `MINIO_ROOT_PASSWORD: minioadmin`, `KC_BOOTSTRAP_ADMIN_PASSWORD: admin` 하드코딩.
- 모든 서비스 settings.py 기본값에도 `postgres:password@` 패턴: gateway/settings.py:12, browser_agent/settings.py:12, chat_agent/main.py:43, orchestrator/main.py:45.
- `.env.example`은 올바르게 `change_me_in_production` 사용하지만 실제 docker-compose.yml은 미변수화.

### 3. Browser Tool 엔드포인트 인증 누락 (api/browser_tools.py) (2026-03-07)
- `/sessions/{id}/commands` SSE, `/sessions/{id}/browser-tools/invoke`, `/sessions/{id}/browser-tools/result/{inv_id}` 모두 `CurrentUser` 인증 없음.
- 의도적(내부 서비스)이지만 외부 노출 시 위험. 적어도 세션 소유자 검증 필요.

### 4. fetch_webpage SSRF 취약점 (chat_agent/main.py) (2026-03-07)
- `fetch_webpage` 도구가 URL 검증 없이 httpx 요청 수행 → LLM을 통한 SSRF 공격 가능.
- 내부 Docker 네트워크 주소(http://gateway:8000, http://postgres:5432 등) 접근 위험.

### 5. content.ts innerHTML Prompt Injection 경로 (content.ts) (2026-03-07)
- `extract_content` action에서 `include_html: true` 시 `container.innerHTML` 반환.
- 악의적 웹페이지가 HTML에 프롬프트 인젝션 페이로드 삽입 가능.

### 6. SessionStore._evict() 메모리 누수 (session_store.py) (2026-03-07 → 수정됨)
- 이전 이슈: `_evict()` 메서드가 부수 데이터를 정리하지 않음.
- 현재 코드(2026-03-07 확인): `_evict()`가 `_semaphores`, `_sse_subscribers`, `_browser_controlling` 모두 정리함. 해결됨.

### 7. ACPClient 연결 풀링 (shared/acp/client.py) (2026-03-07 → 부분 개선됨)
- `start()`/`close()` async context manager 패턴 추가됨. Orchestrator lifespan에서 `start()` 호출.
- fallback 경로(`_get_client()`에서 `self._client is None`일 때)는 여전히 임시 클라이언트 생성.
- `owns_client = self._client is None` 체크보다 `client is not self._client` 비교가 의도를 더 명확히 표현.

### 8. Orchestrator 분류 LLM 이중 호출 (orchestrator/main.py) (2026-03-07)
- `/runs` 경로: LangGraph supervisor_node에서 분류 1회.
- `/runs/stream` 경로: stream_run 함수에서 별도로 분류 1회. 동일 로직 중복.
- 분류 로직이 두 경로에 중복 구현됨 → 유지보수 위험.

### 9. sidepanel/App.tsx에서 직접 fetch 호출 (App.tsx) (2026-03-07)
- `handleSend()`에서 `fetch()`를 직접 호출하여 SSE 스트림 연결 → Background SW 우회.
- 설계 원칙(모든 API 호출은 Background에서만) 위반이나, SSE 스트리밍 특성상 불가피한 측면 있음.
- 토큰을 sidepanel에서 직접 사용하는 구조.

### 10. GatewayBrowserToolsClient 글로벌 싱글톤 (browser_agent/tools/gateway_client.py) (2026-03-07)
- 모듈 레벨 `_gateway_client` 글로벌 변수 → 테스트 간 상태 누수, 멀티 인스턴스 환경 미지원.

### 11. SSE 이벤트 타입 정의 분산 (2026-03-07)
- Python: acp/server.py에서 문자열 리터럴로 {"type": "token"}, {"type": "tool_start"} 등 산재.
- TypeScript: sse-parser.ts의 SSEEvent 타입이 [k]: unknown으로 너무 느슨함.
- api.ts:streamChat이 parseSSEStream을 사용하지 않고 독립 파싱 루프 구현 → 3중 중복.
- extension/lib/sse-events.ts에 discriminated union 타입 정의 권장.

### 12. messaging.ts 타입과 실제 사용 불일치 (2026-03-07)
- messaging.ts에 Message 유니언 타입과 sendToBackground 함수 정의.
- App.tsx, background.ts는 이를 전혀 사용하지 않고 raw browser.runtime.sendMessage 사용.
- RECOVER_SESSION, FOCUS_AGENT_TAB 메시지 타입이 messaging.ts에 누락.

### 13. Orchestrator settings 모듈 레벨 초기화 불일치 (2026-03-07)
- orchestrator/main.py:48-49: 모듈 레벨에서 settings = OrchestratorSettings() 초기화.
- 다른 서비스(Gateway, ChatAgent)는 lifespan 내부에서 초기화 → 불일치, 테스트 어려움.

### 14. 지역 import 패턴 (2026-03-07)
- sessions.py:63, 87: 함수 내부에서 `from fastapi import HTTPException` (파일 상단에 이미 있음).
- acp/server.py:131: `astream_events` 루프 내부에서 `from langchain_core.messages import AIMessage`.
- 두 곳 모두 파일 상단으로 이동 필요.

### 15. BrowserToolResultRequest.inv_id 중복 (2026-03-07)
- gateway/models.py:31-37: BrowserToolResultRequest에 inv_id가 body 필드로 있음.
- 동시에 /sessions/{id}/browser-tools/result/{inv_id}의 path parameter로도 있음.
- body의 inv_id는 실제로 payload 구성에만 사용되어 불필요. 제거 또는 일치 검증 필요.

### 16. AgentActivityCard에 browser_ prefix 잔여 (2026-03-07)
- extension/components/agent/AgentActivityCard.tsx: STEP_LABELS에 browser_navigate, browser_click 등 구버전 항목 잔존.
- browser_ prefix는 이미 제거된 기능이므로 해당 항목들 제거 필요.

### 17. gateway/settings.py에 미사용 database_url (2026-03-07)
- Gateway는 인메모리 SessionStore 사용, PostgreSQL 직접 사용 안함.
- settings.py에 database_url 필드 존재, pyproject.toml에 asyncpg, sqlalchemy 의존성 있음.
- 미사용 설정 및 의존성 제거 권장.

## Project-Specific Conventions
- 모든 API 호출은 Background Service Worker에서만 (sidepanel 직접 호출 금지) - 단, SSE 스트리밍은 예외
- Access token: in-memory only, Refresh token: browser.storage.session
- URL 검증: http/https 스킴만 허용 (content.ts + tab-manager.ts 양쪽 검증)
- asyncio.Queue maxsize=100 (InvocationBroker)
- recursion_limit=25 (shared/acp/server.py, 양 endpoint 동일)
- postToolResult: 3회 재시도, 401 시 토큰 갱신 후 재시도
- sessionId: browser.storage.local, refreshToken: browser.storage.session

## Folder Structure Patterns
- Extension 테스트: `__tests__/`(엔트리포인트)와 `src/__tests__/`(유틸) 두 경로에 분산 → 통일 필요.
- `extension/lib/`이 과부하: config, api, auth, token, tab, sse-parser, messaging, constants 혼용.
  권장: lib/api/, lib/auth/, lib/browser/ 로 세분화.
- `database_url` 기본값이 gateway, chat_agent, browser_agent, orchestrator 4개 서비스에 중복.
  shared/에 DatabaseSettings 추가하거나 최소한 shared/__init__.py에 공통 상수 정의 고려.
- extension/src/__tests__/browser-tools.test.ts는 실제 소스가 src/ 밖에 있어 불일치.

## Known Architecture Decisions
- Browser Tool 엔드포인트 인증 없음: 내부 서비스 간 통신이라 의도적. CORS + 세션 검증으로 간접 보호.
- asyncio.Queue + asyncio.Future: 단일 인스턴스 한정, 수평 확장 시 Redis 필요
- CORS wildcard 허용 시 allow_credentials=False 강제 (올바름)
- _compress_messages: 컨텍스트 오버플로우 방지용 메시지 압축 (스크린샷 base64 제거)
- GatewayBrowserToolsClient 싱글톤: 단일 httpx 클라이언트를 lifespan 동안 유지 (의도적)
- SSE keepalive: 15초마다 comment 전송 (sse_starlette 기본값 아닌 직접 구현)
- progress_check_node: LLM 호출 없는 휴리스틱 (의도적 - 속도 최적화)
