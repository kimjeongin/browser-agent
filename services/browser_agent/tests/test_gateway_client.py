"""Tests for GatewayBrowserToolsClient.

Tests the HTTP client that invokes browser tools through the Gateway,
using respx to mock outbound httpx requests.

New API: GatewayBrowserToolsClient(gateway_url, timeout)
         client.invoke(session_id, tool_name, params)
"""

import httpx
import pytest
import respx

from tools.gateway_client import GatewayBrowserToolsClient

GATEWAY_URL = "http://gateway:8000"
SESSION_ID = "test-session-abc"
INVOKE_URL = f"{GATEWAY_URL}/sessions/{SESSION_ID}/browser-tools/invoke"


# ---------------------------------------------------------------------------
# Successful invocation
# ---------------------------------------------------------------------------


@respx.mock
async def test_invoke_returns_result_on_success():
    respx.post(INVOKE_URL).mock(
        return_value=httpx.Response(
            200,
            json={"success": True, "result": {"url": "https://example.com"}, "inv_id": "inv-1"},
        )
    )

    client = GatewayBrowserToolsClient(GATEWAY_URL, timeout=5.0)
    await client.start()
    result = await client.invoke(SESSION_ID, "navigate", {"url": "https://example.com"})

    assert result == {"url": "https://example.com"}


@respx.mock
async def test_invoke_sends_correct_payload():
    """Verify the exact JSON body sent to the Gateway."""
    route = respx.post(INVOKE_URL).mock(
        return_value=httpx.Response(
            200,
            json={"success": True, "result": {"clicked": "#btn"}, "inv_id": "inv-2"},
        )
    )

    client = GatewayBrowserToolsClient(GATEWAY_URL, timeout=5.0)
    await client.start()
    await client.invoke(SESSION_ID, "click", {"selector": "#btn"})

    assert route.called
    sent = route.calls[0].request
    import json
    body = json.loads(sent.content)
    assert body["tool_name"] == "click"
    assert body["params"] == {"selector": "#btn"}


@respx.mock
async def test_invoke_returns_none_when_result_is_null():
    """When result is None in the response, the client returns None."""
    respx.post(INVOKE_URL).mock(
        return_value=httpx.Response(
            200,
            json={"success": True, "result": None, "inv_id": "inv-3"},
        )
    )

    client = GatewayBrowserToolsClient(GATEWAY_URL, timeout=5.0)
    await client.start()
    result = await client.invoke(SESSION_ID, "screenshot", {})
    # result=None is valid (e.g., screenshot with no data yet)
    assert result is None


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


@respx.mock
async def test_invoke_raises_runtime_error_when_response_contains_error():
    """Gateway returns success=False with an error message -> RuntimeError."""
    respx.post(INVOKE_URL).mock(
        return_value=httpx.Response(
            200,
            json={"success": False, "error": "Element not found: #missing", "inv_id": "inv-4"},
        )
    )

    client = GatewayBrowserToolsClient(GATEWAY_URL, timeout=5.0)
    await client.start()
    with pytest.raises(RuntimeError, match="Element not found"):
        await client.invoke(SESSION_ID, "click", {"selector": "#missing"})


@respx.mock
async def test_invoke_raises_runtime_error_on_504():
    """Gateway returns 504 (Extension timed out) -> RuntimeError."""
    respx.post(INVOKE_URL).mock(
        return_value=httpx.Response(
            504,
            json={"detail": "Browser tool 'navigate' timed out after 60.0s"},
        )
    )

    client = GatewayBrowserToolsClient(GATEWAY_URL, timeout=5.0)
    await client.start()
    with pytest.raises(RuntimeError, match="timed out"):
        await client.invoke(SESSION_ID, "navigate", {"url": "https://slow.example.com"})


@respx.mock
async def test_invoke_raises_runtime_error_on_non_success_http():
    """Non-success HTTP status (except 504) -> RuntimeError with status info."""
    respx.post(INVOKE_URL).mock(
        return_value=httpx.Response(503, json={"detail": "Service unavailable"})
    )

    client = GatewayBrowserToolsClient(GATEWAY_URL, timeout=5.0)
    await client.start()
    with pytest.raises(RuntimeError, match="HTTP 503"):
        await client.invoke(SESSION_ID, "navigate", {"url": "https://example.com"})


@respx.mock
async def test_invoke_raises_runtime_error_on_network_timeout():
    """Network-level TimeoutException is wrapped in RuntimeError."""
    respx.post(INVOKE_URL).mock(
        side_effect=httpx.TimeoutException("Connection timed out")
    )

    client = GatewayBrowserToolsClient(GATEWAY_URL, timeout=1.0)
    await client.start()
    with pytest.raises(RuntimeError, match="timed out"):
        await client.invoke(SESSION_ID, "navigate", {"url": "https://example.com"})


# ---------------------------------------------------------------------------
# URL construction
# ---------------------------------------------------------------------------


def test_client_constructs_correct_invoke_url():
    """Verify the URL is built correctly from gateway_url."""
    client = GatewayBrowserToolsClient("http://gateway:8000", timeout=5.0)
    # The URL is constructed per-invocation using session_id
    assert client._base_url == "http://gateway:8000"


def test_client_strips_trailing_slash():
    """Trailing slash is stripped from gateway_url."""
    client = GatewayBrowserToolsClient("http://gateway:8000/", timeout=5.0)
    assert client._base_url == "http://gateway:8000"


@respx.mock
async def test_invoke_uses_correct_session_in_url():
    """Different session_ids produce different URL paths."""
    client = GatewayBrowserToolsClient(GATEWAY_URL, timeout=5.0)
    await client.start()

    route = respx.post(
        f"{GATEWAY_URL}/sessions/custom-session-id/browser-tools/invoke"
    ).mock(
        return_value=httpx.Response(
            200,
            json={"success": True, "result": {}, "inv_id": "inv-5"},
        )
    )

    await client.invoke("custom-session-id", "get_page_info", {})
    assert route.called


# ---------------------------------------------------------------------------
# Client not started
# ---------------------------------------------------------------------------


async def test_invoke_raises_when_not_started():
    """Invoking without start() should raise RuntimeError."""
    client = GatewayBrowserToolsClient(GATEWAY_URL, timeout=5.0)
    with pytest.raises(RuntimeError, match="not started"):
        await client.invoke(SESSION_ID, "navigate", {"url": "https://example.com"})
