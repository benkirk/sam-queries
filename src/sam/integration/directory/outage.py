"""A process-local latch: after one failed read, skip the directory for a while.

The sync hook runs on the request thread; a daemon reset is 1,300 PUTs in a burst,
and each would otherwise wait out a timeout while the directory is down.
"""

from __future__ import annotations

import time

BACKOFF_SECONDS = 60.0

_down_until = 0.0


def mark_down(seconds: float = BACKOFF_SECONDS) -> None:
    global _down_until
    _down_until = time.monotonic() + seconds


def is_down() -> bool:
    return time.monotonic() < _down_until


def reset() -> None:
    global _down_until
    _down_until = 0.0
