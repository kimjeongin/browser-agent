"""Shared pytest fixtures for Gateway tests.

NOTE: httpx's ASGITransport does not trigger the FastAPI lifespan, so app.state
must be manually initialised in fixtures. We also provide mock objects for the
external services (Redis, Keycloak, ACP) that the lifespan would normally create.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from main import app


@pytest.fixture
async def gateway_client():
    """AsyncClient backed by the real ASGI app, with app.state manually set up.

    Because ASGITransport does not emit lifespan events, we initialise
    app.state directly instead of relying on the lifespan context manager.
    Each fixture invocation starts with a clean state to prevent test pollution.
    """
    mock_redis = AsyncMock()
    mock_redis.aclose = AsyncMock()

    # Manually initialise the state that the lifespan would normally create
    app.state.verifier = MagicMock()
    app.state.redis = mock_redis
    app.state.acp = MagicMock()
    app.state.session_queues = {}
    app.state.pending_invocations = {}
    app.state.browser_tool_manifests = {}

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client

    # Cleanup: prevent state from leaking into subsequent tests
    for key in (
        "verifier", "redis", "acp",
        "session_queues", "pending_invocations", "browser_tool_manifests",
    ):
        app.state._state.pop(key, None)
