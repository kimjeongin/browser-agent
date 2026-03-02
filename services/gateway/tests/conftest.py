"""Shared pytest fixtures for Gateway tests.

NOTE: httpx's ASGITransport does not trigger the FastAPI lifespan, so app.state
must be manually initialised in fixtures. We also provide mock objects for the
external services (Keycloak, ACP) that the lifespan would normally create.
"""

from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from main import app
from core.session_store import SessionStore
from core.invocation_broker import InvocationBroker
from settings import Settings


@pytest.fixture(autouse=True)
def reset_app_state():
    """Reset app.state before each test to prevent cross-test pollution."""
    # Create fresh instances for each test
    app.state.session_store = SessionStore(ttl_seconds=86400)
    app.state.broker = InvocationBroker()
    app.state.settings = Settings()
    yield
    # Cleanup
    for key in ("session_store", "broker", "settings"):
        app.state._state.pop(key, None)


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
