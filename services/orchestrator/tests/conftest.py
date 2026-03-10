"""Shared pytest fixtures for Orchestrator tests.

NOTE: httpx's ASGITransport does not trigger the FastAPI lifespan, so module-level
state must be manually set in fixtures. We mock the graph to avoid real LLM calls.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from main import app


@pytest.fixture
async def orchestrator_client():
    """AsyncClient backed by the real ASGI app (health endpoint only)."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client
