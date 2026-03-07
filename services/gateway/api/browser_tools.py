"""Browser tool invocation and result endpoints.

webMCP-inspired 3-hop browser tool flow:
  Browser Agent POST /sessions/{id}/browser-tools/invoke (blocking, 60s)
    -> asyncio.Queue -> SSE /sessions/{id}/commands -> Extension
    -> Extension executes DOM -> POST /sessions/{id}/browser-tools/result/{inv_id}
    -> asyncio.Future.set_result() -> Browser Agent response returned
"""

import asyncio
import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from api.deps import CurrentUser, get_broker, get_session_store, get_settings, verify_session_owner
from core.invocation_broker import InvocationBroker
from core.session_store import SessionStore
from models import BrowserToolInvokeRequest, BrowserToolResultRequest

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/sessions/{session_id}/commands")
async def browser_command_stream(
    session_id: str,
    request: Request,
    user: CurrentUser,
    store: SessionStore = Depends(get_session_store),
    broker: InvocationBroker = Depends(get_broker),
) -> EventSourceResponse:
    """SSE stream delivering browser tool invocations to the Extension."""
    session = store.get(session_id)
    if session is not None:
        verify_session_owner(session, user)

    queue = broker.get_or_create_queue(session_id)

    async def _command_generator():
        # Track subscriber count
        count = store.increment_sse_subscribers(session_id)
        logger.info(
            "Extension SSE connected for session %s (subscribers=%d)",
            session_id,
            count,
        )
        try:
            while True:
                if await request.is_disconnected():
                    break

                try:
                    # Wait up to 15s for a command, then send keepalive
                    item = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield {"data": json.dumps(item, ensure_ascii=False)}
                    queue.task_done()
                except asyncio.TimeoutError:
                    yield {"comment": "keepalive"}
        finally:
            count = store.decrement_sse_subscribers(session_id)
            logger.info(
                "Extension SSE disconnected for session %s (subscribers=%d)",
                session_id,
                count,
            )

    return EventSourceResponse(_command_generator())


@router.post("/sessions/{session_id}/browser-tools/invoke")
async def invoke_browser_tool(
    session_id: str,
    body: BrowserToolInvokeRequest,
    request: Request,
    store: SessionStore = Depends(get_session_store),
    broker: InvocationBroker = Depends(get_broker),
) -> JSONResponse:
    """Invoke a browser tool and wait for the Extension's result.

    Called by the Browser Agent. Blocks until the Extension posts the result
    (or timeout after configured seconds).
    """
    # Verify session exists
    session = store.get(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found",
        )

    # Wait for Extension SSE to connect (Chrome SW may have been suspended
    # during LLM processing and needs a moment to reconnect).
    if store.get_sse_subscribers(session_id) == 0:
        for _ in range(20):  # up to 10 seconds
            await asyncio.sleep(0.5)
            if store.get_sse_subscribers(session_id) > 0:
                break
        else:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Browser extension is not connected for this session. "
                    "Please ensure the extension is active and connected."
                ),
            )

    settings = request.app.state.settings

    # Acquire per-session semaphore (serializes concurrent tool calls)
    sem = store.get_semaphore(session_id)
    async with sem:
        inv_id = str(uuid.uuid4())
        queue = broker.get_or_create_queue(session_id)

        # Create future BEFORE enqueuing to avoid race condition
        future = broker.create_invocation(session_id, inv_id)

        # Mark session as actively controlling the browser
        store.set_browser_controlling(session_id, True)

        # Enqueue the tool invocation for SSE delivery to Extension
        invocation = {
            "inv_id": inv_id,
            "tool_name": body.tool_name,
            "params": body.params,
        }
        await queue.put(invocation)

        logger.info(
            "Browser tool enqueued: inv_id=%s tool=%s session=%s",
            inv_id,
            body.tool_name,
            session_id,
        )

        try:
            # Block until Extension posts result
            result = await asyncio.wait_for(
                future, timeout=settings.browser_tool_timeout
            )
            logger.info(
                "Browser tool completed: inv_id=%s success=%s",
                inv_id,
                result.get("success"),
            )
            return JSONResponse(content=result)
        except asyncio.TimeoutError:
            logger.warning(
                "Browser tool timed out: inv_id=%s tool=%s session=%s",
                inv_id,
                body.tool_name,
                session_id,
            )
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail=f"Browser tool '{body.tool_name}' timed out after {settings.browser_tool_timeout}s",
            )
        finally:
            broker.cleanup_invocation(inv_id)
            # Clear controlling status if no more pending invocations for session
            if not broker.has_active_invocations(session_id):
                store.set_browser_controlling(session_id, False)


@router.post("/sessions/{session_id}/browser-tools/result/{inv_id}")
async def receive_browser_tool_result(
    session_id: str,
    inv_id: str,
    body: BrowserToolResultRequest,
    request: Request,
    broker: InvocationBroker = Depends(get_broker),
) -> dict[str, Any]:
    """Receive the browser tool execution result from the Extension."""
    result_payload = {
        "success": body.success,
        "result": body.result,
        "error": body.error,
        "inv_id": inv_id,
    }

    resolved = broker.resolve_invocation(inv_id, result_payload)
    if not resolved:
        logger.warning(
            "Received result for unknown/expired inv_id=%s session=%s",
            inv_id,
            session_id,
        )
        return {"ok": False, "reason": "invocation not found or already expired"}

    logger.debug(
        "Result delivered: inv_id=%s session=%s success=%s",
        inv_id,
        session_id,
        body.success,
    )
    return {"ok": True}


@router.get("/sessions/{session_id}/browser-status")
async def get_browser_status(
    session_id: str,
    request: Request,
    store: SessionStore = Depends(get_session_store),
) -> dict[str, Any]:
    """Return current browser control status for a session."""
    return {
        "session_id": session_id,
        "browser_controlling": store.is_browser_controlling(session_id),
    }
