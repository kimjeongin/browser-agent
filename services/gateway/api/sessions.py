"""Session CRUD endpoints."""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, status

from api.deps import (
    CurrentUser,
    get_broker,
    get_session_store,
    verify_session_owner,
)
from core.invocation_broker import InvocationBroker
from core.session_store import SessionStore
from models import SessionResponse
from shared.models.session import Session

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(
    user: CurrentUser,
    store: SessionStore = Depends(get_session_store),
    broker: InvocationBroker = Depends(get_broker),
) -> SessionResponse:
    """Create a new session for the authenticated user."""
    user_id: str = user["sub"]

    session = Session(
        session_id=uuid.uuid4().hex,
        user_id=user_id,
    )

    store.set(session)

    # Pre-create the command queue for this session
    broker.get_or_create_queue(session.session_id)

    logger.info("Session created: %s (user=%s)", session.session_id, user_id)
    return SessionResponse(
        session_id=session.session_id,
        user_id=session.user_id,
        status=session.status,
        browser_controlling=False,
    )


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    user: CurrentUser,
    store: SessionStore = Depends(get_session_store),
) -> SessionResponse:
    """Return session metadata."""
    session = store.get(session_id)
    if session is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    verify_session_owner(session, user)

    return SessionResponse(
        session_id=session.session_id,
        user_id=session.user_id,
        status=session.status,
        browser_controlling=store.is_browser_controlling(session_id),
    )


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    user: CurrentUser,
    store: SessionStore = Depends(get_session_store),
    broker: InvocationBroker = Depends(get_broker),
) -> dict[str, bool]:
    """Mark a session as inactive."""
    session = store.get(session_id)
    if session is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    verify_session_owner(session, user)

    session.status = "inactive"
    session.last_activity = datetime.now(timezone.utc)
    store.set(session)

    broker.cleanup_session(session_id)
    store.delete(session_id)

    logger.info("Session deactivated: %s", session_id)
    return {"ok": True}
