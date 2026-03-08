"""Integration tests for the browser tool invocation round-trip.

These tests verify the full 3-hop webMCP-inspired flow:
  1. Browser Agent POSTs /sessions/{id}/browser-tools/invoke (blocks)
  2. Gateway queues the invocation -> SSE delivers to Extension
  3. Extension POSTs /sessions/{id}/browser-tools/result/{inv_id}
  4. Gateway resolves the Future -> invoke endpoint returns

The tests exercise the interaction between InvocationBroker, SessionStore,
and the browser_tools router as a cohesive system.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from httpx import AsyncClient

from conftest import AUTH_HEADER, TEST_USER_ID
from main import app
from shared.models.session import Session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_session(session_id: str, with_extension: bool = True) -> None:
    """Insert a session and optionally simulate an Extension SSE subscriber."""
    store = app.state.session_store
    broker = app.state.broker

    sess = Session(session_id=session_id, user_id=TEST_USER_ID)
    store.set(sess)
    broker.get_or_create_queue(session_id)

    if with_extension:
        store.increment_sse_subscribers(session_id)


# ---------------------------------------------------------------------------
# Successful round-trip
# ---------------------------------------------------------------------------


class TestBrowserToolRoundtrip:
    """Full invoke -> queue -> result -> unblock cycle."""

    async def test_navigate_roundtrip(self, client: AsyncClient):
        """Navigate tool: invoke blocks until Extension posts result."""
        session_id = "it-roundtrip-nav"
        _seed_session(session_id)

        async def invoke():
            return await client.post(
                f"/sessions/{session_id}/browser-tools/invoke",
                json={"tool_name": "navigate", "params": {"url": "https://example.com"}},
            )

        async def resolve():
            queue = app.state.broker.get_or_create_queue(session_id)
            event = await asyncio.wait_for(queue.get(), timeout=5.0)
            inv_id = event["inv_id"]

            assert event["tool_name"] == "navigate"
            assert event["params"]["url"] == "https://example.com"

            return await client.post(
                f"/sessions/{session_id}/browser-tools/result/{inv_id}",
                json={
                    "inv_id": inv_id,
                    "success": True,
                    "result": {"url": "https://example.com", "title": "Example"},
                },
            )

        invoke_resp, result_resp = await asyncio.gather(invoke(), resolve())

        assert invoke_resp.status_code == 200
        body = invoke_resp.json()
        assert body["success"] is True
        assert body["result"]["title"] == "Example"

        assert result_resp.status_code == 200
        assert result_resp.json()["ok"] is True

    async def test_click_roundtrip_with_error(self, client: AsyncClient):
        """When Extension reports tool failure, invoke returns the error payload."""
        session_id = "it-roundtrip-err"
        _seed_session(session_id)

        async def invoke():
            return await client.post(
                f"/sessions/{session_id}/browser-tools/invoke",
                json={"tool_name": "click", "params": {"selector": "#missing"}},
            )

        async def resolve_with_error():
            queue = app.state.broker.get_or_create_queue(session_id)
            event = await asyncio.wait_for(queue.get(), timeout=5.0)
            inv_id = event["inv_id"]

            return await client.post(
                f"/sessions/{session_id}/browser-tools/result/{inv_id}",
                json={
                    "inv_id": inv_id,
                    "success": False,
                    "error": "Element #missing not found in DOM",
                },
            )

        invoke_resp, _ = await asyncio.gather(invoke(), resolve_with_error())

        assert invoke_resp.status_code == 200
        body = invoke_resp.json()
        assert body["success"] is False
        assert "not found" in body["error"]

    async def test_multiple_sequential_tools(self, client: AsyncClient):
        """Multiple tool invocations on the same session execute sequentially."""
        session_id = "it-sequential"
        _seed_session(session_id)

        execution_order: list[str] = []

        async def invoke_tool(tool_name: str, params: dict):
            return await client.post(
                f"/sessions/{session_id}/browser-tools/invoke",
                json={"tool_name": tool_name, "params": params},
            )

        async def resolve_all():
            """Resolve two sequential invocations."""
            queue = app.state.broker.get_or_create_queue(session_id)

            for _ in range(2):
                event = await asyncio.wait_for(queue.get(), timeout=5.0)
                inv_id = event["inv_id"]
                execution_order.append(event["tool_name"])

                await client.post(
                    f"/sessions/{session_id}/browser-tools/result/{inv_id}",
                    json={"inv_id": inv_id, "success": True, "result": {}},
                )

        # Because of the per-session semaphore, these run sequentially
        async def invoke_both():
            r1 = await invoke_tool("navigate", {"url": "https://a.com"})
            r2 = await invoke_tool("click", {"selector": "#btn"})
            return r1, r2

        (r1, r2), _ = await asyncio.gather(invoke_both(), resolve_all())

        assert r1.status_code == 200
        assert r2.status_code == 200
        assert execution_order == ["navigate", "click"]


# ---------------------------------------------------------------------------
# Error conditions
# ---------------------------------------------------------------------------


class TestBrowserToolErrors:
    """Error handling for browser tool invocations."""

    async def test_invoke_unknown_session_returns_404(self, client: AsyncClient):
        """Invoke on a nonexistent session returns 404."""
        resp = await client.post(
            "/sessions/nonexistent-session/browser-tools/invoke",
            json={"tool_name": "navigate", "params": {"url": "https://x.com"}},
        )
        assert resp.status_code == 404

    async def test_invoke_without_extension_returns_503(self, client: AsyncClient):
        """Invoke returns 503 when no Extension SSE subscriber is connected."""
        session_id = "it-no-ext"
        _seed_session(session_id, with_extension=False)

        resp = await client.post(
            f"/sessions/{session_id}/browser-tools/invoke",
            json={"tool_name": "navigate", "params": {"url": "https://x.com"}},
        )
        assert resp.status_code == 503
        assert "extension" in resp.json()["detail"].lower()

    async def test_result_for_unknown_invocation(self, client: AsyncClient):
        """Posting result for unknown inv_id returns ok=False."""
        resp = await client.post(
            "/sessions/any/browser-tools/result/unknown-inv-id",
            json={"inv_id": "unknown-inv-id", "success": True, "result": {}},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is False

    async def test_invoke_timeout(self, client: AsyncClient):
        """Invoke returns 504 when Extension never posts a result."""
        session_id = "it-timeout"
        _seed_session(session_id)

        # Set a very short timeout to avoid slow tests
        app.state.settings.browser_tool_timeout = 0.5

        resp = await client.post(
            f"/sessions/{session_id}/browser-tools/invoke",
            json={"tool_name": "screenshot", "params": {}},
        )
        assert resp.status_code == 504
        assert "timed out" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Browser controlling state
# ---------------------------------------------------------------------------


class TestBrowserControllingState:
    """browser_controlling flag transitions during tool lifecycle."""

    async def test_controlling_true_during_invoke(self, client: AsyncClient):
        """browser_controlling is True while invoke is blocking."""
        session_id = "it-ctrl-during"
        _seed_session(session_id)

        observed_states: list[bool] = []

        async def invoke():
            return await client.post(
                f"/sessions/{session_id}/browser-tools/invoke",
                json={"tool_name": "click", "params": {"selector": "div"}},
            )

        async def observe_and_resolve():
            queue = app.state.broker.get_or_create_queue(session_id)
            event = await asyncio.wait_for(queue.get(), timeout=5.0)

            # Observe state while invoke is blocked
            observed_states.append(
                app.state.session_store.is_browser_controlling(session_id)
            )

            inv_id = event["inv_id"]
            await client.post(
                f"/sessions/{session_id}/browser-tools/result/{inv_id}",
                json={"inv_id": inv_id, "success": True, "result": {}},
            )

        await asyncio.gather(invoke(), observe_and_resolve())

        assert observed_states == [True]

    async def test_controlling_false_after_completion(self, client: AsyncClient):
        """browser_controlling resets to False after invoke completes."""
        session_id = "it-ctrl-after"
        _seed_session(session_id)

        async def invoke():
            return await client.post(
                f"/sessions/{session_id}/browser-tools/invoke",
                json={"tool_name": "click", "params": {"selector": "div"}},
            )

        async def resolve():
            queue = app.state.broker.get_or_create_queue(session_id)
            event = await asyncio.wait_for(queue.get(), timeout=5.0)
            inv_id = event["inv_id"]
            await client.post(
                f"/sessions/{session_id}/browser-tools/result/{inv_id}",
                json={"inv_id": inv_id, "success": True, "result": {}},
            )

        await asyncio.gather(invoke(), resolve())

        assert app.state.session_store.is_browser_controlling(session_id) is False

    async def test_browser_status_endpoint_reflects_state(self, client: AsyncClient):
        """GET /sessions/{id}/browser-status returns current controlling state."""
        session_id = "it-status-ep"

        # Before any invocation: False
        resp = await client.get(f"/sessions/{session_id}/browser-status")
        assert resp.status_code == 200
        assert resp.json()["browser_controlling"] is False

        # Manually set to True
        app.state.session_store.set_browser_controlling(session_id, True)

        resp = await client.get(f"/sessions/{session_id}/browser-status")
        assert resp.json()["browser_controlling"] is True


# ---------------------------------------------------------------------------
# Session + browser tool interaction
# ---------------------------------------------------------------------------


class TestSessionBrowserToolInteraction:
    """Interactions between session lifecycle and browser tools."""

    async def test_create_session_then_invoke_tool(self, client: AsyncClient):
        """Full flow: create session via API, then invoke a browser tool."""
        # Create session via API
        create_resp = await client.post("/sessions")
        session_id = create_resp.json()["session_id"]

        # Simulate Extension connecting (in real life, Extension calls GET /commands)
        app.state.session_store.increment_sse_subscribers(session_id)

        async def invoke():
            return await client.post(
                f"/sessions/{session_id}/browser-tools/invoke",
                json={"tool_name": "get_text", "params": {"selector": "h1"}},
            )

        async def resolve():
            queue = app.state.broker.get_or_create_queue(session_id)
            event = await asyncio.wait_for(queue.get(), timeout=5.0)
            inv_id = event["inv_id"]
            await client.post(
                f"/sessions/{session_id}/browser-tools/result/{inv_id}",
                json={
                    "inv_id": inv_id,
                    "success": True,
                    "result": {"text": "Welcome"},
                },
            )

        invoke_resp, _ = await asyncio.gather(invoke(), resolve())

        assert invoke_resp.status_code == 200
        assert invoke_resp.json()["result"]["text"] == "Welcome"

    async def test_delete_session_cleans_up_broker(self, client: AsyncClient):
        """Deleting a session clears pending invocations in the broker."""
        create_resp = await client.post("/sessions")
        session_id = create_resp.json()["session_id"]

        # Create a pending invocation directly in the broker
        app.state.broker.create_invocation(session_id, "pending-inv-001")

        assert app.state.broker.has_active_invocations(session_id) is True

        # Delete session via API
        await client.delete(f"/sessions/{session_id}")

        assert app.state.broker.has_active_invocations(session_id) is False
