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

    def __init__(
        self,
        realm_url: str,
        audience: str,
        jwks_url: str | None = None,
    ) -> None:
        """Initialise the verifier.

        Args:
            realm_url: Public Keycloak realm URL used for issuer validation,
                       e.g. ``http://localhost:8080/realms/browser-agent``.
                       Must match the ``iss`` claim in issued JWTs.
            audience: Expected ``aud`` claim value.
            jwks_url: Optional URL for fetching JWKS keys. Defaults to
                      ``{realm_url}/protocol/openid-connect/certs``.
                      Override with an internal URL (e.g. Docker service name)
                      so the Gateway can reach Keycloak without going through
                      the public network while still validating against the
                      public issuer.
        """
        # Strip trailing slash for consistent URL construction
        self._realm_url = realm_url.rstrip("/")
        self._audience = audience
        self._jwks_uri = (
            jwks_url
            if jwks_url
            else f"{self._realm_url}/protocol/openid-connect/certs"
        )
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

        try:
            payload: dict[str, Any] = jwt.decode(
                token,
                jwks,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
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
