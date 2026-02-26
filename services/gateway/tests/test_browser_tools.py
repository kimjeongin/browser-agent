"""Tests for Gateway browser-tools endpoints (webMCP-inspired pattern).

Covered endpoints:
- POST /sessions/{id}/browser-tools/invoke  (Browser Agent → Gateway, blocking)
- POST /sessions/{id}/browser-tools/result/{inv_id}  (Extension → Gateway)
- GET  /sessions/{id}/browser-status
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

import main as gateway_main
from main import app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_session_in_redis(redis_mock, session_id: str = "sess-001"):
    """Simulate a session existing in Redis."""
    from shared.models.session import Session
    sess = Session(session_id=session_id, user_id="user-1")
    redis_mock.get = AsyncMock(return_value=sess.model_dump_json())


# ---------------------------------------------------------------------------
# Invoke -- basic blocking round-trip
# ---------------------------------------------------------------------------


async def test_invoke_and_result_roundtrip_success(gateway_client):
    """Full round-trip: invoke blocks until result endpoint resolves the Future."""
    session_id = "sess-roundtrip"
    _mock_session_in_redis(app.state.redis, session_id)

    async def do_invoke():
        return await gateway_client.post(
            f"/sessions/{session_id}/browser-tools/invoke",
            json={"tool_name": "navigate", "params": {"url": "https://example.com"}},
        )

    async def do_submit_result():
        # Wait for the invocation to be queued
        queue = gateway_main._get_or_create_queue(session_id)
        event = await asyncio.wait_for(queue.get(), timeout=5.0)
        inv_id = event["inv_id"]

        # Verify event shape
        assert event["tool_name"] == "navigate"
        assert event["params"] == {"url": "https://example.com"}

        return await gateway_client.post(
            f"/sessions/{session_id}/browser-tools/result/{inv_id}",
            json={
                "inv_id": inv_id,
                "success": True,
                "result": {"url": "https://example.com"},
            },
        )

    invoke_res, result_res = await asyncio.gather(do_invoke(), do_submit_result())

    assert invoke_res.status_code == 200
    body = invoke_res.json()
    assert body["success"] is True
    assert body["result"] == {"url": "https://example.com"}

    assert result_res.status_code == 200
    assert result_res.json()["ok"] is True


async def test_invoke_and_result_roundtrip_tool_error(gateway_client):
    """When Extension reports failure, invoke returns 200 with the error detail."""
    session_id = "sess-tool-error"
    _mock_session_in_redis(app.state.redis, session_id)

    async def do_invoke():
        return await gateway_client.post(
            f"/sessions/{session_id}/browser-tools/invoke",
            json={"tool_name": "click", "params": {"selector": "#missing"}},
        )

    async def do_submit_failure():
        queue = gateway_main._get_or_create_queue(session_id)
        event = await asyncio.wait_for(queue.get(), timeout=5.0)
        inv_id = event["inv_id"]

        return await gateway_client.post(
            f"/sessions/{session_id}/browser-tools/result/{inv_id}",
            json={
                "inv_id": inv_id,
                "success": False,
                "error": "Element not found: #missing",
            },
        )

    invoke_res, _ = await asyncio.gather(do_invoke(), do_submit_failure())

    # The Gateway returns 200 and the Browser Agent handles the error via result payload
    assert invoke_res.status_code == 200
    body = invoke_res.json()
    assert body["success"] is False
    assert "Element not found" in (body.get("error") or "")


# ---------------------------------------------------------------------------
# Invoke -- 404 when session not found
# ---------------------------------------------------------------------------


async def test_invoke_returns_404_for_unknown_session(gateway_client):
    """Invoke on an unknown session should return 404."""
    app.state.redis.get = AsyncMock(return_value=None)  # session not found

    response = await gateway_client.post(
        "/sessions/no-such-session/browser-tools/invoke",
        json={"tool_name": "navigate", "params": {"url": "https://example.com"}},
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Submit result -- graceful handling of unknown inv_id
# ---------------------------------------------------------------------------


async def test_submit_result_for_unknown_invocation_returns_200(gateway_client):
    """Unknown inv_id returns 200 with ok=False (avoid Extension retry loops)."""
    response = await gateway_client.post(
        "/sessions/any-session/browser-tools/result/nonexistent-id",
        json={
            "inv_id": "nonexistent-id",
            "success": True,
            "result": {},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False


async def test_submit_result_already_done_future_is_idempotent(gateway_client):
    """Submitting a result for an already-resolved Future is safely ignored."""
    inv_id = "already-done-inv"
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()
    future.set_result({"success": True, "result": None})

    gateway_main._pending_invocations[inv_id] = future

    response = await gateway_client.post(
        f"/sessions/any-session/browser-tools/result/{inv_id}",
        json={
            "inv_id": inv_id,
            "success": True,
            "result": {"extra": "data"},
        },
    )
    # Should return 200 - future.done() is checked before set_result
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Browser status endpoint
# ---------------------------------------------------------------------------


async def test_browser_status_returns_false_by_default(gateway_client):
    response = await gateway_client.get("/sessions/sess-abc/browser-status")
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "sess-abc"
    assert body["browser_controlling"] is False


async def test_browser_status_returns_true_when_controlling(gateway_client):
    gateway_main._browser_controlling["sess-xyz"] = True

    response = await gateway_client.get("/sessions/sess-xyz/browser-status")
    assert response.status_code == 200
    body = response.json()
    assert body["browser_controlling"] is True


# ---------------------------------------------------------------------------
# Queue -- event payload structure
# ---------------------------------------------------------------------------


async def test_invoke_enqueues_correct_event_shape(gateway_client):
    """Verify that invoke places the correct event shape in the session queue."""
    session_id = "sess-event-shape"
    _mock_session_in_redis(app.state.redis, session_id)
    params = {"url": "https://test.example.com", "target": "_blank"}

    async def do_invoke():
        return await gateway_client.post(
            f"/sessions/{session_id}/browser-tools/invoke",
            json={"tool_name": "navigate", "params": params},
        )

    async def capture_and_resolve():
        queue = gateway_main._get_or_create_queue(session_id)
        event = await asyncio.wait_for(queue.get(), timeout=5.0)

        # Verify event shape
        assert "inv_id" in event
        assert isinstance(event["inv_id"], str)
        assert event["tool_name"] == "navigate"
        assert event["params"] == params

        inv_id = event["inv_id"]
        await gateway_client.post(
            f"/sessions/{session_id}/browser-tools/result/{inv_id}",
            json={"inv_id": inv_id, "success": True, "result": {}},
        )

    await asyncio.gather(do_invoke(), capture_and_resolve())


# ---------------------------------------------------------------------------
# Browser controlling state transitions
# ---------------------------------------------------------------------------


async def test_browser_controlling_set_during_invoke(gateway_client):
    """_browser_controlling should be True while invoke is waiting for result."""
    session_id = "sess-ctrl-state"
    _mock_session_in_redis(app.state.redis, session_id)

    controlling_during_invoke: list[bool] = []

    async def do_invoke():
        return await gateway_client.post(
            f"/sessions/{session_id}/browser-tools/invoke",
            json={"tool_name": "click", "params": {"selector": "button"}},
        )

    async def observe_and_resolve():
        queue = gateway_main._get_or_create_queue(session_id)
        event = await asyncio.wait_for(queue.get(), timeout=5.0)
        # While waiting, controlling should be True
        controlling_during_invoke.append(
            gateway_main._browser_controlling.get(session_id, False)
        )
        inv_id = event["inv_id"]
        await gateway_client.post(
            f"/sessions/{session_id}/browser-tools/result/{inv_id}",
            json={"inv_id": inv_id, "success": True, "result": {"clicked": True}},
        )

    await asyncio.gather(do_invoke(), observe_and_resolve())

    assert controlling_during_invoke == [True]
    # After completion, controlling should be False
    assert gateway_main._browser_controlling.get(session_id, False) is False
