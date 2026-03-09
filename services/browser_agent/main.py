"""Browser Agent -- FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Request
from langgraph.checkpoint.redis.aio import AsyncRedisSaver

from settings import BrowserAgentSettings
from tools.gateway_client import initialize_client, cleanup
from tools.browser_tools import BROWSER_TOOLS
from graph.builder import build_browser_graph
from shared.acp import create_acp_router
from shared.llm import create_ollama_llm
from shared.observability import setup_telemetry, shutdown_telemetry

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: connect to Gateway, build LLMs and graph; shutdown: clean up."""
    tp, lp = setup_telemetry("browser-agent", app)

    agent_settings = BrowserAgentSettings()
    app.state.settings = agent_settings

    # Initialise Gateway client (singleton, reused across requests)
    gateway_client = initialize_client(
        agent_settings.gateway_url, agent_settings.browser_tool_timeout
    )
    await gateway_client.start()

    # Main actor LLM -- qwen2.5vl supports multimodal input so it can
    # directly inspect screenshot images returned by the screenshot tool.
    actor_llm = create_ollama_llm(agent_settings.browser_model, agent_settings)
    llm_with_tools = actor_llm.bind_tools(BROWSER_TOOLS)

    # Planner/progress-check/replan use lighter model for speed
    planner_llm = create_ollama_llm(agent_settings.planner_model, agent_settings)

    _checkpoint_ttl = {
        "default_ttl": 1440,   # 24 hours in minutes
        "refresh_on_read": True,
    }

    async with AsyncRedisSaver.from_conn_string(agent_settings.redis_url, ttl=_checkpoint_ttl) as checkpointer:
        await checkpointer.asetup()

        app.state.graph = build_browser_graph(
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


app = FastAPI(title="Browser Agent", version="0.3.0", lifespan=lifespan)


@app.get("/health")
async def health(request: Request) -> dict[str, Any]:
    gateway_ok = False
    gw_url = request.app.state.settings.gateway_url
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


# ACP endpoints: /runs, /runs/stream
router = create_acp_router(lambda request: request.app.state.graph)
app.include_router(router)
