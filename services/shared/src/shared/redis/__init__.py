"""Redis client singleton and key namespace helpers."""

from shared.redis.client import get_redis, close_redis
from shared.redis.keys import session_key

__all__ = [
    "get_redis",
    "close_redis",
    "session_key",
]
