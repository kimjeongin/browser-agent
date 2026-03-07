"""HTTP client for invoking browser tools via the Gateway."""

from __future__ import annotations

from typing import Any

import httpx


class GatewayBrowserToolsClient:
    """HTTP client for invoking browser tools via the Gateway.

    The Gateway holds the asyncio.Queue -> SSE -> Extension pipeline.
    This client makes blocking POST requests that return only when the
    Extension has executed the tool and posted its result back.
    """

    def __init__(self, gateway_url: str, timeout: float = 65.0) -> None:
        self._base_url = gateway_url.rstrip("/")
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=self._timeout)

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def invoke(
        self,
        session_id: str,
        tool_name: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Invoke a browser tool and wait for the result.

        Raises:
            RuntimeError: If the tool execution fails or times out.
        """
        if self._client is None:
            raise RuntimeError("GatewayBrowserToolsClient not started")

        url = f"{self._base_url}/sessions/{session_id}/browser-tools/invoke"
        payload = {"tool_name": tool_name, "params": params}

        try:
            resp = await self._client.post(url, json=payload)
        except httpx.TimeoutException as e:
            raise RuntimeError(
                f"Browser tool '{tool_name}' timed out: {e}"
            ) from e

        if resp.status_code == 504:
            raise RuntimeError(
                f"Browser tool '{tool_name}' timed out at Gateway"
            )
        if not resp.is_success:
            raise RuntimeError(
                f"Browser tool '{tool_name}' failed: HTTP {resp.status_code} - {resp.text}"
            )

        data = resp.json()
        if not data.get("success"):
            error = data.get("error", "Unknown browser tool error")
            raise RuntimeError(f"Browser tool '{tool_name}' failed: {error}")

        return data.get("result", data)


# Module-level singleton (initialised via initialize_client)
_gateway_client: GatewayBrowserToolsClient | None = None


def get_client() -> GatewayBrowserToolsClient:
    """Return the module-level singleton client, or raise if not initialised."""
    if _gateway_client is None:
        raise RuntimeError("GatewayBrowserToolsClient not initialised")
    return _gateway_client


def initialize_client(gateway_url: str, timeout: float) -> GatewayBrowserToolsClient:
    """Create and store the module-level singleton client."""
    global _gateway_client
    _gateway_client = GatewayBrowserToolsClient(gateway_url=gateway_url, timeout=timeout)
    return _gateway_client


def cleanup() -> None:
    """Clear module-level singleton (used during shutdown)."""
    global _gateway_client
    _gateway_client = None
