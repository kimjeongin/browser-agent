"""ACP server -- FastAPI router factory for exposing LangGraph graphs."""

import json
import logging
import uuid
from collections.abc import AsyncGenerator, Callable
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class RunRequest(BaseModel):
    """Inbound request payload for an agent run."""

    run_id: str = ""
    thread_id: str
    input: dict[str, Any]


class RunResponse(BaseModel):
    """Response payload for a completed agent run."""

    run_id: str
    status: str  # "completed" | "failed"
    output: dict[str, Any] | None = None
    error: str | None = None


def create_acp_router(graph_factory: Callable[[Request], Any]) -> APIRouter:
    """Create a FastAPI router that exposes a LangGraph graph as ACP endpoints.

    Args:
        graph_factory: A callable that receives a ``Request`` and returns
            the compiled LangGraph graph instance (typically retrieved from
            ``request.app.state``).

    Returns:
        An ``APIRouter`` with ``/runs``, ``/runs/stream``, and ``/health``
        endpoints.
    """
    router = APIRouter()

    @router.post("/runs", response_model=RunResponse)
    async def create_run(body: RunRequest, request: Request) -> RunResponse:
        """Execute a synchronous agent run."""
        run_id = body.run_id or str(uuid.uuid4())
        graph = graph_factory(request)

        try:
            result = await graph.ainvoke(
                body.input,
                config={
                    "configurable": {"thread_id": body.thread_id},
                    "recursion_limit": 25,
                },
            )
            return RunResponse(
                run_id=run_id,
                status="completed",
                output=result if isinstance(result, dict) else {"result": result},
            )
        except Exception as exc:
            logger.exception("Run %s failed", run_id)
            return RunResponse(
                run_id=run_id,
                status="failed",
                error=str(exc),
            )

    @router.post("/runs/stream")
    async def create_stream_run(
        body: RunRequest,
        request: Request,
    ) -> StreamingResponse:
        """Execute a streaming agent run, returning SSE events."""
        run_id = body.run_id or str(uuid.uuid4())
        graph = graph_factory(request)

        async def event_generator() -> AsyncGenerator[str, None]:
            tokens_emitted = False
            # Fallback: capture final AIMessage content when no tokens stream
            # (e.g. when LLM runs in a sub-service called via synchronous HTTP)
            last_ai_content = ""

            try:
                async for event in graph.astream_events(
                    body.input,
                    config={
                        "configurable": {"thread_id": body.thread_id},
                        "recursion_limit": 25,
                    },
                    version="v2",
                ):
                    kind = event.get("event", "")
                    data: dict[str, Any] | None = None

                    if kind == "on_chat_model_stream":
                        content = event.get("data", {}).get("chunk", "")
                        # LangChain message chunks expose .content
                        text = (
                            content.content
                            if hasattr(content, "content")
                            else str(content)
                        )
                        if text:
                            tokens_emitted = True
                            data = {"type": "token", "content": text}

                    elif kind == "on_chain_end":
                        # Capture the last AI message produced by any node.
                        # This is the fallback path for orchestrators that call
                        # sub-agents via synchronous HTTP (no token streaming).
                        output = event.get("data", {}).get("output", {})
                        if isinstance(output, dict):
                            msgs = output.get("messages", [])
                            if msgs:
                                last_msg = msgs[-1]
                                text = ""
                                if hasattr(last_msg, "content") and isinstance(
                                    last_msg.content, str
                                ):
                                    text = last_msg.content
                                elif isinstance(last_msg, dict):
                                    text = last_msg.get("content", "")
                                if text:
                                    last_ai_content = text

                    elif kind == "on_tool_start":
                        data = {
                            "type": "tool_start",
                            "name": event.get("name", ""),
                            "run_id": run_id,
                        }

                    elif kind == "on_tool_end":
                        data = {
                            "type": "tool_end",
                            "name": event.get("name", ""),
                            "run_id": run_id,
                        }

                    if data is not None:
                        yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

                # Emit the final answer as a single token if nothing streamed
                if not tokens_emitted and last_ai_content:
                    yield (
                        f"data: {json.dumps({'type': 'token', 'content': last_ai_content}, ensure_ascii=False)}\n\n"
                    )

                # Signal completion
                yield f"data: {json.dumps({'type': 'done', 'run_id': run_id})}\n\n"

            except Exception as exc:
                logger.exception("Stream run %s failed", run_id)
                error_payload = {
                    "type": "error",
                    "run_id": run_id,
                    "error": str(exc),
                }
                yield f"data: {json.dumps(error_payload, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @router.get("/health")
    async def health() -> dict[str, str]:
        """Simple liveness probe."""
        return {"status": "ok"}

    return router
