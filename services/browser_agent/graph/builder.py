"""LangGraph graph builder for the Browser Agent."""

from __future__ import annotations

from functools import partial
from typing import Any

from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from graph.nodes import actor_node, progress_check_node, replan_node
from graph.router import route_after_actor, route_after_progress
from graph.state import AgentState


def build_browser_graph(
    llm_with_tools: Any,
    planner_llm: Any,
    tools: list,
    checkpointer: Any,
) -> Any:
    """Compile a Progress Ledger LangGraph for the browser agent.

    Graph flow:
      START -> actor -> route_after_actor
        -> tools -> progress_check -> route_after_progress
          -> is_task_complete -> END
          -> is_making_progress -> actor
          -> stall_count >= 3 or stuck_in_loop -> replan -> actor
        -> no_tool_calls -> END
    """
    builder = StateGraph(AgentState)

    builder.add_node("actor", partial(actor_node, llm_with_tools=llm_with_tools))
    builder.add_node("tools", ToolNode(tools))
    builder.add_node("progress_check", progress_check_node)
    builder.add_node("replan", partial(replan_node, llm=planner_llm))

    builder.set_entry_point("actor")

    # actor -> tools (if tool calls) or END (if final answer)
    builder.add_conditional_edges(
        "actor",
        route_after_actor,
        {"tools": "tools", END: END},
    )

    # tools -> progress_check
    builder.add_edge("tools", "progress_check")

    # progress_check -> actor / replan / END
    builder.add_conditional_edges(
        "progress_check",
        route_after_progress,
        {"actor": "actor", "replan": "replan", END: END},
    )

    # replan -> actor (re-execute with new strategy)
    builder.add_edge("replan", "actor")

    return builder.compile(checkpointer=checkpointer)
