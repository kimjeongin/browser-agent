"""Utility functions for the Browser Agent graph."""

from __future__ import annotations

from langchain_core.messages import BaseMessage, ToolMessage


def _compress_messages(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Compress message history to prevent context overflow.

    Strategy (browser-use inspired):
    - Always keep the first message (initial user request)
    - Always keep the last 4 messages (recent context)
    - For older ToolMessages: remove base64 image data to save tokens
    - Keeps conversation coherent while minimizing token usage
    """
    if len(messages) <= 6:
        return messages

    # Keep first (user request) and last 4 messages intact
    first = messages[:1]
    recent = messages[-4:]
    older = messages[1:-4]

    compressed_older: list[BaseMessage] = []
    for msg in older:
        if isinstance(msg, ToolMessage):
            content = msg.content
            # Remove base64 image data (screenshots are very token-heavy).
            # Handles both plain string content and multimodal list content
            # (produced by _enrich_screenshot_messages in nodes.py).
            if isinstance(content, list):
                content = (
                    "[screenshot removed - use get_structured_dom "
                    "for page structure]"
                )
            elif isinstance(content, str) and "data:image" in content:
                content = (
                    "[screenshot removed - use get_structured_dom "
                    "for page structure]"
                )
            # Artifact is intentionally dropped here to free memory.
            compressed_older.append(
                ToolMessage(content=content, tool_call_id=msg.tool_call_id)
            )
        else:
            compressed_older.append(msg)

    return first + compressed_older + recent
