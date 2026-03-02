"""Browser tool definitions for LangChain @tool integration.

Each tool invokes the Gateway's browser-tools endpoint via the singleton
GatewayBrowserToolsClient. The session_id parameter is required and must
be provided by the LLM in every tool call.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from tools.gateway_client import get_client, get_vl_llm
from tools.result_formatter import _format_aci_result, _validate_session_id


@tool
async def browser_navigate(session_id: str, url: str) -> str:
    """Navigate the AI-controlled browser tab to a URL.

    Args:
        session_id: Active session ID linked to the user's browser.
        url: Full URL to navigate to (e.g. https://www.youtube.com).
    """
    if err := _validate_session_id(session_id):
        return err
    try:
        result = await get_client().invoke(session_id, "navigate", {"url": url})
        return _format_aci_result("navigate", True, result)
    except RuntimeError as e:
        return _format_aci_result("navigate", False, None, str(e))


@tool
async def browser_click(
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
        result = await get_client().invoke(session_id, "click", params)
        return _format_aci_result("click", True, result)
    except RuntimeError as e:
        return _format_aci_result("click", False, None, str(e))


@tool
async def browser_type(
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
            "type",
            {"selector": selector, "text": text, "clear_first": clear_first},
        )
        return _format_aci_result("type", True, result)
    except RuntimeError as e:
        return _format_aci_result("type", False, None, str(e))


@tool
async def browser_scroll(
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
        result = await get_client().invoke(session_id, "scroll", params)
        return _format_aci_result("scroll", True, result)
    except RuntimeError as e:
        return _format_aci_result("scroll", False, None, str(e))


@tool
async def browser_screenshot(session_id: str) -> str:
    """Capture a screenshot of the current browser tab with interactive element marks.

    Returns an annotated screenshot with numbered red badges on interactive elements.
    Use browser_click_by_mark_id to click on a marked element.

    Args:
        session_id: Active session ID.
    """
    if err := _validate_session_id(session_id):
        return err
    try:
        result = await get_client().invoke(session_id, "screenshot", {})
        return _format_aci_result("screenshot", True, result)
    except RuntimeError as e:
        return _format_aci_result("screenshot", False, None, str(e))


@tool
async def browser_extract_content(
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
        result = await get_client().invoke(session_id, "extract_content", params)
        return _format_aci_result("extract_content", True, result)
    except RuntimeError as e:
        return _format_aci_result("extract_content", False, None, str(e))


@tool
async def browser_wait_for_element(
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
            "wait_for_element",
            {"selector": selector, "timeout_ms": timeout_ms, "visible": visible},
        )
        return _format_aci_result("wait_for_element", True, result)
    except RuntimeError as e:
        return _format_aci_result("wait_for_element", False, None, str(e))


@tool
async def get_page_info(session_id: str) -> str:
    """Get current page URL, title, and ready state.

    Args:
        session_id: Active session ID.
    """
    if err := _validate_session_id(session_id):
        return err
    try:
        result = await get_client().invoke(session_id, "get_page_info", {})
        return _format_aci_result("get_page_info", True, result)
    except RuntimeError as e:
        return _format_aci_result("get_page_info", False, None, str(e))


@tool
async def browser_get_structured_dom(session_id: str) -> str:
    """Get a compact, structured representation of interactive page elements.

    Returns only visible, interactable elements in the current viewport.
    Use this INSTEAD of browser_extract_content to understand page structure.
    Much more token-efficient than extracting full page text.

    Args:
        session_id: Active session ID linked to the user's browser.
    """
    if err := _validate_session_id(session_id):
        return err
    try:
        result = await get_client().invoke(session_id, "get_structured_dom", {})
        return _format_aci_result("get_structured_dom", True, result)
    except RuntimeError as e:
        return _format_aci_result("get_structured_dom", False, None, str(e))


@tool
async def browser_click_by_mark_id(session_id: str, mark_id: int) -> str:
    """Click an interactive element by its mark number from the last screenshot.

    After calling browser_screenshot, elements are numbered with red badges.
    Use this tool to click element [N] instead of guessing CSS selectors.

    Args:
        session_id: Active session ID.
        mark_id: The number shown on the red badge in the screenshot.
    """
    if err := _validate_session_id(session_id):
        return err
    try:
        result = await get_client().invoke(
            session_id, "click_by_mark_id", {"mark_id": mark_id}
        )
        return _format_aci_result("click_by_mark_id", True, result)
    except RuntimeError as e:
        return _format_aci_result("click_by_mark_id", False, None, str(e))


@tool
async def browser_visual_find(session_id: str, description: str) -> str:
    """Use computer vision to locate a UI element when DOM-based methods fail.

    This is a **last-resort fallback** for elements that are invisible to
    browser_get_structured_dom (e.g. canvas-rendered UIs, complex shadow DOM,
    dynamically injected elements without accessible attributes).

    Workflow:
    1. Captures a screenshot of the current page.
    2. Sends the image to the local vision-language model (qwen3vl).
    3. Returns the model's analysis: probable CSS selector, visible text,
       and approximate position.

    Only call this after browser_get_structured_dom AND browser_screenshot /
    browser_click_by_mark_id have both failed.

    Args:
        session_id: Active session ID linked to the user's browser.
        description: Human-readable description of the element to find,
            e.g. "the blue Subscribe button" or "search input field".
    """
    if err := _validate_session_id(session_id):
        return err

    vl_llm = get_vl_llm()
    if vl_llm is None:
        return "Visual fallback unavailable: vision model not initialised."

    # 1. Capture screenshot (raw result contains base64 image)
    try:
        raw = await get_client().invoke(session_id, "screenshot", {})
    except RuntimeError as e:
        return f"Visual fallback failed: could not capture screenshot -- {e}"

    b64: str = raw.get("screenshot", "")
    if not b64:
        return "Visual fallback failed: screenshot returned no image data."

    # Strip data-URI prefix when present ("data:image/jpeg;base64,...")
    if b64.startswith("data:"):
        b64 = b64.split(",", 1)[-1]

    # 2. Query vision-language model with the screenshot
    from langchain_core.messages import HumanMessage as _HumanMessage

    vl_prompt = (
        f"This is a browser screenshot. Find the UI element described as: "
        f"'{description}'.\n\n"
        "Provide ALL of the following that you can determine:\n"
        "1. Visible text on the element\n"
        "2. Element type (button, input, link, etc.)\n"
        "3. A CSS selector (id, class, aria-label, etc.) if identifiable\n"
        "4. Approximate screen position (e.g. top-left, center, bottom-right)\n\n"
        "Be concise. If the element is not visible, say so clearly."
    )
    vl_message = _HumanMessage(
        content=[
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            },
            {"type": "text", "text": vl_prompt},
        ]
    )

    try:
        response = await vl_llm.ainvoke([vl_message])
        analysis: str = (
            response.content
            if isinstance(response.content, str)
            else str(response.content)
        )
    except Exception as e:
        return f"Visual fallback failed: vision model error -- {e}"

    return f"[Visual analysis for '{description}']\n{analysis}"


BROWSER_TOOLS = [
    browser_navigate,
    browser_click,
    browser_type,
    browser_scroll,
    browser_screenshot,
    browser_click_by_mark_id,
    browser_extract_content,
    browser_wait_for_element,
    get_page_info,
    browser_get_structured_dom,
    browser_visual_find,
]
