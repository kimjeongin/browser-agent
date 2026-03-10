"""Tests for Orchestrator service.

Covered:
- parse_agent_from_response  (JSON parsing with fallback)
- _classify_intent           (LLM classification helper)
- _extract_session_id / _extract_user_text (ACP message helpers)
- /health endpoint
"""

import pytest
from langchain_core.messages import AIMessage
from unittest.mock import AsyncMock

from acp_sdk.models import Message, MessagePart, MessagePartEvent

from classifier import parse_agent_from_response
import main as orchestrator_main
from main import _classify_intent, _extract_session_id, _extract_user_text


# ---------------------------------------------------------------------------
# parse_agent_from_response (unchanged from classifier.py)
# ---------------------------------------------------------------------------


class TestParseAgentFromResponse:
    """Test JSON extraction from LLM classification output."""

    def test_valid_json_chat_agent(self):
        assert parse_agent_from_response('{"agent": "chat_agent"}') == "chat_agent"

    def test_valid_json_browser_agent(self):
        assert parse_agent_from_response('{"agent": "browser_agent"}') == "browser_agent"

    def test_json_embedded_in_text(self):
        text = 'Based on the input, I classify this as {"agent": "browser_agent"} for the user.'
        assert parse_agent_from_response(text) == "browser_agent"

    def test_invalid_json_falls_back_to_chat(self):
        assert parse_agent_from_response("this is not json at all") == "chat_agent"

    def test_invalid_agent_value_falls_back_to_chat(self):
        assert parse_agent_from_response('{"agent": "unknown_agent"}') == "chat_agent"

    def test_malformed_json_falls_back_to_keyword(self):
        assert parse_agent_from_response("{broken json") == "chat_agent"

    def test_fallback_keyword_browser_agent(self):
        assert parse_agent_from_response("I think browser_agent should handle this") == "browser_agent"

    def test_empty_string_returns_chat(self):
        assert parse_agent_from_response("") == "chat_agent"

    def test_json_without_agent_key_returns_chat(self):
        assert parse_agent_from_response('{"action": "browse"}') == "chat_agent"


# ---------------------------------------------------------------------------
# Message helpers
# ---------------------------------------------------------------------------


class TestExtractHelpers:
    """Test ACP message extraction helpers."""

    def test_extract_session_id(self):
        msgs = [Message(role="user", parts=[
            MessagePart(content="sess-123", content_type="text/x-session-id"),
            MessagePart(content="hello", content_type="text/plain"),
        ])]
        assert _extract_session_id(msgs) == "sess-123"

    def test_extract_session_id_missing_returns_default(self):
        msgs = [Message(role="user", parts=[
            MessagePart(content="hello", content_type="text/plain"),
        ])]
        assert _extract_session_id(msgs) == "default"

    def test_extract_user_text(self):
        msgs = [Message(role="user", parts=[
            MessagePart(content="sess-123", content_type="text/x-session-id"),
            MessagePart(content="What is Python?", content_type="text/plain"),
        ])]
        assert _extract_user_text(msgs) == "What is Python?"

    def test_extract_user_text_multiple_parts(self):
        msgs = [Message(role="user", parts=[
            MessagePart(content="Hello", content_type="text/plain"),
            MessagePart(content="World", content_type="text/plain"),
        ])]
        assert _extract_user_text(msgs) == "Hello\nWorld"

    def test_extract_user_text_empty_when_no_plain(self):
        msgs = [Message(role="user", parts=[
            MessagePart(content="sess-123", content_type="text/x-session-id"),
        ])]
        assert _extract_user_text(msgs) == ""


# ---------------------------------------------------------------------------
# _classify_intent
# ---------------------------------------------------------------------------


class TestClassifyIntent:
    """Test LLM-based intent classification."""

    async def test_classifies_as_chat_agent(self, mock_llm):
        mock_llm.ainvoke = AsyncMock(
            return_value=AIMessage(content='{"agent": "chat_agent"}'),
        )
        orchestrator_main._llm = mock_llm
        result = await _classify_intent("What is Python?")
        assert result == "chat_agent"

    async def test_classifies_as_browser_agent(self, mock_llm):
        mock_llm.ainvoke = AsyncMock(
            return_value=AIMessage(content='{"agent": "browser_agent"}'),
        )
        orchestrator_main._llm = mock_llm
        result = await _classify_intent("Click the submit button")
        assert result == "browser_agent"


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
