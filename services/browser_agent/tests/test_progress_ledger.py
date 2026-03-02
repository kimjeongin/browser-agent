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


@pytest.mark.asyncio
async def test_progress_check_node_parses_valid_json_response():
    from graph.nodes import progress_check_node

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(
        return_value=AIMessage(
            content='{"is_task_complete": false, "is_making_progress": true, '
                    '"is_stuck_in_loop": false, "next_action_hint": "Click search button"}'
        )
    )

    state = {
        "messages": [_tool_msg("Navigated to: https://youtube.com")],
        "stall_count": 0,
        "action_history": ["browser_navigate"],
    }

    result = await progress_check_node(state, llm=mock_llm)

    assert result["progress_ledger"]["is_making_progress"] is True
    assert result["progress_ledger"]["next_action_hint"] == "Click search button"
    assert result["stall_count"] == 0  # reset because making progress


@pytest.mark.asyncio
async def test_progress_check_node_fallback_on_non_json_response():
    """Non-JSON response should fall back to 'making progress' without crashing."""
    from graph.nodes import progress_check_node

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(
        return_value=AIMessage(content="The task is still in progress.")
    )

    state = {
        "messages": [_tool_msg("some result")],
        "stall_count": 1,
        "action_history": [],
    }

    result = await progress_check_node(state, llm=mock_llm)

    # Fallback: is_making_progress=True avoids false stall
    assert result["progress_ledger"]["is_making_progress"] is True
    assert result["stall_count"] == 0  # reset because fallback = making progress


@pytest.mark.asyncio
async def test_progress_check_node_strips_markdown_fences():
    """JSON wrapped in ```json code fences should parse correctly."""
    from graph.nodes import progress_check_node

    json_content = (
        "```json\n"
        '{"is_task_complete": true, "is_making_progress": true, '
        '"is_stuck_in_loop": false, "next_action_hint": "done"}\n'
        "```"
    )
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=AIMessage(content=json_content))

    state = {
        "messages": [_tool_msg("Task finished")],
        "stall_count": 0,
        "action_history": [],
    }

    result = await progress_check_node(state, llm=mock_llm)
    assert result["progress_ledger"]["is_task_complete"] is True


@pytest.mark.asyncio
async def test_progress_check_node_increments_stall_count_when_not_progressing():
    from graph.nodes import progress_check_node

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(
        return_value=AIMessage(
            content='{"is_task_complete": false, "is_making_progress": false, '
                    '"is_stuck_in_loop": false, "next_action_hint": "try again"}'
        )
    )

    state = {
        "messages": [_tool_msg("TOOL FAILED [click]: Element not found")],
        "stall_count": 1,
        "action_history": ["browser_click"],
    }

    result = await progress_check_node(state, llm=mock_llm)
    assert result["stall_count"] == 2  # incremented


@pytest.mark.asyncio
async def test_progress_check_node_resets_stall_count_when_progressing():
    from graph.nodes import progress_check_node

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(
        return_value=AIMessage(
            content='{"is_task_complete": false, "is_making_progress": true, '
                    '"is_stuck_in_loop": false, "next_action_hint": "type the query"}'
        )
    )

    state = {
        "messages": [_tool_msg("Navigated to: https://youtube.com")],
        "stall_count": 2,  # was stalled
        "action_history": ["browser_navigate"],
    }

    result = await progress_check_node(state, llm=mock_llm)
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
    """P2 graph should compile with planner, actor, tools, progress_check, replan."""
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
    assert "planner" in node_names
    assert "actor" in node_names
    assert "tools" in node_names
    assert "progress_check" in node_names
    assert "replan" in node_names
    # validator should NOT be present
    assert "validator" not in node_names
