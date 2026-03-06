"""Tool result formatting and validation helpers for browser tools."""

from __future__ import annotations

import json


def _validate_session_id(session_id: str) -> str | None:
    """Return an error string if session_id is invalid, else None."""
    if not session_id or not session_id.strip():
        return (
            "TOOL FAILED: session_id is missing or empty. "
            "Always include the session_id from the conversation state "
            "in every tool call."
        )
    return None


# Recovery hints per tool -- shown to LLM when a tool fails.
_RECOVERY_HINTS: dict[str, str] = {
    "navigate": "Ensure the URL is valid and starts with http:// or https://.",
    "click": (
        "Try browser_get_structured_dom to find the correct selector, "
        "or take a screenshot with marks and use browser_click_by_mark_id."
    ),
    "type": (
        "Verify the input field is visible with browser_get_structured_dom before typing."
    ),
    "scroll": "Verify the target element or window is scrollable.",
    "screenshot": "Ensure the AI tab is active and a page has loaded.",
    "extract_content": (
        "Try a more specific selector or use browser_get_structured_dom instead."
    ),
    "wait_for_element": (
        "The element may use dynamic rendering. "
        "Try a broader selector or increase timeout_ms."
    ),
    "get_page_info": "Navigate to a URL first so a page is loaded.",
    "get_structured_dom": (
        "The page may still be loading. "
        "Try browser_wait_for_element or navigate first."
    ),
    "click_by_mark_id": (
        "Marks expire when the page changes. "
        "Take a new screenshot to refresh marks."
    ),
}


def _format_aci_result(
    tool_name: str,
    success: bool,
    result: dict | None,
    error: str | None = None,
) -> str:
    """Format a tool result as a human-readable string for the LLM.

    Failure path includes a recovery hint so the LLM can self-correct.
    Success path is tool-specific and highlights actionable information.
    """
    if not success:
        hint = _RECOVERY_HINTS.get(tool_name, "Try a different approach.")
        return f"TOOL FAILED [{tool_name}]: {error}\nRecovery hint: {hint}"

    r = result or {}

    if tool_name == "navigate":
        return f"Navigated to: {r.get('url', 'unknown')}"

    if tool_name == "click":
        return f"Clicked element: {r.get('clicked', r.get('selector', 'unknown'))}"

    if tool_name == "click_by_mark_id":
        return (
            f"Clicked mark {r.get('mark_id', '?')}: "
            f"{r.get('clicked_selector', 'unknown')}"
        )

    if tool_name == "type":
        return f"Typed into field: '{r.get('typed', r.get('text', 'unknown'))}'"

    if tool_name == "scroll":
        scrolled = r.get("scrolled", {})
        return (
            f"Scrolled page: dx={scrolled.get('x', 0)}, dy={scrolled.get('y', 0)}"
        )

    if tool_name == "extract_content":
        text = r.get("text", "")
        if not text or not text.strip():
            return (
                "extract_content returned empty: no text found at this selector. "
                "Try browser_get_structured_dom for page structure."
            )
        preview = text.strip()[:200]
        suffix = "..." if len(text) > 200 else ""
        return f"Extracted content ({len(text)} chars): {preview}{suffix}"

    if tool_name == "wait_for_element":
        found = r.get("found", False)
        selector = r.get("selector", "unknown")
        if not found:
            hint = _RECOVERY_HINTS["wait_for_element"]
            return f"Element NOT found: {selector}\nRecovery hint: {hint}"
        return f"Element found: {selector}"

    if tool_name == "get_page_info":
        return (
            f"Page: {r.get('title', 'unknown')} "
            f"| URL: {r.get('url', 'unknown')} "
            f"| State: {r.get('readyState', 'unknown')}"
        )

    if tool_name == "get_structured_dom":
        elements = r.get("elements", [])
        count = len(elements)
        if count == 0:
            return (
                "get_structured_dom: no interactable elements found. "
                "The page may still be loading or the content is not interactive."
            )
        lines = [f"Page: {r.get('title', '?')} ({r.get('url', '?')})"]
        lines.append(f"Found {count} interactable elements:")
        for el in elements:
            idx = el.get("idx", "?")
            selector = el.get("selector") or el.get("tag", "?")
            text = (
                el.get("text")
                or el.get("ariaLabel")
                or el.get("placeholder")
                or ""
            )
            text_part = f" \u2014 {text[:60]}" if text else ""
            lines.append(f"  [{idx}] {selector}{text_part}")
        return "\n".join(lines)

    if tool_name == "screenshot":
        marks = r.get("marks", {})
        screenshot = r.get("screenshot", "")
        mark_count = len(marks)
        if mark_count > 0:
            mark_list = ", ".join(
                f"[{k}]={v.get('tag', '?')}"
                for k, v in list(marks.items())[:10]
            )
            suffix = "..." if mark_count > 10 else ""
            return (
                f"Screenshot captured with {mark_count} interactive element marks. "
                f"Elements: {mark_list}{suffix}. "
                "Use browser_click_by_mark_id(session_id=..., mark_id=N) "
                "to click an element."
            )
        return f"Screenshot captured (no marks). Image data: {len(screenshot)} chars."

    return f"Tool [{tool_name}] completed: {json.dumps(r)[:200]}"
