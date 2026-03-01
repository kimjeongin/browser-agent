# Code Reviewer Memory - Browser Agent Project

## Critical Recurring Issues

### 1. `evaluate_js` Half-Removal Pattern (2026-02-27)
- `content.ts`에서 `evaluate_js` case가 제거되었으나 `browser_agent/main.py`에 `browser_evaluate_js` LangChain tool이 여전히 존재 → LLM이 여전히 호출 가능, content script는 Unknown action으로 거부함.
- 수정 방향: `browser_agent/main.py`에서 tool과 BROWSER_TOOLS 목록에서도 제거해야 완전 제거.

### 2. `asyncio.get_event_loop()` Deprecation (gateway/main.py)
- Python 3.10+에서 `get_event_loop()`는 실행 중인 루프가 없으면 DeprecationWarning → `asyncio.get_running_loop()` 사용 권장.
- 위치: `gateway/main.py:526` `invoke_browser_tool` 함수.

### 3. `_invocation_to_session` 클린업 시 race condition (gateway/main.py)
- `finally` 블록에서 `_invocation_to_session.pop()` 후 `.values()` 순회가 동시 요청에서 dict 변경 가능 → 복사본으로 순회해야 안전.

### 4. SSE 재연결 backoff 구조 버그 (background.ts)
- `connect()` 함수가 성공 시 `return`으로 빠져나가면 외부 while 루프는 없으므로 스트림이 정상 종료되어도 재연결되지 않음.
- 해결: 성공 후 SSE 스트림 종료를 await하는 구조가 필요 (현재 connectCommandsSSE가 Promise를 즉시 반환하고 read()는 fire-and-forget).

### 5. hardcoded credentials in docker-compose.yml
- `docker-compose.yml`에 `POSTGRES_PASSWORD: password`, `MINIO_ROOT_PASSWORD: minioadmin` 하드코딩.
- `.env.example`은 변수화되어 있지만 `docker-compose.services.yml`의 DATABASE_URL에도 `password` 그대로임.

## Project-Specific Conventions
- 모든 API 호출은 Background Service Worker에서만 (sidepanel 직접 호출 금지)
- Access token: in-memory only, Refresh token: browser.storage.session
- URL 검증: http/https 스킴만 허용 (content.ts 기준)
- asyncio.Queue maxsize=100 (Gateway)
- recursion_limit=25 (shared/acp/server.py, 양 endpoint 동일)
- postToolResult: 3회 재시도, 401 시 토큰 갱신 후 재시도

## Known Architecture Decisions
- evaluate_js는 보안상 제거 결정 → content.ts에서 제거됨, browser_agent/main.py에서도 제거 필요
- asyncio.Queue + asyncio.Future: 단일 인스턴스 한정, 수평 확장 시 Redis 필요
- CORS wildcard 허용 시 allow_credentials=False 강제 (올바름)
