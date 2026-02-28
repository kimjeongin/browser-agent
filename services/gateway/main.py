"""Gateway service -- public-facing entry point for the browser extension.

Responsibilities:
- JWT-authenticated session management (in-memory, TTL enforced lazily)
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
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated, Any

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
# In-memory session store
# Sessions are stored here instead of Redis. TTL is enforced lazily on access.
# NOTE: State is process-local. For horizontal scaling, migrate to an external
#       store (e.g. Redis) and replace asyncio.Queue/Future with Redis Streams.
# ---------------------------------------------------------------------------

# session_id → Session model
_sessions: dict[str, Session] = {}

# session_id → expiry (monotonic time)
_session_expires_at: dict[str, float] = {}

# ---------------------------------------------------------------------------
# In-memory state for browser tool invocations
# Per-session asyncio.Queue for command dispatch (SSE → Extension)
# Per-invocation asyncio.Future for result awaiting (Browser Agent blocks)
# ---------------------------------------------------------------------------

# session_id → asyncio.Queue of tool_invocation dicts
_session_queues: dict[str, asyncio.Queue] = {}

# inv_id → (asyncio.Future[dict], created_at_monotonic)
# The float is the creation timestamp (asyncio event loop monotonic time)
# Used for stale invocation cleanup
_pending_invocations: dict[str, tuple[asyncio.Future, float]] = {}

# inv_id → session_id reverse mapping for cleanup and status checks
_invocation_to_session: dict[str, str] = {}

# session_id → bool (True when browser agent is actively controlling)
_browser_controlling: dict[str, bool] = {}

# session_id → number of active SSE subscribers (Extension connections)
_session_sse_subscribers: dict[str, int] = {}

# session_id → asyncio.Semaphore (max 1 concurrent tool call per session)
_session_semaphores: dict[str, asyncio.Semaphore] = {}


def _get_session_semaphore(session_id: str) -> asyncio.Semaphore:
    if session_id not in _session_semaphores:
        _session_semaphores[session_id] = asyncio.Semaphore(1)
    return _session_semaphores[session_id]


def _set_session(session: Session) -> None:
    """Store a session with TTL in the in-memory store."""
    _sessions[session.session_id] = session
    _session_expires_at[session.session_id] = time.monotonic() + settings.session_ttl


def _get_session_or_404(session_id: str) -> Session:
    """Load a session from the in-memory store or raise 404.

    Also evicts the session if its TTL has elapsed (lazy expiry).
    """
    session = _sessions.get(session_id)
    expires_at = _session_expires_at.get(session_id)

    if session is None or (expires_at is not None and time.monotonic() > expires_at):
        _sessions.pop(session_id, None)
        _session_expires_at.pop(session_id, None)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found",
        )
    return session


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
        entry = _pending_invocations.pop(inv_id, None)
        if entry:
            future, _ = entry
            if not future.done():
                future.cancel()
        _invocation_to_session.pop(inv_id, None)

    _session_queues.pop(session_id, None)
    _browser_controlling.pop(session_id, None)
    _sessions.pop(session_id, None)
    _session_expires_at.pop(session_id, None)
    _session_sse_subscribers.pop(session_id, None)
    _session_semaphores.pop(session_id, None)


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
# Background cleanup task -- stale invocation reaper
# ---------------------------------------------------------------------------

_STALE_INVOCATION_TIMEOUT_S: float = 120.0  # 2 minutes


async def _cleanup_stale_invocations() -> None:
    """Periodically clean up invocations that never received a result.

    Normally invocations are cleaned up in invoke_browser_tool's finally block.
    This task handles edge cases like crashed browser agent or network failures
    where cleanup code never ran.
    """
    while True:
        await asyncio.sleep(60)  # run every minute
        try:
            now = asyncio.get_running_loop().time()
            stale = [
                inv_id
                for inv_id, (future, created_at) in list(
                    _pending_invocations.items()
                )
                if (now - created_at) > _STALE_INVOCATION_TIMEOUT_S
            ]
            for inv_id in stale:
                entry = _pending_invocations.pop(inv_id, None)
                if entry:
                    future, _ = entry
                    if not future.done():
                        future.cancel()
                _invocation_to_session.pop(inv_id, None)

            if stale:
                logger.warning(
                    "Cleaned up %d stale invocations (timeout=%ss)",
                    len(stale),
                    _STALE_INVOCATION_TIMEOUT_S,
                )
        except Exception:  # noqa: BLE001
            logger.exception("Error in stale invocation cleanup task")


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

    # ACP client for Orchestrator
    app.state.acp = ACPClient(base_url=settings.orchestrator_url)

    # Start background cleanup task
    cleanup_task = asyncio.create_task(_cleanup_stale_invocations())

    logger.info(
        "Gateway started  [orchestrator=%s]",
        settings.orchestrator_url,
    )

    yield

    # Cleanup
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    logger.info("Gateway shutdown complete")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Browser Agent Gateway",
    version="0.2.0",
    lifespan=lifespan,
)

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

# Type alias for the authenticated-user dependency
CurrentUser = Annotated[dict[str, Any], Depends(get_current_user)]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _acp(request: Request) -> ACPClient:
    """Retrieve the ACP client from app state."""
    return request.app.state.acp


def _get_user_id(user: dict[str, Any]) -> str | None:
    """Extract a stable user ID from the token payload."""
    return user.get("sub") or user.get("preferred_username") or user.get("email")


def _verify_session_owner(session: Session, user: dict[str, Any]) -> None:
    """Ensure the authenticated user owns the session."""
    user_id = _get_user_id(user)
    if not user_id or session.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not own this session",
        )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "gateway",
        "active_sessions": len(_session_queues),
    }


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------


@app.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(
    user: CurrentUser,
) -> SessionResponse:
    """Create a new session for the authenticated user."""
    user_id = _get_user_id(user)
    if not user_id:
        logger.error("Token payload missing identity claims: %s", list(user.keys()))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token missing 'sub', 'preferred_username', or 'email' claims",
        )

    session = Session(
        session_id=uuid.uuid4().hex,
        user_id=user_id,
    )

    _set_session(session)

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
    user: CurrentUser,
) -> SessionResponse:
    """Return session metadata."""
    session = _get_session_or_404(session_id)
    _verify_session_owner(session, user)

    return SessionResponse(
        session_id=session.session_id,
        user_id=session.user_id,
        status=session.status,
        browser_controlling=_browser_controlling.get(session_id, False),
    )


@app.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    user: CurrentUser,
) -> dict[str, bool]:
    """Mark a session as inactive."""
    session = _get_session_or_404(session_id)
    _verify_session_owner(session, user)

    session.status = "inactive"
    session.last_activity = datetime.now(timezone.utc)
    _set_session(session)

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
    session = _get_session_or_404(session_id)
    _verify_session_owner(session, user)

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
    _set_session(session)

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
    session = _get_session_or_404(session_id)
    _verify_session_owner(session, user)

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
        _set_session(session)

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
        # Track subscriber count
        _session_sse_subscribers[session_id] = (
            _session_sse_subscribers.get(session_id, 0) + 1
        )
        logger.info(
            "Extension SSE connected for session %s (subscribers=%d)",
            session_id,
            _session_sse_subscribers[session_id],
        )
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
            count = _session_sse_subscribers.get(session_id, 1)
            _session_sse_subscribers[session_id] = max(0, count - 1)
            logger.info(
                "Extension SSE disconnected for session %s (subscribers=%d)",
                session_id,
                _session_sse_subscribers[session_id],
            )

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
    # Verify session exists
    _get_session_or_404(session_id)

    # Check if Extension SSE is connected (not just queue exists)
    if _session_sse_subscribers.get(session_id, 0) == 0:
        raise HTTPException(
            status_code=503,
            detail=(
                "Browser extension is not connected for this session. "
                "Please ensure the extension is active and connected."
            ),
        )

    # Acquire per-session semaphore (serializes concurrent tool calls)
    sem = _get_session_semaphore(session_id)
    async with sem:
        inv_id = str(uuid.uuid4())
        queue = _get_or_create_queue(session_id)

        # Create future BEFORE enqueuing to avoid race condition
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        _pending_invocations[inv_id] = (future, loop.time())
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
    entry = _pending_invocations.get(inv_id)
    if entry is None:
        logger.warning(
            "Received result for unknown/expired inv_id=%s session=%s",
            inv_id,
            session_id,
        )
        # Return 200 anyway to avoid Extension retry loops
        return {"ok": False, "reason": "invocation not found or already expired"}

    future, _ = entry
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
