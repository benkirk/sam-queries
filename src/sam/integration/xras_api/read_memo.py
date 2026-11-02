"""A memo of XRAS reads for one operator gesture, so a write reads each thing once.

Off unless :func:`read_memo` is open. A roster change re-read the same request four to
six times (route, verify before and after, card patch, dialog re-render): 3-4 s on
api.xras.org. Inside the scope a repeat GET is served from the memo; every admin write
calls :func:`forget`, so a verify read after a write is always fresh. Reference data
(the resource catalog, opportunities) survives a write: no roster or request write
changes it.
"""

from __future__ import annotations

import copy
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, Hashable, Optional

_MEMO: ContextVar[Optional[Dict[Hashable, Any]]] = ContextVar('xras_read_memo', default=None)

#: A cache-miss marker; ``None`` is a real answer (XRAS's 404).
MISS = object()

_REFERENCE_PATHS = ('/v1/resources', '/v1/opportunities/')


@contextmanager
def read_memo():
    """Memoize XRAS GETs until the block exits. A nested scope reuses the outer one."""
    if _MEMO.get() is not None:
        yield
        return
    token = _MEMO.set({})
    try:
        yield
    finally:
        _MEMO.reset(token)


def lookup(key: Hashable) -> Any:
    memo = _MEMO.get()
    if memo is None or key not in memo:
        return MISS
    return copy.deepcopy(memo[key])


def remember(key: Hashable, value: Any) -> None:
    memo = _MEMO.get()
    if memo is not None:
        memo[key] = copy.deepcopy(value)


def forget() -> None:
    """Drop everything a write could have changed (all but reference data)."""
    memo = _MEMO.get()
    if memo is None:
        return
    for key in [k for k in memo if not any(p in k[1] for p in _REFERENCE_PATHS)]:
        del memo[key]
