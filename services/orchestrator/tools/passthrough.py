"""Session-scoped asyncio.Queue registry for streaming pass-through.

Each active orchestrator session registers a queue so that sub-agent tool
wrappers can push intermediate ``MessagePart`` events (browser progress,
chat tokens) back to the ACP response stream in real time.
"""

import asyncio

from acp_sdk.models import MessagePart

# session_id -> Queue mapping; only populated during active requests.
_queues: dict[str, asyncio.Queue[MessagePart | None]] = {}

# session_ids where a sub-agent has already streamed text to the queue.
# Used by main.py to suppress the Supervisor's redundant relay message.
_text_streamed: set[str] = set()


def register(session_id: str, q: asyncio.Queue[MessagePart | None]) -> None:
    """Register a pass-through queue for *session_id*."""
    _queues[session_id] = q


def unregister(session_id: str) -> None:
    """Remove the pass-through queue for *session_id*."""
    _queues.pop(session_id, None)
    _text_streamed.discard(session_id)


def get(session_id: str) -> asyncio.Queue[MessagePart | None] | None:
    """Return the pass-through queue for *session_id*, or ``None``."""
    return _queues.get(session_id)


def mark_text_streamed(session_id: str) -> None:
    """Record that a sub-agent has streamed text for *session_id*."""
    _text_streamed.add(session_id)


def was_text_streamed(session_id: str) -> bool:
    """Return True if any sub-agent streamed text for *session_id*."""
    return session_id in _text_streamed
