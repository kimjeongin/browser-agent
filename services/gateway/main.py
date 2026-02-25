"""Gateway service -- public-facing entry point for the browser extension.

Responsibilities:
- JWT-authenticated session management (Redis-backed)
- Chat proxy to Orchestrator via ACP (sync and SSE streaming)
- Browser command SSE channel (Redis Pub/Sub -> Extension)
- Command result ingestion (Extension -> Redis Pub/Sub -> Browser Relay)
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
from shared.models.browser_command import CommandResult
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

    # Redis connection pool
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
    version="0.1.0",
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
# Browser command SSE channel (no auth -- content script context)
# ---------------------------------------------------------------------------


@app.get("/sessions/{session_id}/commands")
async def browser_command_stream(
    session_id: str,
    request: Request,
) -> EventSourceResponse:
    """SSE stream delivering browser commands from Redis Pub/Sub.

    The extension's background service worker holds this connection open.
    Browser Relay MCP publishes commands to ``browser_cmd:{session_id}``.
    """
    redis = _redis(request)
    channel_name = f"browser_cmd:{session_id}"

    async def _command_generator():
        pubsub = redis.pubsub()
        await pubsub.subscribe(channel_name)
        logger.info("SSE subscribed to %s", channel_name)

        try:
            while True:
                # Check for client disconnect
                if await request.is_disconnected():
                    break

                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if message is not None and message["type"] == "message":
                    yield {"data": message["data"]}
                else:
                    # No message within timeout -- send keepalive
                    yield {"comment": "keepalive"}
                    # Sleep to enforce ~15s keepalive interval when idle.
                    # The timeout=1.0 above already waits 1s per iteration;
                    # we add more sleep only when there is no real data.
                    await asyncio.sleep(14.0)
        finally:
            await pubsub.unsubscribe(channel_name)
            await pubsub.aclose()
            logger.info("SSE unsubscribed from %s", channel_name)

    return EventSourceResponse(_command_generator())


# ---------------------------------------------------------------------------
# Browser command result ingestion
# ---------------------------------------------------------------------------


@app.post("/sessions/{session_id}/command-result")
async def receive_command_result(
    session_id: str,
    body: CommandResult,
    request: Request,
) -> dict[str, bool]:
    """Receive a command execution result from the extension.

    Publishes the result to ``browser_result:{command_id}`` so that
    Browser Relay MCP can pick it up.
    """
    redis = _redis(request)
    result_channel = f"browser_result:{body.command_id}"

    await redis.publish(result_channel, body.model_dump_json())

    logger.debug(
        "Command result published: cmd=%s session=%s success=%s",
        body.command_id,
        session_id,
        body.success,
    )
    return {"ok": True}
