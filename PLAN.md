# Browser Agent - 개발 계획서

> 최종 수정: 2026-02-24 (Keycloak 인증 추가)
> 상태: 계획 확정 (구현 전)

---

## 1. 시스템 개요

브라우저 확장(WXT)에서 동작하는 멀티 에이전트 챗봇 시스템.
사용자 입력을 받아 일반 대화 응답 또는 실제 브라우저 DOM 제어를 수행한다.

### 기술 원칙
- 모든 통신: **SSE 기반** (WebSocket 없음)
- LLM: **로컬 Ollama** (`localhost:11434`)
- 에이전트 간 통신: **ACP 프로토콜** (HTTP POST 기반)
- 에이전트 구현: **LangGraph v1 + LangChain v1**
- 브라우저 도구 추상화: **MCP 프로토콜**
- 인증/인가: **Keycloak** (토큰 발급) + Gateway JWT 검증 (JWKS 오프라인)

---

## 2. 전체 아키텍처

```
                   ┌─────────────────────────┐
                   │   Keycloak (:8080)       │  ← 토큰 발급 전담
                   │   PKCE Authorization     │
                   │   JWKS 공개키 제공        │
                   └──────────┬──────────────┘
                              │ PKCE 로그인 (browser.identity)
┌─────────────────────────────▼───────────────────────────────────────┐
│                    Browser Extension (WXT + React 19)               │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────┐ │
│  │  Sidepanel UI   │  │  Background SW  │  │   Content Script    │ │
│  │  (Chat 인터페이스)│  │  (토큰·SSE 관리) │  │  (DOM 액션 실행기)  │ │
│  └────────┬────────┘  └────────┬────────┘  └──────────┬──────────┘ │
│           │ runtime.msg        │ fetch SSE             │ runtime.msg │
└───────────┼────────────────────┼───────────────────────┼────────────┘
            │               SSE GET /commands             │
            │ Bearer JWT     POST /command-result         │
┌───────────▼────────────────────▼───────────────────────▼────────────┐
│                        Gateway (:8000) - FastAPI                     │
│  JWT 검증: Keycloak JWKS 오프라인 검증 (TTL 60분 캐시)                │
│  /session/{id}/stream       → LLM 토큰 SSE (UI → 사용자)            │
│  /session/{id}/commands     → 브라우저 명령 SSE (SW 수신)            │
│  /session/{id}/command-result → DOM 실행 결과 POST                   │
│  /session/{id}/chat         → 사용자 메시지 POST                     │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ ACP (HTTP POST)
           ┌───────────────────▼─────────────────────┐
           │         Orchestrator (:8001)             │
           │   의도 분류 (ChatOllama) → 라우팅          │
           └──────────┬──────────────────┬────────────┘
                      │ ACP              │ ACP
          ┌───────────▼────┐   ┌────────▼──────────┐
          │ Chat Agent     │   │ Browser Agent     │
          │  (:8002)       │   │  (:8003)          │
          │ ChatOllama     │   │ ChatOllama        │
          │ web_search MCP │   │ browser_relay MCP │
          └────────────────┘   └────────┬──────────┘
                                        │ MCP (HTTP Streamable)
                               ┌────────▼──────────┐
                               │ Browser Relay MCP  │
                               │  (:8010)           │
                               └────────┬──────────┘
                                        │ Redis Pub/Sub
                                   ┌────▼────┐
                                   │  Redis  │
                                   └─────────┘
```

---

## 3. SSE 기반 브라우저 제어 플로우

WebSocket 없이 양방향 브라우저 제어를 구현하는 핵심 패턴:

```
Browser Agent calls MCP tool "browser_click"
    │
    ▼
Browser Relay MCP Server
    │  1. command_id 생성
    │  2. Redis RPUSH cmd:{session_id}
    │  3. Redis SUBSCRIBE result:{command_id}
    │
    ▼
Gateway (Redis 메시지 수신)
    │  SSE push → Extension Command SSE 채널
    │
    ▼
Extension Background Service Worker
    │  SSE 수신 → chrome.tabs.sendMessage
    │
    ▼
Extension Content Script
    │  DOM 액션 실행
    │  결과 반환
    │
    ▼
Background SW → POST /session/{id}/command-result
    │
    ▼
Gateway → Redis PUBLISH result:{command_id}
    │
    ▼
Browser Relay MCP (SUBSCRIBE 대기 중) → 결과 수신
    │
    ▼
MCP tool 응답 → Browser Agent LangGraph 계속 실행
```

### 시퀀스 다이어그램

```
사용자    Extension(UI)   Extension(SW)   Extension(CS)   Gateway      Orchestrator   Browser Agent   MCP Server   Redis
  │            │               │               │              │               │               │              │          │
  │ 메시지입력  │               │               │              │               │               │              │          │
  ├───────────►│               │               │              │               │               │              │          │
  │            │ POST /chat    │               │              │               │               │              │          │
  │            ├───────────────────────────────►              │               │               │              │          │
  │            │               │               │  ACP /runs   │               │               │              │          │
  │            │               │               │  ───────────────────────────►               │              │          │
  │            │               │               │              │  "browser" 분류               │              │          │
  │            │               │               │              │  ACP /runs   │               │              │          │
  │            │               │               │              │  ────────────────────────────►              │          │
  │            │               │               │              │               │  LangGraph    │              │          │
  │            │               │               │              │               │  MCP 도구 호출 │              │          │
  │            │               │               │              │               │  ────────────►              │          │
  │            │               │               │              │               │               │ cmd_id 생성  │          │
  │            │               │               │              │               │               │ ────────────────────────►
  │            │               │               │              │               │               │ SUBSCRIBE    │          │
  │            │               │               │ SSE push(cmd)│               │               │ ────────────────────────►
  │            │◄──────────────│               │              │               │               │              │          │
  │            │               │ DOM 실행 요청  │              │               │               │              │          │
  │            │               ├──────────────►│              │               │               │              │          │
  │            │               │               │ DOM 실행     │               │               │              │          │
  │            │               │               │ 결과 반환    │               │               │              │          │
  │            │               │◄──────────────│              │               │               │              │          │
  │            │               │ POST /result  │              │               │               │              │          │
  │            │               ├───────────────────────────────►              │               │              │          │
  │            │               │               │              │ Redis PUBLISH │               │              │          │
  │            │               │               │              ├────────────────────────────────────────────────────────►│
  │            │               │               │              │               │               │ SUBSCRIBE 수신│          │
  │            │               │               │              │               │               │◄────────────────────────│
  │            │               │               │              │               │◄──────────────│              │          │
  │            │               │               │              │◄──────────────│               │              │          │
  │            │◄──────────────────────────────│◄─────────────│               │               │              │          │
  │ UI 업데이트 │               │               │              │               │               │              │          │
```

---

## 4. 프로젝트 구조

```
browser-agent/
├── PLAN.md                             # 이 파일 (개발 계획)
│
├── extension/                          # WXT Browser Extension
│   ├── src/
│   │   ├── entrypoints/
│   │   │   ├── sidepanel/             # 메인 채팅 UI
│   │   │   │   ├── main.tsx
│   │   │   │   └── index.html
│   │   │   ├── popup/                 # 빠른 액세스 팝업
│   │   │   │   ├── main.tsx
│   │   │   │   └── index.html
│   │   │   ├── background.ts          # Service Worker (API 프록시, SSE Hub)
│   │   │   └── content.ts             # DOM 조작 실행기
│   │   ├── components/
│   │   │   ├── ui/                    # shadcn/ui 컴포넌트
│   │   │   └── chat/                  # ChatWindow, MessageBubble, ChatInput
│   │   ├── stores/
│   │   │   ├── chat.store.ts          # messages, isLoading (Zustand)
│   │   │   └── session.store.ts       # sessionId, connectionStatus
│   │   ├── hooks/
│   │   │   ├── useChat.ts             # 채팅 메시지 전송/수신
│   │   │   └── useSSE.ts              # SSE 스트리밍 훅
│   │   ├── lib/
│   │   │   ├── api.ts                 # Gateway HTTP 클라이언트
│   │   │   └── browser-actions.ts     # DOM 액션 실행기 (content script용)
│   │   └── types/
│   │       ├── messages.ts            # ExtensionMessage 판별 유니온
│   │       └── browser-actions.ts     # BrowserCommand, CommandResult 타입
│   ├── wxt.config.ts
│   ├── tailwind.css                   # @import "tailwindcss"; @theme { ... }
│   └── package.json
│
├── services/                          # Python Backend Services
│   │
│   ├── shared/                        # 공통 Python 패키지 (로컬 설치)
│   │   ├── pyproject.toml
│   │   └── src/shared/
│   │       ├── auth/
│   │       │   ├── jwt_verifier.py    # Keycloak JWKS 오프라인 검증
│   │       │   └── dependencies.py   # FastAPI get_current_user Depends
│   │       ├── llm/
│   │       │   ├── factory.py         # create_ollama_llm()
│   │       │   └── settings.py        # LLMSettings (Pydantic)
│   │       ├── acp/
│   │       │   ├── client.py          # ACP HTTP 클라이언트
│   │       │   └── server.py          # BaseACPAgentServer
│   │       ├── redis/
│   │       │   ├── client.py          # Redis 싱글톤
│   │       │   └── keys.py            # 키 네임스페이스 상수
│   │       └── models/
│   │           ├── session.py         # Session 도메인 모델
│   │           └── browser_command.py # BrowserCommand, CommandResult
│   │
│   ├── gateway/                       # API Gateway (:8000)
│   │   ├── pyproject.toml
│   │   └── src/gateway/
│   │       ├── main.py                # FastAPI app + lifespan
│   │       ├── core/config.py         # Pydantic Settings
│   │       └── api/v1/
│   │           ├── chat.py            # POST /chat, GET /stream (SSE)
│   │           └── browser_commands.py # GET /commands (SSE), POST /result
│   │
│   ├── orchestrator/                  # Orchestrator Agent (:8001)
│   │   ├── pyproject.toml
│   │   └── src/orchestrator/
│   │       ├── main.py                # ACP Server (FastAPI)
│   │       ├── core/config.py
│   │       └── graph/
│   │           ├── orchestrator.py    # LangGraph Supervisor
│   │           └── nodes/
│   │               ├── classify.py    # 의도 분류 노드
│   │               ├── chat_node.py   # Chat Agent ACP 호출
│   │               └── browser_node.py # Browser Agent ACP 호출
│   │
│   ├── chat_agent/                    # Chat Agent (:8002)
│   │   ├── pyproject.toml
│   │   └── src/chat_agent/
│   │       ├── main.py
│   │       ├── core/config.py
│   │       └── graph/
│   │           └── agent.py           # LangGraph ReAct + web_search MCP
│   │
│   └── browser_agent/                 # Browser Control Agent (:8003)
│       ├── pyproject.toml
│       └── src/browser_agent/
│           ├── main.py
│           ├── core/config.py
│           └── graph/
│               └── agent.py           # LangGraph ReAct + browser_relay MCP
│
├── mcp_servers/
│   │
│   ├── browser_relay/                 # Browser Relay MCP (:8010)
│   │   ├── pyproject.toml
│   │   └── src/browser_relay/
│   │       ├── main.py                # FastMCP HTTP 서버
│   │       └── tools/
│   │           └── browser_tools.py   # click, type, navigate, screenshot...
│   │
│   └── web_search/                    # Web Search MCP (stdio)
│       ├── pyproject.toml
│       └── src/web_search/
│           ├── main.py
│           └── tools/
│               └── search_tools.py    # Tavily 검색
│
├── infra/
│   ├── docker-compose.yml             # PostgreSQL, Redis, MinIO
│   ├── docker-compose.services.yml    # 모든 서비스 통합
│   └── postgres/
│       └── init.sql
│
└── Makefile                           # 공통 개발 명령어
```

---

## 5. 기술 스택

### Browser Extension

| 항목 | 선택 | 버전 |
|------|------|------|
| Bundler | WXT | latest |
| UI | React | 19 |
| 언어 | TypeScript | strict mode |
| CSS | Tailwind CSS | v4 (CSS-first config) |
| 컴포넌트 | shadcn/ui | latest |
| 상태관리 | Zustand | latest |
| 패키지매니저 | pnpm | latest |

### Backend Services (Python)

| 항목 | 선택 | 버전 |
|------|------|------|
| 서버 | FastAPI + uvicorn | latest |
| 에이전트 | LangGraph | v1 (0.2+) |
| LLM 통합 | LangChain | v1 (0.3+) |
| LLM 모델 | Ollama (로컬) | - |
| 에이전트 통신 | ACP (HTTP POST) | custom impl |
| MCP 서버 | FastMCP | latest |
| MCP 클라이언트 | langchain-mcp-adapters | latest |
| 패키지매니저 | uv | latest |

### Ollama 모델 권장

| 에이전트 | 모델 | 이유 |
|---------|------|------|
| Orchestrator | `llama3.1:8b` | 의도 분류만 → 속도 우선 |
| Browser Agent | `qwen2.5:14b` | tool calling 정확도 최우선 |
| Chat Agent | `qwen2.5:7b` | 대화 품질 + 속도 균형 |

### Infrastructure

| 항목 | 선택 | 용도 |
|------|------|------|
| DB | PostgreSQL 16 + pgvector | 대화 히스토리, LangGraph Checkpointer |
| Cache/PubSub | Redis 7 | 브라우저 명령 중계, 세션 캐싱 |
| Object Storage | MinIO (aioboto3) | 스크린샷, 파일 첨부 (S3 호환) |
| 인증 | Keycloak 26 | JWT 발급, PKCE 플로우, JWKS 제공 |
| 컨테이너 | Docker Compose | 로컬 개발 환경 |

---

## 6. 각 서비스 엔드포인트

### Gateway (:8000)

```
# 모든 엔드포인트는 Authorization: Bearer <keycloak_access_token> 필수
POST   /api/v1/sessions                       # 세션 생성
DELETE /api/v1/sessions/{session_id}          # 세션 종료
POST   /api/v1/sessions/{session_id}/chat     # 사용자 메시지 전송
GET    /api/v1/sessions/{session_id}/stream   # LLM 토큰 SSE (UI용)
GET    /api/v1/sessions/{session_id}/commands # 브라우저 명령 SSE (SW용)
POST   /api/v1/sessions/{session_id}/command-result # DOM 실행 결과

# 토큰 발급은 Gateway가 아닌 Keycloak이 직접 처리
# Extension → Keycloak PKCE 플로우 → access_token + refresh_token
```

### Keycloak (:8080) — 외부 진입점

```
# Extension이 직접 호출 (Gateway 경유 없음)
GET  /realms/browser-agent/protocol/openid-connect/auth      # 인가 엔드포인트 (PKCE)
POST /realms/browser-agent/protocol/openid-connect/token     # 토큰 교환 / 갱신
POST /realms/browser-agent/protocol/openid-connect/logout    # 로그아웃
GET  /realms/browser-agent/protocol/openid-connect/certs     # JWKS (서버 검증용)
```

### Orchestrator / Chat Agent / Browser Agent (:8001-8003)

```
POST   /runs          # ACP 실행 요청 (동기)
POST   /runs/stream   # ACP 실행 요청 (SSE 스트리밍)
GET    /runs/{run_id} # 실행 상태 조회
GET    /health        # 헬스 체크
```

### Browser Relay MCP (:8010)

```
# MCP 도구 목록 (langchain-mcp-adapters로 자동 로드)
browser_navigate(session_id, url)
browser_click(session_id, selector, description)
browser_type(session_id, selector, text)
browser_scroll(session_id, direction, amount)
browser_screenshot(session_id)
browser_extract_content(session_id, selector)
browser_wait_for_element(session_id, selector, timeout_ms)
browser_evaluate_js(session_id, script)
get_page_info(session_id)
```

---

## 7. Keycloak 인증 플로우

### 전체 인증 흐름

```
Extension (Background SW)                Keycloak (:8080)           Gateway (:8000)
        │                                      │                          │
        │  1. browser.identity.launchWebAuthFlow()                        │
        │  PKCE code_challenge 생성 (SHA-256)  │                          │
        ├─────── GET /auth?response_type=code ─►│                          │
        │              &client_id=extension     │                          │
        │              &code_challenge=...      │                          │
        │              &code_challenge_method=S256                         │
        │                                      │                          │
        │◄──── redirect /?code=AUTH_CODE ──────│                          │
        │                                      │                          │
        │  2. 토큰 교환                          │                          │
        ├─── POST /token (code + verifier) ───►│                          │
        │◄─── access_token (JWT) + refresh_token                          │
        │                                      │                          │
        │  access_token → 메모리 보관           │                          │
        │  refresh_token → storage.session     │                          │
        │                                      │                          │
        │  3. API 호출 (토큰 첨부)              │                          │
        ├──────────────────────────────────────────── Bearer <JWT> ───────►│
        │                                      │                          │
        │                                      │  4. JWT 검증 (JWKS 오프라인)
        │                                      │◄── GET /certs (TTL 캐시) ─│
        │                                      │                          │
        │◄──────────────────────────────────────────── 응답 ──────────────│
```

### Keycloak 클라이언트 설정

```
Realm: browser-agent
Client ID: browser-agent-extension
Client Type: Public (client_secret 없음)
Authentication flows:
  ✅ Standard Flow (Authorization Code)
  ✅ PKCE 필수 (code_challenge_method = S256)
Valid redirect URIs:
  chrome-extension://<EXTENSION_ID>/*
  https://fcmconnection.googleapis.com  # WXT 개발 시 필요할 수 있음
Web Origins: *
```

### Extension: PKCE 인증 구현 (background.ts)

```typescript
// lib/auth.ts — Background Service Worker에서만 실행
const KEYCLOAK_BASE = 'http://localhost:8080/realms/browser-agent';
const CLIENT_ID = 'browser-agent-extension';
const REDIRECT_URI = browser.identity.getRedirectURL(); // chrome-extension://...

// Access token: 메모리 (가장 안전)
let _accessToken: string | null = null;
let _tokenExpiry: number | null = null;

export async function login(): Promise<void> {
  const { verifier, challenge } = await generatePKCE();
  const state = crypto.randomUUID();

  // verifier 임시 보관 (session storage)
  await browser.storage.session.set({ [`pkce_${state}`]: verifier });

  const authUrl = new URL(`${KEYCLOAK_BASE}/protocol/openid-connect/auth`);
  authUrl.searchParams.set('response_type', 'code');
  authUrl.searchParams.set('client_id', CLIENT_ID);
  authUrl.searchParams.set('redirect_uri', REDIRECT_URI);
  authUrl.searchParams.set('scope', 'openid email profile');
  authUrl.searchParams.set('state', state);
  authUrl.searchParams.set('code_challenge', challenge);
  authUrl.searchParams.set('code_challenge_method', 'S256');

  // Chrome Identity API로 Keycloak 로그인 팝업
  const redirected = await browser.identity.launchWebAuthFlow({
    url: authUrl.toString(),
    interactive: true,
  });

  // 인가 코드 추출 + 토큰 교환
  const params = new URL(redirected).searchParams;
  const code = params.get('code')!;
  const returnedState = params.get('state')!;

  const stored = await browser.storage.session.get(`pkce_${returnedState}`);
  const codeVerifier = stored[`pkce_${returnedState}`] as string;
  await browser.storage.session.remove(`pkce_${returnedState}`);

  await exchangeToken(code, codeVerifier);
}

async function exchangeToken(code: string, verifier: string): Promise<void> {
  const res = await fetch(`${KEYCLOAK_BASE}/protocol/openid-connect/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      grant_type: 'authorization_code',
      client_id: CLIENT_ID,
      code,
      redirect_uri: REDIRECT_URI,
      code_verifier: verifier,
    }),
  });
  const { access_token, refresh_token, expires_in } = await res.json();

  // 메모리에 access token 보관
  _accessToken = access_token;
  _tokenExpiry = Date.now() + expires_in * 1000;

  // refresh token은 session storage (브라우저 재시작 시 삭제)
  await browser.storage.session.set({ refresh_token });

  // 만료 5분 전 자동 갱신 예약
  scheduleTokenRefresh(expires_in);
}

export async function getAccessToken(): Promise<string> {
  if (_accessToken && _tokenExpiry && Date.now() < _tokenExpiry - 60_000) {
    return _accessToken;
  }
  // 만료 임박 → 갱신
  await refreshToken();
  return _accessToken!;
}
```

### Gateway: JWT 검증 미들웨어

```python
# services/shared/auth/jwt_verifier.py
from cachetools import TTLCache
from jose import jwt, JWTError
import httpx

class KeycloakJWTVerifier:
    """
    Keycloak JWKS를 사용한 오프라인 JWT 검증.
    - 서명 검증: JWKS 공개키 (RS256)
    - exp / iss / aud 자동 검증
    - JWKS 60분 캐시 (외부 요청 최소화)
    """
    def __init__(self, realm_url: str, audience: str):
        self.jwks_uri = f"{realm_url}/protocol/openid-connect/certs"
        self.issuer = realm_url
        self.audience = audience
        self._cache: TTLCache = TTLCache(maxsize=1, ttl=3600)

    async def _get_jwks(self) -> dict:
        if "keys" in self._cache:
            return self._cache["keys"]
        async with httpx.AsyncClient() as client:
            resp = await client.get(self.jwks_uri)
            resp.raise_for_status()
            keys = resp.json()
            self._cache["keys"] = keys
            return keys

    async def verify(self, token: str) -> dict:
        jwks = await self._get_jwks()
        try:
            return jwt.decode(
                token,
                jwks,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer,
            )
        except JWTError as e:
            raise ValueError(f"Invalid token: {e}") from e


# services/shared/auth/dependencies.py
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer()

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    verifier: KeycloakJWTVerifier = Depends(get_verifier),  # lifespan 싱글톤
) -> dict:
    try:
        return await verifier.verify(credentials.credentials)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

# Gateway 엔드포인트에서 사용
@router.post("/sessions")
async def create_session(user: dict = Depends(get_current_user)) -> SessionResponse:
    user_id = user["sub"]  # Keycloak user UUID
    ...
```

---

## 8. Redis 키 네임스페이스

```
# Pub/Sub 채널 (휘발성)
browser_cmd:{session_id}          → 브라우저 명령 (Gateway → Extension)
browser_result:{command_id}       → 실행 결과 (Extension → MCP Server)

# Hash (세션 상태, TTL: 24h)
session:{session_id}
  field: status                   → "active" | "idle" | "terminated"
  field: created_at
  field: last_activity

# String (단기 캐시)
browser_result_cache:{command_id} → TTL: 5s, 결과 재조회용

# List (명령 이력)
cmd_history:{session_id}          → RPUSH, LTRIM 100개 유지
```

---

## 8. Docker Compose 설정

### infra/docker-compose.yml (인프라만)

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    ports: ["5432:5432"]
    environment:
      POSTGRES_PASSWORD: password
      POSTGRES_DB: browser_agent
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./postgres/init.sql:/docker-entrypoint-initdb.d/init.sql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 5s

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
    command: redis-server --save "" --appendonly no
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s

  minio:
    image: minio/minio
    ports: ["9000:9000", "9001:9001"]
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin
    command: server /data --console-address ":9001"
    volumes:
      - minio_data:/data

  keycloak:
    image: quay.io/keycloak/keycloak:26
    ports: ["8080:8080"]
    environment:
      KC_BOOTSTRAP_ADMIN_USERNAME: admin
      KC_BOOTSTRAP_ADMIN_PASSWORD: admin
      KC_DB: postgres
      KC_DB_URL: jdbc:postgresql://postgres:5432/keycloak
      KC_DB_USERNAME: postgres
      KC_DB_PASSWORD: password
      KC_HOSTNAME: localhost
      KC_HTTP_ENABLED: "true"
    command: start-dev
    depends_on:
      postgres: { condition: service_healthy }
    # 초기 Realm/Client 설정은 realm-export.json으로 import 가능:
    # volumes:
    #   - ./keycloak/realm-export.json:/opt/keycloak/data/import/realm.json
    # command: start-dev --import-realm

volumes:
  postgres_data:
  minio_data:
```

> **Keycloak DB 초기화**: `init.sql`에 `CREATE DATABASE keycloak;` 추가 필요.
> **개발 편의**: Keycloak Admin Console → `http://localhost:8080` (admin/admin)
> - Realm `browser-agent` 생성
> - Client `browser-agent-extension` (Public, PKCE 강제)
> - Valid Redirect URI: `http://localhost/*` (개발용)

### infra/docker-compose.services.yml (서비스 포함)

```yaml
include:
  - docker-compose.yml

services:
  gateway:
    build: ../services/gateway
    ports: ["8000:8000"]
    environment:
      REDIS_URL: redis://redis:6379
      DATABASE_URL: postgresql+asyncpg://postgres:password@postgres:5432/browser_agent
      ORCHESTRATOR_URL: http://orchestrator:8001
      # Keycloak JWT 검증 설정
      KEYCLOAK_REALM_URL: http://keycloak:8080/realms/browser-agent
      KEYCLOAK_AUDIENCE: browser-agent-extension
    depends_on:
      redis: { condition: service_healthy }
      keycloak: { condition: service_started }

  orchestrator:
    build: ../services/orchestrator
    ports: ["8001:8001"]
    environment:
      OLLAMA_BASE_URL: http://host.docker.internal:11434
      ORCHESTRATOR_MODEL: llama3.1:8b
      CHAT_AGENT_URL: http://chat-agent:8002
      BROWSER_AGENT_URL: http://browser-agent:8003
    extra_hosts:
      - "host.docker.internal:host-gateway"

  chat-agent:
    build: ../services/chat_agent
    ports: ["8002:8002"]
    environment:
      OLLAMA_BASE_URL: http://host.docker.internal:11434
      CHAT_MODEL: qwen2.5:7b
    extra_hosts:
      - "host.docker.internal:host-gateway"

  browser-agent:
    build: ../services/browser_agent
    ports: ["8003:8003"]
    environment:
      OLLAMA_BASE_URL: http://host.docker.internal:11434
      BROWSER_MODEL: qwen2.5:14b
      BROWSER_RELAY_MCP_URL: http://browser-relay:8010
    extra_hosts:
      - "host.docker.internal:host-gateway"

  browser-relay:
    build: ../mcp_servers/browser_relay
    ports: ["8010:8010"]
    environment:
      REDIS_URL: redis://redis:6379
    depends_on:
      redis: { condition: service_healthy }
```

---

## 9. Extension 핵심 구현 패턴

### Background Service Worker: SSE Command 채널 (fetch 기반)

```typescript
// Service Worker는 EventSource 미지원 → fetch + ReadableStream 사용
async function connectCommandSSE(sessionId: string): Promise<void> {
  const response = await fetch(`${GATEWAY_URL}/api/v1/sessions/${sessionId}/commands`, {
    headers: { Authorization: `Bearer ${token}` },
    signal: controller.signal,
  });

  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';

    let eventType = '';
    let dataLine = '';
    for (const line of lines) {
      if (line.startsWith('event: ')) eventType = line.slice(7).trim();
      else if (line.startsWith('data: ')) dataLine = line.slice(6);
      else if (line === '' && eventType === 'browser_command') {
        const cmd = JSON.parse(dataLine);
        executeCommandInContentScript(cmd); // 비동기, 블로킹 없이
        eventType = '';
        dataLine = '';
      }
    }
  }
}
```

### Content Script: DOM 액션 실행기

```typescript
// 지원 액션: navigate, click, type, scroll, extract_content, evaluate_js
browser.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type !== 'EXECUTE_BROWSER_ACTION') return false;
  executeDOMAction(message.payload.action, message.payload.params)
    .then(result => sendResponse({ success: true, ...result }))
    .catch(err => sendResponse({ success: false, error: String(err) }));
  return true; // 비동기 응답
});
```

### Sidepanel Chat UI: SSE 스트리밍

```typescript
// Chat 응답: POST /chat → SSE /stream
async function sendChatMessage(content: string): Promise<void> {
  await fetch(`${GATEWAY_URL}/api/v1/sessions/${sessionId}/chat`, {
    method: 'POST',
    body: JSON.stringify({ content }),
  });
  // 응답은 GET /stream SSE 채널로 수신
}
```

---

## 10. LangGraph 에이전트 패턴

### Orchestrator Supervisor Graph

```python
# 의도 분류 → 라우팅
START → classify_intent
    ├── "chat"    → call_chat_agent    → END
    └── "browser" → call_browser_agent → END

# classify_intent: llama3.1:8b 사용, "chat" 또는 "browser" 반환
# call_*_agent: ACP HTTP POST로 서브 에이전트 호출, 결과 반환
```

### Browser/Chat Agent ReAct Graph

```python
# 표준 ReAct 패턴
START → agent (LLM + tools)
    ├── tool_calls → tools (ToolNode) → agent
    └── no tool_calls → END

# PostgreSQL Checkpointer로 thread_id 기반 멀티턴 유지
# config = {"configurable": {"thread_id": f"{session_id}-{agent_type}"}}
```

### Ollama LLM 팩토리

```python
from langchain_ollama import ChatOllama

def create_ollama_llm(model: str, base_url: str) -> ChatOllama:
    return ChatOllama(
        model=model,
        base_url=base_url,  # Docker: "http://host.docker.internal:11434"
        temperature=0.0,
        num_ctx=8192,
    )
```

---

## 11. 설계 의사결정 로그

| 결정 | 선택 | 이유 |
|------|------|------|
| WebSocket 대신 SSE | SSE + POST | 단방향 SSE + HTTP POST로 양방향 구현, 스케일아웃 용이 |
| Future 관리 | Redis Pub/Sub | 멀티 인스턴스 지원, asyncio.Future는 단일 프로세스만 |
| Ollama 접근 | host.docker.internal | Docker에서 Host Ollama 표준 접근 방법 |
| Browser Relay | HTTP MCP (FastMCP) | Browser Agent가 표준 MCP 인터페이스로 호출 |
| Web Search | stdio MCP | 서버 불필요, Chat Agent 내 subprocess로 실행 |
| 에이전트 통신 | ACP (HTTP POST) | REST 표준, 스트리밍은 /runs/stream |
| LangGraph Checkpointer | PostgreSQL | 멀티턴 대화 영속화, 공식 지원 |
| Object Storage SDK | aioboto3 | S3 완전 호환, MinIO/AWS 투명 교체 |
| JWT 발급 | Keycloak (전담) | 표준 OAuth2/OIDC, PKCE 지원, 관리 UI |
| JWT 검증 | JWKS 오프라인 (python-jose) | 매 요청마다 Keycloak 호출 불필요, 성능 |
| JWKS 캐시 | cachetools TTLCache 60분 | 키 교체 주기 고려한 균형 |
| 브라우저 확장 인증 | PKCE (browser.identity) | Public Client — client_secret 보관 불가 |

---

## 12. 개발 환경 Init 명령어

```bash
# 인프라 시작 (Keycloak 포함)
cd infra && docker compose up -d

# Keycloak 초기 설정 (최초 1회)
# Admin Console: http://localhost:8080 (admin/admin)
# 1. Realm "browser-agent" 생성
# 2. Client "browser-agent-extension" 생성
#    - Client authentication: OFF (Public)
#    - Valid redirect URIs: http://localhost/*, chrome-extension://*
#    - PKCE 강제: Advanced > Proof Key for Code Exchange - S256 required

# Extension
cd extension
pnpm install
pnpm dev  # Chrome 개발 서버

# Python 서비스 (각 서비스별)
cd services/gateway
uv sync
uv run uvicorn src.gateway.main:app --reload --port 8000

# MCP 서버
cd mcp_servers/browser_relay
uv sync
uv run python src/browser_relay/main.py

# Ollama 모델 준비
ollama pull llama3.1:8b
ollama pull qwen2.5:7b
ollama pull qwen2.5:14b
```

---

## 13. 구현 우선순위

1. **Phase 1: 기반 인프라**
   - Docker Compose (Redis, PostgreSQL, MinIO, **Keycloak**)
   - Keycloak Realm/Client 초기 설정
   - Extension WXT 프로젝트 init (React 19, Tailwind v4, shadcn)
   - shared Python 패키지 구조 (auth 모듈 포함)

2. **Phase 2: 채팅 기능**
   - Gateway ↔ Orchestrator ↔ Chat Agent 연결
   - Extension Sidepanel Chat UI + SSE 스트리밍
   - Ollama 통합 검증

3. **Phase 3: 브라우저 제어**
   - Browser Relay MCP 서버 + Redis Pub/Sub
   - Extension Command SSE 채널 + Content Script DOM 실행기
   - Browser Agent LangGraph + MCP 도구

4. **Phase 4: 통합 및 개선**
   - Orchestrator 의도 분류 정확도 튜닝
   - 에러 처리, 재연결 로직
   - UI 개선 (도구 호출 상태 표시)
