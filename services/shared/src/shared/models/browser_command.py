"""Browser command and result models."""

from typing import Any, Literal

from pydantic import BaseModel


class BrowserCommand(BaseModel):
    """A command to be executed by the browser extension."""

    command_id: str
    session_id: str
    action: Literal[
        "navigate",
        "click",
        "type",
        "scroll",
        "screenshot",
        "extract_content",
        "wait_for_element",
        "evaluate_js",
        "get_page_info",
    ]
    params: dict[str, Any]


class CommandResult(BaseModel):
    """Result returned by the browser extension after executing a command."""

    command_id: str
    success: bool
    result: Any = None
    error: str | None = None
    screenshot: str | None = None  # base64-encoded PNG
