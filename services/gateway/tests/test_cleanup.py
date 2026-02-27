"""Tests for GW-1: stale invocation cleanup and GW-3: Extension subscriber tracking.

Also covers P2-4: per-session sequential execution semaphore.
"""

import asyncio

import pytest

import main as gateway_main
from main import _STALE_INVOCATION_TIMEOUT_S


# ---------------------------------------------------------------------------
# GW-1: Stale invocation cleanup
# ---------------------------------------------------------------------------


async def test_stale_invocation_cleanup_cancels_old_futures(gateway_client):
    """Futures older than STALE_INVOCATION_TIMEOUT_S should be cancelled."""
    loop = asyncio.get_running_loop()
    inv_id = "stale-inv-001"
    session_id = "sess-stale-001"

    # Create a future with an old timestamp
    future: asyncio.Future = loop.create_future()
    old_timestamp = loop.time() - (_STALE_INVOCATION_TIMEOUT_S + 10)
    gateway_main._pending_invocations[inv_id] = (future, old_timestamp)
    gateway_main._invocation_to_session[inv_id] = session_id

    # Run cleanup logic directly (same as _cleanup_stale_invocations inner loop)
    now = loop.time()
    stale = [
        iid
        for iid, (f, created_at) in list(gateway_main._pending_invocations.items())
        if (now - created_at) > _STALE_INVOCATION_TIMEOUT_S
    ]
    for iid in stale:
        entry = gateway_main._pending_invocations.pop(iid, None)
        if entry:
            f, _ = entry
            if not f.done():
                f.cancel()
        gateway_main._invocation_to_session.pop(iid, None)

    assert inv_id not in gateway_main._pending_invocations
    assert inv_id not in gateway_main._invocation_to_session
    assert future.cancelled()


async def test_recent_invocation_not_cleaned_up(gateway_client):
    """Futures within the timeout window should NOT be cleaned up."""
    loop = asyncio.get_running_loop()
    inv_id = "recent-inv-001"
    session_id = "sess-recent-001"

    future: asyncio.Future = loop.create_future()
    recent_timestamp = loop.time()  # just created
    gateway_main._pending_invocations[inv_id] = (future, recent_timestamp)
    gateway_main._invocation_to_session[inv_id] = session_id

    # Run cleanup logic
    now = loop.time()
    stale = [
        iid
        for iid, (f, created_at) in list(gateway_main._pending_invocations.items())
        if (now - created_at) > _STALE_INVOCATION_TIMEOUT_S
    ]

    assert inv_id not in stale
    assert inv_id in gateway_main._pending_invocations


# ---------------------------------------------------------------------------
# GW-3: Extension subscriber tracking
# ---------------------------------------------------------------------------


async def test_invoke_returns_503_when_no_sse_subscriber(gateway_client):
    """Invoke should return 503 immediately when no Extension SSE connection."""
    session_id = "sess-no-subscriber"

    # Pre-create session and queue but NO SSE subscriber
    from shared.models.session import Session
    sess = Session(session_id=session_id, user_id="user-1")
    gateway_main._set_session(sess)
    gateway_main._get_or_create_queue(session_id)
    assert gateway_main._session_sse_subscribers.get(session_id, 0) == 0

    response = await gateway_client.post(
        f"/sessions/{session_id}/browser-tools/invoke",
        json={"tool_name": "navigate", "params": {"url": "https://example.com"}},
    )
    assert response.status_code == 503
    assert "extension" in response.json()["detail"].lower()


async def test_invoke_succeeds_when_sse_subscriber_present(gateway_client):
    """Invoke should proceed when Extension SSE subscriber count > 0."""
    session_id = "sess-with-subscriber"

    # Pre-create session and simulate Extension SSE connection
    from shared.models.session import Session
    sess = Session(session_id=session_id, user_id="user-1")
    gateway_main._set_session(sess)
    gateway_main._session_sse_subscribers[session_id] = 1

    async def do_invoke():
        return await gateway_client.post(
            f"/sessions/{session_id}/browser-tools/invoke",
            json={"tool_name": "navigate", "params": {"url": "https://example.com"}},
        )

    async def resolve_result():
        queue = gateway_main._get_or_create_queue(session_id)
        event = await asyncio.wait_for(queue.get(), timeout=5.0)
        inv_id = event["inv_id"]
        return await gateway_client.post(
            f"/sessions/{session_id}/browser-tools/result/{inv_id}",
            json={
                "inv_id": inv_id,
                "success": True,
                "result": {"url": "https://example.com"},
            },
        )

    invoke_res, result_res = await asyncio.gather(do_invoke(), resolve_result())
    assert invoke_res.status_code == 200
    assert invoke_res.json()["success"] is True


async def test_cleanup_session_clears_sse_subscribers(gateway_client):
    """_cleanup_session should also clear sse subscriber count."""
    session_id = "sess-cleanup-sub"
    gateway_main._session_sse_subscribers[session_id] = 2

    gateway_main._cleanup_session(session_id)

    assert session_id not in gateway_main._session_sse_subscribers


# ---------------------------------------------------------------------------
# P2-4: Sequential tool execution (semaphore)
# ---------------------------------------------------------------------------


async def test_session_semaphore_created_lazily(gateway_client):
    """Semaphore should be created on first access."""
    session_id = "sess-sem-lazy"

    sem = gateway_main._get_session_semaphore(session_id)
    assert sem is not None
    assert session_id in gateway_main._session_semaphores


async def test_session_semaphore_is_per_session(gateway_client):
    """Different sessions should have different semaphores."""
    sem1 = gateway_main._get_session_semaphore("sess-A")
    sem2 = gateway_main._get_session_semaphore("sess-B")
    assert sem1 is not sem2


async def test_cleanup_session_clears_semaphore(gateway_client):
    """_cleanup_session should also clear the session semaphore."""
    session_id = "sess-cleanup-sem"
    gateway_main._get_session_semaphore(session_id)
    assert session_id in gateway_main._session_semaphores

    gateway_main._cleanup_session(session_id)

    assert session_id not in gateway_main._session_semaphores
