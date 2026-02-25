"""Browser Relay MCP Server.

Exposes browser control tools to LangGraph agents via MCP.
Commands are relayed to the browser extension through Redis Pub/Sub.

Flow:
  Agent → MCP tool call → publish to Redis browser_cmd:{session_id}
                        → subscribe browser_result:{command_id}
                        → wait for CommandResult from extension
"""

import asyncio
import json
import logging
import uuid
from typing import Any

import redis.asyncio as aioredis
from fastmcp import FastMCP
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    redis_url: str = "redis://localhost:6379/0"
    command_timeout: float = 30.0  # seconds to wait for browser result
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8010


settings = Settings()

# ---------------------------------------------------------------------------
# Redis client (module-level singleton)
# ---------------------------------------------------------------------------

_redis: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = await aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
    return _redis


# ---------------------------------------------------------------------------
# Core relay helper
# ---------------------------------------------------------------------------


async def _relay_command(
    session_id: str,
    action: str,
    params: dict[str, Any],
    timeout: float | None = None,
) -> dict[str, Any]:
    """Publish a browser command to Redis and wait for the result.

    Subscribe to the result channel BEFORE publishing the command to avoid
    the race condition where the result arrives before we start listening.
    """
    redis = await get_redis()
    command_id = str(uuid.uuid4())
    timeout = timeout or settings.command_timeout

    cmd_payload = {
        "command_id": command_id,
        "session_id": session_id,
        "action": action,
        "params": params,
    }

    result_channel = f"browser_result:{command_id}"
    cmd_channel = f"browser_cmd:{session_id}"

    pubsub = redis.pubsub()
    try:
        # 1. Subscribe FIRST to avoid race condition
        await pubsub.subscribe(result_channel)

        # 2. Publish command
        await redis.publish(cmd_channel, json.dumps(cmd_payload))
        logger.debug("Published command %s to %s", command_id, cmd_channel)

        # 3. Wait for result with timeout
        async def _wait() -> dict[str, Any]:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    return json.loads(message["data"])
            raise RuntimeError("Pub/Sub stream ended unexpectedly")

        return await asyncio.wait_for(_wait(), timeout=timeout)

    except asyncio.TimeoutError:
        raise TimeoutError(
            f"Browser command '{action}' timed out after {timeout}s "
            f"(session={session_id}, command_id={command_id})"
        )
    finally:
        await pubsub.unsubscribe(result_channel)
        await pubsub.aclose()


# ---------------------------------------------------------------------------
# FastMCP server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "browser-relay",
    instructions=(
        "Controls a real browser tab through a Chrome extension. "
        "All tools require a valid session_id that maps to an active browser tab."
    ),
)


@mcp.tool()
async def browser_navigate(session_id: str, url: str) -> dict[str, Any]:
    """Navigate the browser tab to a URL.

    Args:
        session_id: Active session ID linked to a browser tab.
        url: Full URL to navigate to (e.g. https://example.com).
    """
    return await _relay_command(session_id, "navigate", {"url": url})


@mcp.tool()
async def browser_click(session_id: str, selector: str) -> dict[str, Any]:
    """Click an element in the browser tab.

    Args:
        session_id: Active session ID.
        selector: CSS selector or XPath of the element to click.
    """
    return await _relay_command(session_id, "click", {"selector": selector})


@mcp.tool()
async def browser_type(
    session_id: str, selector: str, text: str, clear_first: bool = True
) -> dict[str, Any]:
    """Type text into an input field.

    Args:
        session_id: Active session ID.
        selector: CSS selector of the input element.
        text: Text to type.
        clear_first: Whether to clear the field before typing.
    """
    return await _relay_command(
        session_id,
        "type",
        {"selector": selector, "text": text, "clear_first": clear_first},
    )


@mcp.tool()
async def browser_scroll(
    session_id: str,
    direction: str = "down",
    amount: int = 300,
    selector: str | None = None,
) -> dict[str, Any]:
    """Scroll the page or a specific element.

    Args:
        session_id: Active session ID.
        direction: 'up', 'down', 'left', or 'right'.
        amount: Pixels to scroll.
        selector: Optional CSS selector to scroll a specific element.
    """
    params: dict[str, Any] = {"direction": direction, "amount": amount}
    if selector:
        params["selector"] = selector
    return await _relay_command(session_id, "scroll", params)


@mcp.tool()
async def browser_screenshot(session_id: str) -> dict[str, Any]:
    """Capture a screenshot of the current browser tab.

    Args:
        session_id: Active session ID.

    Returns:
        Dict with 'screenshot' key containing base64-encoded PNG.
    """
    return await _relay_command(session_id, "screenshot", {})


@mcp.tool()
async def browser_extract_content(
    session_id: str,
    selector: str | None = None,
    include_html: bool = False,
) -> dict[str, Any]:
    """Extract text content from the page or a specific element.

    Args:
        session_id: Active session ID.
        selector: Optional CSS selector. If None, extracts entire page body.
        include_html: Whether to include raw HTML in the result.
    """
    params: dict[str, Any] = {"include_html": include_html}
    if selector:
        params["selector"] = selector
    return await _relay_command(session_id, "extract_content", params)


@mcp.tool()
async def browser_wait_for_element(
    session_id: str,
    selector: str,
    timeout_ms: int = 10000,
    visible: bool = True,
) -> dict[str, Any]:
    """Wait for an element to appear in the DOM.

    Args:
        session_id: Active session ID.
        selector: CSS selector to wait for.
        timeout_ms: Maximum wait time in milliseconds.
        visible: Whether the element must also be visible.
    """
    wait_timeout = (timeout_ms / 1000) + 5  # add buffer for round-trip
    return await _relay_command(
        session_id,
        "wait_for_element",
        {"selector": selector, "timeout_ms": timeout_ms, "visible": visible},
        timeout=wait_timeout,
    )


@mcp.tool()
async def browser_evaluate_js(session_id: str, script: str) -> dict[str, Any]:
    """Execute JavaScript in the browser tab and return the result.

    Args:
        session_id: Active session ID.
        script: JavaScript code to execute. The return value is serialized to JSON.
    """
    return await _relay_command(session_id, "evaluate_js", {"script": script})


@mcp.tool()
async def get_page_info(session_id: str) -> dict[str, Any]:
    """Get current page URL, title, and metadata.

    Args:
        session_id: Active session ID.
    """
    return await _relay_command(session_id, "get_page_info", {})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    mcp.run(
        transport="streamable-http",
        host=settings.mcp_host,
        port=settings.mcp_port,
    )


if __name__ == "__main__":
    main()
