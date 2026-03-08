"""LangGraph state definition for the Browser Agent Progress Ledger."""

from __future__ import annotations

from typing import Annotated, Any

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

MAX_STALL_COUNT = 3  # Trigger replan after this many consecutive stall steps
MAX_REPLAN_COUNT = 3  # Terminate after this many replan attempts


class AgentState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    session_id: str
    stall_count: int           # Consecutive non-progress steps
    replan_count: int          # Number of replan attempts used
    progress_ledger: dict      # Latest output from progress_check_node
    action_history: list[str]  # Recent tool call names (last 5, for loop detection)
    last_screenshot: dict | None       # {screenshot: base64, marks: dict}
    last_navigated_url: str | None     # URL after navigate (used to invalidate screenshot cache)
