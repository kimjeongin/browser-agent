---
name: readme-conventions
description: README 작성 컨벤션 및 best practice. readme-writer 에이전트가 문서 작성 전 참조합니다.
user-invocable: false
---

# README Writing Conventions

## 핵심 원칙

**기술 문서는 광고가 아니다.** 독자는 개발자다. 사실만 전달한다.

| 금지 | 대신 |
|------|------|
| "powerful", "seamless", "robust", "cutting-edge" | 구체적인 수치, 동작 설명 |
| "handles X efficiently" | "validates JWT via JWKS with 60-min TTL cache" |
| "easy to use", "simple setup" | 실제 커맨드 |
| "and much more..." | 전부 나열하거나 생략 |

---

## 문서 구조

### Root README 섹션 순서

```
1. 프로젝트명 + 한 줄 설명 (무엇인지, 어떤 기능인지 X)
2. 아키텍처 개요 (다이어그램 + 컴포넌트 역할 + 포트)
3. 사전 요구사항 (정확한 버전)
4. 빠른 시작 (제로 상태에서 실행까지 번호 붙인 단계)
5. 서비스 맵 (표: 서비스 | 포트 | 역할 | 기술스택)
6. 설정 (환경변수, 필수 초기 설정)
7. 개발 가이드 (개별 서비스 실행, 테스트)
8. 프로젝트 구조 (디렉토리 트리 + 간단 주석)
9. 설계 결정 (WHY: "SSE 사용 이유 — WebSocket은 ...")
```

### Sub-project README 섹션 순서

```
1. 서비스명 + 역할 (한 문장)
2. 책임 (불릿 리스트)
3. API/인터페이스 (메서드, 경로, 요청/응답 스키마)
4. 의존 서비스 (무엇을, 왜)
5. 환경변수
6. 로컬 실행 커맨드
7. 구현 주의사항 (비자명한 것만)
8. 파일 구조
```

---

## 포맷팅 규칙

### 헤더
- `#` — 문서 제목 (1개만)
- `##` — 최상위 섹션
- `###` — 하위 섹션
- `####` — 테이블 캡션이나 단일 항목 제목에만 사용

### 코드 블록
언어 식별자 필수. 셸 커맨드는 `bash`, 설정 파일은 해당 언어:

````markdown
```bash
docker compose up -d
```

```python
REDIS_URL = "redis://localhost:6379/0"
```
````

### 표
구조화된 데이터에 사용 (서비스 맵, 환경변수 목록, API 엔드포인트):

```markdown
| 변수 | 기본값 | 설명 |
|------|--------|------|
| `REDIS_URL` | `redis://localhost:6379/0` | Redis 연결 URL |
| `JWT_TTL` | `3600` | 토큰 캐시 만료 시간 (초) |
```

### 주의사항 블록
`>` 블록쿼트는 **진짜 중요한 경고**에만 사용:

```markdown
> **주의**: `format="json"` 설정 시 tool calling이 비활성화됩니다.
```

### 다이어그램
ASCII 또는 Mermaid 사용. 외부 이미지 링크 금지:

```
Extension ──SSE──▶ Gateway :8000 ──ACP──▶ Orchestrator :8001
                                               ├──ACP──▶ Chat Agent :8002
                                               └──ACP──▶ Browser Agent :8003
```

---

## 컨텐츠 기준

### 환경변수 문서화

항목마다 필수 4가지:

```markdown
| 변수 | 타입 | 예시 | 설명 |
|------|------|------|------|
| `DATABASE_URL` | string | `postgresql+asyncpg://postgres:pw@localhost/db` | PostgreSQL 연결 DSN |
| `SESSION_TTL` | int | `86400` | 세션 Redis TTL (초), 기본 24시간 |
```

### API 엔드포인트 문서화

```markdown
#### `GET /sessions/{session_id}/commands`

브라우저 명령 SSE 채널. Extension background SW가 연결을 유지한다.

- **인증**: 불필요 (Extension content script 컨텍스트)
- **이벤트**: `data: <BrowserCommand JSON>`
- **킵얼라이브**: `: keepalive` 15초마다

**BrowserCommand 스키마**
| 필드 | 타입 | 설명 |
|------|------|------|
| `command_id` | string (UUID) | 명령 식별자 |
| `action` | string | `navigate` \| `click` \| `type` \| ... |
| `params` | object | action별 파라미터 |
```

### 실행 커맨드

반드시 복사해서 바로 실행되는 커맨드:

```markdown
```bash
# 인프라 먼저 시작
cd infra
docker compose up -d

# 서비스 빌드 및 시작
docker compose -f docker-compose.services.yml up --build
```
```

---

## 언어 규칙

- **섹션 제목, 설명 산문**: 한국어
- **기술 용어, 코드, 커맨드, 식별자, 포트, URL**: 영어
- **환경변수명, 파일명**: 영어 (코드블록 또는 인라인 코드 처리)

예:

```markdown
## 설정

서비스 시작 전 아래 환경변수를 `.env`에 설정한다.

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `REDIS_URL` | `redis://localhost:6379/0` | Redis 연결 주소 |
```

---

## 품질 체크리스트

작성 완료 후 확인:

- [ ] 모든 커맨드는 현재 코드 기준으로 실제 동작함
- [ ] 포트 번호가 docker-compose / config 파일과 일치함
- [ ] 환경변수명이 실제 `.env.example` 또는 Settings 클래스와 일치함
- [ ] `TODO`, `TBD` 없음 (진행 중인 WIP이 아닌 한)
- [ ] 코드 블록에 언어 식별자 있음
- [ ] 마케팅 언어 없음
- [ ] 외부 링크는 공식 문서만 (블로그, Medium 등 금지)

---

## 자주 하는 실수

```markdown
# 나쁜 예 — 추상적
The gateway efficiently handles all incoming requests and provides
a seamless authentication experience.

# 좋은 예 — 구체적
Gateway는 모든 요청에서 Keycloak JWKS로 JWT를 검증한다 (RS256, 60분 캐시).
인증 실패 시 HTTP 401, 세션 불일치 시 HTTP 403을 반환한다.
```

```markdown
# 나쁜 예 — 모호한 Quick Start
1. Clone the repository
2. Configure environment
3. Run the application

# 좋은 예 — 즉시 실행 가능
1. Ollama 설치 후 모델 pull
   ```bash
   ollama pull llama3.1:8b && ollama pull qwen2.5:7b && ollama pull qwen2.5:14b
   ```
2. 인프라 시작
   ```bash
   cd infra && docker compose up -d
   ```
3. 서비스 빌드 및 시작
   ```bash
   docker compose -f docker-compose.services.yml up --build
   ```
4. Extension 설치: `chrome://extensions` → 개발자 모드 → `extension/.output/chrome-mv3` 폴더 로드
```
