"""Redis key namespace constants and builder functions.

All Redis keys used across backend services are defined here to prevent
duplication and ensure consistent naming conventions.
"""


def browser_cmd_channel(session_id: str) -> str:
    """Pub/Sub channel for sending browser commands to an extension session."""
    return f"browser_cmd:{session_id}"


def browser_result_channel(command_id: str) -> str:
    """Pub/Sub channel for receiving a specific command's result."""
    return f"browser_result:{command_id}"


def session_key(session_id: str) -> str:
    """Hash key storing session metadata."""
    return f"session:{session_id}"


def browser_result_cache_key(command_id: str) -> str:
    """String key caching a command result for idempotent retrieval."""
    return f"browser_result_cache:{command_id}"
