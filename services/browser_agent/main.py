"""Browser Agent -- FastAPI + LangGraph Progress Ledger agent with Gateway browser tools.

Connects directly to the Gateway service to invoke browser tools on the
user's Chrome extension. No longer depends on the Browser Relay MCP server.

webMCP-inspired tool invocation flow:
  LangGraph tool call
    -> GatewayBrowserToolsClient.invoke(session_id, tool_name, params)
    -> POST /sessions/{id}/browser-tools/invoke (Gateway, blocking 60s)
    -> Gateway SSE -> Extension executes DOM action
    -> Extension POST /browser-tools/result/{inv_id}
    -> Gateway Future resolved -> response returned to agent

Agent architecture: Progress Ledger (Magentic-One inspired)
  planner -> actor -> tools -> progress_check -> route_after_progress
  - Planner: lightweight LLM decides initial strategy (called once + on replan)
  - Actor: tool-calling LLM executes browser actions (skips planner when progressing)
  - ProgressCheck: lightweight LLM evaluates if task is advancing
  - Replan: lightweight LLM generates new strategy when stalled (stall_count >= 3)
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from functools import partial
from typing import Annotated, Any

import httpx
from fastapi import FastAPI
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import tool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing_extensions import TypedDict

from shared.acp import create_acp_router
from shared.llm import LLMSettings, create_ollama_llm

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class BrowserAgentSettings(BaseSettings):
    """Environment-driven configuration for the Browser Agent."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = (
        "postgresql+asyncpg://postgres:password@postgres:5432/browser_agent"
    )
    browser_model: str = "qwen3:14b"
    planner_model: str = "qwen3:8b"  # lighter model for planning/validation
    vision_model: str = "qwen3vl:8b"  # vision-language model for DOM fallback
    gateway_url: str = "http://gateway:8000"
    browser_tool_timeout: float = 65.0  # slightly longer than gateway timeout


# ---------------------------------------------------------------------------
# Gateway Browser Tools Client
# ---------------------------------------------------------------------------


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


# Module-level singletons (initialised in lifespan)
_gateway_client: GatewayBrowserToolsClient | None = None
_vl_llm: Any = None  # Vision-language model for DOM-failure fallback


def _get_client() -> GatewayBrowserToolsClient:
    if _gateway_client is None:
        raise RuntimeError("GatewayBrowserToolsClient not initialised")
    return _gateway_client


# ---------------------------------------------------------------------------
# LangGraph state (Progress Ledger)
# ---------------------------------------------------------------------------


class AgentState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    session_id: str
    # Progress Ledger additions
    stall_count: int           # Consecutive non-progress steps
    progress_ledger: dict      # Latest output from progress_check_node
    action_history: list[str]  # Recent tool call names (last 5, for loop detection)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_STALL_COUNT = 3  # Trigger replan after this many consecutive stall steps


# ---------------------------------------------------------------------------
# P1-5: session_id validation helper
# ---------------------------------------------------------------------------


def _validate_session_id(session_id: str) -> str | None:
    """Return an error string if session_id is invalid, else None."""
    if not session_id or not session_id.strip():
        return (
            "TOOL FAILED: session_id is missing or empty. "
            "Always include the session_id from the conversation state "
            "in every tool call."
        )
    return None


# Recovery hints per tool — shown to LLM when a tool fails.
_RECOVERY_HINTS: dict[str, str] = {
    "navigate": "Ensure the URL is valid and starts with http:// or https://.",
    "click": (
        "Try browser_get_structured_dom to find the correct selector, "
        "or take a screenshot with marks and use browser_click_by_mark_id."
    ),
    "type": (
        "Verify the input field is visible with browser_get_structured_dom before typing."
    ),
    "scroll": "Verify the target element or window is scrollable.",
    "screenshot": "Ensure the AI tab is active and a page has loaded.",
    "extract_content": (
        "Try a more specific selector or use browser_get_structured_dom instead."
    ),
    "wait_for_element": (
        "The element may use dynamic rendering. "
        "Try a broader selector or increase timeout_ms."
    ),
    "get_page_info": "Navigate to a URL first so a page is loaded.",
    "get_structured_dom": (
        "The page may still be loading. "
        "Try browser_wait_for_element or navigate first."
    ),
    "click_by_mark_id": (
        "Marks expire when the page changes. "
        "Take a new screenshot to refresh marks."
    ),
}


def _format_aci_result(
    tool_name: str,
    success: bool,
    result: dict | None,
    error: str | None = None,
) -> str:
    """Format a tool result as a human-readable string for the LLM.

    Failure path includes a recovery hint so the LLM can self-correct.
    Success path is tool-specific and highlights actionable information.
    """
    if not success:
        hint = _RECOVERY_HINTS.get(tool_name, "Try a different approach.")
        return f"TOOL FAILED [{tool_name}]: {error}\nRecovery hint: {hint}"

    r = result or {}

    if tool_name == "navigate":
        return f"Navigated to: {r.get('url', 'unknown')}"

    if tool_name == "click":
        return f"Clicked element: {r.get('clicked', r.get('selector', 'unknown'))}"

    if tool_name == "click_by_mark_id":
        return (
            f"Clicked mark {r.get('mark_id', '?')}: "
            f"{r.get('clicked_selector', 'unknown')}"
        )

    if tool_name == "type":
        return f"Typed into field: '{r.get('typed', r.get('text', 'unknown'))}'"

    if tool_name == "scroll":
        scrolled = r.get("scrolled", {})
        return (
            f"Scrolled page: dx={scrolled.get('x', 0)}, dy={scrolled.get('y', 0)}"
        )

    if tool_name == "extract_content":
        text = r.get("text", "")
        if not text or not text.strip():
            return (
                "extract_content returned empty: no text found at this selector. "
                "Try browser_get_structured_dom for page structure."
            )
        preview = text.strip()[:200]
        suffix = "..." if len(text) > 200 else ""
        return f"Extracted content ({len(text)} chars): {preview}{suffix}"

    if tool_name == "wait_for_element":
        found = r.get("found", False)
        selector = r.get("selector", "unknown")
        if not found:
            hint = _RECOVERY_HINTS["wait_for_element"]
            return f"Element NOT found: {selector}\nRecovery hint: {hint}"
        return f"Element found: {selector}"

    if tool_name == "get_page_info":
        return (
            f"Page: {r.get('title', 'unknown')} "
            f"| URL: {r.get('url', 'unknown')} "
            f"| State: {r.get('readyState', 'unknown')}"
        )

    if tool_name == "get_structured_dom":
        elements = r.get("elements", [])
        count = len(elements)
        if count == 0:
            return (
                "get_structured_dom: no interactable elements found. "
                "The page may still be loading or the content is not interactive."
            )
        lines = [f"Page: {r.get('title', '?')} ({r.get('url', '?')})"]
        lines.append(f"Found {count} interactable elements:")
        for el in elements:
            idx = el.get("idx", "?")
            selector = el.get("selector") or el.get("tag", "?")
            text = (
                el.get("text")
                or el.get("ariaLabel")
                or el.get("placeholder")
                or ""
            )
            text_part = f" \u2014 {text[:60]}" if text else ""
            lines.append(f"  [{idx}] {selector}{text_part}")
        return "\n".join(lines)

    if tool_name == "screenshot":
        marks = r.get("marks", {})
        screenshot = r.get("screenshot", "")
        mark_count = len(marks)
        if mark_count > 0:
            mark_list = ", ".join(
                f"[{k}]={v.get('tag', '?')}"
                for k, v in list(marks.items())[:10]
            )
            suffix = "..." if mark_count > 10 else ""
            return (
                f"Screenshot captured with {mark_count} interactive element marks. "
                f"Elements: {mark_list}{suffix}. "
                "Use browser_click_by_mark_id(session_id=..., mark_id=N) "
                "to click an element."
            )
        return f"Screenshot captured (no marks). Image data: {len(screenshot)} chars."

    return f"Tool [{tool_name}] completed: {json.dumps(r)[:200]}"


# ---------------------------------------------------------------------------
# Browser tools (LangChain @tool, calling Gateway)
# ---------------------------------------------------------------------------
# session_id is passed as a parameter to each tool.
# The LLM is instructed to always include the session_id from state.


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
        result = await _get_client().invoke(session_id, "navigate", {"url": url})
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
        result = await _get_client().invoke(session_id, "click", params)
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
        result = await _get_client().invoke(
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
        result = await _get_client().invoke(session_id, "scroll", params)
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
        result = await _get_client().invoke(session_id, "screenshot", {})
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
        result = await _get_client().invoke(session_id, "extract_content", params)
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
        result = await _get_client().invoke(
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
        result = await _get_client().invoke(session_id, "get_page_info", {})
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
        result = await _get_client().invoke(session_id, "get_structured_dom", {})
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
        result = await _get_client().invoke(
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

    if _vl_llm is None:
        return "Visual fallback unavailable: vision model not initialised."

    # 1. Capture screenshot (raw result contains base64 image)
    try:
        raw = await _get_client().invoke(session_id, "screenshot", {})
    except RuntimeError as e:
        return f"Visual fallback failed: could not capture screenshot — {e}"

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
        response = await _vl_llm.ainvoke([vl_message])
        analysis: str = (
            response.content
            if isinstance(response.content, str)
            else str(response.content)
        )
    except Exception as e:
        return f"Visual fallback failed: vision model error — {e}"

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

# ---------------------------------------------------------------------------
# System prompts (P1-2: updated for selective screenshots, P2-1: planner/validator)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a browser automation agent. You have tools to control a browser tab.

The user's session_id is available in the conversation state. You MUST include
it in every tool call.

Guidelines:
1. Always start by navigating to the relevant URL using browser_navigate.
2. After navigation, use browser_get_structured_dom to understand page structure.
   This shows interactive elements without expensive screenshots.
3. ONLY use browser_screenshot when:
   - The user explicitly asks to see a screenshot
   - Visual verification is absolutely necessary (e.g., confirming video is playing)
   - browser_get_structured_dom fails to find the needed elements
4. Use get_page_info to check the current URL and title.
5. For search tasks: navigate -> get_structured_dom -> click/type in search box ->
   press Enter or click search -> wait for results -> click desired result.
6. When clicking, provide fallback_selectors for resilience:
   browser_click(session_id=..., selector="#primary",
                 fallback_selectors=["[aria-label='Search']", "button[type='submit']"],
                 element_text="Search")
7. Use browser_wait_for_element to wait for dynamic content before interacting.
8. Report each action and its result clearly to the user.
9. If a tool call fails, try once with a different selector before reporting failure.
10. DOM Failure Fallback (use in order):
    a. browser_get_structured_dom — always try first.
    b. browser_screenshot + browser_click_by_mark_id — if DOM lookup fails.
    c. browser_visual_find(session_id=..., description="...") — LAST RESORT only.
       Use this when both DOM and mark-based clicks have failed. It sends the
       screenshot to a vision model to identify elements invisible to the DOM.

Efficiency:
- browser_get_structured_dom is fast and token-efficient. Use it first.
- Screenshots consume many tokens. Use sparingly.
- browser_visual_find is the most expensive call; only use it as a last resort.
- Always include session_id in every single tool call.

For YouTube tasks:
- Navigate to https://www.youtube.com
- Use browser_get_structured_dom to find the search input
- Search box selector: input#search or ytd-searchbox input
- After search, click on the most relevant video result

Set-of-Marks Screenshots:
- browser_screenshot returns an annotated image with numbered red badges on interactive elements.
- Use browser_click_by_mark_id(session_id=..., mark_id=N) to click the element labeled [N].
- Marks expire when the page changes — take a new screenshot if unsure.
- Prefer browser_get_structured_dom for finding elements without screenshots to save tokens.
"""

PLANNER_SYSTEM_PROMPT = """\
You are a browser task planner. Analyze the user's request and the current state,
then decide what single next action to take.

Be concise. Output only the action plan in 1-2 sentences.
Examples:
- "Navigate to https://youtube.com to start the search task."
- "Click the search input and type the query."
- "The page has loaded. Now click on the first video result."

Current session_id will be provided - always include it in tool calls.
"""

PROGRESS_CHECK_SYSTEM_PROMPT = """\
You are a browser task progress checker. Analyze the recent actions and results,
then output a JSON object (no markdown fences) with exactly these fields:

{
  "is_task_complete": true/false,
  "is_making_progress": true/false,
  "is_stuck_in_loop": true/false,
  "next_action_hint": "brief description of what to do next (1 sentence)"
}

Rules:
- is_task_complete: true only if the user's goal is fully achieved
- is_making_progress: true if the last action moved the task forward (even partially)
- is_stuck_in_loop: true if the same action was repeated 3+ times with the same result
- next_action_hint: actionable suggestion for the actor's next step

Respond with ONLY the JSON object. No explanations outside the JSON.
"""

REPLAN_SYSTEM_PROMPT = """\
You are a browser task replanner. The current approach is stuck.
Analyze what has been tried, why it failed, and suggest a NEW strategy.

Be concise. Output 1-2 sentences describing a different approach.
Example: "The direct click approach failed. Try using browser_screenshot with marks
to visually identify the target element, then use browser_click_by_mark_id."

Do not repeat actions that have already failed.
"""


# ---------------------------------------------------------------------------
# P2-3: Context compression
# ---------------------------------------------------------------------------


def _compress_messages(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Compress message history to prevent context overflow.

    Strategy (browser-use inspired):
    - Always keep the first message (initial user request)
    - Always keep the last 4 messages (recent context)
    - For older ToolMessages: remove base64 image data to save tokens
    - Keeps conversation coherent while minimizing token usage
    """
    if len(messages) <= 6:
        return messages

    # Keep first (user request) and last 4 messages intact
    first = messages[:1]
    recent = messages[-4:]
    older = messages[1:-4]

    compressed_older: list[BaseMessage] = []
    for msg in older:
        if isinstance(msg, ToolMessage):
            content = msg.content
            # Remove base64 image data (screenshots are very token-heavy)
            if isinstance(content, str) and "data:image" in content:
                content = (
                    "[screenshot removed - use browser_get_structured_dom "
                    "for page structure]"
                )
            compressed_older.append(
                ToolMessage(content=content, tool_call_id=msg.tool_call_id)
            )
        else:
            compressed_older.append(msg)

    return first + compressed_older + recent


# ---------------------------------------------------------------------------
# Progress Ledger graph nodes
# ---------------------------------------------------------------------------


async def planner_node(state: AgentState, *, llm: Any) -> dict[str, Any]:
    """Analyze current state and decide initial strategy."""
    messages = _compress_messages(state["messages"])
    session_id = state.get("session_id", "")

    system_content = PLANNER_SYSTEM_PROMPT
    if session_id:
        system_content += f"\n\nCurrent session_id: {session_id}"

    # Replace or inject system prompt
    if not messages or not isinstance(messages[0], SystemMessage):
        messages = [SystemMessage(content=system_content), *messages]
    else:
        messages = [SystemMessage(content=system_content), *messages[1:]]

    response = await llm.ainvoke(messages)
    return {"messages": [response]}


async def actor_node(state: AgentState, *, llm_with_tools: Any) -> dict[str, Any]:
    """Execute the next browser action using tools."""
    messages = _compress_messages(state["messages"])
    session_id = state.get("session_id", "")
    progress_ledger = state.get("progress_ledger") or {}

    system_content = SYSTEM_PROMPT
    if session_id:
        system_content += (
            f"\n\nCurrent session_id: {session_id}\n"
            "Always use this session_id in your tool calls."
        )

    # Inject progress hint from previous progress_check if available
    hint = progress_ledger.get("next_action_hint", "")
    if hint:
        system_content += f"\n\nSuggested next action: {hint}"

    # Replace or inject system prompt
    if not messages or not isinstance(messages[0], SystemMessage):
        messages = [SystemMessage(content=system_content), *messages]
    else:
        messages = [SystemMessage(content=system_content), *messages[1:]]

    response = await llm_with_tools.ainvoke(messages)

    # Track which tools were called in action_history (last 5)
    action_history = list(state.get("action_history") or [])
    if isinstance(response, AIMessage) and response.tool_calls:
        for tc in response.tool_calls:
            action_history.append(tc["name"])
    action_history = action_history[-5:]  # keep only last 5

    return {"messages": [response], "action_history": action_history}


async def progress_check_node(state: AgentState, *, llm: Any) -> dict[str, Any]:
    """Evaluate if the task is making progress and update the ledger."""
    messages = state["messages"]

    # Collect recent tool results for analysis
    recent_results: list[str] = []
    for msg in reversed(messages):
        if isinstance(msg, ToolMessage):
            recent_results.insert(0, str(msg.content)[:300])
            if len(recent_results) >= 3:
                break

    action_history = state.get("action_history") or []
    stall_count = state.get("stall_count") or 0

    progress_input = [
        SystemMessage(content=PROGRESS_CHECK_SYSTEM_PROMPT),
        HumanMessage(
            content=(
                f"Recent tool results:\n{chr(10).join(recent_results)}\n\n"
                f"Recent actions taken: {', '.join(action_history) or 'none'}"
            )
        ),
    ]

    response = await llm.ainvoke(progress_input)
    raw = response.content.strip() if hasattr(response, "content") else ""

    # Strip markdown fences if present (```json ... ```)
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(
            line for line in lines if not line.strip().startswith("```")
        ).strip()

    # Parse JSON; fall back to "making progress" to avoid false stalls
    try:
        ledger = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        logger.warning("progress_check_node: failed to parse JSON, using fallback")
        ledger = {
            "is_task_complete": False,
            "is_making_progress": True,
            "is_stuck_in_loop": False,
            "next_action_hint": "",
        }

    # Update stall count
    if ledger.get("is_making_progress", True):
        new_stall_count = 0
    else:
        new_stall_count = stall_count + 1

    # Append next_action_hint to action_history for context
    updated_history = list(action_history)
    hint = ledger.get("next_action_hint", "")
    if hint:
        updated_history.append(f"hint:{hint[:50]}")
    updated_history = updated_history[-5:]

    return {
        "progress_ledger": ledger,
        "stall_count": new_stall_count,
        "action_history": updated_history,
    }


async def replan_node(state: AgentState, *, llm: Any) -> dict[str, Any]:
    """Generate a new strategy when the agent is stuck."""
    messages = _compress_messages(state["messages"])
    session_id = state.get("session_id", "")

    system_content = REPLAN_SYSTEM_PROMPT
    if session_id:
        system_content += f"\n\nCurrent session_id: {session_id}"

    # Include a concise summary of recent failures
    action_history = state.get("action_history") or []
    replan_input = [
        SystemMessage(content=system_content),
        HumanMessage(
            content=(
                f"Actions tried so far: {', '.join(action_history) or 'none'}.\n"
                "What different approach should be taken?"
            )
        ),
    ]

    response = await llm.ainvoke(replan_input)
    return {
        "messages": [response],
        "stall_count": 0,
        "action_history": [],
    }


# ---------------------------------------------------------------------------
# Routing functions
# ---------------------------------------------------------------------------


def route_after_actor(state: AgentState) -> str:
    """After actor: go to tools if there are tool calls, else END."""
    messages = state["messages"]
    last_message = messages[-1] if messages else None

    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        return "tools"
    return END


def route_after_progress(state: AgentState) -> str:
    """After progress_check: route based on task state and stall count."""
    ledger = state.get("progress_ledger") or {}
    stall_count = state.get("stall_count") or 0

    if ledger.get("is_task_complete", False):
        return END

    if ledger.get("is_stuck_in_loop", False) or stall_count >= MAX_STALL_COUNT:
        return "replan"

    # Making progress (or fallback) — skip planner, go directly to actor
    return "actor"


# ---------------------------------------------------------------------------
# LangGraph graph builder (Progress Ledger)
# ---------------------------------------------------------------------------


def build_browser_graph(
    llm_with_tools: Any,
    planner_llm: Any,
    tools: list,
    checkpointer: Any,
) -> Any:
    """Compile a Progress Ledger LangGraph for the browser agent.

    Graph flow:
      START → planner → actor → route_after_actor
        → tools → progress_check → route_after_progress
          → is_task_complete → END
          → is_making_progress → actor   (planner skipped when progressing)
          → stall_count >= 3 or stuck_in_loop → replan → actor
        → no_tool_calls → END
    """
    builder = StateGraph(AgentState)

    builder.add_node("planner", partial(planner_node, llm=planner_llm))
    builder.add_node("actor", partial(actor_node, llm_with_tools=llm_with_tools))
    builder.add_node("tools", ToolNode(tools))
    builder.add_node(
        "progress_check", partial(progress_check_node, llm=planner_llm)
    )
    builder.add_node("replan", partial(replan_node, llm=planner_llm))

    builder.set_entry_point("planner")

    # planner -> actor (always, initial planning only)
    builder.add_edge("planner", "actor")

    # actor -> tools (if tool calls) or END (if final answer)
    builder.add_conditional_edges(
        "actor",
        route_after_actor,
        {"tools": "tools", END: END},
    )

    # tools -> progress_check
    builder.add_edge("tools", "progress_check")

    # progress_check -> actor / replan / END
    builder.add_conditional_edges(
        "progress_check",
        route_after_progress,
        {"actor": "actor", "replan": "replan", END: END},
    )

    # replan -> actor (re-execute with new strategy)
    builder.add_edge("replan", "actor")

    return builder.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: connect to Gateway, build LLMs and graph; shutdown: clean up."""
    global _gateway_client, _vl_llm

    agent_settings = BrowserAgentSettings()
    llm_settings = LLMSettings()

    # AsyncPostgresSaver requires a plain postgresql:// DSN (psycopg).
    db_url = agent_settings.database_url.replace(
        "postgresql+asyncpg://", "postgresql://"
    )

    # Initialise Gateway client (singleton, reused across requests)
    _gateway_client = GatewayBrowserToolsClient(
        gateway_url=agent_settings.gateway_url,
        timeout=agent_settings.browser_tool_timeout,
    )
    await _gateway_client.start()

    # Main actor LLM (tool calling, higher quality model)
    actor_llm = create_ollama_llm(agent_settings.browser_model, llm_settings)
    llm_with_tools = actor_llm.bind_tools(BROWSER_TOOLS)

    # Planner/progress-check/replan use lighter model for speed
    planner_llm = create_ollama_llm(agent_settings.planner_model, llm_settings)

    # Vision-language model for DOM-failure fallback (streaming disabled —
    # the VL model is called directly via ainvoke, not streamed to the user)
    _vl_llm = create_ollama_llm(
        agent_settings.vision_model, llm_settings, streaming=False
    )

    async with AsyncPostgresSaver.from_conn_string(db_url) as checkpointer:
        await checkpointer.setup()

        app.state.graph = build_browser_graph(
            llm_with_tools, planner_llm, BROWSER_TOOLS, checkpointer
        )

        logger.info(
            "Browser Agent ready -- actor=%s, planner=%s, vision=%s, gateway=%s, tools=%d",
            agent_settings.browser_model,
            agent_settings.planner_model,
            agent_settings.vision_model,
            agent_settings.gateway_url,
            len(BROWSER_TOOLS),
        )
        yield

    if _gateway_client:
        await _gateway_client.close()
    _gateway_client = None
    _vl_llm = None


app = FastAPI(title="Browser Agent", version="0.3.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, Any]:
    gateway_ok = False
    gw_url = BrowserAgentSettings().gateway_url
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            resp = await c.get(f"{gw_url.rstrip('/')}/health")
            gateway_ok = resp.is_success
    except Exception:
        pass

    overall = "ok" if gateway_ok else "degraded"
    return {
        "status": overall,
        "service": "browser-agent",
        "gateway": "ok" if gateway_ok else "unavailable",
    }


# ACP endpoints: /runs, /runs/stream
router = create_acp_router(lambda request: request.app.state.graph)
app.include_router(router)
