"""LangGraph node functions for the Browser Agent Progress Ledger."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from graph.state import AgentState
from graph.prompts import (
    REPLAN_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
)
from graph.utils import _compress_messages

logger = logging.getLogger(__name__)


async def actor_node(state: AgentState, *, llm_with_tools: Any) -> dict[str, Any]:
    """Execute the next browser action using tools."""
    messages = _compress_messages(state["messages"])
    session_id = state.get("session_id", "")
    progress_ledger = state.get("progress_ledger") or {}

    system_content = SYSTEM_PROMPT
    if session_id:
        system_content += (
            f"\n\nCurrent session_id: {session_id}\n"
            "Always use this session_id in your tool calls."
        )

    # Inject progress hint from previous progress_check if available
    hint = progress_ledger.get("next_action_hint", "")
    if hint:
        system_content += f"\n\nSuggested next action: {hint}"

    # Replace or inject system prompt
    if not messages or not isinstance(messages[0], SystemMessage):
        messages = [SystemMessage(content=system_content), *messages]
    else:
        messages = [SystemMessage(content=system_content), *messages[1:]]

    response = await llm_with_tools.ainvoke(messages)

    # Track which tools were called in action_history (last 5)
    action_history = list(state.get("action_history") or [])
    if isinstance(response, AIMessage) and response.tool_calls:
        for tc in response.tool_calls:
            action_history.append(tc["name"])
    action_history = action_history[-5:]  # keep only last 5

    return {"messages": [response], "action_history": action_history}


def progress_check_node(state: AgentState) -> dict[str, Any]:
    """Heuristic progress check -- no LLM call required.

    Detects loops (same tool called 3+ times consecutively) and errors
    in the most recent tool result. Always returns is_task_complete=False;
    the actor decides task completion by responding without tool calls,
    which route_after_actor routes to END.
    """
    action_history = state.get("action_history") or []
    stall_count = state.get("stall_count") or 0
    messages = state.get("messages") or []

    # Loop detection: last 3 actions are the same tool
    is_stuck = (
        len(action_history) >= 3
        and len(set(action_history[-3:])) == 1
    )

    # Error detection in the most recent ToolMessage
    last_tool_content = ""
    for msg in reversed(messages):
        if isinstance(msg, ToolMessage):
            last_tool_content = str(msg.content) if msg.content else ""
            break

    error_patterns = ("error", "failed", "exception", "timeout")
    has_error = any(p in last_tool_content.lower() for p in error_patterns)

    is_making_progress = not is_stuck and not has_error

    # Update stall count
    new_stall_count = 0 if is_making_progress else stall_count + 1

    ledger = {
        "is_task_complete": False,
        "is_making_progress": is_making_progress,
        "is_stuck_in_loop": is_stuck,
    }

    return {
        "progress_ledger": ledger,
        "stall_count": new_stall_count,
    }


async def replan_node(state: AgentState, *, llm: Any) -> dict[str, Any]:
    """Generate a new strategy when the agent is stuck."""
    messages = _compress_messages(state["messages"])
    session_id = state.get("session_id", "")

    system_content = REPLAN_SYSTEM_PROMPT
    if session_id:
        system_content += f"\n\nCurrent session_id: {session_id}"

    # Include a concise summary of recent failures
    action_history = state.get("action_history") or []
    replan_input = [
        SystemMessage(content=system_content),
        HumanMessage(
            content=(
                f"Actions tried so far: {', '.join(action_history) or 'none'}.\n"
                "What different approach should be taken?"
            )
        ),
    ]

    response = await llm.ainvoke(replan_input)
    return {
        "messages": [response],
        "stall_count": 0,
        "action_history": [],
    }
