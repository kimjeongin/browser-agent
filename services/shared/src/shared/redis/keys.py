"""Redis key namespace constants and builder functions."""


def session_key(session_id: str) -> str:
    """String key storing session metadata (JSON), TTL 24h."""
    return f"session:{session_id}"
