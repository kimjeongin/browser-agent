"""Redis client singleton and key namespace helpers."""

from shared.redis.client import get_redis, close_redis
from shared.redis.keys import (
    browser_cmd_channel,
    browser_result_channel,
    session_key,
    browser_result_cache_key,
)

__all__ = [
    "get_redis",
    "close_redis",
    "browser_cmd_channel",
    "browser_result_channel",
    "session_key",
    "browser_result_cache_key",
]
