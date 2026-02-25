# web-search

웹 검색과 페이지 본문 추출 기능을 MCP 도구로 제공하는 서버.

## 책임

- `web_search` 도구로 DuckDuckGo Lite 또는 Tavily를 통해 웹을 검색한다.
- `fetch_webpage` 도구로 지정 URL의 HTML을 가져와 태그를 제거한 텍스트를 반환한다.
- `TAVILY_API_KEY`가 설정되지 않으면 API 키 없이 DuckDuckGo Lite를 사용한다.

## 현재 상태

Chat Agent가 DuckDuckGo 검색을 직접 구현 중이므로 이 서버를 독립 실행할 필요가 없다. 이 서버는 향후 독립 배포 또는 다른 에이전트에서 재사용할 때를 위해 준비된 구현이다.

## MCP 도구 목록

### `web_search`

웹을 검색하고 결과 목록을 반환한다.

| 파라미터 | 타입 | 기본값 | 설명 |
|----------|------|--------|------|
| `query` | string | 필수 | 검색 쿼리 |
| `max_results` | int | `5` | 반환할 최대 결과 수 (1–10) |

반환값: `title`, `url`, `snippet` 키를 가진 객체의 배열.

### `fetch_webpage`

URL의 페이지를 가져와 HTML 태그를 제거한 텍스트를 반환한다.

| 파라미터 | 타입 | 기본값 | 설명 |
|----------|------|--------|------|
| `url` | string | 필수 | 가져올 페이지 URL |
| `max_chars` | int | `8000` | 반환할 최대 문자 수. 초과분은 `... [truncated]`로 표시 |

반환값: `url`, `title`, `content` 키를 가진 객체.

## 검색 백엔드

| 조건 | 백엔드 | API 키 |
|------|--------|--------|
| `TAVILY_API_KEY` 미설정 | DuckDuckGo Lite (`https://lite.duckduckgo.com/lite/`) | 불필요 |
| `TAVILY_API_KEY` 설정 | Tavily API (`https://api.tavily.com/search`) | 필요 |

## 의존 서비스

외부 네트워크 접근 외에 별도의 의존 서비스 없음. DuckDuckGo Lite 사용 시 인프라 의존성 없이 실행된다.

## 환경변수

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `TAVILY_API_KEY` | string | 없음 | Tavily API 키. 설정 시 DuckDuckGo 대신 Tavily 사용 |
| `SEARCH_MAX_RESULTS` | int | `5` | `web_search` 기본 최대 결과 수 |
| `FETCH_MAX_CHARS` | int | `8000` | `fetch_webpage` 기본 최대 문자 수 |

## 로컬 실행

```bash
cd mcp_servers/web_search
uv pip install -e .
python main.py
```

> **주의**: 이 서버는 `stdio` transport로 실행된다. MCP 클라이언트(에이전트)가 서브프로세스로 직접 spawn하도록 설계되어 있으며, 독립 HTTP 서버로 실행하지 않는다.

## 파일 구조

```
mcp_servers/web_search/
├── main.py          # FastMCP 서버, 검색 백엔드, 도구 정의
└── pyproject.toml   # Python 3.13+, fastmcp, httpx, pydantic-settings
```
