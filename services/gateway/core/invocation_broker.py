"""asyncio.Queue + asyncio.Future based browser tool invocation broker."""

import asyncio
import logging

logger = logging.getLogger(__name__)

_STALE_INVOCATION_TIMEOUT_S: float = 120.0


class InvocationBroker:
    """Manages browser tool invocations between Browser Agent and Extension.

    Uses asyncio.Queue for command dispatch (SSE -> Extension) and
    asyncio.Future for result awaiting (Browser Agent blocks).
    """

    def __init__(self, queue_maxsize: int = 100) -> None:
        self._queues: dict[str, asyncio.Queue] = {}
        self._pending: dict[str, tuple[asyncio.Future, float]] = {}
        self._inv_to_session: dict[str, str] = {}
        self._queue_maxsize = queue_maxsize

    def get_or_create_queue(self, session_id: str) -> asyncio.Queue:
        if session_id not in self._queues:
            self._queues[session_id] = asyncio.Queue(maxsize=self._queue_maxsize)
        return self._queues[session_id]

    def create_invocation(self, session_id: str, inv_id: str) -> asyncio.Future:
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending[inv_id] = (future, loop.time())
        self._inv_to_session[inv_id] = session_id
        return future

    def resolve_invocation(self, inv_id: str, result: dict) -> bool:
        entry = self._pending.get(inv_id)
        if entry is None:
            return False
        future, _ = entry
        if future.done():
            # Already resolved — idempotent handling for Extension retries
            logger.warning("resolve_invocation called on already-done future: inv_id=%s", inv_id)
            return True
        future.set_result(result)
        # Clean up immediately to minimize done-but-still-in-dict window
        self._pending.pop(inv_id, None)
        self._inv_to_session.pop(inv_id, None)
        return True

    def cleanup_invocation(self, inv_id: str) -> None:
        self._pending.pop(inv_id, None)
        self._inv_to_session.pop(inv_id, None)

    def get_pending_entry(self, inv_id: str):
        return self._pending.get(inv_id)

    def has_active_invocations(self, session_id: str) -> bool:
        return any(v == session_id for v in self._inv_to_session.values())

    def cleanup_session(self, session_id: str) -> None:
        stale = [inv_id for inv_id, sid in self._inv_to_session.items() if sid == session_id]
        for inv_id in stale:
            entry = self._pending.pop(inv_id, None)
            if entry:
                future, _ = entry
                if not future.done():
                    future.cancel()
            self._inv_to_session.pop(inv_id, None)
        self._queues.pop(session_id, None)

    async def run_stale_cleanup(self) -> None:
        """Periodically clean up invocations that never received a result."""
        while True:
            await asyncio.sleep(60)
            try:
                now = asyncio.get_running_loop().time()
                stale = [
                    inv_id for inv_id, (future, created_at) in list(self._pending.items())
                    if (now - created_at) > _STALE_INVOCATION_TIMEOUT_S
                ]
                for inv_id in stale:
                    entry = self._pending.pop(inv_id, None)
                    if entry:
                        future, _ = entry
                        if not future.done():
                            future.cancel()
                    self._inv_to_session.pop(inv_id, None)
                if stale:
                    logger.warning("Cleaned up %d stale invocations", len(stale))
            except Exception:
                logger.exception("Error in stale invocation cleanup")
