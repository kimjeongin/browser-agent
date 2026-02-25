# Browser Agent

WXT 브라우저 확장 + 멀티 에이전트 AI 백엔드로 구성된 AI 챗봇 어시스턴트. 사용자 입력을 채팅 응답 또는 실제 브라우저 DOM 제어로 처리한다.

---

## 아키텍처 개요

webMCP-inspired 아키텍처. Extension이 브라우저 도구 제공자 역할을 하고, Gateway가 도구 브로커 역할을 한다.

```
Extension (WXT/Chrome) ──Bearer JWT──▶ Gateway :8000
                       ◀──SSE chat──
                       ◀──SSE tool_invocation──
                       ──POST tool result──▶

Gateway :8000 ──ACP──▶ Orchestrator :8001
                            ├──ACP──▶ Chat Agent :8002
                            └──ACP──▶ Browser Agent :8003

Browser Agent :8003 ──POST /browser-tools/invoke──▶ Gateway :8000
                                                         │ asyncio.Queue (SSE push)
                                                         ▼
                                                   Extension BG SW
                                                   (BrowserToolRegistry.invoke)
                                                         │ DOM 액션
                                                         ▼
                                                   Extension Content Script
                                                         │
                                                   POST /browser-tools/result
                                                         ▼
                                                   Gateway (asyncio.Future resolved)
                                                         │
                                                   Browser Agent (결과 수신)
```

**구 아키텍처 대비 단순화 (7홉 → 3홉)**

| 항목 | 구버전 | 신버전 |
|------|--------|--------|
| 브라우저 도구 서비스 | Browser Relay MCP (:8010) 별도 프로세스 | 제거 (Gateway가 도구 브로커) |
| 브라우저 명령 중계 | Redis Pub/Sub | asyncio.Queue + asyncio.Future |
| 브라우저 도구 프로토콜 | MCP over streamable-HTTP | webMCP-inspired HTTP REST |

### 컴포넌트 역할

| 컴포넌트 | 포트 | 역할 | 기술 스택 |
|----------|------|------|-----------|
| Extension | — | 사용자 UI, BrowserToolRegistry, DOM 제어 | WXT, React 19, Tailwind v4, Zustand |
| Gateway | 8000 | 진입점, SSE 허브, webMCP 도구 브로커, JWT 검증 | FastAPI |
| Orchestrator | 8001 | 의도 분류, 에이전트 라우팅 | FastAPI, LangGraph |
| Chat Agent | 8002 | 웹 검색, 일반 대화 | FastAPI, LangGraph, DuckDuckGo |
| Browser Agent | 8003 | DOM 제어 (HTTP → Gateway → Extension) | FastAPI, LangGraph |
| Keycloak | 8080 | JWT 발급, PKCE | Keycloak 26 |
| PostgreSQL | 5432 | DB, LangGraph 체크포인트 | pgvector/pgvector:pg16 |
| Redis | 6379 | 세션 캐시 | Redis 7 |
| MinIO | 9000/9001 | 오브젝트 스토리지 | MinIO |

---

## 사전 요구사항

- Docker & Docker Compose v2
- Node.js 20+ + pnpm
- Python 3.13+
- [Ollama](https://ollama.ai)
- Chrome 브라우저 (Manifest V3)

Ollama 모델을 미리 pull한다:

```bash
ollama pull llama3.1:8b      # Orchestrator
ollama pull qwen2.5:7b       # Chat Agent
ollama pull qwen2.5:14b      # Browser Agent
```

---

## 빠른 시작

```bash
# 1. 저장소 클론
git clone <repo-url>
cd browser-agent

# 2. 인프라 시작 (PostgreSQL, Redis, MinIO, Keycloak)
cd infra
docker compose up -d

# Keycloak이 healthy 상태가 될 때까지 대기 (약 60초)

# 3. 백엔드 서비스 빌드 및 시작
docker compose -f docker-compose.services.yml up --build

# 4. Extension 의존성 설치 및 빌드
cd ../extension
pnpm install
pnpm build

# 5. Chrome에서 Extension 로드
# chrome://extensions → 개발자 모드 활성화 → "압축 해제된 확장 프로그램 로드"
# → extension/.output/chrome-mv3 선택
```

테스트 계정: `testuser` / `password` (Keycloak realm import 시 자동 생성)

---

## 설정

주요 환경변수. 서비스별 상세 설정은 각 서비스 README를 참조한다.

### 백엔드 서비스 (`infra/docker-compose.services.yml`)

| 변수 | 서비스 | 예시 |
|------|--------|------|
| `REDIS_URL` | Gateway | `redis://redis:6379/0` |
| `DATABASE_URL` | Gateway, Orchestrator, Chat, Browser | `postgresql+asyncpg://postgres:password@postgres:5432/browser_agent` |
| `ORCHESTRATOR_URL` | Gateway | `http://orchestrator:8001` |
| `KEYCLOAK_REALM_URL` | Gateway | `http://keycloak:8080/realms/browser-agent` |
| `KEYCLOAK_AUDIENCE` | Gateway | `browser-agent-extension` |
| `OLLAMA_BASE_URL` | Orchestrator, Chat, Browser | `http://host.docker.internal:11434` |
| `ORCHESTRATOR_MODEL` | Orchestrator | `llama3.1:8b` |
| `CHAT_MODEL` | Chat Agent | `qwen2.5:7b` |
| `BROWSER_MODEL` | Browser Agent | `qwen2.5:14b` |
| `GATEWAY_URL` | Browser Agent | `http://gateway:8000` |

### Extension (`extension/.env`)

| 변수 | 기본값 |
|------|--------|
| `WXT_PUBLIC_API_BASE_URL` | `http://localhost:8000` |
| `WXT_PUBLIC_KEYCLOAK_REALM_URL` | `http://localhost:8080/realms/browser-agent` |
| `WXT_PUBLIC_KEYCLOAK_CLIENT_ID` | `browser-agent-extension` |

---

## 개발 가이드

```bash
# Extension 개발 서버 (HMR)
cd extension && pnpm dev

# 개별 서비스 로컬 실행 (예: gateway)
cd services/gateway
uv pip install -e ../shared -e .
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

---

## 프로젝트 구조

```
browser-agent/
├── extension/                  # WXT 브라우저 확장
│   ├── entrypoints/
│   │   ├── background.ts       # PKCE 인증, 도구 등록, 도구 호출 SSE 수신
│   │   ├── content.ts          # DOM 액션 실행
│   │   └── sidepanel/          # 채팅 UI (React)
│   ├── lib/
│   │   ├── browser-tools.ts    # BrowserToolRegistry (webMCP-style, 9개 도구)
│   │   ├── api.ts              # GatewayClient (HTTP/SSE)
│   │   ├── auth.ts             # PKCE 유틸리티
│   │   └── config.ts           # 설정
│   └── stores/                 # Zustand 상태 저장소
├── services/
│   ├── shared/                 # 공유 패키지 (auth, acp, llm, models)
│   ├── gateway/                # 진입점 서비스 + webMCP 도구 브로커
│   ├── ochestrator/            # 슈퍼바이저 에이전트
│   ├── chat_agent/             # 웹 검색 에이전트
│   └── browser_agent/          # 브라우저 제어 에이전트 (HTTP 도구)
├── mcp_servers/
│   └── web_search/             # 웹 검색 MCP 서버 (stdio, Chat Agent용)
└── infra/
    ├── docker-compose.yml           # 인프라 서비스
    ├── docker-compose.services.yml  # 애플리케이션 서비스
    ├── postgres/init.sql            # DB 초기화
    └── keycloak/                    # Realm 설정 (자동 import)
```

---

## 설계 결정

| 결정 | 이유 |
|------|------|
| webMCP-inspired 아키텍처 | Extension이 도구 제공자, Gateway가 브로커. 홉 수 7→3 감소. Browser Relay MCP 별도 프로세스 제거 |
| asyncio.Queue + asyncio.Future | Redis Pub/Sub 대체. 단일 Gateway 인스턴스에서 코드 단순화. 스케일아웃 필요 시 Redis Queue/Future로 교체 가능 |
| SSE (WebSocket 아님) | Chrome Extension Service Worker에서 `EventSource` 미지원. `fetch` + `ReadableStream` 사용 |
| ACP 프로토콜 (HTTP POST) | 에이전트 간 표준 인터페이스. 각 서비스가 독립적으로 배포/스케일 가능 |
| Keycloak PKCE | Extension은 `client_secret` 안전 보관 불가 → Public client + PKCE S256 강제 |
| Access Token 메모리 저장 | `localStorage`/`sessionStorage`는 XSS 취약. Service Worker 메모리만 사용 |
| psycopg (LangGraph 체크포인터) | `langgraph-checkpoint-postgres`가 asyncpg가 아닌 psycopg를 요구 |
