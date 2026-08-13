"""Route-level chores shared by the faceted-log pages.

The Flask-aware half of the facade whose query half is :mod:`querykit`. Two
things every one of these pages does identically, and nothing else — the
per-page filter parsing stays in its own route module, because what a page
filters on *is* the page.

Consumers: ``dashboards/admin/notifications_routes.py``,
``dashboards/admin/tasks_routes.py``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

#: Hard ceiling on the lookback, so a hand-edited ``?days=`` cannot turn a
#: paginated page into a full-table scan.
MAX_DAYS = 365


def parse_window(args, *, default_days: int, per_page: int,
                 max_days: int = MAX_DAYS,
                 now: Optional[datetime] = None) -> tuple:
    """Read ``?days=`` / ``?page=`` into ``(since, page)``.

    Args:
        now: the clock. **Pass it explicitly for any naive-UTC table** —
            ``task_run`` is naive-UTC while ``notification_log`` is
            naive-Mountain, and defaulting to ``datetime.now()`` for both is
            how a window silently shifts by 6–7 hours.

    Returns:
        ``(since, page)`` where ``page`` is ``{'n', 'per_page', 'days'}``.
        ``days`` is in there because the fragments print "in the last N days"
        from it — a two-key page dict breaks the headline.
    """
    days = args.get('days', type=int) or default_days
    days = max(1, min(days, max_days))

    since = (now or datetime.now()) - timedelta(days=days)
    page_n = max(1, args.get('page', type=int) or 1)
    return since, {'n': page_n, 'per_page': per_page, 'days': days}


def build_facet_strip(counts: Mapping[str, int],
                      vocabulary: Optional[Iterable[str]] = None,
                      *, key: Optional[Callable] = None) -> List[Dict[str, Any]]:
    """Zero-filled facet rows in vocabulary order, out-of-vocabulary appended.

    Every declared value renders, **including at zero** — an absent bucket
    reads as "not measured" rather than "none", and the strip is something an
    operator scans by position. Zero-count chips are dimmed rather than hidden
    by ``facet_chips.html``, which is what makes the zero-fill worth doing.

    A value *outside* the vocabulary is a bad write rather than a filter miss,
    so it **appends** rather than reshuffling the declared order. Dropping it
    instead was a real bug: ``allocations/blueprint.py`` records that
    re-deriving the strip from the declared tuple alone hid such values while
    the headline total still counted them.

    Pass ``vocabulary=None`` for an observed-only dimension (one with no
    declared vocabulary, like a channel or a runner). Those sort by count
    descending and drop falsy values, since position carries no meaning when
    the set is whatever happens to be in the table.
    """
    if vocabulary is None:
        return sorted(
            ({'value': v, 'count': n} for v, n in counts.items() if v),
            key=key or (lambda r: (-r['count'], r['value'])))

    declared = list(vocabulary)
    strip = [{'value': v, 'count': counts.get(v, 0)} for v in declared]
    strip += [{'value': v, 'count': n} for v, n in sorted(counts.items())
              if v not in declared]
    return strip
