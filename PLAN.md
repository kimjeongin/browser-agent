# Browser Agent - 개발 계획서

> 최종 수정: 2026-02-26 (webMCP-inspired 브라우저 제어 아키텍처로 전면 재설계)
> 상태: 구현 완료

---

## 1. 시스템 개요

브라우저 확장(WXT)에서 동작하는 멀티 에이전트 챗봇 시스템.
사용자 입력을 받아 일반 대화 응답 또는 실제 브라우저 DOM 제어를 수행한다.

### 기술 원칙
- 모든 통신: **SSE 기반** (WebSocket 없음)
- LLM: **로컬 Ollama** (`localhost:11434`)
- 에이전트 간 통신: **ACP 프로토콜** (HTTP POST 기반)
- 에이전트 구현: **LangGraph v1 + LangChain v1**
- 브라우저 도구 추상화: **webMCP-inspired** (Extension이 도구 제공자)
- 인증/인가: **Keycloak** (토큰 발급) + Gateway JWT 검증 (JWKS 오프라인)

---

## 2. 전체 아키텍처 (webMCP-inspired 재설계)

```
                   ┌─────────────────────────┐
                   │   Keycloak (:8080)       │  ← 토큰 발급 전담
                   └──────────┬──────────────┘
                              │ PKCE 로그인 (browser.identity)
┌─────────────────────────────▼───────────────────────────────────────┐
│                    Browser Extension (WXT + React 19)               │
│  ┌─────────────────┐  ┌─────────────────────────────────────────┐  │
│  │  Sidepanel UI   │  │          Background Service Worker       │  │
│  │  (Chat 인터페이스)│  │  ┌─────────────────────────────────┐   │  │
│  └────────┬────────┘  │  │  BrowserToolRegistry (webMCP)   │   │  │
│           │ msg       │  │  navigate / click / type /       │   │  │
│           │           │  │  scroll / screenshot / extract   │   │  │
│           │           │  │  wait_for / evaluate_js /        │   │  │
│           │           │  │  get_page_info                   │   │  │
│           │           │  └─────────────┬───────────────────┘   │  │
│           │           └────────────────┼────────────────────────┘  │
│           │                            │ register manifest          │
│           │      SSE GET /commands     │ POST tool result           │
└───────────┼────────────────────────────┼───────────────────────────┘
            │ Bearer JWT                 │
┌───────────▼────────────────────────────▼────────────────────────────┐
│                        Gateway (:8000) - FastAPI                     │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  Browser Tool Broker (webMCP Bridge)                        │    │
│  │  POST /sessions/{id}/browser-tools/register  ← Extension    │    │
│  │  GET  /sessions/{id}/browser-tools           ← Browser Agent│    │
│  │  POST /sessions/{id}/browser-tools/invoke    ← Browser Agent│    │
│  │  POST /sessions/{id}/browser-tools/result/{inv_id} ← Ext.  │    │
│  │                                                             │    │
│  │  Coordination: asyncio.Queue (SSE delivery) +               │    │
│  │                asyncio.Future (result waiting)              │    │
│  └─────────────────────────────────────────────────────────────┘    │
│  /sessions/{id}/commands  → 도구 호출 SSE (Extension SW 수신)        │
│  /sessions/{id}/chat      → 채팅 SSE 스트리밍                        │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ ACP (HTTP POST)
           ┌───────────────────▼─────────────────────┐
           │         Orchestrator (:8001)             │
           │   의도 분류 (ChatOllama) → 라우팅          │
           └──────────┬──────────────────┬────────────┘
                      │ ACP              │ ACP
          ┌───────────▼────┐   ┌────────▼──────────────────┐
          │ Chat Agent     │   │ Browser Agent (:8003)      │
          │  (:8002)       │   │ GET /sessions/{id}/browser-tools
          │ ChatOllama     │   │ POST /sessions/{id}/browser-tools/invoke
          │ web_search MCP │   │ (HTTP → Gateway → SSE → Extension)
          └────────────────┘   └────────────────────────────┘
```

### 구버전 대비 단순화

| 항목 | 구버전 | 신버전 |
|------|--------|--------|
| 브라우저 도구 서비스 | Browser Relay MCP (:8010) 별도 프로세스 | 제거 (Gateway가 도구 브로커 역할) |
| 브라우저 명령 중계 | Redis Pub/Sub (browser_cmd, browser_result) | asyncio.Queue + asyncio.Future |
| 브라우저 도구 프로토콜 | MCP over streamable-HTTP | webMCP-inspired HTTP REST |
| 도구 제공자 | MCP Server (별도 프로세스) | Extension BrowserToolRegistry |
| 서비스 수 | 5개 (gateway + orchestrator + chat + browser + relay) | 4개 (relay 제거) |
| 홉 수 (도구 호출) | 7홉 (Agent→MCP→Redis→Gateway→Ext→Result→Redis→MCP→Agent) | 3홉 (Agent→Gateway→Ext→Result→Agent) |

---

## 3. webMCP-inspired 브라우저 제어 플로우

```
Browser Agent가 navigate 도구 호출
    │
    │  POST /sessions/{id}/browser-tools/invoke
    │  {"tool": "navigate", "params": {"url": "https://..."}}
    ▼
Gateway
    │  1. invocation_id 생성
    │  2. asyncio.Future 생성, pending_invocations에 저장
    │  3. asyncio.Queue에 tool_invocation 이벤트 enqueue
    ▼
Extension Background SW (SSE 대기 중)
    │  SSE event: tool_invocation
    │  {"invocation_id": "...", "tool": "navigate", "params": {...}}
    │
    │  BrowserToolRegistry.invoke("navigate", params, tabId)
    │    → chrome.tabs.sendMessage(tabId, EXECUTE_BROWSER_COMMAND)
    ▼
Extension Content Script
    │  DOM 액션 실행 → 결과 반환
    ▼
Extension Background SW
    │  POST /sessions/{id}/browser-tools/result/{invocation_id}
    │  {"success": true, "result": {...}}
    ▼
Gateway
    │  asyncio.Future.set_result(result)
    ▼
Browser Agent (await가 풀림)
    │  도구 응답 수신 → LangGraph 계속 실행
    ▼
결과 반환
```

---

## 4. webMCP 도구 등록 플로우

Extension이 세션 생성 직후 도구 매니페스트를 등록한다 (webMCP의 `navigator.modelContext.registerTool()`에 해당).

```
Extension login() 완료
    │
    │  POST /sessions/{id}/browser-tools/register
    │  {"tools": [
    │    {"name": "navigate", "description": "...", "inputSchema": {JSON Schema}},
    │    {"name": "click", "description": "...", "inputSchema": {...}},
    │    ... 9개 도구
    │  ]}
    ▼
Gateway
    │  browser_tool_manifests[session_id] = tools
    ▼
Browser Agent (나중에 호출 시)
    │  GET /sessions/{id}/browser-tools
    │  → 등록된 도구 목록 반환 (JSON Schema 포함)
```

---

## 5. 프로젝트 구조

```
browser-agent/
├── PLAN.md
│
├── extension/                          # WXT Browser Extension
│   ├── entrypoints/
│   │   ├── background.ts              # Service Worker: 인증, SSE, 도구 등록·실행
│   │   ├── content.ts                 # DOM 액션 실행기
│   │   └── sidepanel/                 # Chat UI
│   ├── lib/
│   │   ├── api.ts                     # GatewayClient HTTP 클라이언트
│   │   ├── browser-tools.ts           # BrowserToolRegistry (webMCP-style)
│   │   ├── auth.ts                    # PKCE 유틸리티
│   │   ├── config.ts                  # 설정
│   │   └── messaging.ts               # SW ↔ UI 메시지 계약
│   └── stores/
│       └── chat.ts                    # Zustand 채팅 상태
│
├── services/
│   ├── shared/                        # 공통 Python 패키지
│   │   └── src/shared/
│   │       ├── auth/                  # JWT 검증 (Keycloak JWKS)
│   │       ├── acp/                   # ACP 클라이언트/서버
│   │       ├── llm/                   # Ollama LLM 팩토리
│   │       └── models/                # 도메인 모델
│   │
│   ├── gateway/                       # API Gateway (:8000)
│   │   └── main.py                   # webMCP 브라우저 도구 브로커 포함
│   │
│   ├── ochestrator/                   # Orchestrator (:8001)
│   ├── chat_agent/                    # Chat Agent (:8002)
│   └── browser_agent/                 # Browser Agent (:8003)
│       └── main.py                   # HTTP-based 브라우저 도구 클라이언트
│
├── mcp_servers/
│   └── web_search/                    # Web Search MCP (stdio, Chat Agent용)
│                                      # browser_relay 제거됨
│
└── infra/
    ├── docker-compose.yml             # PostgreSQL, Redis, MinIO, Keycloak
    └── docker-compose.services.yml    # 서비스 (browser-relay 제거)
```

---

## 6. 서비스 포트 맵

| 서비스 | 포트 | 역할 | 변경 |
|--------|------|------|------|
| Gateway | 8000 | 진입점, SSE 허브, webMCP 도구 브로커, JWT 검증 | 도구 브로커 기능 추가 |
| Orchestrator | 8001 | 의도 분류, 에이전트 조율 | 변경 없음 |
| Chat Agent | 8002 | Q&A, 일반 대화 | 변경 없음 |
| Browser Agent | 8003 | DOM 제어 (HTTP → Gateway → Extension) | MCP → HTTP 방식으로 변경 |
| ~~Browser Relay MCP~~ | ~~8010~~ | ~~브라우저 명령 중계~~ | **제거됨** |
| Keycloak | 8080 | JWT 발급, PKCE, Admin UI | 변경 없음 |
| PostgreSQL | 5432 | DB | 변경 없음 |
| Redis | 6379 | 세션 상태 (browser_cmd Pub/Sub 제거) | Pub/Sub 채널 제거 |
| MinIO | 9000/9001 | 오브젝트 스토리지 | 변경 없음 |

---

## 7. Gateway 엔드포인트

```
# 인증 필요
POST   /sessions                              # 세션 생성
GET    /sessions/{id}                         # 세션 조회
DELETE /sessions/{id}                         # 세션 종료
POST   /sessions/{id}/chat                    # 채팅 (동기)
GET    /sessions/{id}/chat/stream             # 채팅 SSE 스트리밍

# 인증 불필요 (Extension/Agent 내부 호출)
GET    /sessions/{id}/commands                # Extension SSE 채널 (도구 호출 수신)
POST   /sessions/{id}/browser-tools/register # Extension: 도구 매니페스트 등록
GET    /sessions/{id}/browser-tools           # Browser Agent: 사용 가능 도구 조회
POST   /sessions/{id}/browser-tools/invoke   # Browser Agent: 도구 호출
POST   /sessions/{id}/browser-tools/result/{inv_id}  # Extension: 도구 결과 반환
```

---

## 8. Browser Agent 도구 목록

도구는 Browser Agent에 정적으로 정의되며, Gateway를 통해 Extension에서 실행된다.

| 도구 | 파라미터 | 설명 |
|------|----------|------|
| `navigate` | `session_id, url` | URL로 이동 |
| `click` | `session_id, selector, description?` | CSS 셀렉터로 클릭 |
| `type_text` | `session_id, selector, text, clear?` | 텍스트 입력 |
| `scroll` | `session_id, direction, amount` | 페이지 스크롤 |
| `take_screenshot` | `session_id` | 스크린샷 반환 |
| `extract_content` | `session_id, selector?` | 텍스트 추출 |
| `wait_for_element` | `session_id, selector, timeout_ms?` | 엘리먼트 대기 |
| `evaluate_js` | `session_id, script` | JavaScript 실행 |
| `get_page_info` | `session_id` | 현재 URL, 제목 반환 |

---

## 9. Redis 키 네임스페이스

```
# 세션 상태 (Hash, TTL: 24h)
session:{session_id}    → status, created_at, last_activity

# 제거됨 (구버전)
# browser_cmd:{session_id}     → Pub/Sub (asyncio.Queue로 대체)
# browser_result:{command_id}  → Pub/Sub (asyncio.Future로 대체)
```

---

## 10. 설계 의사결정 로그

| 결정 | 선택 | 이유 |
|------|------|------|
| Browser Relay MCP 제거 | Gateway가 도구 브로커 역할 | 별도 프로세스 불필요, 홉 수 7→3 감소 |
| Redis Pub/Sub → asyncio | asyncio.Queue + Future | 단일 Gateway 인스턴스, 코드 단순화 |
| webMCP 표준 준수 | JSON Schema tool definition | 향후 native webMCP 전환 용이 (Chrome 146+) |
| 도구 등록 시점 | 세션 생성 직후 | Extension이 활성화된 시점에 즉시 등록 |
| 도구 제공자 | Extension BrowserToolRegistry | 브라우저 컨텍스트에서 실행, 권한 필요 없음 |
| 도구 정의 방식 | 정적 (Extension에 하드코딩) | 표준 9개 도구 고정, 동적 발견 오버헤드 불필요 |
| 스케일아웃 고려 | asyncio 단일 인스턴스 가정 | 다중 인스턴스 필요 시 Redis Queue/Future로 교체 |
| WebSocket 대신 SSE | SSE + POST | 단방향 SSE + HTTP POST로 양방향, 스케일아웃 용이 |
| LangGraph Checkpointer | PostgreSQL | 멀티턴 대화 영속화, 공식 지원 |
| JWT 검증 | JWKS 오프라인 (python-jose) | 매 요청마다 Keycloak 호출 불필요 |

---

## 11. 개발 환경 실행 명령어

```bash
# 인프라 시작
cd infra && docker compose up -d

# 서비스 전체 빌드 및 시작
docker compose -f infra/docker-compose.services.yml up --build

# Extension 개발 서버
cd extension && pnpm install && pnpm dev

# 개별 Python 서비스 (로컬 개발)
cd services/gateway && uv sync && uv run uvicorn main:app --reload --port 8000

# Ollama 모델 준비
ollama pull llama3.1:8b
ollama pull qwen2.5:7b
ollama pull qwen2.5:14b
```

---

## 12. 구현 현황

- [x] Phase 1: 인프라 (Docker Compose, Keycloak, PostgreSQL, Redis, MinIO)
- [x] Phase 2: 채팅 기능 (Gateway ↔ Orchestrator ↔ Chat Agent ↔ Extension)
- [x] Phase 3: 브라우저 제어 (webMCP-inspired, Browser Agent ↔ Gateway ↔ Extension)
- [x] Phase 4: README 문서화
- [x] Phase 5: TDD 개발 워크플로우 구축 (tdd-workflow skill, tdd-test-writer/implementer agents)
