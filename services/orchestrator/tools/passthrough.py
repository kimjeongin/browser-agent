"""Session-scoped asyncio.Queue registry for streaming pass-through.

Each active orchestrator session registers a queue so that sub-agent tool
wrappers can push intermediate ``MessagePart`` events (browser progress,
chat tokens) back to the ACP response stream in real time.
"""

import asyncio

from acp_sdk.models import MessagePart

# session_id -> Queue mapping; only populated during active requests.
_queues: dict[str, asyncio.Queue[MessagePart | None]] = {}


def register(session_id: str, q: asyncio.Queue[MessagePart | None]) -> None:
    """Register a pass-through queue for *session_id*."""
    _queues[session_id] = q


def unregister(session_id: str) -> None:
    """Remove the pass-through queue for *session_id*."""
    _queues.pop(session_id, None)


def get(session_id: str) -> asyncio.Queue[MessagePart | None] | None:
    """Return the pass-through queue for *session_id*, or ``None``."""
    return _queues.get(session_id)
