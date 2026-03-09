"""Orchestrator service -- LangGraph supervisor that routes to sub-agents."""

import json
import logging
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from functools import partial
from typing import Annotated, Any

import httpx

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from pydantic_settings import SettingsConfigDict
from typing_extensions import TypedDict

from shared.acp.client import ACPClient
from shared.acp.server import RunRequest, create_acp_router
from shared.llm.factory import create_ollama_llm
from shared.llm.settings import CommonAgentSettings
from shared.observability import setup_telemetry, shutdown_telemetry

from classifier import CLASSIFICATION_SYSTEM_PROMPT, parse_agent_from_response

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Agent name constants
# ---------------------------------------------------------------------------

CHAT_AGENT = "chat_agent"
BROWSER_AGENT = "browser_agent"

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class OrchestratorSettings(CommonAgentSettings):
    """Environment-driven configuration for the Orchestrator service."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    chat_agent_url: str = "http://chat-agent:8002"
    browser_agent_url: str = "http://browser-agent:8003"


settings = OrchestratorSettings()

# ---------------------------------------------------------------------------
# Supervisor graph state
# ---------------------------------------------------------------------------


class SupervisorState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    next_agent: str | None
    session_id: str | None  # passed through for browser agent


# ---------------------------------------------------------------------------
# Graph nodes
# ---------------------------------------------------------------------------


async def _classify_intent(llm: BaseChatModel, user_message: str) -> str:
    """Classify a user message and return the target agent name."""
    msgs: list[BaseMessage] = [
        SystemMessage(content=CLASSIFICATION_SYSTEM_PROMPT),
        HumanMessage(content=user_message),
    ]
    response = await llm.ainvoke(msgs)
    response_text = response.content if isinstance(response.content, str) else str(response.content)
    return parse_agent_from_response(response_text)


async def supervisor_node(
    state: SupervisorState,
    *,
    llm: BaseChatModel,
) -> dict[str, Any]:
    """Classify the user's intent and decide which sub-agent to invoke."""
    last_human_msg = ""
    for msg in reversed(state["messages"]):
        if isinstance(msg, HumanMessage):
            last_human_msg = msg.content if isinstance(msg.content, str) else str(msg.content)
            break

    if not last_human_msg:
        return {"next_agent": CHAT_AGENT}

    agent = await _classify_intent(llm, last_human_msg)
    logger.info("Supervisor classified intent as '%s' for: %.80s", agent, last_human_msg)
    return {"next_agent": agent}


def _serialize_messages(messages: list[BaseMessage]) -> list[dict[str, str]]:
    """Convert LangChain messages to role/content dicts for ACP transport."""
    serialized: list[dict[str, str]] = []
    for msg in messages:
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        if isinstance(msg, HumanMessage):
            serialized.append({"role": "human", "content": content})
        elif isinstance(msg, AIMessage):
            serialized.append({"role": "ai", "content": content})
        elif isinstance(msg, SystemMessage):
            serialized.append({"role": "system", "content": content})
        else:
            serialized.append({"role": "human", "content": content})
    return serialized


async def call_chat_agent(
    state: SupervisorState,
    *,
    chat_client: ACPClient,
) -> dict[str, Any]:
    """Forward the conversation to the Chat Agent via ACP."""
    session_id = state.get("session_id") or ""
    serialized = _serialize_messages(state["messages"])

    try:
        result = await chat_client.run(
            thread_id=session_id or "default",
            input={"messages": serialized},
        )

        response_text = _extract_response_text(result)
        return {
            "messages": [AIMessage(content=response_text)],
            "next_agent": None,
        }
    except Exception:
        logger.exception("Chat Agent call failed")
        return {
            "messages": [AIMessage(content="Sorry, I was unable to process your request. Please try again.")],
            "next_agent": None,
        }


async def call_browser_agent(
    state: SupervisorState,
    *,
    browser_client: ACPClient,
) -> dict[str, Any]:
    """Forward the conversation to the Browser Agent via ACP.

    Uses session_id as the thread_id so the Browser Agent maintains
    per-session conversation history (multi-turn support).
    Also passes session_id in the input so the LLM can use it in tool calls.
    """
    session_id = state.get("session_id") or ""
    serialized = _serialize_messages(state["messages"])

    try:
        result = await browser_client.run(
            thread_id=session_id or "default",
            input={
                "messages": serialized,
                "session_id": session_id,
            },
        )

        response_text = _extract_response_text(result)
        return {
            "messages": [AIMessage(content=response_text)],
            "next_agent": None,
        }
    except Exception:
        logger.exception("Browser Agent call failed")
        return {
            "messages": [AIMessage(content="Sorry, the browser action could not be completed. Please try again.")],
            "next_agent": None,
        }


def _extract_response_text(result: dict[str, Any]) -> str:
    """Pull the final assistant text out of an ACP RunResponse."""
    output = result.get("output", {})
    if not output:
        return result.get("error", "No response received.")

    messages = output.get("messages", [])
    if messages:
        last = messages[-1]
        if isinstance(last, dict):
            return last.get("content", str(last))
        if hasattr(last, "content"):
            return last.content
        return str(last)

    return str(output)


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def route_from_supervisor(state: SupervisorState) -> str:
    """Conditional edge: pick the sub-agent node based on supervisor decision."""
    if state.get("next_agent") == BROWSER_AGENT:
        return BROWSER_AGENT
    return CHAT_AGENT


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------


def build_supervisor_graph(
    llm: BaseChatModel,
    chat_client: ACPClient,
    browser_client: ACPClient,
    checkpointer: Any,
) -> Any:
    """Construct the compiled LangGraph supervisor graph."""
    builder = StateGraph(SupervisorState)

    builder.add_node("supervisor", partial(supervisor_node, llm=llm))
    builder.add_node(CHAT_AGENT, partial(call_chat_agent, chat_client=chat_client))
    builder.add_node(BROWSER_AGENT, partial(call_browser_agent, browser_client=browser_client))

    builder.set_entry_point("supervisor")
    builder.add_conditional_edges("supervisor", route_from_supervisor)
    builder.add_edge(CHAT_AGENT, END)
    builder.add_edge(BROWSER_AGENT, END)

    return builder.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------


_CHECKPOINT_TTL = {
    "default_ttl": 1440,   # 24 hours in minutes
    "refresh_on_read": True,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: initialise LLM, clients, and the graph."""
    tp, lp = setup_telemetry("orchestrator", app)

    async with AsyncRedisSaver.from_conn_string(settings.redis_url, ttl=_CHECKPOINT_TTL) as checkpointer:
        await checkpointer.asetup()

        llm = create_ollama_llm(
            model=settings.orchestrator_model,
            settings=settings,
            streaming=False,
        )

        chat_client = ACPClient(settings.chat_agent_url)
        browser_client = ACPClient(settings.browser_agent_url)
        await chat_client.start()
        await browser_client.start()

        app.state.graph = build_supervisor_graph(
            llm=llm,
            chat_client=chat_client,
            browser_client=browser_client,
            checkpointer=checkpointer,
        )
        # Store for the custom streaming endpoint
        app.state.llm = llm
        app.state.chat_client = chat_client
        app.state.browser_client = browser_client

        logger.info(
            "Orchestrator ready (chat=%s, browser=%s)",
            settings.chat_agent_url,
            settings.browser_agent_url,
        )
        yield

    await chat_client.close()
    await browser_client.close()
    shutdown_telemetry(tp, lp)


app = FastAPI(
    title="Orchestrator Agent",
    description="LangGraph supervisor that routes requests to Chat or Browser agents.",
    version="0.2.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, Any]:
    async def _check(url: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as c:
                resp = await c.get(f"{url.rstrip('/')}/health")
                return resp.is_success
        except Exception:
            return False

    chat_ok = await _check(settings.chat_agent_url)
    browser_ok = await _check(settings.browser_agent_url)

    overall = "ok" if (chat_ok and browser_ok) else "degraded"
    return {
        "status": overall,
        "service": "orchestrator",
        "chat_agent": "ok" if chat_ok else "unavailable",
        "browser_agent": "ok" if browser_ok else "unavailable",
    }


@app.post("/runs/stream")
async def stream_run(body: RunRequest, request: Request) -> StreamingResponse:
    """Classify intent synchronously then stream tokens from the sub-agent.

    Two-phase approach:
    1. Run the supervisor LLM once (sync) to decide chat_agent vs browser_agent.
    2. Call the classified sub-agent's /runs/stream and pipe tokens back.

    This gives real token-level streaming because we stream directly from the
    LLM running inside the sub-agent service, rather than running the full
    orchestrator LangGraph (which would only yield the supervisor's
    classification JSON as streaming tokens).
    """
    _llm: BaseChatModel = request.app.state.llm
    _chat_client: ACPClient = request.app.state.chat_client
    _browser_client: ACPClient = request.app.state.browser_client
    run_id = body.run_id or str(uuid.uuid4())

    # ── Phase 1: classify intent synchronously ──────────────────────────────
    last_human = ""
    for msg in reversed(body.input.get("messages", [])):
        if isinstance(msg, dict) and msg.get("role") == "human":
            last_human = msg.get("content", "")
            break

    agent = CHAT_AGENT
    if last_human:
        try:
            agent = await _classify_intent(_llm, last_human)
        except Exception:
            logger.exception("Classification failed, defaulting to %s", CHAT_AGENT)

    logger.info(
        "Stream run: intent=%s thread=%s", agent, body.thread_id
    )

    # ── Phase 2: stream from the classified sub-agent ───────────────────────
    if agent == BROWSER_AGENT:
        client = _browser_client
        # Browser agent needs session_id in the input for tool calls
        sub_input = body.input
    else:
        client = _chat_client
        # Chat agent only accepts messages (no session_id in its state schema)
        sub_input = {"messages": body.input.get("messages", [])}

    async def generator() -> AsyncGenerator[str, None]:
        try:
            async for event in client.run_stream(
                thread_id=body.thread_id,
                input=sub_input,
                run_id=run_id,
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:
            logger.exception("Sub-agent stream failed (agent=%s)", agent)
            yield (
                f"data: {json.dumps({'type': 'error', 'error': str(exc), 'run_id': run_id}, ensure_ascii=False)}\n\n"
            )

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


router = create_acp_router(lambda request: request.app.state.graph)
app.include_router(router)
