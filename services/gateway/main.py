"""Gateway service -- public-facing entry point for the browser extension.

Responsibilities:
- JWT-authenticated session management (Redis-backed)
- Chat proxy to Orchestrator via ACP (sync and SSE streaming)
- webMCP-style browser tool architecture:
  - Extension registers tool manifests at session start
  - SSE channel delivers tool_invocation events via asyncio.Queue
  - Tool invocations block until Extension resolves via asyncio.Future
  - Redis used only for session state (no Pub/Sub for browser commands)
"""

import asyncio
import json
import logging
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
    tool_invocation_timeout: float = 30.0  # seconds


settings = Settings()

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


class BrowserToolManifest(BaseModel):
    """Tool manifest registered by the extension (webMCP-style)."""

    tools: list[dict]  # Each dict follows JSON Schema tool definition


class BrowserToolInvoke(BaseModel):
    """Request body for invoking a browser tool."""

    tool: str
    params: dict = {}


class BrowserToolResult(BaseModel):
    """Result submitted by the extension after executing a tool invocation."""

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

    # Redis connection pool (session state only)
    app.state.redis = aioredis.from_url(
        settings.redis_url,
        decode_responses=True,
    )

    # ACP client for Orchestrator
    app.state.acp = ACPClient(base_url=settings.orchestrator_url)

    # webMCP browser tool state
    app.state.session_queues: dict[str, asyncio.Queue] = {}
    app.state.pending_invocations: dict[str, asyncio.Future] = {}
    app.state.browser_tool_manifests: dict[str, list[dict]] = {}

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
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
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "gateway"}


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

    logger.info("Session created: %s (user=%s)", session.session_id, user_id)
    return SessionResponse(
        session_id=session.session_id,
        user_id=session.user_id,
        status=session.status,
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

    # Persist the updated status (keep existing TTL via XX flag is unavailable
    # for SET with EX, so we re-set with full TTL -- acceptable for soft delete)
    await redis.set(
        _session_key(session_id),
        session.model_dump_json(),
        ex=settings.session_ttl,
    )

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

    # Build ACP input following LangGraph message convention
    messages: list[dict[str, Any]] = [
        {"role": "human", "content": body.content},
    ]
    if body.images:
        messages[0]["images"] = body.images

    acp_input: dict[str, Any] = {"messages": messages}

    try:
        result = await acp.run(thread_id=session_id, input=acp_input)
    except Exception as exc:
        logger.error("Orchestrator call failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Orchestrator is unavailable",
        ) from exc

    # Touch session activity
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
    }

    async def _event_generator():
        try:
            async for event in acp.run_stream(
                thread_id=session_id, input=acp_input
            ):
                # Forward each parsed SSE event dict as a JSON SSE frame
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

        # Touch session activity after stream completes
        session.last_activity = datetime.now(timezone.utc)
        await redis.set(
            _session_key(session_id),
            session.model_dump_json(),
            ex=settings.session_ttl,
        )

    return EventSourceResponse(_event_generator())


# ---------------------------------------------------------------------------
# Browser tools -- webMCP-style tool manifest & invocation
# ---------------------------------------------------------------------------


@app.post("/sessions/{session_id}/browser-tools/register")
async def register_browser_tools(
    session_id: str,
    body: BrowserToolManifest,
    request: Request,
) -> dict[str, Any]:
    """Extension registers its available browser tools (webMCP manifest).

    Called once by the Extension Service Worker after establishing the SSE
    channel. No authentication required (Extension SW context).
    """
    request.app.state.browser_tool_manifests[session_id] = body.tools

    logger.info(
        "Browser tools registered: session=%s count=%d tools=%s",
        session_id,
        len(body.tools),
        [t.get("name") for t in body.tools],
    )
    return {"ok": True, "tool_count": len(body.tools)}


@app.get("/sessions/{session_id}/browser-tools")
async def list_browser_tools(
    session_id: str,
    request: Request,
) -> dict[str, Any]:
    """Return the tool manifest for a session.

    Used by Browser Agent to discover available tools. No auth required
    (internal service call).
    """
    tools = request.app.state.browser_tool_manifests.get(session_id, [])
    return {"session_id": session_id, "tools": tools}


@app.post("/sessions/{session_id}/browser-tools/invoke")
async def invoke_browser_tool(
    session_id: str,
    body: BrowserToolInvoke,
    request: Request,
) -> dict[str, Any]:
    """Invoke a browser tool and block until the Extension returns a result.

    The Gateway pushes a ``tool_invocation`` event into the session's SSE
    queue, then awaits an asyncio.Future that the Extension resolves by
    calling the ``/browser-tools/result/{invocation_id}`` endpoint.

    No auth required (internal service call from Browser Agent).
    """
    session_queues: dict[str, asyncio.Queue] = (
        request.app.state.session_queues
    )
    pending: dict[str, asyncio.Future] = (
        request.app.state.pending_invocations
    )

    # Verify the Extension SSE channel is connected
    if session_id not in session_queues:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Extension not connected",
        )

    invocation_id = uuid.uuid4().hex
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()
    pending[invocation_id] = future

    # Push the invocation event into the SSE queue for the Extension
    event_payload = {
        "type": "tool_invocation",
        "invocation_id": invocation_id,
        "tool": body.tool,
        "params": body.params,
    }
    await session_queues[session_id].put(event_payload)

    logger.info(
        "Tool invocation queued: id=%s tool=%s session=%s",
        invocation_id,
        body.tool,
        session_id,
    )

    try:
        result = await asyncio.wait_for(
            future, timeout=settings.tool_invocation_timeout
        )
    except TimeoutError:
        pending.pop(invocation_id, None)
        raise HTTPException(
            status_code=status.HTTP_408_REQUEST_TIMEOUT,
            detail=(
                f"Tool invocation timed out after "
                f"{settings.tool_invocation_timeout:.0f}s"
            ),
        )
    except RuntimeError as exc:
        # Future was resolved with set_exception by the result endpoint
        pending.pop(invocation_id, None)
        return {
            "invocation_id": invocation_id,
            "success": False,
            "result": None,
            "error": str(exc),
        }
    finally:
        pending.pop(invocation_id, None)

    return {
        "invocation_id": invocation_id,
        "success": result.get("success", False),
        "result": result.get("result"),
    }


@app.post("/sessions/{session_id}/browser-tools/result/{invocation_id}")
async def submit_browser_tool_result(
    session_id: str,
    invocation_id: str,
    body: BrowserToolResult,
    request: Request,
) -> dict[str, bool]:
    """Extension submits the result of a tool invocation.

    Resolves (or rejects) the asyncio.Future that the ``invoke`` endpoint
    is awaiting.
    """
    pending: dict[str, asyncio.Future] = (
        request.app.state.pending_invocations
    )
    future = pending.get(invocation_id)

    if future is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No pending invocation with this ID",
        )

    if future.done():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Invocation already resolved",
        )

    if body.success:
        future.set_result({"success": True, "result": body.result})
    else:
        future.set_exception(
            RuntimeError(body.error or "Tool execution failed")
        )

    logger.debug(
        "Tool result received: invocation=%s session=%s success=%s",
        invocation_id,
        session_id,
        body.success,
    )
    return {"ok": True}


# ---------------------------------------------------------------------------
# Browser tool SSE channel (no auth -- Extension SW context)
# ---------------------------------------------------------------------------


@app.get("/sessions/{session_id}/commands")
async def browser_tool_stream(
    session_id: str,
    request: Request,
) -> EventSourceResponse:
    """SSE stream delivering tool invocation events to the Extension.

    The Extension's background Service Worker holds this connection open.
    Events are read from an asyncio.Queue (populated by the ``invoke``
    endpoint). Keepalive comments are sent every ~15 seconds when idle.
    """
    session_queues: dict[str, asyncio.Queue] = (
        request.app.state.session_queues
    )

    queue: asyncio.Queue = asyncio.Queue()
    session_queues[session_id] = queue

    logger.info("SSE tool channel opened: session=%s", session_id)

    async def _event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break

                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except TimeoutError:
                    # No pending invocations -- send keepalive
                    yield {"comment": "keepalive"}
                    continue

                event_type = event.get("type", "message")
                yield {
                    "event": event_type,
                    "data": json.dumps(event, ensure_ascii=False),
                }
        finally:
            # Cleanup: remove queue so invoke endpoint knows Extension
            # is disconnected
            session_queues.pop(session_id, None)
            logger.info("SSE tool channel closed: session=%s", session_id)

    return EventSourceResponse(_event_generator())
