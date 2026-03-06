"""Chat endpoints -- synchronous and streaming proxy to Orchestrator."""

import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from api.deps import (
    CurrentUser,
    get_acp,
    get_session_store,
    verify_session_owner,
)
from core.session_store import SessionStore
from models import ChatRequest
from shared.acp.client import ACPClient
from shared.models.session import Session

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/sessions/{session_id}/chat")
async def chat(
    session_id: str,
    body: ChatRequest,
    user: CurrentUser,
    store: SessionStore = Depends(get_session_store),
    acp: ACPClient = Depends(get_acp),
) -> JSONResponse:
    """Proxy a single chat turn to the Orchestrator (synchronous ACP run)."""
    session = store.get(session_id)
    if session is None:
        # Auto-recreate session for authenticated user (e.g. after Gateway restart)
        session = Session(session_id=session_id, user_id=user["sub"])
        store.set(session)
        logger.info("Auto-recreated session %s (user=%s)", session_id, user["sub"])
    verify_session_owner(session, user)

    messages: list[dict[str, Any]] = [
        {"role": "human", "content": body.content},
    ]
    if body.images:
        messages[0]["images"] = body.images

    acp_input: dict[str, Any] = {
        "messages": messages,
        "session_id": session_id,
    }

    try:
        result = await acp.run(thread_id=session_id, input=acp_input)
    except Exception as exc:
        logger.error("Orchestrator call failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Orchestrator is unavailable",
        ) from exc

    session.last_activity = datetime.now(timezone.utc)
    store.set(session)

    return JSONResponse(content=result)


@router.get("/sessions/{session_id}/chat/stream")
async def chat_stream(
    session_id: str,
    request: Request,
    user: CurrentUser,
    content: str = Query(..., description="User message to send"),
    store: SessionStore = Depends(get_session_store),
    acp: ACPClient = Depends(get_acp),
) -> EventSourceResponse:
    """Stream orchestrator response tokens to the client via SSE."""
    session = store.get(session_id)
    if session is None:
        # Auto-recreate session for authenticated user (e.g. after Gateway restart)
        session = Session(session_id=session_id, user_id=user["sub"])
        store.set(session)
        logger.info("Auto-recreated session %s (user=%s)", session_id, user["sub"])
    verify_session_owner(session, user)

    acp_input: dict[str, Any] = {
        "messages": [{"role": "human", "content": content}],
        "session_id": session_id,
    }

    async def _event_generator():
        try:
            async for event in acp.run_stream(
                thread_id=session_id, input=acp_input
            ):
                yield {"data": json.dumps(event, ensure_ascii=False)}
        except Exception as exc:
            logger.error(
                "Orchestrator stream error: %s", exc, exc_info=True
            )
            yield {
                "data": json.dumps(
                    {"type": "error", "error": "Orchestrator stream failed"}
                )
            }
        finally:
            session.last_activity = datetime.now(timezone.utc)
            store.set(session)

    return EventSourceResponse(_event_generator())
