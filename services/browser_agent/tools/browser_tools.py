"""Browser tool definitions for LangChain @tool integration.

Each tool invokes the Gateway's browser-tools endpoint via the singleton
GatewayBrowserToolsClient. The session_id parameter is required and must
be provided by the LLM in every tool call.

Tool names match the Gateway wire protocol and Extension action names exactly
(no prefix). Python function name for 'type' is type_text to avoid collision
with the built-in, but the LangChain tool name is set to 'type' via @tool("type").
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from tools.gateway_client import get_client
from tools.result_formatter import _format_aci_result, _validate_session_id


@tool
async def navigate(session_id: str, url: str) -> str:
    """Navigate the AI-controlled browser tab to a URL.

    Args:
        session_id: Active session ID linked to the user's browser.
        url: Full URL to navigate to (e.g. https://www.youtube.com).
    """
    if err := _validate_session_id(session_id):
        return err
    try:
        result = await get_client().invoke(session_id, navigate.name, {"url": url})
        return _format_aci_result(navigate.name, True, result)
    except RuntimeError as e:
        return _format_aci_result(navigate.name, False, None, str(e))


@tool
async def click(
    session_id: str,
    selector: str,
    fallback_selectors: list[str] | None = None,
    element_text: str | None = None,
) -> str:
    """Click an element in the browser tab. Supports fallback selectors for resilience.

    Args:
        session_id: Active session ID.
        selector: Primary CSS selector of the element to click.
        fallback_selectors: Optional list of alternative selectors to try if primary fails.
        element_text: Optional visible text of the element for text-based fallback search.
    """
    if err := _validate_session_id(session_id):
        return err
    params: dict[str, Any] = {"selector": selector}
    if fallback_selectors:
        params["fallback_selectors"] = fallback_selectors
    if element_text:
        params["element_text"] = element_text
    try:
        result = await get_client().invoke(session_id, click.name, params)
        return _format_aci_result(click.name, True, result)
    except RuntimeError as e:
        return _format_aci_result(click.name, False, None, str(e))


@tool("type")
async def type_text(
    session_id: str,
    selector: str,
    text: str,
    clear_first: bool = True,
) -> str:
    """Type text into an input field.

    Args:
        session_id: Active session ID.
        selector: CSS selector of the input element.
        text: Text to type.
        clear_first: Whether to clear the field before typing.
    """
    if err := _validate_session_id(session_id):
        return err
    try:
        result = await get_client().invoke(
            session_id,
            type_text.name,
            {"selector": selector, "text": text, "clear_first": clear_first},
        )
        return _format_aci_result(type_text.name, True, result)
    except RuntimeError as e:
        return _format_aci_result(type_text.name, False, None, str(e))


@tool
async def scroll(
    session_id: str,
    direction: str = "down",
    amount: int = 300,
    selector: str | None = None,
) -> str:
    """Scroll the page or a specific element.

    Args:
        session_id: Active session ID.
        direction: 'up', 'down', 'left', or 'right'.
        amount: Pixels to scroll.
        selector: Optional CSS selector to scroll a specific element.
    """
    if err := _validate_session_id(session_id):
        return err
    params: dict[str, Any] = {"direction": direction, "amount": amount}
    if selector:
        params["selector"] = selector
    try:
        result = await get_client().invoke(session_id, scroll.name, params)
        return _format_aci_result(scroll.name, True, result)
    except RuntimeError as e:
        return _format_aci_result(scroll.name, False, None, str(e))


@tool(response_format="content_and_artifact")
async def screenshot(session_id: str) -> tuple[str, dict]:
    """Capture a screenshot of the current browser tab with interactive element marks.

    Returns an annotated screenshot with numbered red badges on interactive elements.
    The screenshot image is passed directly to you for visual inspection — you can
    see the actual page and reason about which elements to interact with.
    Use click_by_mark_id to click an element labeled [N] in the image.

    Args:
        session_id: Active session ID.
    """
    if err := _validate_session_id(session_id):
        return err, {}
    try:
        result = await get_client().invoke(session_id, screenshot.name, {})
        marks = result.get("marks", {})
        text = _format_aci_result(screenshot.name, True, result)
        return text, {"screenshot": result.get("screenshot", ""), "marks": marks}
    except RuntimeError as e:
        return _format_aci_result(screenshot.name, False, None, str(e)), {}


@tool
async def extract_content(
    session_id: str,
    selector: str | None = None,
    include_html: bool = False,
) -> str:
    """Extract text content from the page or a specific element.

    Args:
        session_id: Active session ID.
        selector: Optional CSS selector. If None, extracts entire page body.
        include_html: Whether to include raw HTML in the result.
    """
    if err := _validate_session_id(session_id):
        return err
    params: dict[str, Any] = {"include_html": include_html}
    if selector:
        params["selector"] = selector
    try:
        result = await get_client().invoke(session_id, extract_content.name, params)
        return _format_aci_result(extract_content.name, True, result)
    except RuntimeError as e:
        return _format_aci_result(extract_content.name, False, None, str(e))


@tool
async def wait_for_element(
    session_id: str,
    selector: str,
    timeout_ms: int = 10000,
    visible: bool = True,
) -> str:
    """Wait for an element to appear in the DOM.

    Args:
        session_id: Active session ID.
        selector: CSS selector to wait for.
        timeout_ms: Maximum wait time in milliseconds.
        visible: Whether the element must also be visible.
    """
    if err := _validate_session_id(session_id):
        return err
    try:
        result = await get_client().invoke(
            session_id,
            wait_for_element.name,
            {"selector": selector, "timeout_ms": timeout_ms, "visible": visible},
        )
        return _format_aci_result(wait_for_element.name, True, result)
    except RuntimeError as e:
        return _format_aci_result(wait_for_element.name, False, None, str(e))


@tool
async def get_page_info(session_id: str) -> str:
    """Get current page URL, title, and ready state.

    Args:
        session_id: Active session ID.
    """
    if err := _validate_session_id(session_id):
        return err
    try:
        result = await get_client().invoke(session_id, get_page_info.name, {})
        return _format_aci_result(get_page_info.name, True, result)
    except RuntimeError as e:
        return _format_aci_result(get_page_info.name, False, None, str(e))


@tool
async def get_structured_dom(session_id: str) -> str:
    """Get a compact, structured representation of interactive page elements.

    Returns only visible, interactable elements in the current viewport.
    Use this INSTEAD of extract_content to understand page structure.
    Much more token-efficient than extracting full page text.

    Args:
        session_id: Active session ID linked to the user's browser.
    """
    if err := _validate_session_id(session_id):
        return err
    try:
        result = await get_client().invoke(session_id, get_structured_dom.name, {})
        return _format_aci_result(get_structured_dom.name, True, result)
    except RuntimeError as e:
        return _format_aci_result(get_structured_dom.name, False, None, str(e))


@tool
async def click_by_mark_id(session_id: str, mark_id: int) -> str:
    """Click an interactive element by its mark number from the last screenshot.

    After calling screenshot, elements are numbered with red badges.
    Use this tool to click element [N] instead of guessing CSS selectors.

    Args:
        session_id: Active session ID.
        mark_id: The number shown on the red badge in the screenshot.
    """
    if err := _validate_session_id(session_id):
        return err
    try:
        result = await get_client().invoke(
            session_id, click_by_mark_id.name, {"mark_id": mark_id}
        )
        return _format_aci_result(click_by_mark_id.name, True, result)
    except RuntimeError as e:
        return _format_aci_result(click_by_mark_id.name, False, None, str(e))


BROWSER_TOOLS = [
    navigate,
    click,
    type_text,
    scroll,
    screenshot,
    click_by_mark_id,
    extract_content,
    wait_for_element,
    get_page_info,
    get_structured_dom,
]
