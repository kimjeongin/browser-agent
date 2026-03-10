"""Browser Agent -- acp_sdk Server entry point."""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Any

import httpx

# acp_sdk 1.0.3 references uvicorn.config.LoopSetupType which was removed
# in uvicorn >= 0.34. Patch it before importing acp_sdk.server.
import uvicorn.config as _uvicorn_config

if not hasattr(_uvicorn_config, "LoopSetupType"):
    _uvicorn_config.LoopSetupType = str

from acp_sdk.models import Message, MessagePart
from acp_sdk.server import Context, agent, create_app
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.redis.aio import AsyncRedisSaver

from graph.builder import build_browser_graph
from settings import BrowserAgentSettings
from shared.llm import create_ollama_llm
from shared.observability import setup_telemetry, shutdown_telemetry
from tools.browser_tools import BROWSER_TOOLS
from tools.gateway_client import cleanup, initialize_client

logger = logging.getLogger(__name__)

_graph = None
_settings: BrowserAgentSettings | None = None

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


@agent(name="browser_agent", description="Browser automation agent for DOM control")
async def browser_agent_fn(input: list[Message], context: Context):
    """Executes browser automation tasks using DOM control tools."""
    session_id = _extract_session_id(input)
    user_text = _extract_user_text(input)

    graph_input = {
        "messages": [HumanMessage(content=user_text)],
        "session_id": session_id,
    }
    config = {
        "configurable": {"thread_id": session_id},
        "recursion_limit": 25,
    }

    tokens_emitted = False
    last_ai_content = ""

    async for event in _graph.astream_events(graph_input, config, version="v2"):
        kind = event.get("event", "")

        if kind == "on_chat_model_stream":
            node_name = event.get("metadata", {}).get("langgraph_node", "")
            if node_name in ("planner", "progress_check", "replan"):
                continue
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


@asynccontextmanager
async def lifespan(app):
    global _graph, _settings
    tp, lp = setup_telemetry("browser-agent", app)

    agent_settings = BrowserAgentSettings()
    _settings = agent_settings

    gateway_client = initialize_client(
        agent_settings.gateway_url, agent_settings.browser_tool_timeout
    )
    await gateway_client.start()

    actor_llm = create_ollama_llm(agent_settings.browser_model, agent_settings)
    llm_with_tools = actor_llm.bind_tools(BROWSER_TOOLS)

    planner_llm = create_ollama_llm(agent_settings.planner_model, agent_settings)

    async with AsyncRedisSaver.from_conn_string(agent_settings.redis_url, ttl=_CHECKPOINT_TTL) as checkpointer:
        await checkpointer.asetup()

        _graph = build_browser_graph(
            llm_with_tools, planner_llm, BROWSER_TOOLS, checkpointer
        )

        logger.info(
            "Browser Agent ready -- actor=%s, planner=%s, gateway=%s, tools=%d",
            agent_settings.browser_model,
            agent_settings.planner_model,
            agent_settings.gateway_url,
            len(BROWSER_TOOLS),
        )
        yield

    await gateway_client.close()
    cleanup()
    shutdown_telemetry(tp, lp)


app = create_app(browser_agent_fn, lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, Any]:
    gateway_ok = False
    gw_url = _settings.gateway_url if _settings else "http://gateway:8000"
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
