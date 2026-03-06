"""Shared pytest fixtures for Orchestrator tests.

NOTE: httpx's ASGITransport does not trigger the FastAPI lifespan, so app.state
must be manually initialised in fixtures. We mock the LLM and ACP clients to
avoid any real network or database connections.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage

from main import app


@pytest.fixture
def mock_llm():
    """Mock BaseChatModel that returns a JSON classification response."""
    llm = AsyncMock()
    llm.ainvoke = AsyncMock(
        return_value=AIMessage(content='{"agent": "chat_agent"}'),
    )
    return llm


@pytest.fixture
def mock_chat_client():
    """Mock ACPClient for the chat agent."""
    client = AsyncMock()
    client.run = AsyncMock(
        return_value={
            "run_id": "run-001",
            "status": "completed",
            "output": {
                "messages": [{"role": "ai", "content": "Hello from chat agent!"}],
            },
        },
    )
    return client


@pytest.fixture
def mock_browser_client():
    """Mock ACPClient for the browser agent."""
    client = AsyncMock()
    client.run = AsyncMock(
        return_value={
            "run_id": "run-002",
            "status": "completed",
            "output": {
                "messages": [{"role": "ai", "content": "Browser action completed."}],
            },
        },
    )
    return client


@pytest.fixture
async def orchestrator_client():
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
