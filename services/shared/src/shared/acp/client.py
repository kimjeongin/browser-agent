"""ACP HTTP client for invoking remote agent services."""

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class ACPClient:
    """Asynchronous HTTP client for Agent Communication Protocol endpoints.

    Supports both synchronous ``/runs`` and streaming ``/runs/stream``
    invocations.

    Use ``start()`` / ``close()`` (or async context manager) to manage the
    underlying connection pool.  When used without ``start()``, a short-lived
    client is created per request (backwards-compatible but less efficient).
    """

    def __init__(self, base_url: str, timeout: float = 120.0) -> None:
        """Initialise the client.

        Args:
            base_url: Root URL of the ACP agent server
                      (e.g. ``http://browser-agent:8000``).
            timeout: Default request timeout in seconds.
        """
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        """Create the shared connection pool.  Call once during app lifespan."""
        self._client = httpx.AsyncClient(timeout=self._timeout)

    async def close(self) -> None:
        """Close the shared connection pool."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "ACPClient":
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        # Fallback: create a temporary client (no pooling)
        return httpx.AsyncClient(timeout=self._timeout)

    async def run(
        self,
        thread_id: str,
        input: dict[str, Any],
        *,
        run_id: str = "",
    ) -> dict[str, Any]:
        """Execute a synchronous run against the agent.

        Args:
            thread_id: Conversation / session thread identifier.
            input: Arbitrary input payload forwarded to the agent graph.
            run_id: Optional caller-assigned run identifier.

        Returns:
            The ``RunResponse`` dict containing ``run_id``, ``status``,
            ``output``, and optionally ``error``.
        """
        payload = {"run_id": run_id, "thread_id": thread_id, "input": input}

        client = self._get_client()
        owns_client = self._client is None
        try:
            response = await client.post(
                f"{self._base_url}/runs",
                json=payload,
            )
            response.raise_for_status()
            return response.json()
        finally:
            if owns_client:
                await client.aclose()

    async def run_stream(
        self,
        thread_id: str,
        input: dict[str, Any],
        *,
        run_id: str = "",
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Execute a streaming run, yielding SSE event dicts.

        Each yielded dict has at least a ``type`` field
        (``"token"``, ``"tool_start"``, ``"done"``, etc.).

        Args:
            thread_id: Conversation / session thread identifier.
            input: Arbitrary input payload forwarded to the agent graph.
            run_id: Optional caller-assigned run identifier.

        Yields:
            Parsed SSE event dicts.
        """
        payload = {"run_id": run_id, "thread_id": thread_id, "input": input}

        client = self._get_client()
        owns_client = self._client is None
        try:
            async with client.stream(
                "POST",
                f"{self._base_url}/runs/stream",
                json=payload,
            ) as response:
                response.raise_for_status()

                buffer = ""
                async for chunk in response.aiter_text():
                    buffer += chunk
                    while "\n\n" in buffer:
                        event_text, buffer = buffer.split("\n\n", 1)
                        event_text = event_text.strip()
                        if not event_text:
                            continue

                        # Parse SSE "data: {...}" lines
                        for line in event_text.splitlines():
                            if line.startswith("data: "):
                                data_str = line[len("data: "):]
                                try:
                                    yield json.loads(data_str)
                                except json.JSONDecodeError:
                                    logger.warning(
                                        "Failed to parse SSE data: %s",
                                        data_str,
                                    )
        finally:
            if owns_client:
                await client.aclose()
