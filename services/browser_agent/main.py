"""Browser Agent -- FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from settings import BrowserAgentSettings
from tools.gateway_client import initialize_client, initialize_vl_llm, cleanup
from tools.browser_tools import BROWSER_TOOLS
from graph.builder import build_browser_graph
from shared.acp import create_acp_router
from shared.llm import LLMSettings, create_ollama_llm

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: connect to Gateway, build LLMs and graph; shutdown: clean up."""
    agent_settings = BrowserAgentSettings()
    llm_settings = LLMSettings()

    # AsyncPostgresSaver requires a plain postgresql:// DSN (psycopg).
    db_url = agent_settings.database_url.replace(
        "postgresql+asyncpg://", "postgresql://"
    )

    # Initialise Gateway client (singleton, reused across requests)
    gateway_client = initialize_client(
        agent_settings.gateway_url, agent_settings.browser_tool_timeout
    )
    await gateway_client.start()

    # Main actor LLM (tool calling, higher quality model)
    actor_llm = create_ollama_llm(agent_settings.browser_model, llm_settings)
    llm_with_tools = actor_llm.bind_tools(BROWSER_TOOLS)

    # Planner/progress-check/replan use lighter model for speed
    planner_llm = create_ollama_llm(agent_settings.planner_model, llm_settings)

    # Vision-language model for DOM-failure fallback (streaming disabled --
    # the VL model is called directly via ainvoke, not streamed to the user)
    vl_llm = create_ollama_llm(
        agent_settings.vision_model, llm_settings, streaming=False
    )
    initialize_vl_llm(vl_llm)

    async with AsyncPostgresSaver.from_conn_string(db_url) as checkpointer:
        await checkpointer.setup()

        app.state.graph = build_browser_graph(
            llm_with_tools, planner_llm, BROWSER_TOOLS, checkpointer
        )

        logger.info(
            "Browser Agent ready -- actor=%s, planner=%s, vision=%s, gateway=%s, tools=%d",
            agent_settings.browser_model,
            agent_settings.planner_model,
            agent_settings.vision_model,
            agent_settings.gateway_url,
            len(BROWSER_TOOLS),
        )
        yield

    await gateway_client.close()
    cleanup()


app = FastAPI(title="Browser Agent", version="0.3.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, Any]:
    gateway_ok = False
    gw_url = BrowserAgentSettings().gateway_url
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
