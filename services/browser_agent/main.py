"""Browser Agent -- FastAPI + LangGraph ReAct agent with HTTP browser tools.

Defines browser-control tools as @tool-decorated functions that call the
Gateway's browser-tools HTTP API.  Exposes ACP endpoints (/runs, /runs/stream,
/health) for the orchestrator.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Annotated, Any, Optional

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


# Module-level settings reference, set during lifespan startup.
_settings: BrowserAgentSettings | None = None


# ---------------------------------------------------------------------------
# Gateway Browser Tools Client
# ---------------------------------------------------------------------------

class GatewayBrowserToolsClient:
    """Thin HTTP client that invokes browser tools through the Gateway."""

    def __init__(self, gateway_url: str, session_id: str) -> None:
        self._url = (
            f"{gateway_url}/sessions/{session_id}/browser-tools/invoke"
        )

    async def invoke(self, tool_name: str, params: dict) -> Any:
        """POST a tool invocation to the Gateway and return the result.

        Raises ``RuntimeError`` when the Gateway responds with an error.
        """
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                self._url,
                json={"tool": tool_name, "params": params},
            )
            response.raise_for_status()
            body = response.json()

        if "error" in body:
            raise RuntimeError(
                f"Browser tool '{tool_name}' failed: {body['error']}"
            )
        return body.get("result")


# ---------------------------------------------------------------------------
# Browser Tools (@tool decorated)
# ---------------------------------------------------------------------------

@tool
async def navigate(session_id: str, url: str) -> str:
    """Navigate the browser tab to the given URL."""
    client = GatewayBrowserToolsClient(_settings.gateway_url, session_id)
    result = await client.invoke("navigate", {"url": url})
    return str(result)


@tool
async def click_element(
    session_id: str,
    selector: str,
    description: Optional[str] = None,
) -> str:
    """Click an element on the page identified by a CSS selector."""
    params: dict[str, Any] = {"selector": selector}
    if description is not None:
        params["description"] = description
    client = GatewayBrowserToolsClient(_settings.gateway_url, session_id)
    result = await client.invoke("click", params)
    return str(result)


@tool
async def type_text(
    session_id: str,
    selector: str,
    text: str,
    clear: bool = True,
) -> str:
    """Type text into an input element identified by a CSS selector."""
    client = GatewayBrowserToolsClient(_settings.gateway_url, session_id)
    result = await client.invoke(
        "type_text", {"selector": selector, "text": text, "clear": clear},
    )
    return str(result)


@tool
async def scroll_page(
    session_id: str,
    direction: str,
    amount: int = 3,
) -> str:
    """Scroll the page in the given direction ('up' or 'down')."""
    client = GatewayBrowserToolsClient(_settings.gateway_url, session_id)
    result = await client.invoke(
        "scroll", {"direction": direction, "amount": amount},
    )
    return str(result)


@tool
async def take_screenshot(session_id: str) -> str:
    """Take a screenshot of the current browser tab."""
    client = GatewayBrowserToolsClient(_settings.gateway_url, session_id)
    result = await client.invoke("take_screenshot", {})
    return str(result)


@tool
async def extract_content(
    session_id: str,
    selector: Optional[str] = None,
) -> str:
    """Extract text content from the page, optionally scoped by a CSS selector."""
    params: dict[str, Any] = {}
    if selector is not None:
        params["selector"] = selector
    client = GatewayBrowserToolsClient(_settings.gateway_url, session_id)
    result = await client.invoke("extract_content", params)
    return str(result)


@tool
async def wait_for_element(
    session_id: str,
    selector: str,
    timeout_ms: int = 5000,
) -> str:
    """Wait for an element matching the CSS selector to appear on the page."""
    client = GatewayBrowserToolsClient(_settings.gateway_url, session_id)
    result = await client.invoke(
        "wait_for_element", {"selector": selector, "timeout_ms": timeout_ms},
    )
    return str(result)


@tool
async def evaluate_js(session_id: str, script: str) -> str:
    """Evaluate a JavaScript expression in the browser tab and return the result."""
    client = GatewayBrowserToolsClient(_settings.gateway_url, session_id)
    result = await client.invoke("evaluate_js", {"script": script})
    return str(result)


@tool
async def get_page_info(session_id: str) -> str:
    """Get information about the current page (URL, title, etc.)."""
    client = GatewayBrowserToolsClient(_settings.gateway_url, session_id)
    result = await client.invoke("get_page_info", {})
    return str(result)


# Complete list of browser tools available to the agent.
BROWSER_TOOLS: list = [
    navigate,
    click_element,
    type_text,
    scroll_page,
    take_screenshot,
    extract_content,
    wait_for_element,
    evaluate_js,
    get_page_info,
]


# ---------------------------------------------------------------------------
# LangGraph state & graph builder
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are a browser automation agent. You have tools to control a "
    "browser tab: navigate to URLs, click elements, type text, take "
    "screenshots, and extract page content.\n\n"
    "Guidelines:\n"
    "1. Always start by navigating to the relevant URL.\n"
    "2. Use screenshots to verify your actions succeeded.\n"
    "3. The session_id is provided in the conversation context. "
    "You MUST include it in every tool call.\n"
    "4. If a tool call fails, retry once before reporting the error.\n"
    "5. Report the outcome of each step to the user clearly."
)


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


def build_browser_graph(
    llm_with_tools: Any,
    tools: list,
    checkpointer: Any,
) -> Any:
    """Compile a ReAct-style LangGraph for the browser agent.

    The graph alternates between the LLM node and the tool-execution node,
    using ``tools_condition`` to decide whether to loop or finish.
    """
    builder = StateGraph(AgentState)

    async def call_model(state: AgentState) -> dict[str, Any]:
        messages = state["messages"]
        # Inject the system prompt if it is not already present.
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=SYSTEM_PROMPT), *messages]
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
    """Startup: build LLM, bind tools, compile graph; shutdown: clean up."""
    global _settings  # noqa: PLW0603
    _settings = BrowserAgentSettings()
    llm_settings = LLMSettings()

    # AsyncPostgresSaver requires a plain postgresql:// DSN (psycopg).
    db_url = _settings.database_url.replace(
        "postgresql+asyncpg://", "postgresql://"
    )

    tools = BROWSER_TOOLS

    async with await AsyncPostgresSaver.from_conn_string(db_url) as checkpointer:
        await checkpointer.setup()

        llm = create_ollama_llm(_settings.browser_model, llm_settings)
        llm_with_tools = llm.bind_tools(tools)

        app.state.graph = build_browser_graph(
            llm_with_tools, tools, checkpointer,
        )

        logger.info(
            "Browser Agent ready -- model=%s, gateway_url=%s, tools=%d",
            _settings.browser_model,
            _settings.gateway_url,
            len(tools),
        )
        yield


app = FastAPI(title="Browser Agent", version="0.1.0", lifespan=lifespan)

# ACP endpoints: /runs, /runs/stream, /health
router = create_acp_router(lambda request: request.app.state.graph)
app.include_router(router)
