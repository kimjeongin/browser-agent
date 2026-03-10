"""Orchestrator service -- acp_sdk agent that routes to sub-agents."""

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
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic_settings import SettingsConfigDict

from classifier import CLASSIFICATION_SYSTEM_PROMPT, parse_agent_from_response
from shared.llm import CommonAgentSettings, create_ollama_llm
from shared.observability import setup_telemetry, shutdown_telemetry

logger = logging.getLogger(__name__)

CHAT_AGENT = "chat_agent"
BROWSER_AGENT = "browser_agent"


class OrchestratorSettings(CommonAgentSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    chat_agent_url: str = "http://chat-agent:8002"
    browser_agent_url: str = "http://browser-agent:8003"


# Module-level state (initialized in lifespan)
_llm = None
_chat_client: Client | None = None
_browser_client: Client | None = None
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


async def _classify_intent(user_message: str) -> str:
    """Classify a user message and return the target agent name."""
    msgs = [
        SystemMessage(content=CLASSIFICATION_SYSTEM_PROMPT),
        HumanMessage(content=user_message),
    ]
    response = await _llm.ainvoke(msgs)
    response_text = response.content if isinstance(response.content, str) else str(response.content)
    return parse_agent_from_response(response_text)


@agent(name="orchestrator", description="Routes user requests to the appropriate specialist agent")
async def orchestrator_agent(input: list[Message], context: Context):
    """Classifies intent and routes to chat_agent or browser_agent."""
    user_text = _extract_user_text(input)

    intent = CHAT_AGENT
    if user_text:
        try:
            intent = await _classify_intent(user_text)
        except Exception:
            logger.exception("Classification failed, defaulting to %s", CHAT_AGENT)

    logger.info("Orchestrator routing to '%s' for: %.80s", intent, user_text)

    if intent == BROWSER_AGENT:
        client = _browser_client
        agent_name = BROWSER_AGENT
    else:
        client = _chat_client
        agent_name = CHAT_AGENT

    async for event in client.run_stream(input=input, agent=agent_name):
        if isinstance(event, MessagePartEvent):
            yield event.part


@asynccontextmanager
async def lifespan(app):
    global _llm, _chat_client, _browser_client, _settings
    tp, lp = setup_telemetry("orchestrator", app)

    settings = OrchestratorSettings()
    _settings = settings

    _llm = create_ollama_llm(
        model=settings.orchestrator_model,
        settings=settings,
        streaming=False,
    )

    _acp_headers = {"Content-Type": "application/json"}
    async with (
        Client(base_url=settings.chat_agent_url, timeout=120.0, headers=_acp_headers) as chat_client,
        Client(base_url=settings.browser_agent_url, timeout=120.0, headers=_acp_headers) as browser_client,
    ):
        _chat_client = chat_client
        _browser_client = browser_client

        logger.info(
            "Orchestrator ready (chat=%s, browser=%s)",
            settings.chat_agent_url,
            settings.browser_agent_url,
        )
        yield

    _chat_client = None
    _browser_client = None
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
