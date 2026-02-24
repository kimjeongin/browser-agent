---
name: git-conventions
description: 이 프로젝트의 Git 브랜치 명명 규칙, 커밋 메시지 형식, PR 제목 및 본문 템플릿을 제공합니다. github-pr-publisher 에이전트가 브랜치명, 커밋, PR을 작성하기 전에 참조합니다.
user-invocable: false
---

# Git Conventions

## Branch Naming

**Format:** `<type>/<short-description>`

| Type | When to use |
|------|-------------|
| `feat` | New feature |
| `fix` | Bug fix |
| `refactor` | Code restructuring without behavior change |
| `chore` | Build, tooling, config, dependency updates |
| `docs` | Documentation only |
| `test` | Adding or fixing tests |
| `hotfix` | Urgent production fix |
| `release` | Release preparation |

**Rules:**
- Use **kebab-case** (lowercase, hyphens only)
- Keep the description concise: **3–5 words max** after the type prefix
- Derive the description from the actual diff/change, not from vague labels
- Base new branches off `main` (or `develop` if it exists in the repo)

**Examples:**
```
feat/add-social-login
fix/payment-null-pointer
refactor/auth-module
chore/upgrade-dependencies
docs/update-readme
test/add-unit-tests-auth
hotfix/login-redirect-loop
```

---

## Commit Messages

Follow the **Conventional Commits** specification.

**Format:**
```
<type>(<scope>): <short description>

[optional body]
```

**Rules:**
- Subject line: **imperative mood**, max **72 characters**
- `<scope>`: optional — use the module, feature area, or file domain (e.g., `auth`, `payment`, `ui`, `api`)
- Body: explain *what* and *why*, not *how*
- Never use generic messages like `"fix bug"`, `"update"`, `"wip"`, `"changes"`

**Examples:**
```
feat(auth): add social login with Google OAuth2
fix(payment): handle null pointer when card info is missing
refactor(auth): extract token validation into separate service
chore(deps): upgrade eslint to v9 and update config
docs(api): add endpoint documentation for /users route
test(auth): add unit tests for JWT refresh logic
```

---

## Pull Request Format

### Title
- Mirror the commit subject: `<type>(<scope>): <description>` or plain descriptive title
- Max **72 characters**

### Body Template

```markdown
## 변경 사항 (Changes)

- 변경된 내용과 이유를 불릿 포인트로 작성
- 기술적 결정이나 트레이드오프가 있다면 언급

## 테스트 방법 (How to Test)

1. 테스트 단계를 순서대로 작성
2. 예상되는 결과 명시

## 관련 이슈 (Related Issues)

- Closes #<issue-number>  (or N/A)
```

**Rules:**
- Write in **Korean** by default
- Use `--draft` flag if there are TODO comments or incomplete implementation
- Reference issues with `Closes #N` or `Related to #N` when applicable
- For large diffs (500+ lines), note in the body that careful review is required

---

## Quick Reference

```
Branch  : feat/short-description
Commit  : feat(scope): imperative description (≤72 chars)
PR Title: feat(scope): imperative description (≤72 chars)
PR Body : Changes → How to Test → Related Issues
Language: Korean (default)
Base    : main (or develop if exists)
```
