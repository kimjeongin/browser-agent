"""Tests for Orchestrator service.

Covered:
- _extract_session_id / _extract_user_text helpers
- tools/passthrough queue registry
- browser_agent tool wrapper (ACP pass-through)
- chat_agent tool wrapper (ACP pass-through)
- supervisor_node (LLM call)
- build_orchestrator_graph (graph structure)
- /health endpoint
"""

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from acp_sdk.models import Message, MessagePart, MessagePartEvent
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from main import _extract_session_id, _extract_user_text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_acp_event(content_type: str, content: str) -> MessagePartEvent:
    """Create a real MessagePartEvent so isinstance() checks pass in tools."""
    return MessagePartEvent(part=MessagePart(content=content, content_type=content_type))


async def _fake_run_stream(*events):
    """Async generator that yields pre-built ACP events."""
    for e in events:
        yield e


async def _invoke_tool(tool_obj, **kwargs):
    """Call the underlying coroutine of a LangChain StructuredTool directly,
    bypassing ainvoke/_arun infrastructure which requires extra LangChain args."""
    coro_fn = tool_obj.__dict__["coroutine"]
    return await coro_fn(**kwargs)


# ---------------------------------------------------------------------------
# Message helpers
# ---------------------------------------------------------------------------


class TestExtractHelpers:
    """Test ACP message extraction helpers."""

    def test_extract_session_id(self):
        msgs = [
            Message(
                role="user",
                parts=[
                    MessagePart(content="sess-123", content_type="text/x-session-id"),
                    MessagePart(content="hello", content_type="text/plain"),
                ],
            )
        ]
        assert _extract_session_id(msgs) == "sess-123"

    def test_extract_session_id_missing_returns_default(self):
        msgs = [
            Message(
                role="user",
                parts=[MessagePart(content="hello", content_type="text/plain")],
            )
        ]
        assert _extract_session_id(msgs) == "default"

    def test_extract_user_text(self):
        msgs = [
            Message(
                role="user",
                parts=[
                    MessagePart(content="sess-123", content_type="text/x-session-id"),
                    MessagePart(content="What is Python?", content_type="text/plain"),
                ],
            )
        ]
        assert _extract_user_text(msgs) == "What is Python?"

    def test_extract_user_text_multiple_parts(self):
        msgs = [
            Message(
                role="user",
                parts=[
                    MessagePart(content="Hello", content_type="text/plain"),
                    MessagePart(content="World", content_type="text/plain"),
                ],
            )
        ]
        assert _extract_user_text(msgs) == "Hello\nWorld"

    def test_extract_user_text_empty_when_no_plain(self):
        msgs = [
            Message(
                role="user",
                parts=[MessagePart(content="sess-123", content_type="text/x-session-id")],
            )
        ]
        assert _extract_user_text(msgs) == ""


# ---------------------------------------------------------------------------
# Passthrough queue registry
# ---------------------------------------------------------------------------


class TestPassthrough:
    """Test the session-scoped queue registry."""

    def setup_method(self):
        from tools import passthrough
        passthrough._queues.clear()
        passthrough._text_streamed.clear()

    async def test_register_and_get(self):
        from tools import passthrough
        q = asyncio.Queue()
        passthrough.register("s1", q)
        assert passthrough.get("s1") is q

    async def test_get_unknown_returns_none(self):
        from tools import passthrough
        assert passthrough.get("no-such-session") is None

    async def test_unregister_removes_entry(self):
        from tools import passthrough
        q = asyncio.Queue()
        passthrough.register("s2", q)
        passthrough.unregister("s2")
        assert passthrough.get("s2") is None

    async def test_unregister_nonexistent_is_noop(self):
        from tools import passthrough
        passthrough.unregister("ghost")  # must not raise

    async def test_mark_and_check_text_streamed(self):
        from tools import passthrough
        assert not passthrough.was_text_streamed("s-stream")
        passthrough.mark_text_streamed("s-stream")
        assert passthrough.was_text_streamed("s-stream")

    async def test_unregister_clears_text_streamed_flag(self):
        from tools import passthrough
        passthrough.register("s-clear", asyncio.Queue())
        passthrough.mark_text_streamed("s-clear")
        passthrough.unregister("s-clear")
        assert not passthrough.was_text_streamed("s-clear")


# ---------------------------------------------------------------------------
# Browser agent tool
# ---------------------------------------------------------------------------


class TestBrowserAgentTool:
    """Test the browser_agent tool's underlying coroutine logic."""

    def setup_method(self):
        from tools import passthrough
        passthrough._queues.clear()

    def _get_module(self):
        import tools.browser_agent  # noqa: F401 – ensure it's in sys.modules
        return sys.modules["tools.browser_agent"]

    async def test_returns_collected_text(self):
        ba = self._get_module()
        mock_client = MagicMock()
        mock_client.run_stream = lambda **_kw: _fake_run_stream(
            _make_acp_event("text/plain", "Page content here"),
        )
        ba._browser_client = mock_client

        result = await _invoke_tool(ba.browser_agent, task="extract page", session_id="s-browser")
        assert "Page content here" in result

    async def test_pushes_tool_events_to_passthrough_queue(self):
        from tools import passthrough
        ba = self._get_module()

        q: asyncio.Queue = asyncio.Queue()
        passthrough.register("s-browser2", q)

        mock_client = MagicMock()
        mock_client.run_stream = lambda **_kw: _fake_run_stream(
            _make_acp_event("application/x-tool-event", '{"type":"tool_start","name":"navigate"}'),
            _make_acp_event("text/plain", "Navigated"),
        )
        ba._browser_client = mock_client

        await _invoke_tool(ba.browser_agent, task="navigate to google", session_id="s-browser2")

        assert not q.empty()
        part = q.get_nowait()
        assert part.content_type == "application/x-tool-event"

    async def test_returns_fallback_when_no_text(self):
        ba = self._get_module()
        mock_client = MagicMock()
        mock_client.run_stream = lambda **_kw: _fake_run_stream(
            _make_acp_event("application/x-tool-event", '{"type":"tool_start","name":"click"}'),
        )
        ba._browser_client = mock_client

        result = await _invoke_tool(ba.browser_agent, task="click button", session_id="s-notext")
        assert result == "(no output)"

    async def test_does_not_push_text_to_passthrough(self):
        """Browser tool text is collected as result, NOT pushed to passthrough queue."""
        from tools import passthrough
        ba = self._get_module()

        q: asyncio.Queue = asyncio.Queue()
        passthrough.register("s-textcheck", q)

        mock_client = MagicMock()
        mock_client.run_stream = lambda **_kw: _fake_run_stream(
            _make_acp_event("text/plain", "some page data"),
        )
        ba._browser_client = mock_client

        await _invoke_tool(ba.browser_agent, task="extract", session_id="s-textcheck")

        # text/plain should NOT go into passthrough (it's the tool's return value)
        assert q.empty()

    async def test_concatenates_multiple_text_parts(self):
        ba = self._get_module()
        mock_client = MagicMock()
        mock_client.run_stream = lambda **_kw: _fake_run_stream(
            _make_acp_event("text/plain", "Part A "),
            _make_acp_event("text/plain", "Part B"),
        )
        ba._browser_client = mock_client

        result = await _invoke_tool(ba.browser_agent, task="extract", session_id="s-concat")
        assert result == "Part A Part B"


# ---------------------------------------------------------------------------
# Chat agent tool
# ---------------------------------------------------------------------------


class TestChatAgentTool:
    """Test the chat_agent tool's underlying coroutine logic."""

    def setup_method(self):
        from tools import passthrough
        passthrough._queues.clear()
        passthrough._text_streamed.clear()

    def _get_module(self):
        import tools.chat_agent  # noqa: F401
        return sys.modules["tools.chat_agent"]

    async def test_returns_collected_text(self):
        ca = self._get_module()
        mock_client = MagicMock()
        mock_client.run_stream = lambda **_kw: _fake_run_stream(
            _make_acp_event("text/plain", "Hello, "),
            _make_acp_event("text/plain", "world!"),
        )
        ca._chat_client = mock_client

        result = await _invoke_tool(ca.chat_agent, task="say hello", session_id="s-chat")
        assert result == "Hello, world!"

    async def test_pushes_text_tokens_to_passthrough_queue(self):
        from tools import passthrough
        ca = self._get_module()

        q: asyncio.Queue = asyncio.Queue()
        passthrough.register("s-chat2", q)

        mock_client = MagicMock()
        mock_client.run_stream = lambda **_kw: _fake_run_stream(
            _make_acp_event("text/plain", "Summary: "),
            _make_acp_event("text/plain", "This page is about..."),
        )
        ca._chat_client = mock_client

        await _invoke_tool(ca.chat_agent, task="summarize", session_id="s-chat2")

        parts = []
        while not q.empty():
            parts.append(q.get_nowait())

        assert len(parts) == 2
        assert all(p.content_type == "text/plain" for p in parts)
        assert parts[0].content == "Summary: "
        # Streaming text must set the flag so main.py suppresses supervisor relay
        assert passthrough.was_text_streamed("s-chat2")

    async def test_returns_fallback_when_no_text(self):
        ca = self._get_module()
        mock_client = MagicMock()
        mock_client.run_stream = lambda **_kw: _fake_run_stream()
        ca._chat_client = mock_client

        result = await _invoke_tool(ca.chat_agent, task="empty", session_id="s-empty")
        assert result == "(no output)"

    async def test_no_queue_does_not_crash(self):
        """chat_agent tool works even when no passthrough queue is registered."""
        ca = self._get_module()
        mock_client = MagicMock()
        mock_client.run_stream = lambda **_kw: _fake_run_stream(
            _make_acp_event("text/plain", "response"),
        )
        ca._chat_client = mock_client

        result = await _invoke_tool(ca.chat_agent, task="q", session_id="no-queue-session")
        assert result == "response"


# ---------------------------------------------------------------------------
# Supervisor node
# ---------------------------------------------------------------------------


class TestSupervisorNode:
    """Test the supervisor_node graph node."""

    async def test_invokes_llm_with_system_and_history(self):
        from graph.nodes import init_supervisor_llm, supervisor_node
        from graph.state import OrchestratorState

        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=AIMessage(content="Answer"))
        init_supervisor_llm(mock_llm)

        state: OrchestratorState = {
            "messages": [HumanMessage(content="What is 2+2?")],
            "session_id": "s-node",
        }
        result = await supervisor_node(state)

        assert "messages" in result
        assert isinstance(result["messages"][0], AIMessage)
        call_args = mock_llm.ainvoke.call_args[0][0]
        assert isinstance(call_args[0], SystemMessage)
        assert "s-node" in call_args[0].content

    async def test_session_id_injected_into_system_prompt(self):
        from graph.nodes import init_supervisor_llm, supervisor_node
        from graph.state import OrchestratorState

        captured: list = []

        async def capture_invoke(msgs):
            captured.extend(msgs)
            return AIMessage(content="ok")

        mock_llm = MagicMock()
        mock_llm.ainvoke = capture_invoke
        init_supervisor_llm(mock_llm)

        state: OrchestratorState = {
            "messages": [HumanMessage(content="test")],
            "session_id": "unique-session-xyz",
        }
        await supervisor_node(state)

        assert "unique-session-xyz" in captured[0].content


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------


class TestBuildOrchestratorGraph:
    """Test graph compilation."""

    def test_graph_compiles_without_error(self):
        from langchain_core.tools import tool
        from graph.builder import build_orchestrator_graph

        @tool
        def dummy_tool(x: str) -> str:
            """A dummy tool."""
            return x

        mock_llm = MagicMock()
        mock_llm.bind_tools = MagicMock(return_value=mock_llm)

        graph = build_orchestrator_graph(mock_llm, [dummy_tool])
        assert graph is not None

    def test_graph_has_supervisor_and_tools_nodes(self):
        from langchain_core.tools import tool
        from graph.builder import build_orchestrator_graph

        @tool
        def dummy_tool(x: str) -> str:
            """A dummy tool."""
            return x

        mock_llm = MagicMock()
        mock_llm.bind_tools = MagicMock(return_value=mock_llm)

        graph = build_orchestrator_graph(mock_llm, [dummy_tool])
        node_names = set(graph.get_graph().nodes.keys())
        assert "supervisor" in node_names
        assert "tools" in node_names


# ---------------------------------------------------------------------------
# /health endpoint
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    """Test the health endpoint."""

    async def test_health_returns_response(self, orchestrator_client):
        response = await orchestrator_client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] in ("ok", "degraded")
        assert body["service"] == "orchestrator"
