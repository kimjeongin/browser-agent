---
name: react-typescript-patterns
description: React + TypeScript 애플리케이션의 컴포넌트, 상태 관리, 비동기 데이터 패칭, 테스트 패턴. React/TypeScript 코드 작성 및 수정 시 자동으로 로드됩니다.
user-invokable: false
---

# React + TypeScript Best Practices

## TypeScript 설정 (strict mode)

```json
// tsconfig.json
{
  "compilerOptions": {
    "strict": true,
    "noUncheckedIndexedAccess": true,
    "exactOptionalPropertyTypes": true,
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx"
  }
}
```

**규칙:**
- `any` 타입 사용 금지 → `unknown` + 타입 가드 사용
- `interface` 대신 `type` 선호 (확장이 필요한 경우만 `interface`)
- React 컴포넌트 Props는 `type`으로 정의

---

## 컴포넌트 패턴

```typescript
// components/UserCard.tsx
type UserCardProps = {
  userId: string;
  onSelect?: (id: string) => void;
  className?: string;
};

export function UserCard({ userId, onSelect, className }: UserCardProps) {
  const { data: user, isLoading, error } = useUser(userId);

  if (isLoading) return <Skeleton />;
  if (error) return <ErrorMessage error={error} />;
  if (!user) return null;

  return (
    <div className={cn("card", className)} onClick={() => onSelect?.(userId)}>
      <h3>{user.name}</h3>
      <p>{user.email}</p>
    </div>
  );
}
```

**규칙:**
- Named export 사용 (`export default` 지양 — 리팩터링 시 이름 추적 어려움)
- Props에 기본값보다 옵셔널(`?`) + 조건부 렌더링 선호
- 컴포넌트는 순수하게 — 사이드이펙트는 `useEffect` 또는 커스텀 훅으로 분리

---

## 서버 상태 관리: TanStack Query

```typescript
// hooks/useUser.ts
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { fetchUser, updateUser } from '@/api/users';

// 쿼리 키 팩토리 (타입 안전성 + 무효화 편의)
export const userKeys = {
  all: ['users'] as const,
  detail: (id: string) => ['users', id] as const,
};

export function useUser(userId: string) {
  return useQuery({
    queryKey: userKeys.detail(userId),
    queryFn: () => fetchUser(userId),
    staleTime: 5 * 60 * 1000, // 5분 캐시
  });
}

export function useUpdateUser(userId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: UpdateUserInput) => updateUser(userId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: userKeys.detail(userId) });
    },
  });
}
```

---

## 클라이언트 상태 관리: Zustand

```typescript
// stores/uiStore.ts
import { create } from 'zustand';
import { devtools } from 'zustand/middleware';

type UIState = {
  sidebarOpen: boolean;
  toggleSidebar: () => void;
  theme: 'light' | 'dark';
  setTheme: (theme: 'light' | 'dark') => void;
};

export const useUIStore = create<UIState>()(
  devtools(
    (set) => ({
      sidebarOpen: false,
      toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),
      theme: 'light',
      setTheme: (theme) => set({ theme }),
    }),
    { name: 'ui-store' },
  ),
);
```

**언제 무엇을 사용할까:**
- **TanStack Query**: API 데이터, 서버 상태 (캐싱, 리페치, 로딩/에러 처리)
- **Zustand**: UI 상태, 사용자 설정, 앱 전역 클라이언트 상태
- **`useState`**: 단일 컴포넌트 로컬 상태
- **Context API**: 테마, 인증 같은 변경이 드문 전역 값

---

## API 클라이언트

```typescript
// api/client.ts
const BASE_URL = import.meta.env.VITE_API_URL as string;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...init?.headers,
    },
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
    throw new APIError(response.status, error.detail);
  }

  return response.json() as Promise<T>;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body: unknown) =>
    request<T>(path, { method: 'POST', body: JSON.stringify(body) }),
};

// api/users.ts
export async function fetchUser(id: string): Promise<User> {
  return api.get<User>(`/users/${id}`);
}
```

---

## 커스텀 훅 패턴

```typescript
// hooks/useDebounce.ts
import { useState, useEffect } from 'react';

export function useDebounce<T>(value: T, delay: number): T {
  const [debouncedValue, setDebouncedValue] = useState<T>(value);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedValue(value), delay);
    return () => clearTimeout(timer); // cleanup 필수
  }, [value, delay]);

  return debouncedValue;
}
```

**커스텀 훅 규칙:**
- `use` 접두사 필수
- 하나의 관심사만 처리
- `useEffect` cleanup 항상 구현 (메모리 누수 방지)
- 반환 타입 명시

---

## 에러 처리

```typescript
// components/ErrorBoundary.tsx
import { Component, ErrorInfo, ReactNode } from 'react';

type Props = { children: ReactNode; fallback?: ReactNode };
type State = { hasError: boolean; error: Error | null };

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Uncaught error:', error, info);
  }

  render() {
    if (this.state.hasError) {
      return this.props.fallback ?? <div>Something went wrong.</div>;
    }
    return this.props.children;
  }
}

// Promise rejection 처리 (전역)
window.addEventListener('unhandledrejection', (event) => {
  console.error('Unhandled promise rejection:', event.reason);
});
```

---

## 테스트: Vitest + Testing Library

```typescript
// components/__tests__/UserCard.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { UserCard } from '../UserCard';

// API 모킹
vi.mock('@/api/users', () => ({
  fetchUser: vi.fn(),
}));

function renderWithProviders(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
}

describe('UserCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('로딩 중에 스켈레톤을 표시한다', () => {
    vi.mocked(fetchUser).mockReturnValue(new Promise(() => {})); // 영원히 pending
    renderWithProviders(<UserCard userId="1" />);
    expect(screen.getByRole('status')).toBeInTheDocument(); // Skeleton
  });

  it('사용자 정보를 표시한다', async () => {
    vi.mocked(fetchUser).mockResolvedValue({ id: '1', name: 'Alice', email: 'a@example.com' });
    renderWithProviders(<UserCard userId="1" />);
    await waitFor(() => expect(screen.getByText('Alice')).toBeInTheDocument());
  });

  it('클릭 시 onSelect 핸들러를 호출한다', async () => {
    vi.mocked(fetchUser).mockResolvedValue({ id: '1', name: 'Alice', email: 'a@example.com' });
    const onSelect = vi.fn();
    renderWithProviders(<UserCard userId="1" onSelect={onSelect} />);
    await userEvent.click(await screen.findByText('Alice'));
    expect(onSelect).toHaveBeenCalledWith('1');
  });
});
```

```typescript
// vitest.config.ts
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

---

## 핵심 원칙

- **Named export**: 컴포넌트/함수 리팩터링 추적 용이
- **단일 책임**: 컴포넌트는 렌더링만, 로직은 커스텀 훅으로 분리
- **타입 안전성**: `any` 금지, `unknown` + 타입 가드 사용
- **서버 상태 ≠ 클라이언트 상태**: TanStack Query vs Zustand 역할 구분
- **테스트는 사용자 관점**: 구현 세부사항이 아닌 동작을 테스트
- **cleanup 의무화**: `useEffect`, 타이머, 이벤트 리스너 해제 누락 금지
