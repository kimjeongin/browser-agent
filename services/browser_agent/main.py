"""Browser Agent -- FastAPI + LangGraph ReAct agent with Gateway browser tools.

Connects directly to the Gateway service to invoke browser tools on the
user's Chrome extension. No longer depends on the Browser Relay MCP server.

webMCP-inspired tool invocation flow:
  LangGraph tool call
    → GatewayBrowserToolsClient.invoke(session_id, tool_name, params)
    → POST /sessions/{id}/browser-tools/invoke (Gateway, blocking 60s)
    → Gateway SSE → Extension executes DOM action
    → Extension POST /browser-tools/result/{inv_id}
    → Gateway Future resolved → response returned to agent
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Annotated, Any

import httpx
from fastapi import FastAPI
from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
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
    gateway_url: str = "http://gateway:8000"
    browser_tool_timeout: float = 65.0  # slightly longer than gateway timeout


# ---------------------------------------------------------------------------
# Gateway Browser Tools Client
# ---------------------------------------------------------------------------


class GatewayBrowserToolsClient:
    """HTTP client for invoking browser tools via the Gateway.

    The Gateway holds the asyncio.Queue → SSE → Extension pipeline.
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
# LangGraph state
# ---------------------------------------------------------------------------


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    session_id: str


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
    return await _get_client().invoke(session_id, "navigate", {"url": url})


@tool
async def browser_click(session_id: str, selector: str) -> dict[str, Any]:
    """Click an element in the browser tab.

    Args:
        session_id: Active session ID.
        selector: CSS selector of the element to click.
    """
    return await _get_client().invoke(session_id, "click", {"selector": selector})


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
    return await _get_client().invoke(session_id, "get_page_info", {})


BROWSER_TOOLS = [
    browser_navigate,
    browser_click,
    browser_type,
    browser_scroll,
    browser_screenshot,
    browser_extract_content,
    browser_wait_for_element,
    get_page_info,
]

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a browser automation agent. You have tools to control a browser tab:
navigate to URLs, click elements, type text, take screenshots, and extract content.

The user's session_id is available in the conversation state. You MUST include
it in every tool call.

Guidelines:
1. Always start by navigating to the relevant URL using browser_navigate.
2. After navigation, wait briefly then take a screenshot to verify the page loaded.
3. Use get_page_info to check the current URL and title before acting.
4. For search tasks: navigate to the site → click/type in search box → press Enter or click search button → wait for results → click the desired result.
5. If a selector fails, try alternatives (e.g. input[name="search_query"] for YouTube search).
6. Use browser_wait_for_element to wait for dynamic content before interacting.
7. Take screenshots at key steps to verify your progress.
8. Report each action you take and its result to the user clearly.
9. If a tool call fails, try once with a different selector before reporting failure.

For YouTube tasks:
- Navigate to https://www.youtube.com
- Search box selector: input#search or ytd-searchbox input
- Search button: button#search-icon-legacy or ytd-searchbox button[aria-label='Search']
- After search, click on the most relevant video result
- Verify playback with a screenshot
"""


# ---------------------------------------------------------------------------
# LangGraph graph builder
# ---------------------------------------------------------------------------


def build_browser_graph(
    llm_with_tools: Any,
    tools: list,
    checkpointer: Any,
) -> Any:
    """Compile a ReAct-style LangGraph for the browser agent."""
    builder = StateGraph(AgentState)

    async def call_model(state: AgentState) -> dict[str, Any]:
        messages = state["messages"]
        session_id = state.get("session_id", "")

        # Inject system prompt with session_id context
        system_content = SYSTEM_PROMPT
        if session_id:
            system_content += f"\n\nCurrent session_id: {session_id}\nAlways use this session_id in your tool calls."

        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=system_content), *messages]

        response = await llm_with_tools.ainvoke(messages)
        return {"messages": [response]}

    builder.add_node("agent", call_model)
    builder.add_node("tools", ToolNode(tools))
    builder.set_entry_point("agent")
    builder.add_conditional_edges("agent", tools_condition)
    builder.add_edge("tools", "agent")

    return builder.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: connect to Gateway, build LLM and graph; shutdown: clean up."""
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

    llm = create_ollama_llm(agent_settings.browser_model, llm_settings)
    llm_with_tools = llm.bind_tools(BROWSER_TOOLS)

    async with await AsyncPostgresSaver.from_conn_string(db_url) as checkpointer:
        await checkpointer.setup()

        app.state.graph = build_browser_graph(
            llm_with_tools, BROWSER_TOOLS, checkpointer
        )

        logger.info(
            "Browser Agent ready -- model=%s, gateway=%s, tools=%d",
            agent_settings.browser_model,
            agent_settings.gateway_url,
            len(BROWSER_TOOLS),
        )
        yield

    if _gateway_client:
        await _gateway_client.close()
    _gateway_client = None


app = FastAPI(title="Browser Agent", version="0.2.0", lifespan=lifespan)


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
