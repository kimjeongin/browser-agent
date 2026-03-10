"""Shared pytest fixtures for Orchestrator tests.

NOTE: httpx's ASGITransport does not trigger the FastAPI lifespan, so module-level
state must be manually set in fixtures. We mock the LLM and acp_sdk Clients to
avoid any real network connections.
"""

from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage

from acp_sdk.models import MessagePart, MessagePartEvent

import main as orchestrator_main
from main import app


@pytest.fixture
def mock_llm():
    """Mock BaseChatModel that returns a JSON classification response."""
    llm = AsyncMock()
    llm.ainvoke = AsyncMock(
        return_value=AIMessage(content='{"agent": "chat_agent"}'),
    )
    return llm


def _make_message_part_event(content: str, content_type: str = "text/plain") -> MessagePartEvent:
    return MessagePartEvent(part=MessagePart(content=content, content_type=content_type))


@pytest.fixture
def mock_chat_client():
    """Mock acp_sdk Client for the chat agent."""
    client = AsyncMock()

    async def fake_run_stream(input, *, agent):
        yield _make_message_part_event("Hello from chat agent!")

    client.run_stream = fake_run_stream
    return client


@pytest.fixture
def mock_browser_client():
    """Mock acp_sdk Client for the browser agent."""
    client = AsyncMock()

    async def fake_run_stream(input, *, agent):
        yield _make_message_part_event("Browser action completed.")

    client.run_stream = fake_run_stream
    return client


@pytest.fixture
async def orchestrator_client(mock_llm, mock_chat_client, mock_browser_client):
    """AsyncClient backed by the real ASGI app, with module state mocked."""
    orchestrator_main._llm = mock_llm
    orchestrator_main._chat_client = mock_chat_client
    orchestrator_main._browser_client = mock_browser_client

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client

    orchestrator_main._llm = None
    orchestrator_main._chat_client = None
    orchestrator_main._browser_client = None
