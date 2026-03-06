---
name: tdd-workflow
description: TDD(Test-Driven Development) 방법론. 새 기능 구현 또는 버그 수정 시 자동으로 로드됩니다. Red-Green-Refactor 사이클, 테스트 무결성 제약, 모킹 전략을 정의합니다.
user-invocable: false
---

# TDD Workflow

## 핵심 원칙

**테스트가 먼저다.** 구현 코드를 작성하기 전에 실패하는 테스트를 먼저 작성한다.
요구사항이 변경되면 테스트를 먼저 변경하고, 그 테스트를 통과시키는 방향으로 구현을 수정한다.

---

## 기존 코드 수정 시 필수 절차 (변경 전/후 테스트 실행)

새 기능이 아닌 기존 코드를 변경할 때도 동일한 원칙 적용:

```
1. 변경 전: 테스트 실행 → baseline green 확인
2. 코드 변경
3. 변경 후: 테스트 실행 → 깨진 테스트 있으면 수정 → 다시 green 확인
```

**절대로 "일단 수정하고 테스트는 나중에"는 없다.**
변경이 기존 테스트를 깨면: 구현이 잘못된 것이거나, 테스트가 새 동작을 반영해야 한다.
어떤 경우든 깨진 채로 완료 처리하지 않는다.

---

## Red → Green → Refactor 사이클

### 🔴 RED — 실패하는 테스트 작성

**목표**: 요구사항을 테스트 코드로 표현한다. 테스트는 반드시 실패해야 한다.

**규칙:**
1. 구현 코드 없이 테스트부터 작성한다
2. 테스트를 실행하고 실패를 확인한다 (`FAILED` 메시지, 올바른 이유로 실패하는지 확인)
3. 올바른 이유로 실패해야 한다: "AttributeError: 함수가 없음" 또는 "AssertionError: 기댓값과 다름"
4. 잘못된 이유로 실패하면 (import 오류, 문법 오류) 테스트 코드를 수정한다
5. 실패를 확인하기 전까지 GREEN 단계로 넘어가지 않는다

**결과물**: 실패하는 테스트 파일 + 실패 출력 로그

---

### 🟢 GREEN — 테스트를 통과시키는 최소 구현

**목표**: 테스트를 통과시키기 위한 **최소한의** 코드만 작성한다.

**규칙:**
1. 테스트를 통과시키는 것만 구현한다 — 미래를 위한 기능 추가 금지
2. 구현 후 테스트를 실행하여 `PASSED` 를 확인한다
3. 모든 기존 테스트도 통과해야 한다 (regression 없음)
4. `PASSED`를 확인하기 전까지 REFACTOR 단계로 넘어가지 않는다

**결과물**: 테스트를 통과시키는 구현 코드 + 통과 출력 로그

---

### 🔵 REFACTOR — 테스트를 유지하며 코드 개선

**목표**: 코드 품질을 개선하면서 테스트는 계속 통과시킨다.

**규칙:**
1. 리팩터링 후 반드시 테스트를 다시 실행한다
2. 동작(behavior)이 변경되면 리팩터링이 아니다 — 먼저 테스트를 업데이트한다
3. "있으면 좋겠는 기능"은 이 단계에서 추가하지 않는다 — 새로운 RED 사이클로 시작한다

**결과물**: 개선된 코드 + 테스트 통과 확인

---

## 테스트 무결성 제약 (절대 위반 금지)

이 규칙들을 위반하면 테스트 코드는 거짓된 신뢰를 준다. 반드시 지킨다.

### ❌ 절대 하지 말 것

```python
# ❌ 테스트 대상 함수 자체를 mock하기 — 아무것도 테스트하지 않음
with patch('mymodule.my_function') as mock_fn:
    mock_fn.return_value = expected
    result = my_function(input)  # 실제 함수를 호출하지 않음
    assert result == expected    # 항상 통과

# ❌ 항상 통과하는 단언 — 아무것도 검증하지 않음
assert True
assert result is not None
assert isinstance(result, type(result))

# ❌ 테스트 통과를 위해 예외를 삼키기
try:
    result = my_function(invalid_input)
    assert result is not None  # 예외가 발생해야 하는데 잡아버림
except Exception:
    pass  # 실패를 숨김

# ❌ 테스트를 skip으로 통과시키기
@pytest.mark.skip(reason="나중에 구현")
def test_important_feature():
    ...

# ❌ mock 자체를 검증하기 — 실제 로직을 검증하지 않음
mock_service.get_user.return_value = user_data
result = mock_service.get_user(1)  # mock을 호출
assert result == user_data          # mock의 return_value를 검증 (당연히 통과)
```

```typescript
// ❌ 동일한 패턴 (TypeScript)
vi.mock('./myModule', () => ({ myFn: vi.fn().mockReturnValue(expected) }));
const result = myFn(input); // mock이므로 실제 로직 테스트 안 됨
expect(result).toBe(expected); // 항상 통과

// ❌ expect 없는 테스트 — 아무것도 검증하지 않음
it('works', async () => {
  await someAsyncOperation(); // 예외 없으면 통과 — 의도적이면 rejects/resolves 사용
});
```

### ✅ 올바른 패턴

```python
# ✅ 의존성(외부)을 mock하고, 비즈니스 로직을 테스트
mock_repo = AsyncMock(spec=UserRepository)
mock_repo.find_by_id.return_value = User(id=1, name="Alice")
service = UserService(repository=mock_repo)  # DI로 mock 주입

result = await service.get_user(1)  # 실제 서비스 로직 실행

assert result.name == "Alice"  # 구체적인 값 검증
mock_repo.find_by_id.assert_awaited_once_with(1)  # 의존성 호출 검증

# ✅ 예외 케이스 검증
mock_repo.find_by_id.return_value = None
with pytest.raises(UserNotFoundError, match="User 999 not found"):
    await service.get_user(999)
```

---

## 무엇을 mock하는가

### 단위 테스트: 외부 연결은 전부 mock

| 대상 | Python | TypeScript |
|------|--------|------------|
| HTTP 호출 | `respx`, `httpx.MockTransport` | `vi.mock('axios')`, `msw` |
| 데이터베이스 | `AsyncMock(spec=Repository)` | `vi.mock('./db')` |
| Redis | `AsyncMock(spec=Redis)` | `vi.mock('./redis')` |
| 파일 시스템 | `unittest.mock.patch('builtins.open')` | `vi.mock('fs')` |
| 시간 | `freezegun`, `time-machine` | `vi.useFakeTimers()` |
| 환경변수 | `monkeypatch.setenv()` | `vi.stubEnv()` |
| Ollama/LLM | `AsyncMock(spec=ChatOllama)` | `vi.mock('./llm')` |
| MCP 클라이언트 | `AsyncMock(spec=ClientSession)` | — |

### 무엇을 mock하지 않는가

- 테스트 대상 모듈 자체
- 순수 함수 (외부 의존성 없는 계산 로직)
- 프로젝트 내 다른 레이어 (통합 테스트에서는 실제 레이어 사용 가능)

---

## 테스트 레이어와 전략

```
단위 테스트 (Unit)   — 가장 많음, 가장 빠름, 외부 전부 mock
통합 테스트 (Integration) — 레이어 간 연동 테스트, DB/Redis는 실제 또는 인메모리
E2E 테스트          — 전체 흐름, 실제 서비스, 가장 적게
```

**FastAPI 엔드포인트 통합 테스트:**
```python
# ASGITransport으로 실제 HTTP 처리 없이 앱 테스트 (DB mock 가능)
async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
    response = await client.post("/sessions", json={"user_id": "u1"})
    assert response.status_code == 201
```

**LangGraph 에이전트 테스트:**
```python
# LLM을 mock하고 graph 로직만 테스트
mock_llm = MagicMock()
mock_llm.invoke.return_value = AIMessage(content="", tool_calls=[...])
graph = build_graph(llm=mock_llm)
result = await graph.ainvoke({"messages": [HumanMessage(content="test")]})
```

---

## 프로젝트 테스트 설정

### Python 서비스 (`services/*/`)

```toml
# pyproject.toml
[dependency-groups]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.24",
    "respx>=0.21",
    "freezegun>=1.5",
    "anyio[trio]>=4",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

```bash
# 테스트 실행
uv run pytest
uv run pytest tests/test_specific.py -v
uv run pytest -x  # 첫 실패 시 중단
```

### TypeScript Extension (`extension/`)

```typescript
// vitest.config.ts
import { defineConfig } from 'vitest/config';
export default defineConfig({
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
  },
});
```

```bash
# 테스트 실행
pnpm test
pnpm test --reporter=verbose
pnpm test --watch  # watch 모드
```

---

## 컨텍스트 격리: 서브에이전트 활용

테스트 작성자가 구현 계획을 알면 테스트가 실제 요구사항이 아닌 예상 구현에 맞춰진다.
**복잡한 기능**은 서브에이전트로 각 단계를 격리한다:

```
사용자 요청
  → tdd-test-writer 서브에이전트 (RED) — 테스트 작성, 실패 확인
  → tdd-implementer 서브에이전트 (GREEN) — 최소 구현, 통과 확인
  → 메인 컨텍스트에서 REFACTOR — 코드 품질 개선
```

단순한 기능 (함수 하나, 명확한 스펙)은 메인 컨텍스트에서 직접 처리해도 된다.

---

## Gateway webMCP 비동기 패턴 테스트

Gateway의 `invoke_browser_tool` 엔드포인트는 asyncio.Future로 Extension의 응답을 기다린다.
이 패턴을 테스트하려면 두 HTTP 요청을 **동시에** 실행해야 한다.

### Lifespan 모킹 (Redis / Keycloak / ACP 연결 없이 테스트)

```python
# tests/conftest.py
import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from gateway.main import app


@pytest.fixture
async def gateway_client():
    """AsyncClient with mocked external services."""
    mock_redis = AsyncMock()
    mock_redis.aclose = AsyncMock()

    with (
        patch("gateway.main.aioredis.from_url", return_value=mock_redis),
        patch("gateway.main.KeycloakJWTVerifier"),
        patch("gateway.main.ACPClient"),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client
```

### asyncio.gather()로 invoke ↔ result 동시 테스트

```python
async def test_invoke_and_result_roundtrip(gateway_client):
    session_id = "test-session"

    # SSE 채널 연결 시뮬레이션: queue를 직접 app.state에 주입
    queue = asyncio.Queue()
    app.state.session_queues[session_id] = queue

    async def do_invoke():
        return await gateway_client.post(
            f"/sessions/{session_id}/browser-tools/invoke",
            json={"tool": "navigate", "params": {"url": "https://example.com"}},
        )

    async def do_submit_result():
        # invoke가 queue에 이벤트를 넣을 때까지 대기
        event = await asyncio.wait_for(queue.get(), timeout=5.0)
        invocation_id = event["invocation_id"]
        return await gateway_client.post(
            f"/sessions/{session_id}/browser-tools/result/{invocation_id}",
            json={"success": True, "result": {"url": "https://example.com"}},
        )

    # 두 코루틴을 동시에 실행 — invoke는 Future를 기다리고,
    # submit_result가 Future를 resolve해서 invoke가 응답을 반환
    invoke_res, result_res = await asyncio.gather(do_invoke(), do_submit_result())

    assert invoke_res.status_code == 200
    assert invoke_res.json()["success"] is True
    assert result_res.status_code == 200
```

### app.state 직접 조작 (Future 상태 테스트)

```python
async def test_submit_result_409_already_resolved(gateway_client):
    invocation_id = "test-inv-id"
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    future.set_result({"success": True, "result": None})  # 이미 완료된 Future

    app.state.pending_invocations[invocation_id] = future

    response = await gateway_client.post(
        f"/sessions/any/browser-tools/result/{invocation_id}",
        json={"success": True, "result": {}},
    )
    assert response.status_code == 409
```

---

## 품질 체크리스트

구현 완료 전 확인:

- [ ] 테스트가 먼저 작성되었고, 실패를 확인했는가?
- [ ] 각 테스트가 하나의 동작만 검증하는가?
- [ ] 외부 의존성 (HTTP, DB, Redis, LLM)이 전부 mock되었는가?
- [ ] 테스트 대상 함수/클래스 자체를 mock하지 않았는가?
- [ ] 항상 통과하는 단언이 없는가?
- [ ] 실패 케이스 (에러, None, 빈 값, 경계값)가 테스트되었는가?
- [ ] `@pytest.mark.skip`, `// @ts-ignore`로 테스트를 억압하지 않았는가?
- [ ] 테스트 실행 결과 `PASSED`를 직접 확인했는가?
