"""Routing functions for the Browser Agent graph."""

from __future__ import annotations

import logging

from langchain_core.messages import AIMessage
from langgraph.graph import END

from graph.state import AgentState, MAX_STALL_COUNT, MAX_REPLAN_COUNT

logger = logging.getLogger(__name__)


def route_after_actor(state: AgentState) -> str:
    """After actor: go to tools if there are tool calls, else END."""
    messages = state["messages"]
    last_message = messages[-1] if messages else None

    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        return "tools"
    return END


def route_after_progress(state: AgentState) -> str:
    """After progress_check: route based on task state and stall count."""
    ledger = state.get("progress_ledger") or {}
    stall_count = state.get("stall_count") or 0
    replan_count = state.get("replan_count") or 0

    if ledger.get("is_task_complete", False):
        return END

    if ledger.get("is_stuck_in_loop", False) or stall_count >= MAX_STALL_COUNT:
        if replan_count >= MAX_REPLAN_COUNT:
            logger.warning("Max replan attempts reached (%d). Terminating.", MAX_REPLAN_COUNT)
            return END
        return "replan"

    # Making progress (or fallback) -- skip planner, go directly to actor
    return "actor"
