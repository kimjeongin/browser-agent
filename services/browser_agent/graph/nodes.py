"""LangGraph node functions for the Browser Agent Progress Ledger."""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from graph.state import AgentState
from graph.prompts import (
    PLANNER_SYSTEM_PROMPT,
    PROGRESS_CHECK_SYSTEM_PROMPT,
    REPLAN_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
)
from graph.utils import _compress_messages

logger = logging.getLogger(__name__)


async def planner_node(state: AgentState, *, llm: Any) -> dict[str, Any]:
    """Analyze current state and decide initial strategy."""
    messages = _compress_messages(state["messages"])
    session_id = state.get("session_id", "")

    system_content = PLANNER_SYSTEM_PROMPT
    if session_id:
        system_content += f"\n\nCurrent session_id: {session_id}"

    # Replace or inject system prompt
    if not messages or not isinstance(messages[0], SystemMessage):
        messages = [SystemMessage(content=system_content), *messages]
    else:
        messages = [SystemMessage(content=system_content), *messages[1:]]

    response = await llm.ainvoke(messages)
    return {"messages": [response]}


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


async def progress_check_node(state: AgentState, *, llm: Any) -> dict[str, Any]:
    """Evaluate if the task is making progress and update the ledger."""
    messages = state["messages"]

    # Collect recent tool results for analysis
    recent_results: list[str] = []
    for msg in reversed(messages):
        if isinstance(msg, ToolMessage):
            recent_results.insert(0, str(msg.content)[:300])
            if len(recent_results) >= 3:
                break

    action_history = state.get("action_history") or []
    stall_count = state.get("stall_count") or 0

    progress_input = [
        SystemMessage(content=PROGRESS_CHECK_SYSTEM_PROMPT),
        HumanMessage(
            content=(
                f"Recent tool results:\n{chr(10).join(recent_results)}\n\n"
                f"Recent actions taken: {', '.join(action_history) or 'none'}"
            )
        ),
    ]

    response = await llm.ainvoke(progress_input)
    raw = response.content.strip() if hasattr(response, "content") else ""

    # Strip markdown fences if present (```json ... ```)
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(
            line for line in lines if not line.strip().startswith("```")
        ).strip()

    # Parse JSON; fall back to "making progress" to avoid false stalls
    try:
        ledger = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        logger.warning("progress_check_node: failed to parse JSON, using fallback")
        ledger = {
            "is_task_complete": False,
            "is_making_progress": True,
            "is_stuck_in_loop": False,
            "next_action_hint": "",
        }

    # Update stall count
    if ledger.get("is_making_progress", True):
        new_stall_count = 0
    else:
        new_stall_count = stall_count + 1

    # Append next_action_hint to action_history for context
    updated_history = list(action_history)
    hint = ledger.get("next_action_hint", "")
    if hint:
        updated_history.append(f"hint:{hint[:50]}")
    updated_history = updated_history[-5:]

    return {
        "progress_ledger": ledger,
        "stall_count": new_stall_count,
        "action_history": updated_history,
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
