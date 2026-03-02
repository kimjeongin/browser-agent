"""Gateway service -- public-facing entry point for the browser extension.

Responsibilities:
- JWT-authenticated session management (in-memory, TTL enforced lazily)
- Chat proxy to Orchestrator via ACP (sync and SSE streaming)
- Browser tool invocation channel (asyncio.Queue -> SSE -> Extension)
- Browser tool result ingestion (Extension -> asyncio.Future -> Browser Agent)
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.session_store import SessionStore
from core.invocation_broker import InvocationBroker
from settings import Settings
from shared.acp.client import ACPClient
from shared.auth.jwt_verifier import KeycloakJWTVerifier

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise and tear down application-wide resources."""
    s = Settings()

    app.state.session_store = SessionStore(ttl_seconds=s.session_ttl)
    app.state.broker = InvocationBroker()
    app.state.settings = s
    app.state.verifier = KeycloakJWTVerifier(
        realm_url=s.keycloak_realm_url,
        audience=s.keycloak_audience,
        jwks_url=s.keycloak_jwks_url or None,
    )
    app.state.acp = ACPClient(base_url=s.orchestrator_url)

    cleanup_task = asyncio.create_task(app.state.broker.run_stale_cleanup())
    logger.info("Gateway started [orchestrator=%s]", s.orchestrator_url)

    yield

    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    logger.info("Gateway shutdown complete")


app = FastAPI(
    title="Browser Agent Gateway",
    version="0.2.0",
    lifespan=lifespan,
)

# CORS setup
_chrome_extension_id = os.getenv("CHROME_EXTENSION_ID", "")
_environment = os.getenv("ENVIRONMENT", "production")

_cors_origins_raw = os.getenv("CORS_ORIGINS", "").split(",")
_cors_origins = [o.strip() for o in _cors_origins_raw if o.strip()]

if _chrome_extension_id:
    _cors_origins.append(f"chrome-extension://{_chrome_extension_id}")

if _environment == "development":
    _cors_origins.extend(["http://localhost:3000", "http://localhost:5173"])

_cors_wildcard = not _cors_origins  # wildcard only if no specific origins configured

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _cors_wildcard else _cors_origins,
    allow_credentials=not _cors_wildcard,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)

# Register routers
from api.sessions import router as sessions_router
from api.chat import router as chat_router
from api.browser_tools import router as browser_tools_router

app.include_router(sessions_router)
app.include_router(chat_router)
app.include_router(browser_tools_router)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "gateway",
        "active_sessions": app.state.session_store.active_session_count()
        if hasattr(app.state, "session_store")
        else 0,
    }
