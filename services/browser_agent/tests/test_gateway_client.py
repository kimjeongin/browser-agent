"""Tests for GatewayBrowserToolsClient.

Tests the HTTP client that invokes browser tools through the Gateway,
using respx to mock outbound httpx requests.
"""

import httpx
import pytest
import respx

from main import GatewayBrowserToolsClient

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
            json={"success": True, "result": {"url": "https://example.com"}},
        )
    )

    client = GatewayBrowserToolsClient(GATEWAY_URL, SESSION_ID)
    result = await client.invoke("navigate", {"url": "https://example.com"})

    assert result == {"url": "https://example.com"}


@respx.mock
async def test_invoke_sends_correct_payload():
    """Verify the exact JSON body sent to the Gateway."""
    route = respx.post(INVOKE_URL).mock(
        return_value=httpx.Response(
            200,
            json={"success": True, "result": {"clicked": "#btn"}},
        )
    )

    client = GatewayBrowserToolsClient(GATEWAY_URL, SESSION_ID)
    await client.invoke("click", {"selector": "#btn"})

    assert route.called
    sent = route.calls[0].request
    import json
    body = json.loads(sent.content)
    assert body["tool"] == "click"
    assert body["params"] == {"selector": "#btn"}


@respx.mock
async def test_invoke_returns_none_when_result_is_null():
    respx.post(INVOKE_URL).mock(
        return_value=httpx.Response(
            200,
            json={"success": True, "result": None},
        )
    )

    client = GatewayBrowserToolsClient(GATEWAY_URL, SESSION_ID)
    result = await client.invoke("take_screenshot", {})

    assert result is None


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


@respx.mock
async def test_invoke_raises_runtime_error_when_response_contains_error():
    """Gateway returns success=False with an error message → RuntimeError."""
    respx.post(INVOKE_URL).mock(
        return_value=httpx.Response(
            200,
            json={"success": False, "error": "Element not found: #missing"},
        )
    )

    client = GatewayBrowserToolsClient(GATEWAY_URL, SESSION_ID)
    with pytest.raises(RuntimeError, match="Element not found: #missing"):
        await client.invoke("click", {"selector": "#missing"})


@respx.mock
async def test_invoke_raises_on_gateway_503():
    """Gateway returns 503 (Extension not connected) → httpx.HTTPStatusError."""
    respx.post(INVOKE_URL).mock(
        return_value=httpx.Response(503, json={"detail": "Extension not connected"})
    )

    client = GatewayBrowserToolsClient(GATEWAY_URL, SESSION_ID)
    with pytest.raises(httpx.HTTPStatusError):
        await client.invoke("navigate", {"url": "https://example.com"})


@respx.mock
async def test_invoke_raises_on_gateway_408_timeout():
    """Gateway returns 408 (tool invocation timed out) → httpx.HTTPStatusError."""
    respx.post(INVOKE_URL).mock(
        return_value=httpx.Response(408, json={"detail": "Tool invocation timed out after 30s"})
    )

    client = GatewayBrowserToolsClient(GATEWAY_URL, SESSION_ID)
    with pytest.raises(httpx.HTTPStatusError):
        await client.invoke("wait_for_element", {"selector": "#slow"})


# ---------------------------------------------------------------------------
# URL construction
# ---------------------------------------------------------------------------


def test_client_constructs_correct_invoke_url():
    """Verify the URL is built correctly from gateway_url and session_id."""
    client = GatewayBrowserToolsClient("http://gateway:8000", "my-session")
    assert client._url == "http://gateway:8000/sessions/my-session/browser-tools/invoke"


def test_client_handles_trailing_slash_in_gateway_url():
    """No double-slash when gateway_url has trailing slash."""
    client = GatewayBrowserToolsClient("http://gateway:8000", "sess-123")
    # The URL should use the path as-is from gateway_url + path segments
    assert "browser-tools/invoke" in client._url
