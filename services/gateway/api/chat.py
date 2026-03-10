"""Chat endpoints -- synchronous and streaming proxy to Orchestrator via acp_sdk."""

import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from acp_sdk.client import Client
from acp_sdk.models import Message, MessagePart, MessagePartEvent, RunCompletedEvent

from api.deps import (
    CurrentUser,
    get_acp,
    get_session_store,
    verify_session_owner,
)
from core.session_store import SessionStore
from models import ChatRequest
from shared.models.session import Session

logger = logging.getLogger(__name__)

router = APIRouter()


def _build_acp_input(session_id: str, user_text: str, images: list | None = None) -> list[Message]:
    """Build ACP message list with session_id and user text."""
    parts = [
        MessagePart(content=session_id, content_type="text/x-session-id"),
        MessagePart(content=user_text, content_type="text/plain"),
    ]
    # TODO: handle images if needed (base64 encode into MessagePart)
    return [Message(role="user", parts=parts)]


@router.post("/sessions/{session_id}/chat")
async def chat(
    session_id: str,
    body: ChatRequest,
    user: CurrentUser,
    store: SessionStore = Depends(get_session_store),
    acp: Client = Depends(get_acp),
) -> JSONResponse:
    """Proxy a single chat turn to the Orchestrator (synchronous ACP run)."""
    session = store.get(session_id)
    if session is None:
        session = Session(session_id=session_id, user_id=user["sub"])
        store.set(session)
        logger.info("Auto-recreated session %s (user=%s)", session_id, user["sub"])
    verify_session_owner(session, user)

    acp_input = _build_acp_input(session_id, body.content, body.images)

    try:
        run = await acp.run_sync(input=acp_input, agent="orchestrator")
    except Exception as exc:
        logger.error("Orchestrator call failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Orchestrator is unavailable",
        ) from exc

    session.last_activity = datetime.now(timezone.utc)
    store.set(session)

    # Extract text from Run.output (list[Message])
    full_text = ""
    for message in run.output:
        for part in message.parts:
            if part.content_type == "text/plain" and part.content:
                full_text += part.content

    return JSONResponse(content={"response": full_text, "status": run.status.value})


@router.get("/sessions/{session_id}/chat/stream")
async def chat_stream(
    session_id: str,
    request: Request,
    user: CurrentUser,
    content: str = Query(..., description="User message to send"),
    store: SessionStore = Depends(get_session_store),
    acp: Client = Depends(get_acp),
) -> EventSourceResponse:
    """Stream orchestrator response tokens to the client via SSE."""
    session = store.get(session_id)
    if session is None:
        session = Session(session_id=session_id, user_id=user["sub"])
        store.set(session)
        logger.info("Auto-recreated session %s (user=%s)", session_id, user["sub"])
    verify_session_owner(session, user)

    acp_input = _build_acp_input(session_id, content)

    async def _event_generator():
        done_emitted = False
        try:
            async for event in acp.run_stream(input=acp_input, agent="orchestrator"):
                if isinstance(event, MessagePartEvent):
                    part = event.part
                    if part.content_type == "text/plain" and part.content:
                        yield {"data": json.dumps({"type": "token", "content": part.content}, ensure_ascii=False)}
                    elif part.content_type == "application/x-tool-event" and part.content:
                        try:
                            tool_data = json.loads(part.content)
                            yield {"data": json.dumps(tool_data, ensure_ascii=False)}
                        except json.JSONDecodeError:
                            pass
                elif isinstance(event, RunCompletedEvent):
                    yield {"data": json.dumps({"type": "done"}, ensure_ascii=False)}
                    done_emitted = True
            if not done_emitted:
                yield {"data": json.dumps({"type": "done"}, ensure_ascii=False)}
        except Exception as exc:
            logger.error("Orchestrator stream error: %s", exc, exc_info=True)
            yield {
                "data": json.dumps({"type": "error", "error": "Orchestrator stream failed"})
            }
        finally:
            session.last_activity = datetime.now(timezone.utc)
            store.set(session)

    return EventSourceResponse(_event_generator())
