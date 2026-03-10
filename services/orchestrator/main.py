"""Orchestrator service -- ACP agent backed by a LangGraph ReAct supervisor."""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

import httpx

# acp_sdk 1.0.3 references uvicorn.config.LoopSetupType which was removed
# in uvicorn >= 0.34. Patch it before importing acp_sdk.server.
import uvicorn.config as _uvicorn_config

if not hasattr(_uvicorn_config, "LoopSetupType"):
    _uvicorn_config.LoopSetupType = str

from acp_sdk.client import Client
from acp_sdk.models import Message, MessagePart, MessagePartEvent
from acp_sdk.server import Context, agent, create_app
from langchain_core.messages import AIMessage, HumanMessage
from pydantic_settings import SettingsConfigDict

from graph.builder import build_orchestrator_graph
from shared.llm import CommonAgentSettings, create_ollama_llm
from shared.observability import setup_telemetry, shutdown_telemetry
from tools import passthrough
from tools.browser_agent import browser_agent as browser_agent_tool
from tools.browser_agent import init_browser_client
from tools.chat_agent import chat_agent as chat_agent_tool
from tools.chat_agent import init_chat_client

logger = logging.getLogger(__name__)


class OrchestratorSettings(CommonAgentSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    chat_agent_url: str = "http://chat-agent:8002"
    browser_agent_url: str = "http://browser-agent:8003"


# Module-level state (initialized in lifespan)
_graph = None
_settings: OrchestratorSettings | None = None


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


@agent(name="orchestrator", description="Routes user requests to specialist agents")
async def orchestrator_agent(input: list[Message], context: Context):
    """Run the LangGraph supervisor graph and stream results via pass-through queue."""
    session_id = _extract_session_id(input)
    user_text = _extract_user_text(input)

    combined_q: asyncio.Queue[MessagePart | None] = asyncio.Queue()
    passthrough.register(session_id, combined_q)

    async def run_graph():
        """Execute the supervisor graph; push sentinel when done."""
        graph_input = {
            "messages": [HumanMessage(content=user_text)],
            "session_id": session_id,
        }
        try:
            direct_answer_parts: list[str] = []

            async for event in _graph.astream_events(
                graph_input, {"recursion_limit": 10}, version="v2"
            ):
                kind = event.get("event", "")

                if kind == "on_chat_model_stream":
                    # Capture streaming tokens from the supervisor node
                    node = event.get("metadata", {}).get("langgraph_node", "")
                    if node == "supervisor":
                        chunk = event.get("data", {}).get("chunk", "")
                        text = chunk.content if hasattr(chunk, "content") else ""
                        if text:
                            direct_answer_parts.append(text)

                elif kind == "on_chain_end":
                    # Detect when supervisor responds directly (no tool calls)
                    output = event.get("data", {}).get("output", {})
                    if isinstance(output, dict):
                        msgs = output.get("messages", [])
                        if msgs and isinstance(msgs[-1], AIMessage):
                            last = msgs[-1]
                            if not last.tool_calls and last.content:
                                await combined_q.put(
                                    MessagePart(
                                        content=last.content,
                                        content_type="text/plain",
                                    )
                                )
                                direct_answer_parts.clear()

        except Exception:
            logger.exception("Orchestrator graph error")
        finally:
            if direct_answer_parts:
                await combined_q.put(
                    MessagePart(
                        content="".join(direct_answer_parts),
                        content_type="text/plain",
                    )
                )
            await combined_q.put(None)  # sentinel

    graph_task = asyncio.create_task(run_graph())
    try:
        while True:
            part = await combined_q.get()
            if part is None:
                break
            yield part
    finally:
        passthrough.unregister(session_id)
        if not graph_task.done():
            graph_task.cancel()


@asynccontextmanager
async def lifespan(app):
    global _graph, _settings
    tp, lp = setup_telemetry("orchestrator", app)

    settings = OrchestratorSettings()
    _settings = settings

    llm = create_ollama_llm(
        model=settings.orchestrator_model,
        settings=settings,
        streaming=True,
    )

    _acp_headers = {"Content-Type": "application/json"}
    async with (
        Client(
            base_url=settings.chat_agent_url,
            timeout=120.0,
            headers=_acp_headers,
        ) as chat_client,
        Client(
            base_url=settings.browser_agent_url,
            timeout=120.0,
            headers=_acp_headers,
        ) as browser_client,
    ):
        init_chat_client(chat_client)
        init_browser_client(browser_client)

        tools = [browser_agent_tool, chat_agent_tool]
        _graph = build_orchestrator_graph(llm, tools)

        logger.info(
            "Orchestrator ready (chat=%s, browser=%s)",
            settings.chat_agent_url,
            settings.browser_agent_url,
        )
        yield

    _graph = None
    shutdown_telemetry(tp, lp)


app = create_app(orchestrator_agent, lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, Any]:
    async def _check(url: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as c:
                resp = await c.get(f"{url.rstrip('/')}/health")
                return resp.is_success
        except Exception:
            return False

    settings = _settings or OrchestratorSettings()
    chat_ok = await _check(settings.chat_agent_url)
    browser_ok = await _check(settings.browser_agent_url)

    overall = "ok" if (chat_ok and browser_ok) else "degraded"
    return {
        "status": overall,
        "service": "orchestrator",
        "chat_agent": "ok" if chat_ok else "unavailable",
        "browser_agent": "ok" if browser_ok else "unavailable",
    }
