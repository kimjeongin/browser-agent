# GitHub PR Publisher - Project Memory

## 프로젝트 기본 정보
- **Remote**: https://github.com/kimjeongin/browser-agent.git
- **Base branch**: `main`
- **Git protocol**: HTTPS (SSH 키 미등록으로 SSH 사용 불가 → HTTPS 사용)
- **Push 방법**: `GIT_ASKPASS=/usr/local/bin/gh git push` (gh CLI 인증 활용)

## Git 컨벤션 요약
- **Branch**: `<type>/<kebab-case-description>` (3-5 words max)
- **Commit**: `<type>(<scope>): <imperative description>` (≤72 chars, Conventional Commits)
- **PR 본문 언어**: 한국어
- **PR 섹션**: 변경 사항 → 테스트 방법 → 관련 이슈

## 주의사항
- `.claude/settings.local.json` 커밋 금지 (루트 `.gitignore`에 등록됨)
- `.venv/`, `node_modules/` 는 서브디렉토리 `.gitignore`로 관리됨 — 루트에도 추가됨
- 환경변수 파일(`.env`, `.env.local`) 커밋 금지

## gh CLI 활용
- `gh pr create`, `gh pr list`, `gh repo view` 등 GitHub 작업에 gh CLI 우선 사용
- git push 시 SSH 대신 `GIT_ASKPASS=/usr/local/bin/gh git push` 패턴 사용
