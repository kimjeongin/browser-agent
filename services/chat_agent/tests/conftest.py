"""Shared pytest fixtures for Chat Agent tests.

NOTE: httpx's ASGITransport does not trigger the FastAPI lifespan, so app.state
must be manually initialised in fixtures. We mock the graph to avoid any real
LLM or database connections.
"""

from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from main import app


@pytest.fixture
async def chat_client():
    """AsyncClient backed by the real ASGI app, with app.state manually set up.

    Because ASGITransport does not emit lifespan events, we initialise
    app.state directly instead of relying on the lifespan context manager.
    """
    app.state.graph = MagicMock()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client

    app.state._state.pop("graph", None)
