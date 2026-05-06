"""
Shared Redis-client factory for the caching subsystem.

`make_redis_client(url)` produces a connected `redis.Redis`, or returns
`None` when no URL is configured. Connection errors propagate to callers
so they can decide whether to fall back to in-process caching.
"""

import os
from typing import Optional

import redis


def make_redis_client(url: Optional[str] = None,
                      *,
                      socket_timeout: float = 5.0) -> Optional["redis.Redis"]:
    """Build a redis client and validate connectivity with PING.

    Returns None when neither `url` nor `CACHE_REDIS_URL` is set.
    Raises `redis.RedisError` on unreachable / misconfigured server.
    """
    url = url or os.environ.get('CACHE_REDIS_URL')
    if not url:
        return None
    client = redis.Redis.from_url(url, socket_timeout=socket_timeout)
    client.ping()
    return client
