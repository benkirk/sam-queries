"""Payload builders for ``sam-admin tasks``.

One payload, two renderers — ``display.py`` renders exactly the dict that
``--format json`` emits, so the Rich table and the JSON envelope cannot drift.
Builders never import Rich and never touch the console.

Key order is the wire order: ``output_json`` uses ``sort_keys=False``
deliberately, so the literal order below is what a consumer sees.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional


def build_task_list(registry, ledger, *, now: datetime,
                    disabled: Optional[set] = None) -> Dict[str, Any]:
    """The ``task_list`` envelope: every registered task and its latest run.

    One ``ORDER BY claimed_at DESC LIMIT 1`` per task. With a handful of tasks
    a loop is correct; a window function here would be cleverness nobody needs.
    """
    disabled = disabled or set()
    tasks: List[Dict[str, Any]] = []

    for name, task in registry.items():
        latest = ledger.latest(name)
        tasks.append({
            'name':            name,
            'schedule':        task.schedule.describe(),
            'description':     task.description or None,
            'needs':           list(task.needs),
            'enabled':         name not in disabled,
            'catchup':         task.catchup.value,
            'misfire_grace_s': int(task.misfire_grace.total_seconds()),
            'last_run':        _run_summary(latest),
            # Display only — the control flow must never reason forward.
            'next_occurrence': _iso(task.schedule.next_occurrence(now)),
        })

    return {
        'kind':     'task_list',
        'now':      now.isoformat(),
        'count':    len(tasks),
        'disabled': sorted(disabled),
        'tasks':    tasks,
    }


def build_task_history(ledger, *, task_name: Optional[str],
                       limit: int) -> Dict[str, Any]:
    """The ``task_history`` envelope: recent runs, newest first."""
    rows = ledger.history(task_name=task_name, limit=limit)
    return {
        'kind':  'task_history',
        'task':  task_name,
        'limit': limit,
        'count': len(rows),
        'runs':  [_run_summary(r) for r in rows],
    }


def build_task_dispatch(result: Dict[str, Any], *, dry_run: bool) -> Dict[str, Any]:
    """The ``task_dispatch`` envelope, from ``run_due()``'s return value."""
    return {
        'kind':    'task_dispatch',
        'now':     result['now'],
        'dry_run': dry_run,
        'results': result['results'],
        'counts':  result['counts'],
    }


def _run_summary(row: Optional[dict]) -> Optional[Dict[str, Any]]:
    """One ledger row, flattened for the wire."""
    if row is None:
        return None
    return {
        'task':        row['task_name'],
        'occurrence':  row['occurrence_key'],
        'state':       row['state'],
        'trigger':     row['trigger'],
        'attempt':     row['attempt'],
        'claimed_at':  _iso(row['claimed_at']),
        'finished_at': _iso(row['finished_at']),
        'duration_ms': row['duration_ms'],
        'runner_id':   row['runner_id'],
        'detail':      row['detail'],
    }


def _iso(value) -> Optional[str]:
    return None if value is None else value.isoformat()
