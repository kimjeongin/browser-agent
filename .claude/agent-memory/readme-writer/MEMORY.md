# README Writer Agent Memory

## 프로젝트 README 현황 (2026-03-08 마지막 업데이트)

모든 README 작성 완료. 아래 파일이 최신 상태다:

- `/README.md` — 루트 (아키텍처, 빠른시작, 서비스맵, Observability 섹션, 설계결정)
- `/services/gateway/README.md` — API 전체, InvocationBroker/SessionStore 설명
- `/services/orchestrator/README.md` — 2-phase 스트리밍 설명 포함
- `/services/chat_agent/README.md` — DuckDuckGo 직접 파싱 구현 설명
- `/services/browser_agent/README.md` — Progress Ledger 그래프, 10개 도구 전부
- `/services/shared/README.md` — observability/logging_config 모듈 추가, 모듈별 API
- `/extension/README.md` — PKCE 흐름, SSE 구현 방식, Tab Groups
- `/infra/README.md` — Observability 스택 전체 (Phoenix, OTel Collector, Loki, Prometheus, Grafana)

## 핵심 파악 사항

### 모델 현황 (docker-compose.services.yml 기준)
- Orchestrator: `qwen3:8b`
- Chat Agent: `qwen3:8b`
- Browser Agent actor: `qwen2.5vl:7b` (멀티모달, settings.py 기준)
- Browser Agent planner: `qwen3:8b`
- shared/LLMSettings 기본값: orchestrator=`qwen3:8b`, browser_agent=`qwen3:14b`, chat=`qwen3:8b`, vision=`qwen3vl:8b`

> 주의: settings.py와 docker-compose 환경변수가 다를 수 있다. 코드가 진실의 출처.

### Browser Agent 그래프 (Progress Ledger)
단순 ReAct가 아님. `actor → tools → progress_check → actor|replan → actor` 루프.
- `stall_count >= 3`이면 `replan` 노드 실행 후 `actor` 재진입
- `screenshot` 도구: `response_format="content_and_artifact"` — LangGraph가 artifact 저장, `_enrich_screenshot_messages()`로 멀티모달 content 재구성

### Gateway 내부 구조
`main.py`에 전부 있지 않음. `api/` + `core/` 하위 패키지로 분리됨:
- `core/session_store.py`: SessionStore (TTL lazy expiry)
- `core/invocation_broker.py`: InvocationBroker (asyncio.Queue + Future, 120s stale cleanup)
- `api/sessions.py`, `api/chat.py`, `api/browser_tools.py`, `api/deps.py`

### Orchestrator 디렉토리명 오타
`services/orchestrator/` (올바름) — 예전 PLAN.md에 `ochestrator`라는 오타가 있었음. README는 올바른 이름 사용.

### Extension 파일 구조 (2026-03-08 이후 최신)
- `extension/__tests__/`: `background.test.ts`, `content.test.ts` (이전의 `browser-tools.test.ts`는 제거됨)
- `extension/services/command-executor.ts`: 도구 호출 디스패처. navigate/screenshot은 tab-manager 직접 처리, 나머지는 content script로 위임
- `extension/lib/`: api.ts, auth.ts, browser-tool-names.ts, config.ts, messaging.ts, sse-parser.ts, tab-manager.ts, token-manager.ts
- `extension/entrypoints/sidepanel/hooks/`: useAuthState.ts, useBrowserControl.ts
- `extension/entrypoints/sidepanel/screens/`: LoginScreen.tsx

### Observability 스택 구조 (2026-03-08 추가)
- `shared.observability.setup_telemetry(service_name, app)` 한 번 호출로 traces+logs+metrics 모두 초기화
- `shared.logging_config.configure_logging()` — setup_telemetry 내부에서 자동 호출 (개별 서비스 호출 불필요)
- OTel Collector 파이프라인: `infra/otel/otel-collector-config.yaml`
  - traces → Phoenix:6006 (otlphttp), logs → Loki:3100, metrics → Prometheus scrape :8889
- Prometheus는 OTel Collector의 `:8889`를 단일 스크랩. 서비스 직접 스크랩 없음
- Grafana `derivedFields`: Loki 로그의 `trace_id` → Phoenix 트레이스 URL 자동 링크
- Phoenix 인증: `PHOENIX_SECRET` 환경변수 필수 (`PHOENIX_ENABLE_AUTH=true`)
- `docker-compose.yml` (인프라)에 phoenix, otel-collector, loki, prometheus, grafana 모두 포함
- 서비스 컨테이너는 `OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318` 환경변수 수신

### 테스트 수 현황 (2026-03-08 마지막 업데이트)
- Gateway: 19개
- Orchestrator: 31개
- Chat Agent: 14개
- Browser Agent: 62개
- Extension: 56개
- 합계: 182개

## 포맷팅 패턴

- 한국어 산문 + 영어 기술 용어
- 코드블록 언어 식별자 필수 (bash, python, typescript, toml, sql)
- 표: 서비스맵, 환경변수, API 엔드포인트, 도구 목록
- `>` 블록쿼트: 진짜 중요한 경고만 (예: localStorage 금지, format="json" 금지)
