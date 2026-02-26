"""Tests for Gateway health endpoint."""


async def test_health_returns_ok(gateway_client):
    response = await gateway_client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "gateway"
