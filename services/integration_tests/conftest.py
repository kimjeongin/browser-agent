"""Shared fixtures for Gateway integration tests.

Integration tests exercise real HTTP flows through the ASGI app, verifying
that routers, middleware, session store, and invocation broker interact
correctly as a cohesive system.

Auth is stubbed via a mock KeycloakJWTVerifier that always returns a fixed
user payload, since Keycloak is not available in the test environment.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from core.invocation_broker import InvocationBroker
from core.session_store import SessionStore
from main import app
from settings import Settings

# ---------------------------------------------------------------------------
# Fixed test user
# ---------------------------------------------------------------------------
TEST_USER_ID = "test-user-001"
TEST_USER_PAYLOAD: dict[str, Any] = {
    "sub": TEST_USER_ID,
    "preferred_username": "testuser",
    "email": "test@example.com",
}

OTHER_USER_ID = "other-user-002"
OTHER_USER_PAYLOAD: dict[str, Any] = {
    "sub": OTHER_USER_ID,
    "preferred_username": "otheruser",
    "email": "other@example.com",
}

AUTH_HEADER = {"Authorization": "Bearer fake-jwt-token"}
OTHER_AUTH_HEADER = {"Authorization": "Bearer other-fake-jwt-token"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_mock_verifier(payload: dict[str, Any]) -> MagicMock:
    """Create a mock verifier that returns *payload* for any token."""
    verifier = MagicMock()
    verifier.verify = AsyncMock(return_value=payload)
    return verifier


@pytest.fixture(autouse=True)
def _reset_app_state():
    """Reset app.state before each test to prevent cross-test pollution."""
    app.state.session_store = SessionStore(ttl_seconds=86400)
    app.state.broker = InvocationBroker()
    app.state.settings = Settings()
    yield
    for key in ("session_store", "broker", "settings", "verifier", "acp"):
        app.state._state.pop(key, None)


@pytest.fixture
async def client() -> AsyncClient:
    """AsyncClient wired to the real Gateway ASGI app.

    The verifier mock always authenticates requests as TEST_USER_ID.
    """
    app.state.verifier = _make_mock_verifier(TEST_USER_PAYLOAD)
    app.state.acp = MagicMock()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=AUTH_HEADER,
    ) as c:
        yield c


@pytest.fixture
async def other_client() -> AsyncClient:
    """AsyncClient authenticated as a different user (OTHER_USER_ID).

    Shares the same app state as *client*, so cross-user ownership checks
    can be tested.
    """
    # The verifier needs to distinguish tokens.  We replace the mock's
    # side_effect so it returns the right payload per token value.
    async def _verify_by_token(token: str) -> dict[str, Any]:
        if token == "other-fake-jwt-token":
            return OTHER_USER_PAYLOAD
        return TEST_USER_PAYLOAD

    verifier = MagicMock()
    verifier.verify = AsyncMock(side_effect=_verify_by_token)
    app.state.verifier = verifier
    app.state.acp = app.state.acp if hasattr(app.state, "acp") else MagicMock()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=OTHER_AUTH_HEADER,
    ) as c:
        yield c
