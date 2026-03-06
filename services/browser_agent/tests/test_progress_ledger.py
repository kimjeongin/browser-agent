"""Tests for P2 Progress Ledger -- progress_check_node, replan_node, route_after_progress."""

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from unittest.mock import AsyncMock, MagicMock, patch


def _tool_msg(content: str, tool_call_id: str = "call-001") -> ToolMessage:
    return ToolMessage(content=content, tool_call_id=tool_call_id)


# ---------------------------------------------------------------------------
# route_after_progress
# ---------------------------------------------------------------------------


def test_route_after_progress_returns_end_when_complete():
    from langgraph.graph import END as GRAPH_END
    from graph.router import route_after_progress

    state = {
        "messages": [],
        "progress_ledger": {
            "is_task_complete": True,
            "is_making_progress": True,
            "is_stuck_in_loop": False,
            "next_action_hint": "",
        },
        "stall_count": 0,
    }
    assert route_after_progress(state) == GRAPH_END


def test_route_after_progress_returns_actor_when_making_progress():
    from graph.router import route_after_progress

    state = {
        "messages": [],
        "progress_ledger": {
            "is_task_complete": False,
            "is_making_progress": True,
            "is_stuck_in_loop": False,
            "next_action_hint": "Click the search button",
        },
        "stall_count": 0,
    }
    assert route_after_progress(state) == "actor"


def test_route_after_progress_returns_replan_when_stall_count_gte_3():
    from graph.state import MAX_STALL_COUNT
    from graph.router import route_after_progress

    state = {
        "messages": [],
        "progress_ledger": {
            "is_task_complete": False,
            "is_making_progress": False,
            "is_stuck_in_loop": False,
            "next_action_hint": "",
        },
        "stall_count": MAX_STALL_COUNT,
    }
    assert route_after_progress(state) == "replan"


def test_route_after_progress_returns_replan_when_stuck_in_loop():
    from graph.router import route_after_progress

    state = {
        "messages": [],
        "progress_ledger": {
            "is_task_complete": False,
            "is_making_progress": True,
            "is_stuck_in_loop": True,
            "next_action_hint": "",
        },
        "stall_count": 0,
    }
    assert route_after_progress(state) == "replan"


# ---------------------------------------------------------------------------
# progress_check_node
# ---------------------------------------------------------------------------


def test_progress_check_node_making_progress_on_success():
    """No error, no loop → is_making_progress=True, stall_count reset to 0."""
    from graph.nodes import progress_check_node

    state = {
        "messages": [_tool_msg("Navigated to: https://youtube.com")],
        "stall_count": 2,
        "action_history": ["browser_navigate"],
    }

    result = progress_check_node(state)

    assert result["progress_ledger"]["is_making_progress"] is True
    assert result["progress_ledger"]["is_task_complete"] is False
    assert result["progress_ledger"]["is_stuck_in_loop"] is False
    assert result["stall_count"] == 0  # reset because making progress


def test_progress_check_node_detects_error_in_last_tool_message():
    """Error keyword in last ToolMessage → is_making_progress=False, stall incremented."""
    from graph.nodes import progress_check_node

    state = {
        "messages": [_tool_msg("TOOL FAILED [click]: Element not found")],
        "stall_count": 1,
        "action_history": ["browser_click"],
    }

    result = progress_check_node(state)

    assert result["progress_ledger"]["is_making_progress"] is False
    assert result["stall_count"] == 2  # incremented


def test_progress_check_node_detects_loop_when_same_tool_repeated():
    """Same tool 3 times in action_history → is_stuck_in_loop=True."""
    from graph.nodes import progress_check_node

    state = {
        "messages": [_tool_msg("Clicked element: #btn")],
        "stall_count": 0,
        "action_history": ["browser_click", "browser_click", "browser_click"],
    }

    result = progress_check_node(state)

    assert result["progress_ledger"]["is_stuck_in_loop"] is True
    assert result["progress_ledger"]["is_making_progress"] is False
    assert result["stall_count"] == 1  # incremented


def test_progress_check_node_increments_stall_count_on_error():
    """Error in tool result should increment stall_count."""
    from graph.nodes import progress_check_node

    state = {
        "messages": [_tool_msg("timeout: request timed out after 30s")],
        "stall_count": 1,
        "action_history": ["browser_navigate"],
    }

    result = progress_check_node(state)
    assert result["stall_count"] == 2  # incremented


def test_progress_check_node_resets_stall_count_when_progressing():
    """Successful tool result should reset stall_count to 0."""
    from graph.nodes import progress_check_node

    state = {
        "messages": [_tool_msg("Navigated to: https://youtube.com")],
        "stall_count": 2,  # was stalled
        "action_history": ["browser_navigate"],
    }

    result = progress_check_node(state)
    assert result["stall_count"] == 0  # reset on progress


# ---------------------------------------------------------------------------
# replan_node
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replan_node_resets_stall_count_to_zero():
    from graph.nodes import replan_node

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(
        return_value=AIMessage(content="Try using screenshot with marks instead.")
    )

    state = {
        "messages": [HumanMessage(content="Search YouTube")],
        "session_id": "sess-001",
        "stall_count": 3,
        "action_history": ["browser_click", "browser_click", "browser_click"],
    }

    result = await replan_node(state, llm=mock_llm)
    assert result["stall_count"] == 0


@pytest.mark.asyncio
async def test_replan_node_clears_action_history():
    from graph.nodes import replan_node

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(
        return_value=AIMessage(content="New strategy: use DOM tool first.")
    )

    state = {
        "messages": [HumanMessage(content="Search YouTube")],
        "session_id": "sess-001",
        "stall_count": 3,
        "action_history": ["browser_click", "browser_click", "browser_navigate"],
    }

    result = await replan_node(state, llm=mock_llm)
    assert result["action_history"] == []


# ---------------------------------------------------------------------------
# actor_node with Progress Ledger integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_actor_node_appends_tool_call_to_action_history():
    """actor_node should record tool call names in action_history."""
    from graph.nodes import actor_node

    mock_llm = MagicMock()
    tool_call = {
        "name": "browser_navigate",
        "args": {"session_id": "s", "url": "https://youtube.com"},
        "id": "call-1",
        "type": "tool_call",
    }
    mock_llm.ainvoke = AsyncMock(
        return_value=AIMessage(content="", tool_calls=[tool_call])
    )

    state = {
        "messages": [HumanMessage(content="Navigate to YouTube")],
        "session_id": "sess-001",
        "stall_count": 0,
        "progress_ledger": {},
        "action_history": ["browser_get_structured_dom"],
    }

    result = await actor_node(state, llm_with_tools=mock_llm)
    assert "browser_navigate" in result["action_history"]
    # Previous history preserved (up to 5)
    assert "browser_get_structured_dom" in result["action_history"]


@pytest.mark.asyncio
async def test_actor_node_includes_progress_hint_in_system_prompt():
    """actor_node should include next_action_hint from progress_ledger in system prompt."""
    from graph.nodes import actor_node

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=AIMessage(content="Done"))

    state = {
        "messages": [HumanMessage(content="Do something")],
        "session_id": "sess-001",
        "stall_count": 0,
        "progress_ledger": {
            "next_action_hint": "Click the blue submit button",
        },
        "action_history": [],
    }

    await actor_node(state, llm_with_tools=mock_llm)

    call_args = mock_llm.ainvoke.call_args[0][0]
    system_msg = call_args[0]
    assert isinstance(system_msg, SystemMessage)
    assert "Click the blue submit button" in system_msg.content


# ---------------------------------------------------------------------------
# Graph compilation
# ---------------------------------------------------------------------------


def test_build_browser_graph_p2_compiles_with_all_nodes():
    """Graph should compile with actor, tools, progress_check, replan (no planner)."""
    from tools.browser_tools import BROWSER_TOOLS
    from graph.builder import build_browser_graph

    mock_llm = MagicMock()
    mock_llm.bind_tools = MagicMock(return_value=mock_llm)

    graph = build_browser_graph(
        llm_with_tools=mock_llm,
        planner_llm=mock_llm,
        tools=BROWSER_TOOLS,
        checkpointer=None,
    )

    assert graph is not None
    # Verify key nodes are in the compiled graph
    node_names = list(graph.get_graph().nodes.keys())
    assert "actor" in node_names
    assert "tools" in node_names
    assert "progress_check" in node_names
    assert "replan" in node_names
    # planner and validator should NOT be present
    assert "planner" not in node_names
    assert "validator" not in node_names
