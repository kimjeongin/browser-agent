---
name: oauth2-jwt-patterns
description: OAuth2 인증 플로우, JWT 검증, 토큰 저장 전략 패턴. 인증/인가 코드 작성 시, JWT 검증 또는 OAuth2 플로우를 구현할 때 자동으로 로드됩니다.
user-invokable: false
---

# OAuth2 + JWT Best Practices

## OAuth2 플로우 선택 기준

| 클라이언트 유형 | 권장 플로우 | 이유 |
|---|---|---|
| SPA / 브라우저 확장 | **Authorization Code + PKCE** | client_secret 저장 불가 (Public Client) |
| 서버 사이드 웹앱 | Authorization Code | client_secret 서버에서 안전하게 관리 |
| 서버 간 통신 | Client Credentials | 사용자 없는 M2M 통신 |
| 모바일 앱 | Authorization Code + PKCE | 네이티브 앱도 Public Client |

**PKCE (RFC 7636)**: Public Client에서 인가 코드 가로채기 공격 방어를 위해 **필수**.

---

## PKCE 플로우 구현 (TypeScript)

```typescript
// auth/pkce.ts
export async function generatePKCE(): Promise<{ verifier: string; challenge: string }> {
  // code_verifier: 43~128자 랜덤 문자열
  const array = new Uint8Array(96);
  crypto.getRandomValues(array);
  const verifier = btoa(String.fromCharCode(...array))
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '')
    .slice(0, 128);

  // code_challenge = BASE64URL(SHA-256(code_verifier))
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier));
  const challenge = btoa(String.fromCharCode(...new Uint8Array(digest)))
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');

  return { verifier, challenge };
}

export async function startAuthFlow(config: OAuthConfig): Promise<TokenSet> {
  const { verifier, challenge } = await generatePKCE();
  const state = generateRandomString(32); // CSRF 방어
  const nonce = generateRandomString(32); // ID Token 재사용 방어

  // verifier, nonce를 임시 저장 (state로 매핑)
  sessionStorage.setItem(`pkce_${state}`, JSON.stringify({ verifier, nonce }));

  const authUrl = new URL(`${config.issuer}/protocol/openid-connect/auth`);
  authUrl.searchParams.set('response_type', 'code');
  authUrl.searchParams.set('client_id', config.clientId);
  authUrl.searchParams.set('redirect_uri', config.redirectUri);
  authUrl.searchParams.set('scope', 'openid email profile');
  authUrl.searchParams.set('state', state);
  authUrl.searchParams.set('nonce', nonce);
  authUrl.searchParams.set('code_challenge', challenge);
  authUrl.searchParams.set('code_challenge_method', 'S256');

  window.location.href = authUrl.toString();
}
```

---

## 토큰 교환 및 저장 전략

```typescript
// auth/token-storage.ts

// ✅ Access token: 메모리에만 저장 (XSS 공격으로 탈취 불가)
let _accessToken: string | null = null;
let _tokenExpiry: number | null = null;

export function setAccessToken(token: string, expiresIn: number): void {
  _accessToken = token;
  _tokenExpiry = Date.now() + expiresIn * 1000;
}

export function getAccessToken(): string | null {
  if (_tokenExpiry && Date.now() >= _tokenExpiry - 60_000) {
    return null; // 만료 60초 전부터 null 반환 → 리프레시 트리거
  }
  return _accessToken;
}

// ✅ Refresh token: httpOnly 쿠키 (웹앱) 또는 storage.session (확장)
// 웹앱: 서버에서 Set-Cookie: refreshToken=...; HttpOnly; Secure; SameSite=Strict
// 브라우저 확장: chrome.storage.session (브라우저 재시작 시 삭제)
```

```typescript
// auth/token-exchange.ts
export async function exchangeCode(
  code: string,
  state: string,
  config: OAuthConfig,
): Promise<TokenSet> {
  const stored = sessionStorage.getItem(`pkce_${state}`);
  if (!stored) throw new Error('Invalid state: PKCE data not found');

  const { verifier, nonce } = JSON.parse(stored) as { verifier: string; nonce: string };
  sessionStorage.removeItem(`pkce_${state}`); // 사용 후 즉시 삭제

  const response = await fetch(`${config.issuer}/protocol/openid-connect/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      grant_type: 'authorization_code',
      client_id: config.clientId,
      code,
      redirect_uri: config.redirectUri,
      code_verifier: verifier,
    }),
  });

  if (!response.ok) throw new Error('Token exchange failed');
  const tokens = await response.json() as TokenSet;

  // nonce 검증 (ID Token 재사용 방어)
  verifyNonce(tokens.id_token, nonce);

  return tokens;
}
```

---

## JWT 검증 (Python 서버)

```python
# auth/jwt_verifier.py
from functools import lru_cache
from jose import jwt, JWTError
import httpx
from cachetools import TTLCache

class JWTVerifier:
    def __init__(self, jwks_uri: str, audience: str, issuer: str):
        self.jwks_uri = jwks_uri
        self.audience = audience
        self.issuer = issuer
        # JWKS 캐시: 60분 TTL (서명 키는 자주 바뀌지 않음)
        self._jwks_cache: TTLCache = TTLCache(maxsize=1, ttl=3600)

    async def _get_jwks(self) -> dict:
        if "jwks" in self._jwks_cache:
            return self._jwks_cache["jwks"]

        async with httpx.AsyncClient() as client:
            response = await client.get(self.jwks_uri)
            response.raise_for_status()
            self._jwks_cache["jwks"] = response.json()
            return self._jwks_cache["jwks"]

    async def verify(self, token: str) -> dict:
        """
        JWT 검증 체크리스트:
        ✅ 서명 검증 (JWKS 공개키)
        ✅ 만료 시간 (exp)
        ✅ Issuer (iss)
        ✅ Audience (aud)
        """
        jwks = await self._get_jwks()
        try:
            payload = jwt.decode(
                token,
                jwks,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer,
                options={"verify_exp": True},
            )
        except JWTError as e:
            raise AuthenticationError(f"Token verification failed: {e}") from e

        return payload
```

```python
# FastAPI 의존성
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer()

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    verifier: JWTVerifier = Depends(get_verifier),
) -> UserInfo:
    try:
        payload = await verifier.verify(credentials.credentials)
        return UserInfo.from_payload(payload)
    except AuthenticationError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
```

---

## 자동 토큰 갱신 (프로액티브)

```typescript
// auth/token-refresh.ts
class TokenManager {
  private refreshTimer: ReturnType<typeof setTimeout> | null = null;

  async initialize(tokens: TokenSet): Promise<void> {
    setAccessToken(tokens.access_token, tokens.expires_in);
    this.scheduleRefresh(tokens.expires_in);
    // Refresh token은 별도 안전한 저장소에 저장
    await secureStorage.set('refresh_token', tokens.refresh_token);
  }

  private scheduleRefresh(expiresIn: number): void {
    if (this.refreshTimer) clearTimeout(this.refreshTimer);
    // 만료 5분 전에 갱신 시도
    const refreshIn = Math.max((expiresIn - 300) * 1000, 0);
    this.refreshTimer = setTimeout(() => this.refresh(), refreshIn);
  }

  private async refresh(): Promise<void> {
    const refreshToken = await secureStorage.get('refresh_token');
    if (!refreshToken) {
      this.onSessionExpired(); // 로그인 페이지로 리다이렉트
      return;
    }

    try {
      const tokens = await refreshAccessToken(refreshToken);
      await this.initialize(tokens);
    } catch {
      await this.logout(); // 갱신 실패 시 로그아웃
    }
  }

  destroy(): void {
    if (this.refreshTimer) clearTimeout(this.refreshTimer);
  }
}
```

---

## 로그아웃 (토큰 무효화)

```typescript
// auth/logout.ts
export async function logout(config: OAuthConfig): Promise<void> {
  const refreshToken = await secureStorage.get('refresh_token');

  // 1. 메모리에서 access token 삭제
  clearAccessToken();

  // 2. 저장소에서 refresh token 삭제
  await secureStorage.remove('refresh_token');

  // 3. 인가 서버에 토큰 무효화 요청 (OIDC End Session)
  if (refreshToken) {
    const logoutUrl = new URL(`${config.issuer}/protocol/openid-connect/logout`);
    logoutUrl.searchParams.set('client_id', config.clientId);
    logoutUrl.searchParams.set('refresh_token', refreshToken);

    await fetch(logoutUrl.toString(), { method: 'POST' }).catch(() => {
      // 로그아웃 API 실패 시에도 로컬 토큰은 이미 삭제됨
    });
  }
}
```

---

## 보안 체크리스트

- [ ] **PKCE 필수**: Public Client (SPA, 모바일, 확장)에서 `code_challenge_method=S256`
- [ ] **state 파라미터**: CSRF 방어 — 요청 시 생성, 응답에서 검증 후 삭제
- [ ] **nonce 파라미터**: ID Token 재사용 방어 — PKCE 사용 시 함께 사용
- [ ] **Access token 메모리 저장**: `localStorage`, `sessionStorage` 금지 (XSS 취약)
- [ ] **Refresh token 안전한 저장소**: `httpOnly cookie` (웹) 또는 `storage.session` (확장)
- [ ] **JWT 검증 4종 세트**: 서명 + exp + iss + aud 모두 검증
- [ ] **JWKS 캐시**: 외부 요청 최소화 (TTL 60분 권장)
- [ ] **프로액티브 갱신**: 만료 5분 전 자동 갱신으로 사용자 경험 유지
- [ ] **에러 메시지**: 401 응답에 구체적인 실패 이유 노출 금지
