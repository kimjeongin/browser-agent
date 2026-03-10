"""Chat Agent -- acp_sdk Server wrapping a LangGraph ReAct agent.

Exposes ACP agent endpoints via acp_sdk's create_app.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Annotated, Any

import httpx

# acp_sdk 1.0.3 references uvicorn.config.LoopSetupType which was removed
# in uvicorn >= 0.34. Patch it before importing acp_sdk.server.
import uvicorn.config as _uvicorn_config

if not hasattr(_uvicorn_config, "LoopSetupType"):
    _uvicorn_config.LoopSetupType = str

from acp_sdk.models import Message, MessagePart
from acp_sdk.server import Context, agent, create_app
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from langgraph.graph import StateGraph
from langgraph.graph.message import add_messages
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
# LangGraph state & graph builder
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = "You are a helpful AI assistant."


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


def build_chat_graph(llm: Any, checkpointer: Any) -> Any:
    """Compile a LangGraph for the chat agent."""
    builder = StateGraph(AgentState)

    async def call_model(state: AgentState) -> dict[str, Any]:
        messages = state["messages"]
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=SYSTEM_PROMPT), *messages]
        response = await llm.ainvoke(messages)
        return {"messages": [response]}

    builder.add_node("agent", call_model)
    builder.set_entry_point("agent")
    builder.set_finish_point("agent")

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


@agent(name="chat_agent", description="Chat agent for general Q&A and conversation")
async def chat_agent_fn(input: list[Message], context: Context):
    """Processes user queries and returns conversational responses."""
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

        _graph = build_chat_graph(llm, checkpointer)
        logger.info("Chat Agent ready -- model=%s", settings.chat_model)
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
