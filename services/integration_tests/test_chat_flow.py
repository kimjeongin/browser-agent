"""Integration tests for session lifecycle and chat proxy flow.

These tests verify the end-to-end interaction between:
- Session CRUD endpoints
- Chat proxy to Orchestrator (via mocked ACP client)
- SSE streaming chat responses
- Session ownership enforcement across users
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from conftest import AUTH_HEADER, OTHER_AUTH_HEADER, TEST_USER_ID
from main import app


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


class TestSessionLifecycle:
    """Full session create -> get -> delete flow."""

    async def test_create_and_retrieve_session(self, client: AsyncClient):
        """POST /sessions creates a session; GET /sessions/{id} retrieves it."""
        create_resp = await client.post("/sessions")
        assert create_resp.status_code == 201

        body = create_resp.json()
        session_id = body["session_id"]
        assert body["user_id"] == TEST_USER_ID
        assert body["status"] == "active"
        assert body["browser_controlling"] is False

        get_resp = await client.get(f"/sessions/{session_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["session_id"] == session_id
        assert get_resp.json()["user_id"] == TEST_USER_ID

    async def test_get_nonexistent_session_returns_404(self, client: AsyncClient):
        """GET /sessions/{id} with unknown ID returns 404."""
        resp = await client.get("/sessions/does-not-exist")
        assert resp.status_code == 404

    async def test_delete_session(self, client: AsyncClient):
        """DELETE /sessions/{id} deactivates the session."""
        create_resp = await client.post("/sessions")
        session_id = create_resp.json()["session_id"]

        del_resp = await client.delete(f"/sessions/{session_id}")
        assert del_resp.status_code == 200
        assert del_resp.json()["ok"] is True

        # Session should no longer be retrievable
        get_resp = await client.get(f"/sessions/{session_id}")
        assert get_resp.status_code == 404

    async def test_delete_nonexistent_session_returns_404(self, client: AsyncClient):
        """DELETE /sessions/{id} with unknown ID returns 404."""
        resp = await client.delete("/sessions/nonexistent")
        assert resp.status_code == 404

    async def test_multiple_sessions_are_independent(self, client: AsyncClient):
        """Each POST /sessions creates a distinct session."""
        resp1 = await client.post("/sessions")
        resp2 = await client.post("/sessions")

        id1 = resp1.json()["session_id"]
        id2 = resp2.json()["session_id"]
        assert id1 != id2

        # Deleting one does not affect the other
        await client.delete(f"/sessions/{id1}")
        get_resp = await client.get(f"/sessions/{id2}")
        assert get_resp.status_code == 200


# ---------------------------------------------------------------------------
# Session ownership
# ---------------------------------------------------------------------------


class TestSessionOwnership:
    """Cross-user ownership checks."""

    async def test_other_user_cannot_get_session(
        self, client: AsyncClient, other_client: AsyncClient
    ):
        """A session created by user A cannot be retrieved by user B."""
        create_resp = await client.post(
            "/sessions", headers=AUTH_HEADER
        )
        session_id = create_resp.json()["session_id"]

        get_resp = await other_client.get(f"/sessions/{session_id}")
        assert get_resp.status_code == 403

    async def test_other_user_cannot_delete_session(
        self, client: AsyncClient, other_client: AsyncClient
    ):
        """A session created by user A cannot be deleted by user B."""
        create_resp = await client.post(
            "/sessions", headers=AUTH_HEADER
        )
        session_id = create_resp.json()["session_id"]

        del_resp = await other_client.delete(f"/sessions/{session_id}")
        assert del_resp.status_code == 403


# ---------------------------------------------------------------------------
# Chat proxy (synchronous)
# ---------------------------------------------------------------------------


class TestChatProxy:
    """POST /sessions/{id}/chat proxies to Orchestrator via ACP."""

    async def test_chat_returns_orchestrator_response(self, client: AsyncClient):
        """Successful chat proxies Orchestrator result back to caller."""
        # Create session
        create_resp = await client.post("/sessions")
        session_id = create_resp.json()["session_id"]

        # Mock ACP client to return a canned response
        expected_result: dict[str, Any] = {
            "type": "response",
            "content": "Hello from the orchestrator!",
        }
        app.state.acp.run = AsyncMock(return_value=expected_result)

        chat_resp = await client.post(
            f"/sessions/{session_id}/chat",
            json={"content": "Hello, world!"},
        )
        assert chat_resp.status_code == 200
        assert chat_resp.json() == expected_result

        # Verify ACP was called with correct args
        app.state.acp.run.assert_called_once()
        call_kwargs = app.state.acp.run.call_args
        assert call_kwargs.kwargs["thread_id"] == session_id
        assert call_kwargs.kwargs["input"]["messages"][0]["content"] == "Hello, world!"

    async def test_chat_with_images(self, client: AsyncClient):
        """Chat request with images passes them through to orchestrator."""
        create_resp = await client.post("/sessions")
        session_id = create_resp.json()["session_id"]

        app.state.acp.run = AsyncMock(return_value={"type": "response", "content": "ok"})

        await client.post(
            f"/sessions/{session_id}/chat",
            json={"content": "Describe this", "images": ["data:image/png;base64,abc"]},
        )

        call_kwargs = app.state.acp.run.call_args
        assert "images" in call_kwargs.kwargs["input"]["messages"][0]

    async def test_chat_returns_502_when_orchestrator_fails(self, client: AsyncClient):
        """When Orchestrator raises, Gateway returns 502."""
        create_resp = await client.post("/sessions")
        session_id = create_resp.json()["session_id"]

        app.state.acp.run = AsyncMock(side_effect=ConnectionError("refused"))

        chat_resp = await client.post(
            f"/sessions/{session_id}/chat",
            json={"content": "Hello"},
        )
        assert chat_resp.status_code == 502

    async def test_chat_auto_creates_session_for_authenticated_user(
        self, client: AsyncClient
    ):
        """Chat endpoint auto-creates session if it does not exist yet."""
        app.state.acp.run = AsyncMock(return_value={"content": "auto-created"})

        # Call chat on a session that was never explicitly created
        chat_resp = await client.post(
            "/sessions/auto-sess-123/chat",
            json={"content": "Hi"},
        )
        assert chat_resp.status_code == 200

        # Session should now exist in store
        session = app.state.session_store.get("auto-sess-123")
        assert session is not None
        assert session.user_id == TEST_USER_ID


# ---------------------------------------------------------------------------
# Chat streaming (SSE)
# ---------------------------------------------------------------------------


class TestChatStream:
    """GET /sessions/{id}/chat/stream returns SSE events."""

    async def test_chat_stream_emits_events(self, client: AsyncClient):
        """SSE stream should emit events from orchestrator."""
        create_resp = await client.post("/sessions")
        session_id = create_resp.json()["session_id"]

        # Mock ACP streaming response
        async def _mock_stream(**kwargs):
            yield {"type": "token", "content": "Hello"}
            yield {"type": "token", "content": " world"}
            yield {"type": "done"}

        app.state.acp.run_stream = MagicMock(side_effect=_mock_stream)

        # Use httpx streaming to read SSE events
        events: list[str] = []
        async with client.stream(
            "GET",
            f"/sessions/{session_id}/chat/stream",
            params={"content": "Say hello"},
        ) as response:
            assert response.status_code == 200
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    events.append(line.removeprefix("data:").strip())

        assert len(events) >= 2
        parsed_first = json.loads(events[0])
        assert parsed_first["type"] == "token"
        assert parsed_first["content"] == "Hello"

    async def test_chat_stream_handles_orchestrator_error(self, client: AsyncClient):
        """SSE stream should emit error event when orchestrator fails."""
        create_resp = await client.post("/sessions")
        session_id = create_resp.json()["session_id"]

        async def _mock_failing_stream(**kwargs):
            yield {"type": "token", "content": "partial"}
            raise ConnectionError("stream broken")

        app.state.acp.run_stream = MagicMock(side_effect=_mock_failing_stream)

        events: list[str] = []
        async with client.stream(
            "GET",
            f"/sessions/{session_id}/chat/stream",
            params={"content": "Hello"},
        ) as response:
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    events.append(line.removeprefix("data:").strip())

        # Should contain the partial token and an error event
        assert len(events) >= 2
        error_events = [e for e in events if "error" in e]
        assert len(error_events) >= 1
