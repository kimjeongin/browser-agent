"""Tests for Chat Agent.

Covered:
- /health endpoint
"""


class TestHealthEndpoint:
    """Test the ACP health endpoint."""

    async def test_health_returns_ok(self, chat_client):
        response = await chat_client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
