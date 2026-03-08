"""Tests for P2 Progress Ledger -- progress_check_node, replan_node, route_after_progress."""

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch


def _tool_msg(
    content: str,
    tool_call_id: str = "call-001",
    name: str | None = None,
    artifact: Any = None,
) -> ToolMessage:
    msg = ToolMessage(content=content, tool_call_id=tool_call_id, name=name)
    if artifact is not None:
        msg.artifact = artifact
    return msg


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


def test_route_after_progress_returns_end_when_max_replan_reached():
    """When replan_count >= MAX_REPLAN_COUNT and stuck, should return END instead of replan."""
    from langgraph.graph import END as GRAPH_END
    from graph.router import route_after_progress
    from graph.state import MAX_REPLAN_COUNT

    state = {
        "messages": [],
        "progress_ledger": {"is_task_complete": False, "is_stuck_in_loop": True},
        "stall_count": 0,
        "replan_count": MAX_REPLAN_COUNT,
    }
    result = route_after_progress(state)
    assert result == GRAPH_END


def test_route_after_progress_returns_replan_when_under_max_replan():
    """When replan_count < MAX_REPLAN_COUNT and stuck, should still return replan."""
    from graph.router import route_after_progress
    from graph.state import MAX_REPLAN_COUNT

    state = {
        "messages": [],
        "progress_ledger": {"is_task_complete": False, "is_stuck_in_loop": True},
        "stall_count": 0,
        "replan_count": MAX_REPLAN_COUNT - 1,
    }
    result = route_after_progress(state)
    assert result == "replan"


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


def test_progress_check_node_caches_screenshot_after_screenshot_tool():
    """progress_check_node should store screenshot result in last_screenshot state."""
    from graph.nodes import progress_check_node

    screenshot_artifact = {
        "screenshot": "base64encodeddata",
        "marks": {"1": {"x": 10, "y": 20}, "2": {"x": 30, "y": 40}},
    }
    state = {
        "messages": [
            _tool_msg(
                content="Screenshot taken with 2 marks",
                name="screenshot",
                artifact=screenshot_artifact,
            ),
        ],
        "stall_count": 0,
        "action_history": ["screenshot"],
    }

    result = progress_check_node(state)

    assert result["last_screenshot"] is not None
    assert result["last_screenshot"]["screenshot"] == "base64encodeddata"
    assert len(result["last_screenshot"]["marks"]) == 2


def test_progress_check_node_invalidates_screenshot_after_navigate():
    """progress_check_node should clear last_screenshot when last tool is navigate."""
    from graph.nodes import progress_check_node

    state = {
        "messages": [
            _tool_msg(
                content="Navigated to: https://youtube.com",
                name="navigate",
            ),
        ],
        "stall_count": 0,
        "action_history": ["navigate"],
        "last_screenshot": {
            "screenshot": "old_cached_data",
            "marks": {"1": {"x": 10, "y": 20}},
        },
    }

    result = progress_check_node(state)

    assert result["last_screenshot"] is None
    assert result["last_navigated_url"] == "Navigated to: https://youtube.com"


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


@pytest.mark.asyncio
async def test_replan_node_increments_replan_count():
    """replan_node should increment replan_count from its current value."""
    from graph.nodes import replan_node

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(
        return_value=AIMessage(content="Try a different selector.")
    )

    state = {
        "messages": [HumanMessage(content="Search YouTube")],
        "session_id": "sess-001",
        "stall_count": 3,
        "action_history": ["browser_click", "browser_click", "browser_click"],
        "replan_count": 1,
    }

    result = await replan_node(state, llm=mock_llm)
    assert result["replan_count"] == 2


@pytest.mark.asyncio
async def test_replan_node_initializes_replan_count_when_missing():
    """replan_node should start replan_count at 1 when not present in state."""
    from graph.nodes import replan_node

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(
        return_value=AIMessage(content="Use screenshot to inspect the page.")
    )

    state = {
        "messages": [HumanMessage(content="Search YouTube")],
        "session_id": "sess-001",
        "stall_count": 3,
        "action_history": ["browser_click", "browser_click", "browser_click"],
        # replan_count intentionally omitted
    }

    result = await replan_node(state, llm=mock_llm)
    assert result["replan_count"] == 1


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
