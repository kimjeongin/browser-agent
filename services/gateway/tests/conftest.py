"""Shared pytest fixtures for Gateway tests.

NOTE: httpx's ASGITransport does not trigger the FastAPI lifespan, so app.state
must be manually initialised in fixtures. We also provide mock objects for the
external services (Keycloak, ACP) that the lifespan would normally create.
"""

from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

import main as gateway_main
from main import app


@pytest.fixture(autouse=True)
def reset_module_state():
    """Reset module-level in-memory state before each test."""
    gateway_main._sessions.clear()
    gateway_main._session_expires_at.clear()
    gateway_main._session_queues.clear()
    gateway_main._pending_invocations.clear()
    gateway_main._browser_controlling.clear()
    yield
    gateway_main._sessions.clear()
    gateway_main._session_expires_at.clear()
    gateway_main._session_queues.clear()
    gateway_main._pending_invocations.clear()
    gateway_main._browser_controlling.clear()


@pytest.fixture
async def gateway_client():
    """AsyncClient backed by the real ASGI app, with app.state manually set up.

    Because ASGITransport does not emit lifespan events, we initialise
    app.state directly instead of relying on the lifespan context manager.
    Each fixture invocation starts with a clean state to prevent test pollution.
    """
    app.state.verifier = MagicMock()
    app.state.acp = MagicMock()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client

    for key in ("verifier", "acp"):
        app.state._state.pop(key, None)
