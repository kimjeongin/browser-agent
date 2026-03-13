"""Orchestrator graph state definition."""

from typing import Annotated

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class OrchestratorState(TypedDict):
    """State for the orchestrator supervisor graph.

    Attributes:
        messages: Accumulated LLM conversation messages.
        session_id: Browser extension session identifier, passed through to sub-agents.
    """

    messages: Annotated[list[BaseMessage], add_messages]
    session_id: str
