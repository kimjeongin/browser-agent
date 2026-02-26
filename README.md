# Browser Agent

WXT 브라우저 확장 + 멀티 에이전트 AI 백엔드로 구성된 AI 챗봇 어시스턴트. 사용자 입력을 채팅 응답 또는 실제 브라우저 DOM 제어로 처리한다.

---

## 아키텍처 개요

```
Extension (WXT/Chrome) ──Bearer JWT──▶ Gateway :8000
                       ◀──SSE chat stream──
                       ◀──SSE browser commands──

Gateway :8000 ──ACP──▶ Orchestrator :8001
                            ├──ACP──▶ Chat Agent :8002
                            └──ACP──▶ Browser Agent :8003

Browser Agent :8003 ──POST /browser-tools/invoke──▶ Gateway :8000
Gateway :8000 ──asyncio.Queue──▶ SSE /commands──▶ Extension
Extension ──POST /browser-tools/result/{inv_id}──▶ Gateway :8000
```

### webMCP-inspired 브라우저 제어 흐름 (3-hop)

1. Browser Agent가 `POST /sessions/{id}/browser-tools/invoke` 호출 (blocking, 60s)
2. Gateway가 `asyncio.Queue`에 tool invocation 이벤트 추가 → SSE로 Extension push
3. Extension이 DOM 액션 실행 후 `POST /sessions/{id}/browser-tools/result/{inv_id}` 호출
4. Gateway `asyncio.Future.set_result()` → Browser Agent 응답 반환

### 컴포넌트 역할

| 컴포넌트 | 포트 | 역할 | 기술 스택 |
|----------|------|------|-----------|
| Extension | — | 사용자 UI, DOM 제어 | WXT, React 19, Tailwind v4, Zustand |
| Gateway | 8000 | 진입점, SSE 허브, JWT 검증, 브라우저 도구 브로커 | FastAPI, Redis |
| Orchestrator | 8001 | 의도 분류, 에이전트 라우팅 | FastAPI, LangGraph |
| Chat Agent | 8002 | 웹 검색, 일반 대화 | FastAPI, LangGraph, DuckDuckGo |
| Browser Agent | 8003 | DOM 제어 | FastAPI, LangGraph, httpx |
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
ollama pull llama3.1:8b      # Orchestrator (의도 분류)
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
| `BROWSER_TOOL_TIMEOUT` | Gateway, Browser Agent | `60.0` / `65.0` |

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

### 테스트 실행

```bash
# Gateway 테스트
cd services/gateway && uv run pytest

# Browser Agent 테스트
cd services/browser_agent && uv run pytest

# Extension 테스트
cd extension && pnpm test
```

---

## 프로젝트 구조

```
browser-agent/
├── extension/                  # WXT 브라우저 확장
│   ├── entrypoints/
│   │   ├── background.ts       # PKCE 인증, 명령 SSE 수신, 탭 그룹 관리
│   │   ├── content.ts          # DOM 액션 실행
│   │   └── sidepanel/          # 채팅 UI (React) + 브라우저 제어 상태 표시
│   ├── lib/                    # API 클라이언트, 인증 유틸
│   └── stores/                 # Zustand 상태 저장소
├── services/
│   ├── shared/                 # 공유 패키지 (auth, acp, llm, models)
│   ├── gateway/                # 진입점 서비스 + 브라우저 도구 브로커
│   ├── ochestrator/            # 슈퍼바이저 에이전트
│   ├── chat_agent/             # 웹 검색 에이전트
│   └── browser_agent/          # 브라우저 제어 에이전트
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
| SSE (WebSocket 아님) | Chrome Extension Service Worker에서 `EventSource` 미지원. `fetch` + `ReadableStream` 사용 |
| asyncio.Queue + asyncio.Future | 브라우저 도구 조율을 단일 Gateway 인스턴스 내에서 처리. Redis Pub/Sub보다 단순하고 오버헤드 없음 |
| Gateway 직접 HTTP (MCP 서버 없음) | Browser Agent → Gateway 직접 호출로 중간 계층 제거. 3홉으로 충분 |
| ACP 프로토콜 (HTTP POST) | 에이전트 간 표준 인터페이스. 각 서비스가 독립적으로 배포/스케일 가능 |
| session_id = thread_id | Browser Agent가 session 단위로 LangGraph 체크포인트를 유지해 멀티턴 대화 지원 |
| Keycloak PKCE | Extension은 `client_secret` 안전 보관 불가 → Public client + PKCE S256 강제 |
| Access Token 메모리 저장 | `localStorage`/`sessionStorage`는 XSS 취약. Service Worker 메모리만 사용 |
| Chrome Tab Groups API | AI 제어 탭을 "AI Assistant" 그룹으로 격리해 사용자가 시각적으로 구분 가능 |
| psycopg (LangGraph 체크포인터) | `langgraph-checkpoint-postgres`가 asyncpg가 아닌 psycopg를 요구 |
