"""Intent classifier for routing to sub-agents."""

import json
import re


CLASSIFICATION_SYSTEM_PROMPT = """\
You are a request classifier for a browser extension AI assistant.
Your job is to decide which agent should handle the user's latest message.

Available agents:
- "browser_agent": Handles tasks that require interacting with a web browser,
  such as clicking, typing, navigating to URLs, scrolling, taking screenshots
  of specific web pages, filling forms, extracting visible page content, or
  any action that manipulates or reads the DOM of a live web page.
  Examples: "유튜브에서 아이유 검색해줘", "이 버튼 클릭해줘", "구글에서 검색해줘"
- "chat_agent": Handles everything else -- general questions, web search
  queries, summarisation, translation, coding help, math, and conversation.

Respond ONLY with a JSON object. No explanation, no markdown.
Example: {"agent": "chat_agent"}
"""


def parse_agent_from_response(text: str) -> str:
    """Extract agent name from the LLM's JSON response, with fallback."""
    json_match = re.search(r"\{.*?\}", text, re.DOTALL)
    if json_match:
        try:
            parsed = json.loads(json_match.group())
            agent = parsed.get("agent", "chat_agent")
            if agent in ("browser_agent", "chat_agent"):
                return agent
        except (json.JSONDecodeError, AttributeError):
            pass

    lower = text.lower()
    if "browser_agent" in lower:
        return "browser_agent"
    return "chat_agent"
