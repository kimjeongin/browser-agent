"""Tests for browser tool output formatting (P0), session_id validation,
Set-of-Marks tool (P1), context compression (P2-3), and graph structure.
"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# P1-5: session_id validation
# ---------------------------------------------------------------------------


def test_validate_session_id_returns_error_for_empty():
    from tools.result_formatter import _validate_session_id

    result = _validate_session_id("")
    assert result is not None
    assert isinstance(result, str)
    assert "TOOL FAILED" in result
    assert "session_id" in result.lower()


def test_validate_session_id_returns_error_for_whitespace():
    from tools.result_formatter import _validate_session_id

    result = _validate_session_id("   ")
    assert result is not None
    assert "TOOL FAILED" in result


def test_validate_session_id_returns_none_for_valid():
    from tools.result_formatter import _validate_session_id

    result = _validate_session_id("valid-session-id")
    assert result is None


@pytest.mark.asyncio
async def test_navigate_returns_error_for_empty_session_id():
    """navigate should return TOOL FAILED string when session_id is empty."""
    from tools.browser_tools import navigate

    result = await navigate.ainvoke(
        {"session_id": "", "url": "https://example.com"}
    )
    assert isinstance(result, str)
    assert "TOOL FAILED" in result
    assert "session_id" in result.lower()


@pytest.mark.asyncio
async def test_click_returns_error_for_empty_session_id():
    """click should return TOOL FAILED string when session_id is empty."""
    from tools.browser_tools import click

    result = await click.ainvoke(
        {"session_id": "", "selector": "#btn"}
    )
    assert isinstance(result, str)
    assert "TOOL FAILED" in result


@pytest.mark.asyncio
async def test_type_text_returns_error_for_empty_session_id():
    """type_text should return TOOL FAILED string when session_id is empty."""
    from tools.browser_tools import type_text

    result = await type_text.ainvoke(
        {"session_id": "", "selector": "#input", "text": "hello"}
    )
    assert isinstance(result, str)
    assert "TOOL FAILED" in result


@pytest.mark.asyncio
async def test_scroll_returns_error_for_empty_session_id():
    """scroll should return TOOL FAILED string when session_id is empty."""
    from tools.browser_tools import scroll

    result = await scroll.ainvoke({"session_id": ""})
    assert isinstance(result, str)
    assert "TOOL FAILED" in result


@pytest.mark.asyncio
async def test_screenshot_returns_error_for_empty_session_id():
    """screenshot should return TOOL FAILED string when session_id is empty."""
    from tools.browser_tools import screenshot

    result = await screenshot.ainvoke({"session_id": ""})
    assert isinstance(result, str)
    assert "TOOL FAILED" in result


@pytest.mark.asyncio
async def test_extract_content_returns_error_for_empty_session_id():
    """extract_content should return TOOL FAILED string when session_id is empty."""
    from tools.browser_tools import extract_content

    result = await extract_content.ainvoke({"session_id": ""})
    assert isinstance(result, str)
    assert "TOOL FAILED" in result


@pytest.mark.asyncio
async def test_wait_for_element_returns_error_for_empty_session_id():
    """wait_for_element should return TOOL FAILED string when session_id is empty."""
    from tools.browser_tools import wait_for_element

    result = await wait_for_element.ainvoke(
        {"session_id": "", "selector": "#el"}
    )
    assert isinstance(result, str)
    assert "TOOL FAILED" in result


@pytest.mark.asyncio
async def test_get_page_info_returns_error_for_empty_session_id():
    """get_page_info should return TOOL FAILED string when session_id is empty."""
    from tools.browser_tools import get_page_info

    result = await get_page_info.ainvoke({"session_id": ""})
    assert isinstance(result, str)
    assert "TOOL FAILED" in result


@pytest.mark.asyncio
async def test_get_structured_dom_returns_error_for_empty_session_id():
    """get_structured_dom should return TOOL FAILED string when session_id is missing."""
    from tools.browser_tools import get_structured_dom

    result = await get_structured_dom.ainvoke({"session_id": ""})
    assert isinstance(result, str)
    assert "TOOL FAILED" in result


# ---------------------------------------------------------------------------
# P0: _format_aci_result helper tests
# ---------------------------------------------------------------------------


def test_format_aci_result_success_navigate():
    from tools.result_formatter import _format_aci_result

    result = _format_aci_result("navigate", True, {"url": "https://example.com"})
    assert result == "Navigated to: https://example.com"


def test_format_aci_result_success_click():
    from tools.result_formatter import _format_aci_result

    result = _format_aci_result("click", True, {"clicked": "#submit-btn"})
    assert result == "Clicked element: #submit-btn"


def test_format_aci_result_success_type():
    from tools.result_formatter import _format_aci_result

    result = _format_aci_result("type", True, {"typed": "hello world"})
    assert result == "Typed into field: 'hello world'"


def test_format_aci_result_success_scroll():
    from tools.result_formatter import _format_aci_result

    result = _format_aci_result("scroll", True, {"scrolled": {"x": 0, "y": 300}})
    assert "300" in result
    assert "Scrolled" in result


def test_format_aci_result_empty_extract_content():
    from tools.result_formatter import _format_aci_result

    result = _format_aci_result("extract_content", True, {"text": ""})
    assert "returned empty" in result
    assert "get_structured_dom" in result


def test_format_aci_result_failure_click_includes_recovery_hint():
    from tools.result_formatter import _format_aci_result

    result = _format_aci_result("click", False, None, "Element not found: #btn")
    assert "TOOL FAILED [click]" in result
    assert "Recovery hint:" in result
    assert "Element not found: #btn" in result


def test_format_aci_result_dom_zero_elements():
    from tools.result_formatter import _format_aci_result

    result = _format_aci_result(
        "get_structured_dom", True, {"elements": [], "url": "https://x.com", "title": "X"}
    )
    assert "no interactable elements found" in result


def test_format_aci_result_dom_with_elements_formats_list():
    from tools.result_formatter import _format_aci_result

    elements = [
        {"idx": 0, "tag": "input", "selector": "#search", "text": "Search", "ariaLabel": None, "placeholder": None},
        {"idx": 1, "tag": "button", "selector": "#submit", "text": "Submit", "ariaLabel": None, "placeholder": None},
    ]
    result = _format_aci_result(
        "get_structured_dom",
        True,
        {"elements": elements, "url": "https://x.com", "title": "X"},
    )
    assert "[0] #search" in result
    assert "[1] #submit" in result
    assert "Found 2 interactable elements" in result


def test_format_aci_result_screenshot_with_marks():
    from tools.result_formatter import _format_aci_result

    marks = {
        "1": {"selector": "#btn", "tag": "button"},
        "2": {"selector": "a.link", "tag": "a"},
    }
    result = _format_aci_result("screenshot", True, {"screenshot": "data:...", "marks": marks})
    assert "2 interactive element marks" in result
    assert "click_by_mark_id" in result


def test_format_aci_result_screenshot_without_marks():
    from tools.result_formatter import _format_aci_result

    result = _format_aci_result("screenshot", True, {"screenshot": "data:img/jpeg;base64,abc123", "marks": {}})
    assert "no marks" in result
    assert "Screenshot captured" in result


@pytest.mark.asyncio
async def test_navigate_returns_string_on_success():
    """navigate should return ACI-formatted string on success."""
    from tools.browser_tools import navigate

    mock_client = MagicMock()
    mock_client.invoke = AsyncMock(return_value={"url": "https://example.com", "navigated": True})

    with patch("tools.gateway_client._gateway_client", mock_client):
        result = await navigate.ainvoke(
            {"session_id": "sess-001", "url": "https://example.com"}
        )

    assert isinstance(result, str)
    assert "Navigated to" in result
    assert "https://example.com" in result


@pytest.mark.asyncio
async def test_click_returns_string_on_success():
    """click should return ACI-formatted string on success."""
    from tools.browser_tools import click

    mock_client = MagicMock()
    mock_client.invoke = AsyncMock(return_value={"clicked": "#search-btn"})

    with patch("tools.gateway_client._gateway_client", mock_client):
        result = await click.ainvoke(
            {"session_id": "sess-001", "selector": "#search-btn"}
        )

    assert isinstance(result, str)
    assert "Clicked element" in result
    assert "#search-btn" in result


@pytest.mark.asyncio
async def test_screenshot_returns_string_with_marks():
    """screenshot should return ACI-formatted string describing marks."""
    from tools.browser_tools import screenshot

    mock_client = MagicMock()
    mock_client.invoke = AsyncMock(
        return_value={
            "screenshot": "data:image/jpeg;base64,abc",
            "marks": {
                "1": {"selector": "#btn", "tag": "button"},
                "2": {"selector": "a.nav", "tag": "a"},
            },
        }
    )

    with patch("tools.gateway_client._gateway_client", mock_client):
        result = await screenshot.ainvoke({"session_id": "sess-001"})

    assert isinstance(result, str)
    assert "2 interactive element marks" in result
    assert "click_by_mark_id" in result


# ---------------------------------------------------------------------------
# P1: click_by_mark_id tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_click_by_mark_id_calls_gateway_with_correct_params():
    """click_by_mark_id should invoke 'click_by_mark_id' with mark_id param."""
    from tools.browser_tools import click_by_mark_id

    mock_client = MagicMock()
    mock_client.invoke = AsyncMock(
        return_value={"mark_id": 3, "clicked_selector": "#submit-btn"}
    )

    with patch("tools.gateway_client._gateway_client", mock_client):
        result = await click_by_mark_id.ainvoke(
            {"session_id": "sess-001", "mark_id": 3}
        )

    mock_client.invoke.assert_awaited_once_with(
        "sess-001", "click_by_mark_id", {"mark_id": 3}
    )
    assert isinstance(result, str)
    assert "Clicked mark 3" in result
    assert "#submit-btn" in result


@pytest.mark.asyncio
async def test_click_by_mark_id_returns_aci_error_for_empty_session_id():
    """click_by_mark_id should return TOOL FAILED string for empty session_id."""
    from tools.browser_tools import click_by_mark_id

    result = await click_by_mark_id.ainvoke({"session_id": "", "mark_id": 1})
    assert isinstance(result, str)
    assert "TOOL FAILED" in result


# ---------------------------------------------------------------------------
# P2-3: Context compression
# ---------------------------------------------------------------------------


def _make_tool_message(content: str, tool_call_id: str = "call-001") -> ToolMessage:
    return ToolMessage(content=content, tool_call_id=tool_call_id)


def test_compress_messages_keeps_short_history_intact():
    """Messages <= 6 should not be compressed."""
    from graph.utils import _compress_messages

    messages = [
        HumanMessage(content="Navigate to YouTube"),
        AIMessage(content="Navigating..."),
        _make_tool_message('{"url": "https://youtube.com"}'),
    ]
    result = _compress_messages(messages)
    assert len(result) == len(messages)


def test_compress_messages_keeps_first_and_last_4():
    """With >6 messages: first + compressed older + last 4."""
    from graph.utils import _compress_messages

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
    from graph.utils import _compress_messages

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


def test_compress_messages_removes_multimodal_content_from_old_tool_messages():
    """Old ToolMessages with list (multimodal) content should have it stripped."""
    from graph.utils import _compress_messages

    multimodal_content = [
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,abc"}},
        {"type": "text", "text": "Screenshot captured with 5 marks."},
    ]

    messages = [
        HumanMessage(content="User request"),  # first
        AIMessage(content="Taking screenshot..."),
        ToolMessage(content=multimodal_content, tool_call_id="call-screenshot"),  # should be compressed
        AIMessage(content="Another step"),
        _make_tool_message("page info", "call-info"),
        AIMessage(content="More steps"),  # recent start
        _make_tool_message("recent result", "call-recent"),
        AIMessage(content="Almost done"),
        _make_tool_message("final result", "call-final"),
    ]

    result = _compress_messages(messages)

    compressed_screenshot = None
    for msg in result:
        if isinstance(msg, ToolMessage) and msg.tool_call_id == "call-screenshot":
            compressed_screenshot = msg
            break

    assert compressed_screenshot is not None
    assert isinstance(compressed_screenshot.content, str)
    assert "screenshot removed" in compressed_screenshot.content


def test_compress_messages_preserves_recent_screenshots():
    """Screenshots in the last 4 messages should NOT be stripped."""
    from graph.utils import _compress_messages

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
# P1-1: get_structured_dom tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_structured_dom_calls_gateway():
    """get_structured_dom should invoke 'get_structured_dom' on gateway."""
    from tools.browser_tools import get_structured_dom

    mock_client = MagicMock()
    mock_client.invoke = AsyncMock(
        return_value={
            "url": "https://example.com",
            "title": "Example",
            "interactable_count": 0,
            "elements": [],
            "page_text_preview": "Example page",
        }
    )

    with patch("tools.gateway_client._gateway_client", mock_client):
        result = await get_structured_dom.ainvoke(
            {"session_id": "sess-001"}
        )

    mock_client.invoke.assert_awaited_once_with("sess-001", "get_structured_dom", {})
    # Returns ACI-formatted string now
    assert isinstance(result, str)
    assert "no interactable elements found" in result


# ---------------------------------------------------------------------------
# P2-1: Planner-Actor-Validator graph structure
# ---------------------------------------------------------------------------


def test_build_browser_graph_compiles_without_error():
    """Graph should compile successfully with planner, actor, tools, progress_check, replan."""
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


def test_route_after_actor_returns_tools_when_tool_calls():
    """When actor produces tool calls, should route to tools."""
    from graph.router import route_after_actor

    tool_call = {
        "name": "navigate",
        "args": {"session_id": "s", "url": "http://x.com"},
        "id": "call-1",
    }
    state = {
        "messages": [AIMessage(content="", tool_calls=[tool_call])],
        "session_id": "sess-001",
        "stall_count": 0,
        "progress_ledger": {},
        "action_history": [],
    }

    result = route_after_actor(state)
    assert result == "tools"


def test_route_after_actor_returns_end_when_no_tool_calls():
    """When actor has no tool calls (final answer), should route to END."""
    from langgraph.graph import END as GRAPH_END

    from graph.router import route_after_actor

    state = {
        "messages": [AIMessage(content="Task completed!")],
        "session_id": "sess-001",
        "stall_count": 0,
        "progress_ledger": {},
        "action_history": [],
    }

    result = route_after_actor(state)
    assert result == GRAPH_END


# ---------------------------------------------------------------------------
# P2-1: Node functions (unit tests with mocked LLM)
# ---------------------------------------------------------------------------



@pytest.mark.asyncio
async def test_actor_node_injects_system_prompt_with_session_id():
    """Actor node should use SYSTEM_PROMPT and inject session_id."""
    from graph.nodes import actor_node

    mock_llm = MagicMock()
    mock_response = AIMessage(content="Navigating to YouTube...")
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)

    state = {
        "messages": [HumanMessage(content="Navigate to YouTube")],
        "session_id": "sess-001",
        "stall_count": 0,
        "progress_ledger": {},
        "action_history": [],
    }

    result = await actor_node(state, llm_with_tools=mock_llm)

    call_args = mock_llm.ainvoke.call_args[0][0]
    assert isinstance(call_args[0], SystemMessage)
    assert "sess-001" in call_args[0].content
    assert len(result["messages"]) == 1
