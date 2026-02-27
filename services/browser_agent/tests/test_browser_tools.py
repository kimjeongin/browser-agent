"""Tests for P1-1 (get_structured_dom), P1-5 (session_id validation), P2-3 (context compression).

Also covers P2-1 (Planner-Actor-Validator) graph structure and routing functions.
"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# P1-5: session_id validation
# ---------------------------------------------------------------------------


def test_validate_session_id_returns_error_for_empty():
    from main import _validate_session_id

    result = _validate_session_id("")
    assert result is not None
    assert result["success"] is False
    assert "session_id" in result["error"].lower()


def test_validate_session_id_returns_error_for_whitespace():
    from main import _validate_session_id

    result = _validate_session_id("   ")
    assert result is not None
    assert result["success"] is False


def test_validate_session_id_returns_none_for_valid():
    from main import _validate_session_id

    result = _validate_session_id("valid-session-id")
    assert result is None


@pytest.mark.asyncio
async def test_browser_navigate_returns_error_for_empty_session_id():
    """browser_navigate should return error dict when session_id is empty."""
    from main import browser_navigate

    result = await browser_navigate.ainvoke(
        {"session_id": "", "url": "https://example.com"}
    )
    assert result["success"] is False
    assert "session_id" in result["error"].lower()


@pytest.mark.asyncio
async def test_browser_click_returns_error_for_empty_session_id():
    """browser_click should return error dict when session_id is empty."""
    from main import browser_click

    result = await browser_click.ainvoke(
        {"session_id": "", "selector": "#btn"}
    )
    assert result["success"] is False
    assert "session_id" in result["error"].lower()


@pytest.mark.asyncio
async def test_browser_type_returns_error_for_empty_session_id():
    """browser_type should return error dict when session_id is empty."""
    from main import browser_type

    result = await browser_type.ainvoke(
        {"session_id": "", "selector": "#input", "text": "hello"}
    )
    assert result["success"] is False


@pytest.mark.asyncio
async def test_browser_scroll_returns_error_for_empty_session_id():
    """browser_scroll should return error dict when session_id is empty."""
    from main import browser_scroll

    result = await browser_scroll.ainvoke({"session_id": ""})
    assert result["success"] is False


@pytest.mark.asyncio
async def test_browser_screenshot_returns_error_for_empty_session_id():
    """browser_screenshot should return error dict when session_id is empty."""
    from main import browser_screenshot

    result = await browser_screenshot.ainvoke({"session_id": ""})
    assert result["success"] is False


@pytest.mark.asyncio
async def test_browser_extract_content_returns_error_for_empty_session_id():
    """browser_extract_content should return error dict when session_id is empty."""
    from main import browser_extract_content

    result = await browser_extract_content.ainvoke({"session_id": ""})
    assert result["success"] is False


@pytest.mark.asyncio
async def test_browser_wait_for_element_returns_error_for_empty_session_id():
    """browser_wait_for_element should return error dict when session_id is empty."""
    from main import browser_wait_for_element

    result = await browser_wait_for_element.ainvoke(
        {"session_id": "", "selector": "#el"}
    )
    assert result["success"] is False


@pytest.mark.asyncio
async def test_get_page_info_returns_error_for_empty_session_id():
    """get_page_info should return error dict when session_id is empty."""
    from main import get_page_info

    result = await get_page_info.ainvoke({"session_id": ""})
    assert result["success"] is False


@pytest.mark.asyncio
async def test_browser_get_structured_dom_returns_error_for_empty_session_id():
    """browser_get_structured_dom should return error when session_id is missing."""
    from main import browser_get_structured_dom

    result = await browser_get_structured_dom.ainvoke({"session_id": ""})
    assert result["success"] is False


# ---------------------------------------------------------------------------
# P2-3: Context compression
# ---------------------------------------------------------------------------


def _make_tool_message(content: str, tool_call_id: str = "call-001") -> ToolMessage:
    return ToolMessage(content=content, tool_call_id=tool_call_id)


def test_compress_messages_keeps_short_history_intact():
    """Messages <= 6 should not be compressed."""
    from main import _compress_messages

    messages = [
        HumanMessage(content="Navigate to YouTube"),
        AIMessage(content="Navigating..."),
        _make_tool_message('{"url": "https://youtube.com"}'),
    ]
    result = _compress_messages(messages)
    assert len(result) == len(messages)


def test_compress_messages_keeps_first_and_last_4():
    """With >6 messages: first + compressed older + last 4."""
    from main import _compress_messages

    messages = [
        HumanMessage(content="User request"),  # [0] first
        AIMessage(content="Step 1"),
        _make_tool_message("tool result 1", "call-1"),
        AIMessage(content="Step 2"),
        _make_tool_message("tool result 2", "call-2"),
        AIMessage(content="Step 3"),  # [-4] start of recent
        _make_tool_message("tool result 3", "call-3"),
        AIMessage(content="Step 4"),
        _make_tool_message("tool result 4", "call-4"),
    ]
    result = _compress_messages(messages)

    # First message preserved
    assert result[0].content == "User request"
    # Last 4 messages preserved
    assert result[-4].content == "Step 3"
    assert result[-1].content == "tool result 4"


def test_compress_messages_removes_base64_from_old_tool_messages():
    """Old ToolMessages with base64 image data should have it stripped."""
    from main import _compress_messages

    base64_content = "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQEASABIAAD"

    messages = [
        HumanMessage(content="User request"),  # first
        AIMessage(content="Taking screenshot..."),
        _make_tool_message(base64_content, "call-screenshot"),  # should be compressed
        AIMessage(content="Another step"),
        _make_tool_message("page info", "call-info"),
        AIMessage(content="More steps"),  # recent start
        _make_tool_message("recent result", "call-recent"),
        AIMessage(content="Almost done"),
        _make_tool_message("final result", "call-final"),
    ]

    result = _compress_messages(messages)

    # Find the compressed screenshot message
    compressed_screenshot = None
    for msg in result:
        if isinstance(msg, ToolMessage) and msg.tool_call_id == "call-screenshot":
            compressed_screenshot = msg
            break

    assert compressed_screenshot is not None
    assert "data:image" not in compressed_screenshot.content
    assert "screenshot removed" in compressed_screenshot.content


def test_compress_messages_preserves_recent_screenshots():
    """Screenshots in the last 4 messages should NOT be stripped."""
    from main import _compress_messages

    base64_content = "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQEASABIAAD"

    messages = [
        HumanMessage(content="User request"),
        AIMessage(content="Step 1"),
        _make_tool_message("result 1", "call-1"),
        AIMessage(content="Step 2"),
        _make_tool_message("result 2", "call-2"),
        AIMessage(content="Taking screenshot"),
        _make_tool_message(base64_content, "call-recent-screenshot"),  # in last 4
        AIMessage(content="Done"),
    ]

    result = _compress_messages(messages)

    # Find recent screenshot - should be preserved
    recent_screenshot = None
    for msg in result:
        if isinstance(msg, ToolMessage) and msg.tool_call_id == "call-recent-screenshot":
            recent_screenshot = msg
            break

    assert recent_screenshot is not None
    assert "data:image" in recent_screenshot.content  # preserved!


# ---------------------------------------------------------------------------
# P1-1: browser_get_structured_dom tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_browser_get_structured_dom_calls_gateway():
    """browser_get_structured_dom should invoke 'get_structured_dom' on gateway."""
    from main import browser_get_structured_dom

    mock_client = MagicMock()
    mock_client.invoke = AsyncMock(
        return_value={
            "url": "https://example.com",
            "title": "Example",
            "interactable_count": 5,
            "elements": [],
            "page_text_preview": "Example page",
        }
    )

    with patch("main._gateway_client", mock_client):
        result = await browser_get_structured_dom.ainvoke(
            {"session_id": "sess-001"}
        )

    mock_client.invoke.assert_awaited_once_with("sess-001", "get_structured_dom", {})
    assert result["url"] == "https://example.com"
    assert result["interactable_count"] == 5


# ---------------------------------------------------------------------------
# P2-1: Planner-Actor-Validator graph structure
# ---------------------------------------------------------------------------


def test_build_browser_graph_compiles_without_error():
    """Graph should compile successfully with planner, actor, tools, validator."""
    from main import BROWSER_TOOLS, build_browser_graph

    mock_llm = MagicMock()
    mock_llm.bind_tools = MagicMock(return_value=mock_llm)

    graph = build_browser_graph(
        llm_with_tools=mock_llm,
        planner_llm=mock_llm,
        validator_llm=mock_llm,
        tools=BROWSER_TOOLS,
        checkpointer=None,
    )

    assert graph is not None


def test_route_after_actor_returns_tools_when_tool_calls():
    """When actor produces tool calls, should route to tools."""
    from main import route_after_actor

    tool_call = {
        "name": "browser_navigate",
        "args": {"session_id": "s", "url": "http://x.com"},
        "id": "call-1",
    }
    state = {
        "messages": [AIMessage(content="", tool_calls=[tool_call])],
        "session_id": "sess-001",
        "validation_failed": False,
        "retry_count": 0,
    }

    result = route_after_actor(state)
    assert result == "tools"


def test_route_after_actor_returns_end_when_no_tool_calls():
    """When actor has no tool calls (final answer), should route to END."""
    from langgraph.graph import END as GRAPH_END

    from main import route_after_actor

    state = {
        "messages": [AIMessage(content="Task completed!")],
        "session_id": "sess-001",
        "validation_failed": False,
        "retry_count": 0,
    }

    result = route_after_actor(state)
    assert result == GRAPH_END


def test_route_after_validator_retries_on_failure():
    """When validation fails and retries < max, should route to actor."""
    from main import MAX_VALIDATION_RETRIES, route_after_validator

    state = {
        "messages": [],
        "session_id": "sess-001",
        "validation_failed": True,
        "retry_count": MAX_VALIDATION_RETRIES - 1,
    }

    result = route_after_validator(state)
    assert result == "actor"


def test_route_after_validator_goes_to_planner_when_max_retries_exceeded():
    """When max retries exceeded, should route to planner."""
    from main import MAX_VALIDATION_RETRIES, route_after_validator

    state = {
        "messages": [],
        "session_id": "sess-001",
        "validation_failed": True,
        "retry_count": MAX_VALIDATION_RETRIES,
    }

    result = route_after_validator(state)
    assert result == "planner"


def test_route_after_validator_goes_to_planner_on_success():
    """When validation succeeds, should route to planner for next step."""
    from main import route_after_validator

    state = {
        "messages": [],
        "session_id": "sess-001",
        "validation_failed": False,
        "retry_count": 0,
    }

    result = route_after_validator(state)
    assert result == "planner"


# ---------------------------------------------------------------------------
# P2-1: Node functions (unit tests with mocked LLM)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_planner_node_injects_planner_system_prompt():
    """Planner node should use PLANNER_SYSTEM_PROMPT."""
    from main import PLANNER_SYSTEM_PROMPT, planner_node

    mock_llm = MagicMock()
    mock_response = AIMessage(content="Navigate to YouTube.")
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)

    state = {
        "messages": [HumanMessage(content="Search YouTube for cats")],
        "session_id": "sess-001",
        "validation_failed": False,
        "retry_count": 0,
    }

    result = await planner_node(state, llm=mock_llm)

    # Verify the LLM was called with planner prompt
    call_args = mock_llm.ainvoke.call_args[0][0]
    assert isinstance(call_args[0], SystemMessage)
    assert "planner" in call_args[0].content.lower()
    assert result["validation_failed"] is False


@pytest.mark.asyncio
async def test_actor_node_injects_system_prompt_with_session_id():
    """Actor node should use SYSTEM_PROMPT and inject session_id."""
    from main import actor_node

    mock_llm = MagicMock()
    mock_response = AIMessage(content="Navigating to YouTube...")
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)

    state = {
        "messages": [HumanMessage(content="Navigate to YouTube")],
        "session_id": "sess-001",
        "validation_failed": False,
        "retry_count": 0,
    }

    result = await actor_node(state, llm_with_tools=mock_llm)

    call_args = mock_llm.ainvoke.call_args[0][0]
    assert isinstance(call_args[0], SystemMessage)
    assert "sess-001" in call_args[0].content
    assert len(result["messages"]) == 1


@pytest.mark.asyncio
async def test_validator_node_returns_success_for_valid_result():
    """Validator should return validation_failed=False for 'success' verdict."""
    from main import validator_node

    mock_llm = MagicMock()
    mock_response = AIMessage(content="success")
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)

    state = {
        "messages": [
            HumanMessage(content="Navigate"),
            _make_tool_message('{"url": "https://youtube.com"}', "call-1"),
        ],
        "session_id": "sess-001",
        "validation_failed": False,
        "retry_count": 0,
    }

    result = await validator_node(state, llm=mock_llm)
    assert result["validation_failed"] is False
    assert result["retry_count"] == 0


@pytest.mark.asyncio
async def test_validator_node_returns_retry_on_failure():
    """Validator should set validation_failed=True and increment retry_count."""
    from main import validator_node

    mock_llm = MagicMock()
    mock_response = AIMessage(content="retry")
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)

    state = {
        "messages": [
            HumanMessage(content="Click button"),
            _make_tool_message('{"error": "Element not found"}', "call-1"),
        ],
        "session_id": "sess-001",
        "validation_failed": False,
        "retry_count": 1,
    }

    result = await validator_node(state, llm=mock_llm)
    assert result["validation_failed"] is True
    assert result["retry_count"] == 2


@pytest.mark.asyncio
async def test_validator_node_handles_no_tool_messages():
    """Validator should return success when no ToolMessage found."""
    from main import validator_node

    mock_llm = MagicMock()

    state = {
        "messages": [HumanMessage(content="Hello")],
        "session_id": "sess-001",
        "validation_failed": False,
        "retry_count": 0,
    }

    result = await validator_node(state, llm=mock_llm)
    assert result["validation_failed"] is False
    assert result["retry_count"] == 0
