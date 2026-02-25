---
name: add-tests
description: 지정한 파일이나 모듈에 대한 테스트 코드를 작성합니다. Python(pytest)과 TypeScript(vitest) 모두 지원합니다.
disable-model-invocation: true
argument-hint: file-path
---

# 테스트 작성

`$ARGUMENTS`에 대한 테스트를 작성합니다.

## 테스트 작성 프로세스

### 1. 대상 파일 분석

- `$ARGUMENTS` 파일을 읽어 언어(Python/TypeScript), 구조, 주요 로직 파악
- 테스트해야 할 함수/클래스/컴포넌트 목록 작성
- 기존 테스트가 있는지 확인 (`__tests__/`, `tests/`, `*.test.ts`, `*_test.py` 등)
- import하는 의존성을 확인해 무엇을 mock해야 할지 파악

### 2. Python 테스트 (pytest)

파일 위치: `tests/test_{module}.py` (프로젝트 루트 기준)
실행 명령: `uv run pytest` 또는 `pytest`

```python
# tests/test_my_module.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

@pytest.mark.asyncio
async def test_function_success():
    # Arrange
    mock_dep = AsyncMock()
    mock_dep.get.return_value = {"id": 1, "name": "Test"}
    sut = MyService(dependency=mock_dep)

    # Act
    result = await sut.do_something(id=1)

    # Assert
    assert result.name == "Test"
    mock_dep.get.assert_awaited_once_with(1)

@pytest.mark.asyncio
async def test_function_raises_on_not_found():
    mock_dep = AsyncMock()
    mock_dep.get.return_value = None
    sut = MyService(dependency=mock_dep)

    with pytest.raises(NotFoundError, match="not found"):
        await sut.do_something(id=999)

@pytest.fixture
def mock_repository() -> AsyncMock:
    return AsyncMock(spec=MyRepository)

@pytest.fixture
def my_service(mock_repository: AsyncMock) -> MyService:
    return MyService(repository=mock_repository)
```

**레이어별 전략:**
- **도메인/순수 함수**: mock 없이 단위 테스트
- **서비스 레이어**: 의존성을 `AsyncMock(spec=Interface)`으로 교체
- **HTTP 클라이언트**: `respx` 또는 `httpx.MockTransport`로 외부 API mock
- **FastAPI 엔드포인트**: `httpx.AsyncClient(transport=ASGITransport(app=app))`으로 통합 테스트

**pyproject.toml 설정:**
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"

[dependency-groups]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.24",
    "respx>=0.21",
]
```

### 3. TypeScript 테스트 (Vitest)

파일 위치: `src/{module}/__tests__/{file}.test.ts` 또는 파일 옆에 `{file}.test.ts`
실행 명령: `pnpm test` 또는 `npx vitest`

```typescript
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

// 모듈 모킹
vi.mock('@/api/users', () => ({
  fetchUser: vi.fn(),
}));

describe('MyComponent', () => {
  beforeEach(() => {
    vi.clearAllMocks(); // 각 테스트 전 mock 초기화
  });

  it('데이터를 성공적으로 로드하면 표시한다', async () => {
    // Arrange
    vi.mocked(fetchUser).mockResolvedValue({ id: '1', name: 'Alice' });

    // Act
    render(<MyComponent userId="1" />);

    // Assert
    expect(await screen.findByText('Alice')).toBeInTheDocument();
  });

  it('API 오류 시 에러 메시지를 표시한다', async () => {
    vi.mocked(fetchUser).mockRejectedValue(new Error('Network error'));
    render(<MyComponent userId="1" />);
    expect(await screen.findByRole('alert')).toBeInTheDocument();
  });
});

// 비동기 훅 테스트
import { renderHook, act } from '@testing-library/react';

describe('useMyHook', () => {
  it('상태를 올바르게 업데이트한다', async () => {
    const { result } = renderHook(() => useMyHook());
    await act(async () => {
      await result.current.doSomething();
    });
    expect(result.current.data).toEqual(expectedData);
  });
});
```

**vitest.config.ts:**
```typescript
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    coverage: { provider: 'v8', reporter: ['text', 'html'] },
  },
});
```

**설치 명령:**
```bash
pnpm add -D vitest @testing-library/react @testing-library/user-event @vitest/coverage-v8 jsdom @vitejs/plugin-react
```

### 4. MCP 서버 테스트

```python
# tests/test_mcp_tools.py
import pytest
from fastmcp import Client
from main import mcp  # FastMCP 앱 임포트

@pytest.mark.asyncio
async def test_tool_success():
    async with Client(mcp) as client:
        result = await client.call_tool("my_tool", {"param": "value"})
        assert result[0].text == "expected output"

@pytest.mark.asyncio
async def test_tool_error_handling():
    async with Client(mcp) as client:
        with pytest.raises(Exception, match="expected error"):
            await client.call_tool("my_tool", {"param": "invalid"})
```

## 테스트 원칙

- **AAA 패턴**: Arrange(준비) → Act(실행) → Assert(검증)
- **한 테스트, 하나의 동작**: 성공/실패 케이스를 분리 (`test_success`, `test_raises_on_invalid`)
- **경계값 포함**: 빈 입력, 최댓값, null/None, 네트워크 실패 케이스
- **외부 의존성 mock 필수**: HTTP 호출, DB, 파일 시스템, 시간(`datetime.now()`) 모두 mock
- **테스트 이름은 동작 설명**: `test_should_return_404_when_item_not_found` 스타일
- **`beforeEach`에서 mock 초기화**: 테스트 간 상태 오염 방지
- **사용자 관점 테스트**: 내부 구현이 아닌 외부 동작을 검증
