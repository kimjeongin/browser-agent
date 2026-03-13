"""Browser agent tool -- LangChain tool wrapping the ACP browser_agent."""

import logging

from acp_sdk.client import Client
from acp_sdk.models import Message, MessagePart, MessagePartEvent
from langchain_core.tools import tool

from tools import passthrough

logger = logging.getLogger(__name__)

_browser_client: Client | None = None


def init_browser_client(client: Client) -> None:
    """Inject the ACP client connected to the browser agent service."""
    global _browser_client
    _browser_client = client


@tool
async def browser_agent(task: str, session_id: str) -> str:
    """Perform browser automation: navigate URLs, click elements, fill forms,
    extract page content, take screenshots. Use for any browser/DOM task."""
    q = passthrough.get(session_id)
    collected: list[str] = []

    async for event in _browser_client.run_stream(
        input=[
            Message(
                role="user",
                parts=[
                    MessagePart(content=session_id, content_type="text/x-session-id"),
                    MessagePart(content=task, content_type="text/plain"),
                ],
            )
        ],
        agent="browser_agent",
    ):
        if isinstance(event, MessagePartEvent):
            if event.part.content_type == "application/x-tool-event" and q:
                await q.put(event.part)
            elif event.part.content_type == "text/plain" and event.part.content:
                collected.append(event.part.content)

    return "".join(collected) or "(no output)"
