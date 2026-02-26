"""Tests for Orchestrator supervisor graph components.

Covered:
- _parse_agent_from_response  (JSON parsing with fallback)
- _serialize_messages          (LangChain message serialization)
- route_from_supervisor        (conditional routing)
- supervisor_node              (LLM classification)
- call_chat_agent              (ACP call with session_id as thread_id)
- call_browser_agent           (ACP call with session_id in input and thread_id)
- /health endpoint
"""

from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from main import (
    _parse_agent_from_response,
    _serialize_messages,
    call_browser_agent,
    call_chat_agent,
    route_from_supervisor,
    supervisor_node,
)


# ---------------------------------------------------------------------------
# _parse_agent_from_response
# ---------------------------------------------------------------------------


class TestParseAgentFromResponse:
    """Test JSON extraction from LLM classification output."""

    def test_valid_json_chat_agent(self):
        assert _parse_agent_from_response('{"agent": "chat_agent"}') == "chat_agent"

    def test_valid_json_browser_agent(self):
        assert _parse_agent_from_response('{"agent": "browser_agent"}') == "browser_agent"

    def test_json_embedded_in_text(self):
        text = 'Based on the input, I classify this as {"agent": "browser_agent"} for the user.'
        assert _parse_agent_from_response(text) == "browser_agent"

    def test_invalid_json_falls_back_to_chat(self):
        assert _parse_agent_from_response("this is not json at all") == "chat_agent"

    def test_invalid_agent_value_falls_back_to_chat(self):
        assert _parse_agent_from_response('{"agent": "unknown_agent"}') == "chat_agent"

    def test_malformed_json_falls_back_to_keyword(self):
        assert _parse_agent_from_response("{broken json") == "chat_agent"

    def test_fallback_keyword_browser_agent(self):
        assert _parse_agent_from_response("I think browser_agent should handle this") == "browser_agent"

    def test_empty_string_returns_chat(self):
        assert _parse_agent_from_response("") == "chat_agent"

    def test_json_without_agent_key_returns_chat(self):
        assert _parse_agent_from_response('{"action": "browse"}') == "chat_agent"


# ---------------------------------------------------------------------------
# _serialize_messages
# ---------------------------------------------------------------------------


class TestSerializeMessages:
    """Test LangChain message conversion to role/content dicts."""

    def test_human_message(self):
        msgs = [HumanMessage(content="Hello")]
        result = _serialize_messages(msgs)
        assert result == [{"role": "human", "content": "Hello"}]

    def test_ai_message(self):
        msgs = [AIMessage(content="Hi there")]
        result = _serialize_messages(msgs)
        assert result == [{"role": "ai", "content": "Hi there"}]

    def test_system_message(self):
        msgs = [SystemMessage(content="You are helpful")]
        result = _serialize_messages(msgs)
        assert result == [{"role": "system", "content": "You are helpful"}]

    def test_mixed_messages_preserve_order(self):
        msgs = [
            SystemMessage(content="system prompt"),
            HumanMessage(content="user input"),
            AIMessage(content="assistant reply"),
        ]
        result = _serialize_messages(msgs)
        assert len(result) == 3
        assert result[0]["role"] == "system"
        assert result[1]["role"] == "human"
        assert result[2]["role"] == "ai"

    def test_empty_list(self):
        assert _serialize_messages([]) == []


# ---------------------------------------------------------------------------
# route_from_supervisor
# ---------------------------------------------------------------------------


class TestRouteFromSupervisor:
    """Test conditional edge routing."""

    def test_routes_to_browser_agent(self):
        state = {"messages": [], "next_agent": "browser_agent", "session_id": None}
        assert route_from_supervisor(state) == "browser_agent"

    def test_routes_to_chat_agent_by_default(self):
        state = {"messages": [], "next_agent": "chat_agent", "session_id": None}
        assert route_from_supervisor(state) == "chat_agent"

    def test_routes_to_chat_when_none(self):
        state = {"messages": [], "next_agent": None, "session_id": None}
        assert route_from_supervisor(state) == "chat_agent"

    def test_routes_to_chat_when_missing(self):
        state = {"messages": []}
        assert route_from_supervisor(state) == "chat_agent"


# ---------------------------------------------------------------------------
# supervisor_node
# ---------------------------------------------------------------------------


class TestSupervisorNode:
    """Test the LLM classification node."""

    async def test_classifies_as_chat_agent(self, mock_llm):
        mock_llm.ainvoke = AsyncMock(
            return_value=AIMessage(content='{"agent": "chat_agent"}'),
        )
        state = {
            "messages": [HumanMessage(content="What is Python?")],
            "next_agent": None,
            "session_id": "sess-1",
        }
        result = await supervisor_node(state, llm=mock_llm)
        assert result["next_agent"] == "chat_agent"
        mock_llm.ainvoke.assert_awaited_once()

    async def test_classifies_as_browser_agent(self, mock_llm):
        mock_llm.ainvoke = AsyncMock(
            return_value=AIMessage(content='{"agent": "browser_agent"}'),
        )
        state = {
            "messages": [HumanMessage(content="Click the submit button")],
            "next_agent": None,
            "session_id": "sess-2",
        }
        result = await supervisor_node(state, llm=mock_llm)
        assert result["next_agent"] == "browser_agent"

    async def test_returns_chat_when_no_human_message(self, mock_llm):
        state = {
            "messages": [AIMessage(content="previous response")],
            "next_agent": None,
            "session_id": "sess-3",
        }
        result = await supervisor_node(state, llm=mock_llm)
        assert result["next_agent"] == "chat_agent"
        # LLM should not be called when there is no human message
        mock_llm.ainvoke.assert_not_awaited()

    async def test_uses_last_human_message(self, mock_llm):
        """When multiple HumanMessages exist, the last one is used."""
        mock_llm.ainvoke = AsyncMock(
            return_value=AIMessage(content='{"agent": "chat_agent"}'),
        )
        state = {
            "messages": [
                HumanMessage(content="First question"),
                AIMessage(content="First answer"),
                HumanMessage(content="Second question"),
            ],
            "next_agent": None,
            "session_id": "sess-4",
        }
        await supervisor_node(state, llm=mock_llm)

        # Verify the classification prompt contains the last human message
        call_args = mock_llm.ainvoke.call_args[0][0]
        human_in_classification = call_args[1].content
        assert human_in_classification == "Second question"


# ---------------------------------------------------------------------------
# call_chat_agent
# ---------------------------------------------------------------------------


class TestCallChatAgent:
    """Test ACP call to the Chat Agent."""

    async def test_forwards_messages_and_returns_response(self, mock_chat_client):
        state = {
            "messages": [HumanMessage(content="Hello")],
            "next_agent": "chat_agent",
            "session_id": "sess-chat-1",
        }
        result = await call_chat_agent(state, chat_client=mock_chat_client)

        assert len(result["messages"]) == 1
        assert isinstance(result["messages"][0], AIMessage)
        assert result["messages"][0].content == "Hello from chat agent!"
        assert result["next_agent"] is None

    async def test_uses_session_id_as_thread_id(self, mock_chat_client):
        state = {
            "messages": [HumanMessage(content="Hi")],
            "next_agent": "chat_agent",
            "session_id": "my-session-123",
        }
        await call_chat_agent(state, chat_client=mock_chat_client)

        mock_chat_client.run.assert_awaited_once()
        call_kwargs = mock_chat_client.run.call_args
        assert call_kwargs.kwargs.get("thread_id") or call_kwargs[1].get("thread_id") or call_kwargs[0][0] == "my-session-123"

    async def test_uses_default_thread_id_when_no_session(self, mock_chat_client):
        state = {
            "messages": [HumanMessage(content="Hi")],
            "next_agent": "chat_agent",
            "session_id": None,
        }
        await call_chat_agent(state, chat_client=mock_chat_client)

        call_args = mock_chat_client.run.call_args
        thread_id = call_args[1].get("thread_id", call_args[0][0] if call_args[0] else None)
        assert thread_id == "default"

    async def test_handles_acp_failure_gracefully(self, mock_chat_client):
        mock_chat_client.run = AsyncMock(side_effect=Exception("Connection refused"))

        state = {
            "messages": [HumanMessage(content="Hi")],
            "next_agent": "chat_agent",
            "session_id": "sess-fail",
        }
        result = await call_chat_agent(state, chat_client=mock_chat_client)

        assert len(result["messages"]) == 1
        assert "unable to process" in result["messages"][0].content.lower()
        assert result["next_agent"] is None


# ---------------------------------------------------------------------------
# call_browser_agent
# ---------------------------------------------------------------------------


class TestCallBrowserAgent:
    """Test ACP call to the Browser Agent."""

    async def test_forwards_messages_and_returns_response(self, mock_browser_client):
        state = {
            "messages": [HumanMessage(content="Navigate to google.com")],
            "next_agent": "browser_agent",
            "session_id": "sess-browser-1",
        }
        result = await call_browser_agent(state, browser_client=mock_browser_client)

        assert len(result["messages"]) == 1
        assert isinstance(result["messages"][0], AIMessage)
        assert result["messages"][0].content == "Browser action completed."
        assert result["next_agent"] is None

    async def test_uses_session_id_as_thread_id(self, mock_browser_client):
        state = {
            "messages": [HumanMessage(content="Click button")],
            "next_agent": "browser_agent",
            "session_id": "browser-sess-456",
        }
        await call_browser_agent(state, browser_client=mock_browser_client)

        call_args = mock_browser_client.run.call_args
        thread_id = call_args[1].get("thread_id", call_args[0][0] if call_args[0] else None)
        assert thread_id == "browser-sess-456"

    async def test_includes_session_id_in_input(self, mock_browser_client):
        state = {
            "messages": [HumanMessage(content="Scroll down")],
            "next_agent": "browser_agent",
            "session_id": "browser-sess-789",
        }
        await call_browser_agent(state, browser_client=mock_browser_client)

        call_args = mock_browser_client.run.call_args
        input_payload = call_args[1].get("input", call_args[0][1] if len(call_args[0]) > 1 else None)
        assert input_payload["session_id"] == "browser-sess-789"

    async def test_handles_acp_failure_gracefully(self, mock_browser_client):
        mock_browser_client.run = AsyncMock(side_effect=Exception("Timeout"))

        state = {
            "messages": [HumanMessage(content="Click")],
            "next_agent": "browser_agent",
            "session_id": "sess-fail",
        }
        result = await call_browser_agent(state, browser_client=mock_browser_client)

        assert len(result["messages"]) == 1
        assert "could not be completed" in result["messages"][0].content.lower()
        assert result["next_agent"] is None


# ---------------------------------------------------------------------------
# /health endpoint
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    """Test the ACP health endpoint."""

    async def test_health_returns_ok(self, orchestrator_client):
        response = await orchestrator_client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
