"""The validate-only lookup memo: one dict on ``session.info`` while dispatch only checks.

``dispatch_action(validate_only=True)`` opens it for one action, and the Pending Users
worklist (``records_from_action_log``) opens it around its whole loop, so the keyed
reads the handlers repeat per action (resource key, opportunity map, allocation type,
area of interest, request overrides, users, projects) run once per key. A miss is kept
too: nothing writes on that path. Live posts never open one and query exactly as before.
"""

from contextlib import contextmanager
from typing import Any, Callable, Hashable, Iterable, Tuple

_INFO_KEY = 'xras_lookups'


@contextmanager
def batch_lookups(session):
    """Open a memo on *session* for the block, or join the one already open."""
    if _INFO_KEY in session.info:
        yield session.info[_INFO_KEY]
        return
    memo = session.info[_INFO_KEY] = {}
    try:
        yield memo
    finally:
        session.info.pop(_INFO_KEY, None)


def cached(session, key: Hashable, load: Callable[[], Any]) -> Any:
    """``load()`` once per *key* while a memo is open; a plain call when none is."""
    memo = session.info.get(_INFO_KEY)
    if memo is None:
        return load()
    if key not in memo:
        memo[key] = load()
    return memo[key]


def find_project(session, projcode: str):
    """``Project.get_by_projcode`` through the memo; the dispatcher and the handlers share it."""
    from sam.projects.projects import Project
    return cached(session, ('project', projcode.upper()),
                  lambda: Project.get_by_projcode(session, projcode))


def seed(session, items: Iterable[Tuple[Hashable, Any]]) -> None:
    """Fill the open memo with ``(key, value)`` pairs; a no-op when none is open."""
    memo = session.info.get(_INFO_KEY)
    if memo is not None:
        memo.update(items)
