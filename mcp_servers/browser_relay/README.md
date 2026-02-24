# browser-relay

Browser Agent의 MCP 도구 호출을 Extension의 실제 브라우저 탭과 연결하는 중계 서버.

## 책임

- Browser Agent(LangGraph)가 호출하는 MCP 도구를 streamable-HTTP로 수신한다.
- 명령을 JSON으로 직렬화해 Redis `browser_cmd:{session_id}` 채널에 PUBLISH한다.
- 결과 채널 `browser_result:{command_id}`를 구독하고 Extension이 보낸 실행 결과를 반환한다.
- 타임아웃 초과 시 `TimeoutError`를 MCP 호출자에게 전파한다.

## 명령 흐름

```
Browser Agent (MCP client) ──tool call──▶ Browser Relay MCP :8010
Browser Relay ──PUBLISH──▶ Redis browser_cmd:{session_id}
Redis ──▶ Gateway :8000 (subscriber) ──SSE──▶ Extension background.ts
Extension content.ts ──DOM action──▶ 실제 브라우저
Extension ──POST /sessions/{id}/command-result──▶ Gateway
Gateway ──PUBLISH──▶ Redis browser_result:{command_id}
Redis ──▶ Browser Relay (subscriber) ──tool result──▶ Browser Agent
```

### 핵심 패턴: subscribe-before-publish

결과 채널을 먼저 구독한 뒤 명령을 발행한다. 순서가 바뀌면 Extension이 결과를 PUBLISH했을 때 수신자가 없어 결과가 유실된다.

```python
# 1. 결과 채널 구독 (먼저)
await pubsub.subscribe(f"browser_result:{command_id}")

# 2. 명령 발행 (나중)
await redis.publish(f"browser_cmd:{session_id}", json.dumps(cmd_payload))

# 3. 결과 대기
result = await asyncio.wait_for(_wait(), timeout=timeout)
```

## MCP 도구 목록

| 도구 | 파라미터 | 설명 |
|------|----------|------|
| `browser_navigate` | `session_id`, `url` | 탭을 지정 URL로 이동 |
| `browser_click` | `session_id`, `selector` | CSS selector 또는 XPath 요소 클릭 |
| `browser_type` | `session_id`, `selector`, `text`, `clear_first` | 입력 필드에 텍스트 입력. `clear_first=true`이면 기존 값 삭제 후 입력 |
| `browser_scroll` | `session_id`, `direction`, `amount`, `selector?` | 페이지 또는 특정 요소 스크롤. `direction`: `up`/`down`/`left`/`right` |
| `browser_screenshot` | `session_id` | 현재 탭 스크린샷 캡처. 반환값에 base64 PNG 포함 |
| `browser_extract_content` | `session_id`, `selector?`, `include_html` | 페이지 또는 요소의 텍스트(및 HTML) 추출 |
| `browser_wait_for_element` | `session_id`, `selector`, `timeout_ms`, `visible` | DOM에 요소가 나타날 때까지 대기 |
| `browser_evaluate_js` | `session_id`, `script` | 탭에서 JavaScript 실행 후 결과 반환 |
| `get_page_info` | `session_id` | 현재 탭의 URL, 제목, `readyState` 반환 |

모든 도구는 `session_id`를 필수로 받는다. `session_id`는 활성 브라우저 탭과 1:1로 매핑된다.

## 의존 서비스

| 서비스 | 용도 |
|--------|------|
| Redis `:6379` | 명령 발행(`browser_cmd:*`) 및 결과 수신(`browser_result:*`) Pub/Sub |
| Gateway `:8000` | Redis 명령 구독 후 Extension에 SSE push, Extension 결과를 Redis에 PUBLISH |

## 환경변수

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `REDIS_URL` | string | `redis://localhost:6379/0` | Redis 연결 URL |
| `COMMAND_TIMEOUT` | float | `30.0` | 브라우저 결과 대기 최대 시간 (초) |
| `MCP_HOST` | string | `0.0.0.0` | MCP 서버 바인드 주소 |
| `MCP_PORT` | int | `8010` | MCP 서버 포트 |

## 로컬 실행

```bash
cd mcp_servers/browser_relay
uv pip install -e .
python main.py
```

서버 시작 후 `http://localhost:8010/mcp` 엔드포인트(streamable-HTTP transport)에서 MCP 클라이언트 연결을 수신한다.

## 구현 주의사항

- `browser_wait_for_element`는 `timeout_ms`에 왕복 지연 버퍼(5초)를 더해 `COMMAND_TIMEOUT`을 동적으로 조정한다. `timeout_ms`가 `COMMAND_TIMEOUT`보다 길면 전체 타임아웃이 서버 기본값을 초과할 수 있다.
- Redis 클라이언트는 모듈 수준 싱글톤(`_redis`)으로 유지된다. 연결이 끊어지면 다음 호출 시 재생성된다.
- MCP transport: `streamable-http` (FastMCP 2.0+). stdio가 아니므로 서브프로세스로 실행할 필요 없다.

## 파일 구조

```
mcp_servers/browser_relay/
├── main.py          # FastMCP 서버 및 모든 도구 정의
└── pyproject.toml   # Python 3.13+, fastmcp, redis[hiredis], pydantic-settings
```
