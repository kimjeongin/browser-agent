"""Redis key namespace constants and builder functions.

All Redis keys used across backend services are defined here to prevent
duplication and ensure consistent naming conventions.
"""


def session_key(session_id: str) -> str:
    """Hash key storing session metadata."""
    return f"session:{session_id}"
