"""Chat Agent -- FastAPI + LangGraph ReAct agent with web search tools.

Exposes ACP endpoints (/runs, /runs/stream, /health) for the orchestrator
to invoke. Uses DuckDuckGo search and webpage fetching as tools so the LLM
can answer questions that require up-to-date information.
"""

from __future__ import annotations

import html
import logging
import re
from contextlib import asynccontextmanager
from typing import Annotated, Any
from urllib.parse import quote_plus

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

class ChatAgentSettings(BaseSettings):
    """Environment-driven configuration for the Chat Agent."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = (
        "postgresql+asyncpg://postgres:password@postgres:5432/browser_agent"
    )
    chat_model: str = "qwen3:8b"

    # Inherited LLM settings are loaded by LLMSettings separately.


# ---------------------------------------------------------------------------
# Web search tools
# ---------------------------------------------------------------------------

_HTTP_TIMEOUT = httpx.Timeout(15.0, connect=10.0)
_SEARCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}


def _extract_text(raw_html: str) -> str:
    """Strip HTML tags and collapse whitespace from *raw_html*."""
    text = re.sub(r"<script[^>]*>.*?</script>", "", raw_html, flags=re.S)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


@tool
async def web_search(query: str, max_results: int = 5) -> list[dict[str, str]]:
    """Search the web for information using DuckDuckGo.

    Args:
        query: The search query string.
        max_results: Maximum number of results to return (default 5).

    Returns:
        A list of dicts with ``title``, ``url``, and ``snippet`` keys.
    """
    encoded_query = quote_plus(query)
    url = f"https://lite.duckduckgo.com/lite/?q={encoded_query}"

    async with httpx.AsyncClient(
        timeout=_HTTP_TIMEOUT, headers=_SEARCH_HEADERS, follow_redirects=True,
    ) as client:
        response = await client.get(url)
        response.raise_for_status()

    body = response.text
    results: list[dict[str, str]] = []

    # DuckDuckGo Lite returns results in <a class="result-link"> or table rows.
    # We parse with simple regex to avoid heavy HTML parser dependency.
    link_pattern = re.compile(
        r'<a[^>]+rel="nofollow"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        re.S,
    )
    snippet_pattern = re.compile(
        r'<td[^>]*class="result-snippet"[^>]*>(.*?)</td>',
        re.S,
    )

    links = link_pattern.findall(body)
    snippets = snippet_pattern.findall(body)

    for idx, (href, title_html) in enumerate(links):
        if idx >= max_results:
            break
        title_text = _extract_text(title_html).strip()
        snippet_text = _extract_text(snippets[idx]).strip() if idx < len(snippets) else ""
        if href and title_text:
            results.append({
                "title": title_text,
                "url": href,
                "snippet": snippet_text,
            })

    if not results:
        return [{"title": "No results", "url": "", "snippet": f"No results found for: {query}"}]

    return results


@tool
async def fetch_webpage(url: str, max_chars: int = 8000) -> dict[str, str]:
    """Fetch and extract text content from a webpage.

    Args:
        url: The URL to fetch.
        max_chars: Maximum number of characters to return (default 8000).

    Returns:
        A dict with ``url``, ``title``, and ``content`` keys.
    """
    async with httpx.AsyncClient(
        timeout=_HTTP_TIMEOUT, headers=_SEARCH_HEADERS, follow_redirects=True,
    ) as client:
        response = await client.get(url)
        response.raise_for_status()

    body = response.text

    # Extract <title>
    title_match = re.search(r"<title[^>]*>(.*?)</title>", body, re.S | re.I)
    title = _extract_text(title_match.group(1)) if title_match else ""

    content = _extract_text(body)
    if len(content) > max_chars:
        content = content[:max_chars] + "... [truncated]"

    return {"url": url, "title": title, "content": content}


# ---------------------------------------------------------------------------
# LangGraph state & graph builder
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are a helpful AI assistant. You can search the web for current "
    "information using the web_search tool and fetch specific pages with "
    "fetch_webpage. When users ask questions that may require up-to-date "
    "information, use web_search first. Always cite sources when using "
    "search results by including the URL."
)


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


def build_chat_graph(
    llm_with_tools: Any,
    tools: list,
    checkpointer: Any,
) -> Any:
    """Compile a ReAct-style LangGraph for the chat agent.

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
    """Startup: initialise LLM, tools, graph and checkpointer."""
    settings = ChatAgentSettings()
    llm_settings = LLMSettings()

    # AsyncPostgresSaver requires a plain postgresql:// DSN (psycopg).
    db_url = settings.database_url.replace(
        "postgresql+asyncpg://", "postgresql://"
    )

    async with AsyncPostgresSaver.from_conn_string(db_url) as checkpointer:
        await checkpointer.setup()

        llm = create_ollama_llm(settings.chat_model, llm_settings)
        tools = [web_search, fetch_webpage]
        llm_with_tools = llm.bind_tools(tools)

        app.state.graph = build_chat_graph(llm_with_tools, tools, checkpointer)
        logger.info(
            "Chat Agent ready -- model=%s, tools=%s",
            settings.chat_model,
            [t.name for t in tools],
        )
        yield


app = FastAPI(title="Chat Agent", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, Any]:
    ollama_ok = False
    llm_settings = LLMSettings()
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            resp = await c.get(f"{llm_settings.ollama_base_url.rstrip('/')}/api/tags")
            ollama_ok = resp.is_success
    except Exception:
        pass

    overall = "ok" if ollama_ok else "degraded"
    return {
        "status": overall,
        "service": "chat-agent",
        "ollama": "ok" if ollama_ok else "unavailable",
    }


# ACP endpoints: /runs, /runs/stream
router = create_acp_router(lambda request: request.app.state.graph)
app.include_router(router)
