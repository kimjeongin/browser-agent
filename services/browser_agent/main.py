"""Browser Agent -- FastAPI + LangGraph ReAct agent with MCP browser tools.

Connects to the Browser Relay MCP server over streamable-HTTP transport,
loads browser-control tools via ``langchain-mcp-adapters``, and exposes
ACP endpoints (/runs, /runs/stream, /health) for the orchestrator.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import FastAPI
from langchain_core.messages import BaseMessage, SystemMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from langchain_mcp_adapters.tools import load_mcp_tools
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
    browser_relay_mcp_url: str = "http://browser-relay:8010/mcp"


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
    "Include it in every tool call that requires it.\n"
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
# MCP connection management
# ---------------------------------------------------------------------------

class MCPConnection:
    """Manages the lifecycle of a persistent MCP client session.

    The MCP streamable-HTTP transport must stay open for the entire
    application lifetime so that the agent can invoke browser tools on
    demand without reconnecting for every request.
    """

    def __init__(self) -> None:
        self._transport_cm: Any = None
        self._session_cm: Any = None
        self.session: ClientSession | None = None
        self.tools: list = []

    async def connect(self, mcp_url: str) -> list:
        """Open transport and session, then load LangChain tools."""
        self._transport_cm = streamablehttp_client(mcp_url)
        read, write, _ = await self._transport_cm.__aenter__()

        self._session_cm = ClientSession(read, write)
        self.session = await self._session_cm.__aenter__()
        await self.session.initialize()

        self.tools = await load_mcp_tools(self.session)
        logger.info(
            "MCP connected -- loaded %d tools: %s",
            len(self.tools),
            [t.name for t in self.tools],
        )
        return self.tools

    async def disconnect(self) -> None:
        """Gracefully close session and transport."""
        if self._session_cm is not None:
            try:
                await self._session_cm.__aexit__(None, None, None)
            except Exception:
                logger.warning("MCP session close failed", exc_info=True)
        if self._transport_cm is not None:
            try:
                await self._transport_cm.__aexit__(None, None, None)
            except Exception:
                logger.warning("MCP transport close failed", exc_info=True)
        self.session = None
        self.tools = []


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: connect MCP, build LLM and graph; shutdown: clean up."""
    settings = BrowserAgentSettings()
    llm_settings = LLMSettings()

    # AsyncPostgresSaver requires a plain postgresql:// DSN (psycopg).
    db_url = settings.database_url.replace(
        "postgresql+asyncpg://", "postgresql://"
    )

    mcp_conn = MCPConnection()

    async with await AsyncPostgresSaver.from_conn_string(db_url) as checkpointer:
        await checkpointer.setup()

        # Connect to Browser Relay MCP and load tools.
        tools = await mcp_conn.connect(settings.browser_relay_mcp_url)

        if not tools:
            logger.warning(
                "No tools loaded from MCP at %s -- the browser agent "
                "will not be able to control the browser.",
                settings.browser_relay_mcp_url,
            )

        llm = create_ollama_llm(settings.browser_model, llm_settings)
        llm_with_tools = llm.bind_tools(tools) if tools else llm

        app.state.graph = build_browser_graph(
            llm_with_tools, tools, checkpointer,
        )
        app.state.mcp_connection = mcp_conn

        logger.info(
            "Browser Agent ready -- model=%s, mcp_url=%s, tools=%d",
            settings.browser_model,
            settings.browser_relay_mcp_url,
            len(tools),
        )
        yield

    # Cleanup: close MCP connection (outside the checkpointer context).
    await mcp_conn.disconnect()


app = FastAPI(title="Browser Agent", version="0.1.0", lifespan=lifespan)

# ACP endpoints: /runs, /runs/stream, /health
router = create_acp_router(lambda request: request.app.state.graph)
app.include_router(router)
