"""Orchestrator service -- LangGraph supervisor that routes to sub-agents."""

import json
import logging
import re
from contextlib import asynccontextmanager
from functools import partial
from typing import Annotated, Any

import httpx

from fastapi import FastAPI
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing_extensions import TypedDict

from shared.acp.client import ACPClient
from shared.acp.server import create_acp_router
from shared.llm.factory import create_ollama_llm
from shared.llm.settings import LLMSettings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class OrchestratorSettings(BaseSettings):
    """Environment-driven configuration for the Orchestrator service."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    chat_agent_url: str = "http://chat-agent:8002"
    browser_agent_url: str = "http://browser-agent:8003"
    database_url: str = "postgresql+asyncpg://postgres:password@postgres:5432/browser_agent"


settings = OrchestratorSettings()
llm_settings = LLMSettings()

# ---------------------------------------------------------------------------
# Supervisor graph state
# ---------------------------------------------------------------------------

CLASSIFICATION_SYSTEM_PROMPT = """\
You are a request classifier for a browser extension AI assistant.
Your job is to decide which agent should handle the user's latest message.

Available agents:
- "browser_agent": Handles tasks that require interacting with a web browser,
  such as clicking, typing, navigating to URLs, scrolling, taking screenshots
  of specific web pages, filling forms, extracting visible page content, or
  any action that manipulates or reads the DOM of a live web page.
  Examples: "유튜브에서 아이유 검색해줘", "이 버튼 클릭해줘", "구글에서 검색해줘"
- "chat_agent": Handles everything else -- general questions, web search
  queries, summarisation, translation, coding help, math, and conversation.

Respond ONLY with a JSON object. No explanation, no markdown.
Example: {"agent": "chat_agent"}
"""


class SupervisorState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    next_agent: str | None
    session_id: str | None  # passed through for browser agent


# ---------------------------------------------------------------------------
# Graph nodes
# ---------------------------------------------------------------------------


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
        return {"next_agent": "chat_agent"}

    classification_messages = [
        SystemMessage(content=CLASSIFICATION_SYSTEM_PROMPT),
        HumanMessage(content=last_human_msg),
    ]

    response = await llm.ainvoke(classification_messages)
    response_text = response.content if isinstance(response.content, str) else str(response.content)

    agent = _parse_agent_from_response(response_text)
    logger.info("Supervisor classified intent as '%s' for: %.80s", agent, last_human_msg)
    return {"next_agent": agent}


def _parse_agent_from_response(text: str) -> str:
    """Extract agent name from the LLM's JSON response, with fallback."""
    json_match = re.search(r"\{.*?\}", text, re.DOTALL)
    if json_match:
        try:
            parsed = json.loads(json_match.group())
            agent = parsed.get("agent", "chat_agent")
            if agent in ("browser_agent", "chat_agent"):
                return agent
        except (json.JSONDecodeError, AttributeError):
            pass

    lower = text.lower()
    if "browser_agent" in lower:
        return "browser_agent"
    return "chat_agent"


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
    if state.get("next_agent") == "browser_agent":
        return "browser_agent"
    return "chat_agent"


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------


def build_supervisor_graph(
    llm: BaseChatModel,
    chat_client: ACPClient,
    browser_client: ACPClient,
    checkpointer: AsyncPostgresSaver,
) -> Any:
    """Construct the compiled LangGraph supervisor graph."""
    builder = StateGraph(SupervisorState)

    builder.add_node("supervisor", partial(supervisor_node, llm=llm))
    builder.add_node("chat_agent", partial(call_chat_agent, chat_client=chat_client))
    builder.add_node("browser_agent", partial(call_browser_agent, browser_client=browser_client))

    builder.set_entry_point("supervisor")
    builder.add_conditional_edges("supervisor", route_from_supervisor)
    builder.add_edge("chat_agent", END)
    builder.add_edge("browser_agent", END)

    return builder.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------


def _psycopg_connection_string(database_url: str) -> str:
    return re.sub(r"^postgresql\+asyncpg://", "postgresql://", database_url)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: initialise LLM, clients, and the graph."""
    conn_string = _psycopg_connection_string(settings.database_url)

    async with AsyncPostgresSaver.from_conn_string(conn_string) as checkpointer:
        await checkpointer.setup()

        llm = create_ollama_llm(
            model=llm_settings.orchestrator_model,
            settings=llm_settings,
            streaming=False,
        )

        chat_client = ACPClient(settings.chat_agent_url)
        browser_client = ACPClient(settings.browser_agent_url)

        app.state.graph = build_supervisor_graph(
            llm=llm,
            chat_client=chat_client,
            browser_client=browser_client,
            checkpointer=checkpointer,
        )

        logger.info(
            "Orchestrator ready (chat=%s, browser=%s)",
            settings.chat_agent_url,
            settings.browser_agent_url,
        )
        yield


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


router = create_acp_router(lambda request: request.app.state.graph)
app.include_router(router)
