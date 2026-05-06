"""
CacheBase: uniform contract for every cache layer.
"""

import sys
from abc import ABC, abstractmethod
from typing import Any


def approx_bytes(obj: Any, _seen: set = None) -> int:
    """Best-effort byte-size approximation for arbitrary cached values.

    Walks one level into list/tuple/dict/set containers and sums
    `sys.getsizeof` of each leaf. NOT a true deep-size measurement —
    Python's reference counting and shared-string interning mean the
    real footprint can be lower; conversely, deeply nested structures
    will undercount. Treat the result as a useful lower bound for
    operator dashboards, not a precise number.
    """
    if obj is None:
        return 0
    if _seen is None:
        _seen = set()
    obj_id = id(obj)
    if obj_id in _seen:
        return 0
    _seen.add(obj_id)

    size = sys.getsizeof(obj)
    if isinstance(obj, (str, bytes, bytearray, int, float, bool)):
        return size
    if isinstance(obj, dict):
        for k, v in obj.items():
            size += approx_bytes(k, _seen) + approx_bytes(v, _seen)
        return size
    if isinstance(obj, (list, tuple, set, frozenset)):
        for v in obj:
            size += approx_bytes(v, _seen)
        return size
    return size


class CacheBase(ABC):
    """Uniform stats/clear contract for any cache layer.

    Subclasses live both in sam (framework-agnostic — TTLCacheAdapter)
    and in webapp (ChartCache, FlaskCacheAdapter). The `info()` dict has
    a canonical shape so a single Jinja macro can render any adapter.
    """

    name: str  # human label for stats display

    @abstractmethod
    def info(self) -> dict:
        """Return canonical stats dict.

        Shape:
            {
              'name':        str,
              'enabled':     bool,
              'currsize':    int | None,    # entry count, None if not introspectable
              'maxsize':     int | None,    # capacity, None if unbounded
              'ttl':         float | None,  # seconds; None for non-TTL caches
              'hits':        int  | None,   # None if backend doesn't track
              'misses':      int  | None,
              'bytes_approx': int | None,   # lower-bound (see approx_bytes)
              'extras':      dict,          # backend-specific (e.g. flask groups)
            }
        """

    @abstractmethod
    def clear(self) -> int:
        """Invalidate all entries. Return number cleared."""
