"""Tests for Gateway browser-tools endpoints (webMCP broker).

Covered endpoints:
- POST /sessions/{id}/browser-tools/register
- GET  /sessions/{id}/browser-tools
- POST /sessions/{id}/browser-tools/invoke
- POST /sessions/{id}/browser-tools/result/{invocation_id}
"""

import asyncio

from main import app

SAMPLE_TOOLS = [
    {"name": "navigate", "description": "Navigate to a URL", "inputSchema": {"type": "object"}},
    {"name": "click", "description": "Click an element", "inputSchema": {"type": "object"}},
]


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------


async def test_register_browser_tools_stores_manifest(gateway_client):
    response = await gateway_client.post(
        "/sessions/sess-001/browser-tools/register",
        json={"tools": SAMPLE_TOOLS},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["tool_count"] == 2

    # Manifest is persisted in app state
    assert app.state.browser_tool_manifests.get("sess-001") == SAMPLE_TOOLS


async def test_register_browser_tools_overwrites_previous(gateway_client):
    await gateway_client.post(
        "/sessions/sess-002/browser-tools/register",
        json={"tools": SAMPLE_TOOLS},
    )
    new_tools = [{"name": "scroll", "description": "Scroll", "inputSchema": {"type": "object"}}]
    response = await gateway_client.post(
        "/sessions/sess-002/browser-tools/register",
        json={"tools": new_tools},
    )
    assert response.status_code == 200
    assert response.json()["tool_count"] == 1
    assert app.state.browser_tool_manifests["sess-002"] == new_tools


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


async def test_list_browser_tools_returns_manifest(gateway_client):
    # Register first
    await gateway_client.post(
        "/sessions/sess-list/browser-tools/register",
        json={"tools": SAMPLE_TOOLS},
    )

    response = await gateway_client.get("/sessions/sess-list/browser-tools")
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "sess-list"
    assert body["tools"] == SAMPLE_TOOLS


async def test_list_browser_tools_returns_empty_for_unknown_session(gateway_client):
    response = await gateway_client.get("/sessions/no-such-session/browser-tools")
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "no-such-session"
    assert body["tools"] == []


# ---------------------------------------------------------------------------
# Invoke -- 503 when no SSE channel
# ---------------------------------------------------------------------------


async def test_invoke_returns_503_when_extension_not_connected(gateway_client):
    # No queue registered for this session → Extension not connected
    response = await gateway_client.post(
        "/sessions/disconnected/browser-tools/invoke",
        json={"tool": "navigate", "params": {"url": "https://example.com"}},
    )
    assert response.status_code == 503
    assert "not connected" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Invoke + Result -- happy path (concurrent)
# ---------------------------------------------------------------------------


async def test_invoke_and_result_roundtrip_success(gateway_client):
    """Full round-trip: invoke blocks until result endpoint resolves the Future."""
    session_id = "sess-roundtrip"

    # Simulate Extension SSE channel being open
    queue: asyncio.Queue = asyncio.Queue()
    app.state.session_queues[session_id] = queue

    async def do_invoke():
        return await gateway_client.post(
            f"/sessions/{session_id}/browser-tools/invoke",
            json={"tool": "navigate", "params": {"url": "https://example.com"}},
        )

    async def do_submit_result():
        # Wait for the invocation event placed in the queue by do_invoke
        event = await asyncio.wait_for(queue.get(), timeout=5.0)
        invocation_id = event["invocation_id"]
        assert event["type"] == "tool_invocation"
        assert event["tool"] == "navigate"

        return await gateway_client.post(
            f"/sessions/{session_id}/browser-tools/result/{invocation_id}",
            json={"success": True, "result": {"url": "https://example.com"}},
        )

    invoke_res, result_res = await asyncio.gather(do_invoke(), do_submit_result())

    assert invoke_res.status_code == 200
    body = invoke_res.json()
    assert body["success"] is True
    assert body["result"] == {"url": "https://example.com"}

    assert result_res.status_code == 200
    assert result_res.json()["ok"] is True


async def test_invoke_and_result_roundtrip_tool_error(gateway_client):
    """When Extension reports failure, invoke returns success=False with error."""
    session_id = "sess-tool-error"
    queue: asyncio.Queue = asyncio.Queue()
    app.state.session_queues[session_id] = queue

    async def do_invoke():
        return await gateway_client.post(
            f"/sessions/{session_id}/browser-tools/invoke",
            json={"tool": "click", "params": {"selector": "#missing"}},
        )

    async def do_submit_failure():
        event = await asyncio.wait_for(queue.get(), timeout=5.0)
        invocation_id = event["invocation_id"]
        return await gateway_client.post(
            f"/sessions/{session_id}/browser-tools/result/{invocation_id}",
            json={"success": False, "error": "Element not found: #missing"},
        )

    invoke_res, _ = await asyncio.gather(do_invoke(), do_submit_failure())

    assert invoke_res.status_code == 200
    body = invoke_res.json()
    assert body["success"] is False
    assert "Element not found" in body["error"]


# ---------------------------------------------------------------------------
# Submit result -- error cases
# ---------------------------------------------------------------------------


async def test_submit_result_404_for_unknown_invocation_id(gateway_client):
    response = await gateway_client.post(
        "/sessions/any-session/browser-tools/result/nonexistent-id",
        json={"success": True, "result": {}},
    )
    assert response.status_code == 404
    assert "pending" in response.json()["detail"].lower()


async def test_submit_result_409_when_already_resolved(gateway_client):
    """Submitting a result for an already-resolved Future returns 409."""
    invocation_id = "already-done-inv"
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()
    future.set_result({"success": True, "result": None})

    app.state.pending_invocations[invocation_id] = future

    response = await gateway_client.post(
        f"/sessions/any-session/browser-tools/result/{invocation_id}",
        json={"success": True, "result": {"data": "extra"}},
    )
    assert response.status_code == 409
    assert "already" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Invoke event payload structure
# ---------------------------------------------------------------------------


async def test_invoke_event_has_correct_structure(gateway_client):
    """Verify the SSE event payload structure enqueued during invoke."""
    session_id = "sess-event-struct"
    queue: asyncio.Queue = asyncio.Queue()
    app.state.session_queues[session_id] = queue

    params = {"url": "https://test.example.com"}

    async def do_invoke():
        return await gateway_client.post(
            f"/sessions/{session_id}/browser-tools/invoke",
            json={"tool": "navigate", "params": params},
        )

    async def capture_and_resolve():
        event = await asyncio.wait_for(queue.get(), timeout=5.0)
        # Verify event shape
        assert "invocation_id" in event
        assert event["type"] == "tool_invocation"
        assert event["tool"] == "navigate"
        assert event["params"] == params

        invocation_id = event["invocation_id"]
        await gateway_client.post(
            f"/sessions/{session_id}/browser-tools/result/{invocation_id}",
            json={"success": True, "result": {}},
        )

    await asyncio.gather(do_invoke(), capture_and_resolve())
