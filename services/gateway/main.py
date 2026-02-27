"""Gateway service -- public-facing entry point for the browser extension.

Responsibilities:
- JWT-authenticated session management (Redis-backed)
- Chat proxy to Orchestrator via ACP (sync and SSE streaming)
- Browser tool invocation channel (asyncio.Queue → SSE → Extension)
- Browser tool result ingestion (Extension → asyncio.Future → Browser Agent)

webMCP-inspired 3-hop browser tool flow:
  Browser Agent POST /sessions/{id}/browser-tools/invoke (blocking, 60s)
    → asyncio.Queue → SSE /sessions/{id}/commands → Extension
    → Extension executes DOM → POST /sessions/{id}/browser-tools/result/{inv_id}
    → asyncio.Future.set_result() → Browser Agent response returned
"""

import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated, Any

import redis.asyncio as aioredis
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict
from sse_starlette.sse import EventSourceResponse

from shared.acp.client import ACPClient
from shared.auth.dependencies import get_current_user
from shared.auth.jwt_verifier import KeycloakJWTVerifier
from shared.models.session import Session

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    redis_url: str = "redis://redis:6379/0"
    database_url: str = (
        "postgresql+asyncpg://postgres:password@postgres:5432/browser_agent"
    )
    orchestrator_url: str = "http://orchestrator:8001"
    keycloak_realm_url: str = "http://keycloak:8080/realms/browser-agent"
    keycloak_audience: str = "browser-agent-extension"
    session_ttl: int = 86400  # 24 hours
    browser_tool_timeout: float = 60.0  # seconds to wait for extension result


settings = Settings()

# ---------------------------------------------------------------------------
# In-memory state for browser tool invocations
# Per-session asyncio.Queue for command dispatch (SSE → Extension)
# Per-invocation asyncio.Future for result awaiting (Browser Agent blocks)
# NOTE: Single-instance only. For horizontal scaling, replace with Redis streams.
# ---------------------------------------------------------------------------

# session_id → asyncio.Queue of tool_invocation dicts
_session_queues: dict[str, asyncio.Queue] = {}

# inv_id → asyncio.Future[dict] for blocking the Browser Agent call
_pending_invocations: dict[str, asyncio.Future] = {}

# inv_id → session_id reverse mapping for cleanup and status checks
_invocation_to_session: dict[str, str] = {}

# session_id → bool (True when browser agent is actively controlling)
_browser_controlling: dict[str, bool] = {}


def _get_or_create_queue(session_id: str) -> asyncio.Queue:
    if session_id not in _session_queues:
        _session_queues[session_id] = asyncio.Queue(maxsize=100)
    return _session_queues[session_id]


def _cleanup_session(session_id: str) -> None:
    # Cancel any pending futures for this session
    stale_inv_ids = [
        inv_id for inv_id, sid in _invocation_to_session.items()
        if sid == session_id
    ]
    for inv_id in stale_inv_ids:
        future = _pending_invocations.pop(inv_id, None)
        if future and not future.done():
            future.cancel()
        _invocation_to_session.pop(inv_id, None)

    _session_queues.pop(session_id, None)
    _browser_controlling.pop(session_id, None)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    """Incoming chat message from the extension."""

    content: str
    images: list[str] = []


class SessionResponse(BaseModel):
    """Public representation of a session."""

    session_id: str
    user_id: str
    status: str
    browser_controlling: bool = False


class BrowserToolInvokeRequest(BaseModel):
    """Browser tool invocation request from Browser Agent."""

    tool_name: str
    params: dict[str, Any]


class BrowserToolResultRequest(BaseModel):
    """Browser tool execution result from Extension."""

    inv_id: str
    success: bool
    result: Any = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Lifespan -- initialise shared resources
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise and tear down application-wide resources."""

    # JWT verifier (attached to app.state for get_current_user dependency)
    app.state.verifier = KeycloakJWTVerifier(
        realm_url=settings.keycloak_realm_url,
        audience=settings.keycloak_audience,
    )

    # Redis connection pool (session state only, no Pub/Sub for browser cmds)
    app.state.redis = aioredis.from_url(
        settings.redis_url,
        decode_responses=True,
    )

    # ACP client for Orchestrator
    app.state.acp = ACPClient(base_url=settings.orchestrator_url)

    logger.info(
        "Gateway started  [orchestrator=%s, redis=%s]",
        settings.orchestrator_url,
        settings.redis_url,
    )

    yield

    # Cleanup
    await app.state.redis.aclose()
    logger.info("Gateway shutdown complete")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Browser Agent Gateway",
    version="0.2.0",
    lifespan=lifespan,
)

_cors_origins_raw = os.getenv("CORS_ORIGINS", "*").split(",")
_cors_origins = [o.strip() for o in _cors_origins_raw if o.strip()]
_cors_wildcard = "*" in _cors_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _cors_wildcard else _cors_origins,
    allow_credentials=not _cors_wildcard,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Type alias for the authenticated-user dependency
CurrentUser = Annotated[dict[str, Any], Depends(get_current_user)]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _redis(request: Request) -> aioredis.Redis:
    """Retrieve the Redis client from app state."""
    return request.app.state.redis


def _acp(request: Request) -> ACPClient:
    """Retrieve the ACP client from app state."""
    return request.app.state.acp


def _session_key(session_id: str) -> str:
    return f"session:{session_id}"


async def _get_session_or_404(
    redis: aioredis.Redis, session_id: str
) -> Session:
    """Load a session from Redis or raise 404."""
    raw = await redis.get(_session_key(session_id))
    if raw is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found",
        )
    return Session.model_validate_json(raw)


async def _verify_session_owner(
    session: Session, user: dict[str, Any]
) -> None:
    """Ensure the authenticated user owns the session."""
    if session.user_id != user.get("sub"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not own this session",
        )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/health")
async def health(request: Request) -> dict[str, Any]:
    redis_ok = False
    try:
        redis_ok = await _redis(request).ping()
    except Exception:
        pass

    active_sessions = len(_session_queues)
    overall = "ok" if redis_ok else "degraded"

    return {
        "status": overall,
        "service": "gateway",
        "redis": "ok" if redis_ok else "unavailable",
        "active_sessions": active_sessions,
    }


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------


@app.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(
    request: Request,
    user: CurrentUser,
) -> SessionResponse:
    """Create a new session for the authenticated user."""
    redis = _redis(request)
    user_id: str = user["sub"]

    session = Session(
        session_id=uuid.uuid4().hex,
        user_id=user_id,
    )

    await redis.set(
        _session_key(session.session_id),
        session.model_dump_json(),
        ex=settings.session_ttl,
    )

    # Pre-create the command queue for this session
    _get_or_create_queue(session.session_id)

    logger.info("Session created: %s (user=%s)", session.session_id, user_id)
    return SessionResponse(
        session_id=session.session_id,
        user_id=session.user_id,
        status=session.status,
        browser_controlling=False,
    )


@app.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    request: Request,
    user: CurrentUser,
) -> SessionResponse:
    """Return session metadata."""
    redis = _redis(request)
    session = await _get_session_or_404(redis, session_id)
    await _verify_session_owner(session, user)

    return SessionResponse(
        session_id=session.session_id,
        user_id=session.user_id,
        status=session.status,
        browser_controlling=_browser_controlling.get(session_id, False),
    )


@app.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    request: Request,
    user: CurrentUser,
) -> dict[str, bool]:
    """Mark a session as inactive."""
    redis = _redis(request)
    session = await _get_session_or_404(redis, session_id)
    await _verify_session_owner(session, user)

    session.status = "inactive"
    session.last_activity = datetime.now(timezone.utc)

    await redis.set(
        _session_key(session_id),
        session.model_dump_json(),
        ex=settings.session_ttl,
    )

    _cleanup_session(session_id)

    logger.info("Session deactivated: %s", session_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Chat -- synchronous proxy
# ---------------------------------------------------------------------------


@app.post("/sessions/{session_id}/chat")
async def chat(
    session_id: str,
    body: ChatRequest,
    request: Request,
    user: CurrentUser,
) -> JSONResponse:
    """Proxy a single chat turn to the Orchestrator (synchronous ACP run)."""
    redis = _redis(request)
    session = await _get_session_or_404(redis, session_id)
    await _verify_session_owner(session, user)

    acp = _acp(request)

    messages: list[dict[str, Any]] = [
        {"role": "human", "content": body.content},
    ]
    if body.images:
        messages[0]["images"] = body.images

    acp_input: dict[str, Any] = {
        "messages": messages,
        "session_id": session_id,
    }

    try:
        result = await acp.run(thread_id=session_id, input=acp_input)
    except Exception as exc:
        logger.error("Orchestrator call failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Orchestrator is unavailable",
        ) from exc

    session.last_activity = datetime.now(timezone.utc)
    await redis.set(
        _session_key(session_id),
        session.model_dump_json(),
        ex=settings.session_ttl,
    )

    return JSONResponse(content=result)


# ---------------------------------------------------------------------------
# Chat -- SSE streaming proxy
# ---------------------------------------------------------------------------


@app.get("/sessions/{session_id}/chat/stream")
async def chat_stream(
    session_id: str,
    request: Request,
    user: CurrentUser,
    content: str = Query(..., description="User message to send"),
) -> EventSourceResponse:
    """Stream orchestrator response tokens to the client via SSE."""
    redis = _redis(request)
    session = await _get_session_or_404(redis, session_id)
    await _verify_session_owner(session, user)

    acp = _acp(request)
    acp_input: dict[str, Any] = {
        "messages": [{"role": "human", "content": content}],
        "session_id": session_id,
    }

    async def _event_generator():
        try:
            async for event in acp.run_stream(
                thread_id=session_id, input=acp_input
            ):
                yield {"data": json.dumps(event, ensure_ascii=False)}
        except Exception as exc:
            logger.error(
                "Orchestrator stream error: %s", exc, exc_info=True
            )
            yield {
                "data": json.dumps(
                    {"type": "error", "error": "Orchestrator stream failed"}
                )
            }

        session.last_activity = datetime.now(timezone.utc)
        await redis.set(
            _session_key(session_id),
            session.model_dump_json(),
            ex=settings.session_ttl,
        )

    return EventSourceResponse(_event_generator())


# ---------------------------------------------------------------------------
# Browser command SSE channel -- Extension listens here
# Replaces Redis Pub/Sub with asyncio.Queue (webMCP-inspired)
# ---------------------------------------------------------------------------


@app.get("/sessions/{session_id}/commands")
async def browser_command_stream(
    session_id: str,
    request: Request,
) -> EventSourceResponse:
    """SSE stream delivering browser tool invocations to the Extension.

    The extension's background service worker holds this connection open.
    Browser Agent POSTs to /browser-tools/invoke, which enqueues here.
    """
    queue = _get_or_create_queue(session_id)

    async def _command_generator():
        logger.info("Extension SSE connected for session %s", session_id)
        try:
            while True:
                if await request.is_disconnected():
                    break

                try:
                    # Wait up to 15s for a command, then send keepalive
                    item = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield {"data": json.dumps(item, ensure_ascii=False)}
                    queue.task_done()
                except asyncio.TimeoutError:
                    yield {"comment": "keepalive"}
        finally:
            logger.info("Extension SSE disconnected for session %s", session_id)

    return EventSourceResponse(_command_generator())


# ---------------------------------------------------------------------------
# Browser tool invocation -- Browser Agent calls this (blocking)
# webMCP-inspired: Browser Agent → Gateway → asyncio.Queue → SSE → Extension
# ---------------------------------------------------------------------------


@app.post("/sessions/{session_id}/browser-tools/invoke")
async def invoke_browser_tool(
    session_id: str,
    body: BrowserToolInvokeRequest,
    request: Request,
) -> JSONResponse:
    """Invoke a browser tool and wait for the Extension's result.

    Called by the Browser Agent. Blocks until the Extension posts the result
    (or timeout after 60s). Uses asyncio.Future for zero-overhead signalling.
    """
    # Verify extension is connected (queue exists)
    if session_id not in _session_queues:
        raise HTTPException(
            status_code=400,
            detail="No extension connected for this session",
        )

    # Verify session exists
    redis = _redis(request)
    raw = await redis.get(_session_key(session_id))
    if raw is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found",
        )

    inv_id = str(uuid.uuid4())
    queue = _get_or_create_queue(session_id)

    # Create future BEFORE enqueuing to avoid race condition
    future: asyncio.Future = asyncio.get_running_loop().create_future()
    _pending_invocations[inv_id] = future
    _invocation_to_session[inv_id] = session_id

    # Mark session as actively controlling the browser
    _browser_controlling[session_id] = True

    # Enqueue the tool invocation for SSE delivery to Extension
    invocation = {
        "inv_id": inv_id,
        "tool_name": body.tool_name,
        "params": body.params,
    }
    await queue.put(invocation)

    logger.info(
        "Browser tool enqueued: inv_id=%s tool=%s session=%s",
        inv_id,
        body.tool_name,
        session_id,
    )

    try:
        # Block until Extension posts result (60s timeout)
        result = await asyncio.wait_for(
            future, timeout=settings.browser_tool_timeout
        )
        logger.info(
            "Browser tool completed: inv_id=%s success=%s",
            inv_id,
            result.get("success"),
        )
        return JSONResponse(content=result)
    except asyncio.TimeoutError:
        logger.warning(
            "Browser tool timed out: inv_id=%s tool=%s session=%s",
            inv_id,
            body.tool_name,
            session_id,
        )
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Browser tool '{body.tool_name}' timed out after {settings.browser_tool_timeout}s",
        )
    finally:
        _pending_invocations.pop(inv_id, None)
        _invocation_to_session.pop(inv_id, None)
        # Clear controlling status if no more pending invocations for session
        if not any(v == session_id for v in _invocation_to_session.values()):
            _browser_controlling[session_id] = False


# ---------------------------------------------------------------------------
# Browser tool result -- Extension posts result here
# webMCP-inspired: Extension → Gateway → asyncio.Future → Browser Agent
# ---------------------------------------------------------------------------


@app.post("/sessions/{session_id}/browser-tools/result/{inv_id}")
async def receive_browser_tool_result(
    session_id: str,
    inv_id: str,
    body: BrowserToolResultRequest,
    request: Request,
) -> dict[str, Any]:
    """Receive the browser tool execution result from the Extension.

    Resolves the asyncio.Future that the Browser Agent is waiting on.
    """
    future = _pending_invocations.get(inv_id)
    if future is None:
        logger.warning(
            "Received result for unknown/expired inv_id=%s session=%s",
            inv_id,
            session_id,
        )
        # Return 200 anyway to avoid Extension retry loops
        return {"ok": False, "reason": "invocation not found or already expired"}

    if not future.done():
        future.set_result({
            "success": body.success,
            "result": body.result,
            "error": body.error,
            "inv_id": inv_id,
        })

    logger.debug(
        "Result delivered: inv_id=%s session=%s success=%s",
        inv_id,
        session_id,
        body.success,
    )
    return {"ok": True}


# ---------------------------------------------------------------------------
# Browser control status endpoint (Extension polls / SSE notification)
# ---------------------------------------------------------------------------


@app.get("/sessions/{session_id}/browser-status")
async def get_browser_status(
    session_id: str,
    request: Request,
) -> dict[str, Any]:
    """Return current browser control status for a session."""
    return {
        "session_id": session_id,
        "browser_controlling": _browser_controlling.get(session_id, False),
    }
