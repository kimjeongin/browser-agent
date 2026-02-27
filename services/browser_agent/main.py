"""Browser Agent -- FastAPI + LangGraph Planner-Actor-Validator agent with Gateway browser tools.

Connects directly to the Gateway service to invoke browser tools on the
user's Chrome extension. No longer depends on the Browser Relay MCP server.

webMCP-inspired tool invocation flow:
  LangGraph tool call
    -> GatewayBrowserToolsClient.invoke(session_id, tool_name, params)
    -> POST /sessions/{id}/browser-tools/invoke (Gateway, blocking 60s)
    -> Gateway SSE -> Extension executes DOM action
    -> Extension POST /browser-tools/result/{inv_id}
    -> Gateway Future resolved -> response returned to agent

Agent architecture: Planner-Actor-Validator (P2-1)
  planner -> actor -> tools -> validator -> planner (loop)
  - Planner: lightweight LLM decides next action
  - Actor: tool-calling LLM executes browser actions
  - Validator: lightweight LLM checks if action succeeded
"""

from __future__ import annotations

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
    browser_model: str = "qwen2.5:14b"
    planner_model: str = "llama3.1:8b"  # lighter model for planning/validation
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


# Module-level client singleton (initialised in lifespan)
_gateway_client: GatewayBrowserToolsClient | None = None


def _get_client() -> GatewayBrowserToolsClient:
    if _gateway_client is None:
        raise RuntimeError("GatewayBrowserToolsClient not initialised")
    return _gateway_client


# ---------------------------------------------------------------------------
# LangGraph state (P2-1: extended for Planner-Actor-Validator)
# ---------------------------------------------------------------------------


class AgentState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    session_id: str
    # Planner-Actor-Validator additions
    validation_failed: bool  # True if last validation failed
    retry_count: int  # How many times current action was retried


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_VALIDATION_RETRIES = 2  # Maximum retries for a failed action


# ---------------------------------------------------------------------------
# P1-5: session_id validation helper
# ---------------------------------------------------------------------------


def _validate_session_id(session_id: str) -> dict[str, Any] | None:
    """Return an error dict if session_id is invalid, else None."""
    if not session_id or not session_id.strip():
        return {
            "error": (
                "session_id is missing or empty. This is a critical error. "
                "Always include the session_id from the conversation state "
                "in every tool call."
            ),
            "success": False,
        }
    return None


# ---------------------------------------------------------------------------
# Browser tools (LangChain @tool, calling Gateway)
# ---------------------------------------------------------------------------
# session_id is passed as a parameter to each tool.
# The LLM is instructed to always include the session_id from state.


@tool
async def browser_navigate(session_id: str, url: str) -> dict[str, Any]:
    """Navigate the AI-controlled browser tab to a URL.

    Args:
        session_id: Active session ID linked to the user's browser.
        url: Full URL to navigate to (e.g. https://www.youtube.com).
    """
    if err := _validate_session_id(session_id):
        return err
    return await _get_client().invoke(session_id, "navigate", {"url": url})


@tool
async def browser_click(
    session_id: str,
    selector: str,
    fallback_selectors: list[str] | None = None,
    element_text: str | None = None,
) -> dict[str, Any]:
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
    return await _get_client().invoke(session_id, "click", params)


@tool
async def browser_type(
    session_id: str,
    selector: str,
    text: str,
    clear_first: bool = True,
) -> dict[str, Any]:
    """Type text into an input field.

    Args:
        session_id: Active session ID.
        selector: CSS selector of the input element.
        text: Text to type.
        clear_first: Whether to clear the field before typing.
    """
    if err := _validate_session_id(session_id):
        return err
    return await _get_client().invoke(
        session_id,
        "type",
        {"selector": selector, "text": text, "clear_first": clear_first},
    )


@tool
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
    if err := _validate_session_id(session_id):
        return err
    params: dict[str, Any] = {"direction": direction, "amount": amount}
    if selector:
        params["selector"] = selector
    return await _get_client().invoke(session_id, "scroll", params)


@tool
async def browser_screenshot(session_id: str) -> dict[str, Any]:
    """Capture a screenshot of the current browser tab.

    Args:
        session_id: Active session ID.

    Returns:
        Dict with 'screenshot' key containing base64-encoded PNG.
    """
    if err := _validate_session_id(session_id):
        return err
    return await _get_client().invoke(session_id, "screenshot", {})


@tool
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
    if err := _validate_session_id(session_id):
        return err
    params: dict[str, Any] = {"include_html": include_html}
    if selector:
        params["selector"] = selector
    return await _get_client().invoke(session_id, "extract_content", params)


@tool
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
    if err := _validate_session_id(session_id):
        return err
    return await _get_client().invoke(
        session_id,
        "wait_for_element",
        {"selector": selector, "timeout_ms": timeout_ms, "visible": visible},
    )


@tool
async def get_page_info(session_id: str) -> dict[str, Any]:
    """Get current page URL, title, and ready state.

    Args:
        session_id: Active session ID.
    """
    if err := _validate_session_id(session_id):
        return err
    return await _get_client().invoke(session_id, "get_page_info", {})


@tool
async def browser_get_structured_dom(session_id: str) -> dict[str, Any]:
    """Get a compact, structured representation of interactive page elements.

    Returns only visible, interactable elements in the current viewport.
    Use this INSTEAD of browser_extract_content to understand page structure.
    Much more token-efficient than extracting full page text.

    Args:
        session_id: Active session ID linked to the user's browser.

    Returns:
        Dict with 'url', 'title', 'interactable_count', 'elements' (up to 50),
        and 'page_text_preview' (first 2000 chars of page text).
    """
    if err := _validate_session_id(session_id):
        return err
    return await _get_client().invoke(session_id, "get_structured_dom", {})


BROWSER_TOOLS = [
    browser_navigate,
    browser_click,
    browser_type,
    browser_scroll,
    browser_screenshot,
    browser_extract_content,
    browser_wait_for_element,
    get_page_info,
    browser_get_structured_dom,
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

Efficiency:
- browser_get_structured_dom is fast and token-efficient. Use it first.
- Screenshots consume many tokens. Use sparingly.
- Always include session_id in every single tool call.

For YouTube tasks:
- Navigate to https://www.youtube.com
- Use browser_get_structured_dom to find the search input
- Search box selector: input#search or ytd-searchbox input
- After search, click on the most relevant video result
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

VALIDATOR_SYSTEM_PROMPT = """\
You are a browser action validator. Check if the last action succeeded.

Look at the most recent tool result and determine:
1. Did the action execute without errors?
2. Is the page in the expected state for the next step?

Respond with ONLY one word:
- "success" if the action worked correctly
- "retry" if the action failed but should be retried
- "done" if the entire task is complete

Do not add any explanation.
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
# P2-1: Planner-Actor-Validator graph nodes
# ---------------------------------------------------------------------------


async def planner_node(state: AgentState, *, llm: Any) -> dict[str, Any]:
    """Analyze current state and decide next action."""
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
    return {
        "messages": [response],
        "validation_failed": False,
    }


async def actor_node(state: AgentState, *, llm_with_tools: Any) -> dict[str, Any]:
    """Execute the planned action using browser tools."""
    messages = _compress_messages(state["messages"])
    session_id = state.get("session_id", "")

    system_content = SYSTEM_PROMPT
    if session_id:
        system_content += (
            f"\n\nCurrent session_id: {session_id}\n"
            "Always use this session_id in your tool calls."
        )

    # Replace or inject system prompt
    if not messages or not isinstance(messages[0], SystemMessage):
        messages = [SystemMessage(content=system_content), *messages]
    else:
        messages = [SystemMessage(content=system_content), *messages[1:]]

    response = await llm_with_tools.ainvoke(messages)
    return {"messages": [response]}


async def validator_node(state: AgentState, *, llm: Any) -> dict[str, Any]:
    """Verify if the last action succeeded."""
    messages = state["messages"]

    # Find the most recent tool result
    last_tool_result = None
    for msg in reversed(messages):
        if isinstance(msg, ToolMessage):
            last_tool_result = msg.content
            break

    if last_tool_result is None:
        # No tool was called, task may be done
        return {"validation_failed": False, "retry_count": 0}

    # Quick validation using lightweight LLM
    validation_input = [
        SystemMessage(content=VALIDATOR_SYSTEM_PROMPT),
        HumanMessage(content=f"Tool result: {str(last_tool_result)[:500]}"),
    ]

    response = await llm.ainvoke(validation_input)
    verdict = (
        response.content.strip().lower()
        if hasattr(response, "content")
        else "success"
    )

    if verdict == "retry":
        current_retries = state.get("retry_count", 0) or 0
        return {
            "validation_failed": True,
            "retry_count": current_retries + 1,
        }

    return {
        "validation_failed": False,
        "retry_count": 0,
    }


# ---------------------------------------------------------------------------
# P2-1: Routing functions
# ---------------------------------------------------------------------------


def route_after_actor(state: AgentState) -> str:
    """After actor: go to tools if there are tool calls, else END."""
    messages = state["messages"]
    last_message = messages[-1] if messages else None

    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        return "tools"
    return END


def route_after_validator(state: AgentState) -> str:
    """After validator: retry actor if failed, else go to planner for next step."""
    validation_failed = state.get("validation_failed", False) or False
    retry_count = state.get("retry_count", 0) or 0

    if validation_failed and retry_count < MAX_VALIDATION_RETRIES:
        return "actor"  # retry
    return "planner"  # next step or end


# ---------------------------------------------------------------------------
# LangGraph graph builder (P2-1: Planner-Actor-Validator)
# ---------------------------------------------------------------------------


def build_browser_graph(
    llm_with_tools: Any,
    planner_llm: Any,
    validator_llm: Any,
    tools: list,
    checkpointer: Any,
) -> Any:
    """Compile a Planner-Actor-Validator LangGraph for the browser agent."""
    builder = StateGraph(AgentState)

    builder.add_node("planner", partial(planner_node, llm=planner_llm))
    builder.add_node("actor", partial(actor_node, llm_with_tools=llm_with_tools))
    builder.add_node("tools", ToolNode(tools))
    builder.add_node("validator", partial(validator_node, llm=validator_llm))

    builder.set_entry_point("planner")

    # planner -> actor (always, planner just plans)
    builder.add_edge("planner", "actor")

    # actor -> tools (if tool calls) or END (if final answer)
    builder.add_conditional_edges(
        "actor",
        route_after_actor,
        {"tools": "tools", END: END},
    )

    # tools -> validator
    builder.add_edge("tools", "validator")

    # validator -> actor (retry) or planner (next step)
    builder.add_conditional_edges(
        "validator",
        route_after_validator,
        {"actor": "actor", "planner": "planner"},
    )

    return builder.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: connect to Gateway, build LLMs and graph; shutdown: clean up."""
    global _gateway_client

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

    # Planner and validator use lighter model for speed
    planner_llm = create_ollama_llm(agent_settings.planner_model, llm_settings)
    validator_llm = create_ollama_llm(agent_settings.planner_model, llm_settings)

    async with await AsyncPostgresSaver.from_conn_string(db_url) as checkpointer:
        await checkpointer.setup()

        app.state.graph = build_browser_graph(
            llm_with_tools, planner_llm, validator_llm, BROWSER_TOOLS, checkpointer
        )

        logger.info(
            "Browser Agent ready -- actor=%s, planner=%s, gateway=%s, tools=%d",
            agent_settings.browser_model,
            agent_settings.planner_model,
            agent_settings.gateway_url,
            len(BROWSER_TOOLS),
        )
        yield

    if _gateway_client:
        await _gateway_client.close()
    _gateway_client = None


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
