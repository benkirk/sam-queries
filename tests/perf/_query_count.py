"""SQL query counter for performance regression tests.

Extracted from ``utils/profiling/profile_user_dashboard.py``.  The
``_SQLStats`` class hooks into SQLAlchemy's ``before_cursor_execute`` /
``after_cursor_execute`` events to count and time every query that runs
through an engine.

Usage in tests (via the ``count_queries`` fixture in conftest.py)::

    def test_something(engine, session, count_queries):
        with count_queries(engine) as stats:
            do_work(session)
        assert stats.count <= 25, f"query regression: {stats.count}"
"""

import re
from contextlib import contextmanager
from typing import Dict, List

from sqlalchemy import event

from webapp.request_timing import timed_cursor_listeners

_TABLE_RE = re.compile(r'\bFROM\s+([a-z_][a-z0-9_]*)', re.IGNORECASE)


def _extract_table(statement: str) -> str:
    """Best-effort: pull the first FROM <table> name out of a SQL statement."""
    m = _TABLE_RE.search(statement)
    if m:
        return m.group(1).lower()
    return statement.strip().split(None, 1)[0].lower()[:30]


class SQLStats:
    """Accumulates query count, total time, per-table breakdown, and slowest queries."""

    def __init__(self):
        self._listeners = None   # (before, after) from timed_cursor_listeners
        self.reset()

    def reset(self):
        self.count = 0
        self.total_time = 0.0
        self.slowest: List[tuple] = []
        self.by_table: Dict[str, List[float]] = {}

    def _record(self, elapsed_ms, statement, executemany):
        elapsed = elapsed_ms / 1000.0
        self.count += 1
        self.total_time += elapsed
        self.slowest.append((elapsed, statement.strip()[:140]))
        self.slowest.sort(reverse=True)
        self.slowest = self.slowest[:10]

        table = _extract_table(statement)
        if statement.strip().lower().startswith('with anchors'):
            table = '<batched WITH anchors CTE>'
        elif statement.strip().lower().startswith('with w '):
            table = '<batched WITH w (rolling) CTE>'
        self.by_table.setdefault(table, []).append(elapsed)

    def attach(self, engine):
        """Start listening on *engine* (via the shared cursor primitive)."""
        self._listeners = timed_cursor_listeners(self._record)
        before, after = self._listeners
        event.listen(engine, 'before_cursor_execute', before)
        event.listen(engine, 'after_cursor_execute', after)

    def detach(self, engine):
        """Stop listening on *engine*."""
        before, after = self._listeners
        event.remove(engine, 'before_cursor_execute', before)
        event.remove(engine, 'after_cursor_execute', after)

    def summary(self) -> str:
        """One-line human-readable summary."""
        return (
            f"{self.count} queries in {self.total_time*1000:.1f}ms "
            f"({len(self.by_table)} tables)"
        )


@contextmanager
def count_queries(engine):
    """Context manager that yields an ``SQLStats`` tracking all queries on *engine*.

    Example::

        with count_queries(engine) as stats:
            session.query(User).all()
        print(stats.count)
    """
    stats = SQLStats()
    stats.attach(engine)
    try:
        yield stats
    finally:
        stats.detach(engine)
