"""In-memory session store with TTL-based lazy expiry."""

import asyncio
import time

from shared.models.session import Session


class SessionStore:
    """Thread-safe in-memory session store with lazy TTL expiry.

    NOTE: State is process-local. For horizontal scaling, migrate to an
    external store (e.g. Redis) and replace asyncio primitives with
    distributed equivalents.
    """

    def __init__(self, ttl_seconds: int = 86400) -> None:
        self._sessions: dict[str, Session] = {}
        self._expires_at: dict[str, float] = {}
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._sse_subscribers: dict[str, int] = {}
        self._browser_controlling: dict[str, bool] = {}
        self._ttl_seconds = ttl_seconds

    def set(self, session: Session) -> None:
        self._sessions[session.session_id] = session
        self._expires_at[session.session_id] = time.monotonic() + self._ttl_seconds

    def get(self, session_id: str) -> Session | None:
        session = self._sessions.get(session_id)
        expires_at = self._expires_at.get(session_id)
        if session is None or (expires_at is not None and time.monotonic() > expires_at):
            self._evict(session_id)
            return None
        return session

    def _evict(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
        self._expires_at.pop(session_id, None)

    def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
        self._expires_at.pop(session_id, None)
        self._semaphores.pop(session_id, None)
        self._sse_subscribers.pop(session_id, None)
        self._browser_controlling.pop(session_id, None)

    def get_semaphore(self, session_id: str) -> asyncio.Semaphore:
        if session_id not in self._semaphores:
            self._semaphores[session_id] = asyncio.Semaphore(1)
        return self._semaphores[session_id]

    def get_sse_subscribers(self, session_id: str) -> int:
        return self._sse_subscribers.get(session_id, 0)

    def increment_sse_subscribers(self, session_id: str) -> int:
        count = self._sse_subscribers.get(session_id, 0) + 1
        self._sse_subscribers[session_id] = count
        return count

    def decrement_sse_subscribers(self, session_id: str) -> int:
        count = max(0, self._sse_subscribers.get(session_id, 1) - 1)
        self._sse_subscribers[session_id] = count
        return count

    def set_browser_controlling(self, session_id: str, controlling: bool) -> None:
        self._browser_controlling[session_id] = controlling

    def is_browser_controlling(self, session_id: str) -> bool:
        return self._browser_controlling.get(session_id, False)

    def active_session_count(self) -> int:
        return len(self._sessions)
