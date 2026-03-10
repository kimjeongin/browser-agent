"""Chat Agent -- acp_sdk Server wrapping a LangGraph ReAct agent.

Exposes ACP agent endpoints via acp_sdk's create_app. Uses DuckDuckGo search
and webpage fetching as tools so the LLM can answer questions that require
up-to-date information.
"""

from __future__ import annotations

import html
import ipaddress
import json
import logging
import re
from contextlib import asynccontextmanager
from typing import Annotated, Any
from urllib.parse import quote_plus, urlparse

import httpx

# acp_sdk 1.0.3 references uvicorn.config.LoopSetupType which was removed
# in uvicorn >= 0.34. Patch it before importing acp_sdk.server.
import uvicorn.config as _uvicorn_config

if not hasattr(_uvicorn_config, "LoopSetupType"):
    _uvicorn_config.LoopSetupType = str

from acp_sdk.models import Message, MessagePart
from acp_sdk.server import Context, agent, create_app
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from langgraph.graph import StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from pydantic_settings import SettingsConfigDict
from typing_extensions import TypedDict

from shared.llm import CommonAgentSettings, LLMSettings, create_ollama_llm
from shared.observability import setup_telemetry, shutdown_telemetry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class ChatAgentSettings(CommonAgentSettings):
    """Environment-driven configuration for the Chat Agent."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    chat_model: str = "qwen3:8b"


# ---------------------------------------------------------------------------
# Web search tools
# ---------------------------------------------------------------------------

_HTTP_TIMEOUT = httpx.Timeout(15.0, connect=10.0)
_ALLOWED_SCHEMES = {"http", "https"}
_BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}


def _is_safe_url(url: str) -> bool:
    """Return True only if *url* is safe to fetch (not an internal address)."""
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in _ALLOWED_SCHEMES:
        return False
    host = parsed.hostname or ""
    if not host:
        return False
    if host.lower() in _BLOCKED_HOSTS:
        return False
    try:
        addr = ipaddress.ip_address(host)
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
            return False
    except ValueError:
        pass  # hostname, not an IP literal -- allow
    return True


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
    if not _is_safe_url(url):
        return {"url": url, "title": "", "content": "Error: URL is not allowed (internal or invalid address)."}

    async with httpx.AsyncClient(
        timeout=_HTTP_TIMEOUT, headers=_SEARCH_HEADERS, follow_redirects=True,
    ) as client:
        response = await client.get(url)
        response.raise_for_status()

    body = response.text

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
    """Compile a ReAct-style LangGraph for the chat agent."""
    builder = StateGraph(AgentState)

    async def call_model(state: AgentState) -> dict[str, Any]:
        messages = state["messages"]
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
# ACP agent definition
# ---------------------------------------------------------------------------

# Module-level state (initialized in lifespan)
_graph = None

_CHECKPOINT_TTL = {
    "default_ttl": 1440,
    "refresh_on_read": True,
}


def _extract_session_id(input: list[Message]) -> str:
    for msg in input:
        for part in msg.parts:
            if part.content_type == "text/x-session-id" and part.content:
                return part.content
    return "default"


def _extract_user_text(input: list[Message]) -> str:
    texts = []
    for msg in input:
        for part in msg.parts:
            if part.content_type == "text/plain" and part.content:
                texts.append(part.content)
    return "\n".join(texts)


@agent(name="chat_agent", description="Chat agent with web search and webpage fetching capabilities")
async def chat_agent_fn(input: list[Message], context: Context):
    """Processes user queries using web search when needed."""
    from langchain_core.messages import HumanMessage

    session_id = _extract_session_id(input)
    user_text = _extract_user_text(input)

    graph_input = {"messages": [HumanMessage(content=user_text)]}
    config = {
        "configurable": {"thread_id": session_id},
        "recursion_limit": 25,
    }

    tokens_emitted = False
    last_ai_content = ""

    async for event in _graph.astream_events(graph_input, config, version="v2"):
        kind = event.get("event", "")

        if kind == "on_chat_model_stream":
            chunk = event.get("data", {}).get("chunk", "")
            text = chunk.content if hasattr(chunk, "content") else str(chunk)
            if text:
                tokens_emitted = True
                yield MessagePart(content=text, content_type="text/plain")

        elif kind == "on_chain_end":
            output = event.get("data", {}).get("output", {})
            if isinstance(output, dict):
                msgs = output.get("messages", [])
                if msgs and isinstance(msgs[-1], AIMessage):
                    text = msgs[-1].content if isinstance(msgs[-1].content, str) else ""
                    if text:
                        last_ai_content = text

        elif kind == "on_tool_start":
            tool_data = json.dumps({"type": "tool_start", "name": event.get("name", "")})
            yield MessagePart(content=tool_data, content_type="application/x-tool-event")

        elif kind == "on_tool_end":
            tool_data = json.dumps({"type": "tool_end", "name": event.get("name", "")})
            yield MessagePart(content=tool_data, content_type="application/x-tool-event")

    if not tokens_emitted and last_ai_content:
        yield MessagePart(content=last_ai_content, content_type="text/plain")


# ---------------------------------------------------------------------------
# Lifespan & application
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app):
    global _graph
    tp, lp = setup_telemetry("chat-agent", app)
    settings = ChatAgentSettings()

    async with AsyncRedisSaver.from_conn_string(settings.redis_url, ttl=_CHECKPOINT_TTL) as checkpointer:
        await checkpointer.asetup()

        llm = create_ollama_llm(settings.chat_model, settings)
        tools = [web_search, fetch_webpage]
        llm_with_tools = llm.bind_tools(tools)

        _graph = build_chat_graph(llm_with_tools, tools, checkpointer)
        logger.info(
            "Chat Agent ready -- model=%s, tools=%s",
            settings.chat_model,
            [t.name for t in tools],
        )
        yield

    shutdown_telemetry(tp, lp)


app = create_app(chat_agent_fn, lifespan=lifespan)


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
