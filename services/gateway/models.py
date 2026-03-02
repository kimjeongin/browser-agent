"""Request and response models for Gateway endpoints."""

from typing import Any

from pydantic import BaseModel


class ChatRequest(BaseModel):
    """Incoming chat message from the extension."""

    content: str
    images: list[str] = []


class SessionResponse(BaseModel):
    """Public representation of a session."""

    session_id: str
    user_id: str
    status: str
    browser_controlling: bool = False


class BrowserToolInvokeRequest(BaseModel):
    """Browser tool invocation request from Browser Agent."""

    tool_name: str
    params: dict[str, Any]


class BrowserToolResultRequest(BaseModel):
    """Browser tool execution result from Extension."""

    inv_id: str
    success: bool
    result: Any = None
    error: str | None = None
