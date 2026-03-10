"""Chat agent tool -- LangChain tool wrapping the ACP chat_agent."""

import logging

from acp_sdk.client import Client
from acp_sdk.models import Message, MessagePart, MessagePartEvent
from langchain_core.tools import tool

from tools import passthrough

logger = logging.getLogger(__name__)

_chat_client: Client | None = None


def init_chat_client(client: Client) -> None:
    """Inject the ACP client connected to the chat agent service."""
    global _chat_client
    _chat_client = client


@tool
async def chat_agent(task: str, session_id: str) -> str:
    """Handle Q&A, summarization, translation, coding, or general conversation.
    Pass relevant context (e.g., browser content) in the task description."""
    q = passthrough.get(session_id)
    collected: list[str] = []

    async for event in _chat_client.run_stream(
        input=[
            Message(
                role="user",
                parts=[
                    MessagePart(content=session_id, content_type="text/x-session-id"),
                    MessagePart(content=task, content_type="text/plain"),
                ],
            )
        ],
        agent="chat_agent",
    ):
        if isinstance(event, MessagePartEvent):
            if event.part.content_type == "text/plain" and event.part.content:
                collected.append(event.part.content)
                if q:
                    await q.put(event.part)

    return "".join(collected) or "(no output)"
