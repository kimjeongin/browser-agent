"""Supervisor node for the orchestrator graph."""

import logging

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage

from graph.state import OrchestratorState

logger = logging.getLogger(__name__)

SUPERVISOR_SYSTEM_PROMPT = """\
You are an orchestrator AI that coordinates specialist tools.

Available tools:
- browser_agent: For any task requiring browser/DOM interaction: navigating URLs,
  clicking, form filling, extracting page content, taking screenshots, scrolling.
- chat_agent: For Q&A, summarization, translation, coding help, general conversation.
  When calling after browser work, include the retrieved data in the task description.

For multi-step tasks (e.g., "summarize this page", "translate current article"):
1. Call browser_agent to get the data
2. Call chat_agent with the data + user's question

Always pass the exact session_id provided to you."""

# Module-level LLM instance, set by init_supervisor_llm().
_llm_with_tools: BaseChatModel | None = None


def init_supervisor_llm(llm_with_tools: BaseChatModel) -> None:
    """Inject the tool-bound LLM instance used by the supervisor node."""
    global _llm_with_tools
    _llm_with_tools = llm_with_tools


async def supervisor_node(state: OrchestratorState) -> dict:
    """Invoke the supervisor LLM with system prompt and conversation history."""
    system = SystemMessage(
        content=f"{SUPERVISOR_SYSTEM_PROMPT}\n\nCurrent session_id: {state['session_id']}"
    )
    messages = [system, *state["messages"]]
    response = await _llm_with_tools.ainvoke(messages)
    return {"messages": [response]}
