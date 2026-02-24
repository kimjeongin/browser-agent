"""FastAPI dependency injection helpers for Keycloak authentication."""

from typing import Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError

from shared.auth.jwt_verifier import KeycloakJWTVerifier

_bearer_scheme = HTTPBearer()


def get_verifier(request: Request) -> KeycloakJWTVerifier:
    """Retrieve the ``KeycloakJWTVerifier`` singleton from ``app.state``.

    The verifier must be initialised during the application lifespan
    (e.g. inside ``@asynccontextmanager`` lifespan function) and stored
    as ``app.state.verifier``.
    """
    verifier: KeycloakJWTVerifier | None = getattr(
        request.app.state, "verifier", None
    )
    if verifier is None:
        raise RuntimeError(
            "KeycloakJWTVerifier not initialised. "
            "Set app.state.verifier during application lifespan."
        )
    return verifier


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    verifier: KeycloakJWTVerifier = Depends(get_verifier),
) -> dict[str, Any]:
    """Extract and verify the Bearer token, returning the JWT payload.

    Raises:
        HTTPException(401): If the token is missing, invalid, or expired.
    """
    try:
        payload = await verifier.verify(credentials.credentials)
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    return payload
