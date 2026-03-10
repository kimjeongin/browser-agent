"""FastAPI dependencies for Gateway."""

from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status

from core.session_store import SessionStore
from core.invocation_broker import InvocationBroker
from acp_sdk.client import Client
from shared.auth.dependencies import get_current_user
from shared.models.session import Session

CurrentUser = Annotated[dict[str, Any], Depends(get_current_user)]


def get_session_store(request: Request) -> SessionStore:
    return request.app.state.session_store


def get_broker(request: Request) -> InvocationBroker:
    return request.app.state.broker


def get_acp(request: Request) -> Client:
    return request.app.state.acp


def get_settings(request: Request):
    return request.app.state.settings


def get_session_or_404(
    session_id: str,
    store: SessionStore = Depends(get_session_store),
) -> Session:
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return session


def verify_session_owner(session: Session, user: dict[str, Any]) -> None:
    if session.user_id != user.get("sub"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not own this session",
        )
