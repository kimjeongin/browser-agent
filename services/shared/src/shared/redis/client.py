"""Redis async client singleton."""

import logging

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

_redis_client: aioredis.Redis | None = None


async def get_redis(redis_url: str = "redis://localhost:6379") -> aioredis.Redis:
    """Return the shared Redis client, creating it on first call.

    Designed to be called once during application lifespan startup and
    then reused throughout the application lifecycle.

    Args:
        redis_url: Redis connection URL.

    Returns:
        An ``aioredis.Redis`` instance.
    """
    global _redis_client  # noqa: PLW0603

    if _redis_client is None:
        _redis_client = aioredis.from_url(
            redis_url,
            decode_responses=True,
        )
        logger.info("Redis client created for %s", redis_url)

    return _redis_client


async def close_redis() -> None:
    """Close the shared Redis client connection.

    Should be called during application lifespan shutdown.
    """
    global _redis_client  # noqa: PLW0603

    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None
        logger.info("Redis client closed")
