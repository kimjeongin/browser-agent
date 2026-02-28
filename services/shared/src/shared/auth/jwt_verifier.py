"""Keycloak JWKS offline JWT verification."""

import logging
from typing import Any

import httpx
from cachetools import TTLCache
from jose import jwt, JWTError

logger = logging.getLogger(__name__)

_JWKS_CACHE_TTL = 3600  # 60 minutes
_JWKS_CACHE_MAXSIZE = 1


class KeycloakJWTVerifier:
    """Verify JWTs issued by a Keycloak realm using JWKS offline validation.

    The JWKS endpoint response is cached for 60 minutes to avoid redundant
    network calls on every request.
    """

    def __init__(self, realm_url: str, audience: str) -> None:
        """Initialise the verifier.

        Args:
            realm_url: Full Keycloak realm URL,
                       e.g. ``http://keycloak:8080/realms/browser-agent``.
            audience: Expected ``aud`` claim value.
        """
        # Strip trailing slash for consistent URL construction
        self._realm_url = realm_url.rstrip("/")
        self._audience = audience
        self._jwks_uri = f"{self._realm_url}/protocol/openid-connect/certs"
        self._issuer = self._realm_url
        self._jwks_cache: TTLCache[str, dict[str, Any]] = TTLCache(
            maxsize=_JWKS_CACHE_MAXSIZE,
            ttl=_JWKS_CACHE_TTL,
        )

    async def verify(self, token: str) -> dict[str, Any]:
        """Verify a JWT and return its decoded payload.

        Raises:
            JWTError: If the token is invalid, expired, or signature
                      verification fails.
        """
        jwks = await self._get_jwks()

        # Docker 환경 호환성: Gateway는 내부망(keycloak:8080)을 보지만
        # 클라이언트는 외부망(localhost:8080) 토큰을 가져올 수 있음.
        expected_issuer = self._issuer
        try:
            claims = jwt.get_unverified_claims(token)
            token_iss = claims.get("iss")
            if token_iss and token_iss != self._issuer:
                alt_issuer = self._issuer.replace("://keycloak:", "://localhost:")
                if token_iss == alt_issuer:
                    expected_issuer = token_iss
        except JWTError:
            pass

        try:
            payload: dict[str, Any] = jwt.decode(
                token,
                jwks,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=expected_issuer,
            )
        except JWTError:
            logger.warning("JWT verification failed", exc_info=True)
            raise

        return payload

    async def _get_jwks(self) -> dict[str, Any]:
        """Fetch JWKS from Keycloak, using a TTL cache."""
        cache_key = "jwks"

        if cache_key in self._jwks_cache:
            return self._jwks_cache[cache_key]

        async with httpx.AsyncClient() as client:
            response = await client.get(self._jwks_uri, timeout=10.0)
            response.raise_for_status()
            jwks = response.json()

        self._jwks_cache[cache_key] = jwks
        logger.debug("JWKS refreshed from %s", self._jwks_uri)
        return jwks
