"""Read-side queries over ``task_run``.

The webapp's view of the scheduled-task ledger: the Admin -> Configuration
card's counts, and the run-history page's table plus its facet chips.

Built on :mod:`querykit` — the count / page / facet skeleton and the
self-exclusion rule live there; the ``_filters`` body below is this table's
own.

WARNING: **The clock.** ``task_run`` timestamps are naive **UTC**, like everything
else on this bind. ``sam/queries/notifications.py`` — the module this one is
modeled on — computes its windows with ``datetime.now()`` because
``notification_log`` lives in SAM MySQL and is naive-**Mountain**. Copying
that import verbatim would shift every count here by 6–7 hours: exactly the
bug ``SCHEDULED_TASKS.md`` § 3.1 found in the old cleanup script, in exactly
the same way. Every window in this module goes through
:func:`system_status.timeutil.utcnow_naive`.

Returns ORM rows rather than dicts, unlike its neighbors in this package
(``user_proj_queues`` shapes rows for templates). That is the facade's shape,
inherited from the notifications page it mirrors; the detail modal needs the
row's own columns and there is no CLI consumer to keep in step.

``TaskLedger``'s read helpers (``latest`` / ``history`` / ``stale_running``)
remain the CLI's path and are deliberately not used here — it takes a
``session_factory`` and closes the session per call, so handing it Flask's
scoped ``db.session`` would close it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from querykit import LogSpec, count_rows, facet_counts, page_rows

from ..models.task_run import TASK_STATES, TASK_TRIGGERS, TaskRun
from ..timeutil import utcnow_naive

#: The states the card renders as named rows, in card order.
#:
#: Deliberately shorter than :data:`TASK_STATES`: ``running`` has no row
#: because a run that is *currently* running is not news — a run still running
#: past its lease is, and that is :func:`count_stale_running`. Same split as
#: ``CARD_STATUSES`` vs ``NOTIFICATION_STATUSES`` in the notifications module.
CARD_STATES = ('succeeded', 'partial', 'failed', 'skipped')

#: How far back the card's headline counts look. Shorter than the
#: notifications card's 24h-equivalent default is not wanted here: the
#: dispatcher wakes hourly, so a day is ~24 dispatches — a readable number.
DEFAULT_WINDOW_HOURS = 24

#: Facet vocabularies. Both come from the model rather than being re-declared,
#: so a state added to the ledger cannot go missing from the chips.
#:
#: Note ``TASK_TRIGGERS`` includes ``catchup``, which the current runner never
#: writes. Its chip will read 0 permanently, and that is correct: an absent
#: bucket reads as "not measured" where a zero reads as "none".
FACET_STATES = TASK_STATES
FACET_TRIGGERS = TASK_TRIGGERS


def _default_lease_seconds() -> int:
    """The reclaim rule's lease, in seconds.

    Imported lazily so this module stays importable without ``scheduling`` —
    and read from ``MIN_LEASE`` rather than re-declared, so the card and the
    dispatcher cannot disagree about what "stale" means. The same
    one-mechanism discipline ``queued_stale_seconds`` keeps between the
    notifications card and the mailer.
    """
    from scheduling.ledger import MIN_LEASE
    return int(MIN_LEASE.total_seconds())


def summarize_task_runs(session: Session, *,
                        since: Optional[datetime] = None,
                        window_hours: int = DEFAULT_WINDOW_HOURS,
                        stale_lease_seconds: Optional[int] = None
                        ) -> Dict[str, Any]:
    """Counts for the Admin -> Configuration card.

    One grouped query for the states, plus one scalar for the stale counter —
    not one query per row on the card.

    Returns:
        ``{'window_start', 'window_hours', 'by_state', 'total',
        'stale_running', 'last_dispatch', <state>: n}`` with every
        :data:`CARD_STATES` key present and zeroed, so the template needs no
        ``default(0)`` on any of them.
    """
    window_start = since or (utcnow_naive() - timedelta(hours=window_hours))

    rows = session.execute(
        select(TaskRun.state, func.count(TaskRun.task_run_id))
        .where(TaskRun.claimed_at >= window_start)
        .group_by(TaskRun.state)
    ).all()

    by_state = {state: count for state, count in rows}

    summary: Dict[str, Any] = {
        'window_start': window_start,
        'window_hours': window_hours,
        'by_state': by_state,
        'total': sum(by_state.values()),
        # Deliberately NOT windowed — see count_stale_running.
        'stale_running': count_stale_running(
            session, stale_lease_seconds=stale_lease_seconds),
        # Unwindowed too: "when did the dispatcher last wake" is meaningless
        # if the answer can fall off the edge of the window and read as never.
        'last_dispatch': last_dispatch(session),
    }
    # The age, not just the instant, because `fmt_ago` takes a timedelta — and
    # because the subtraction has to happen against utcnow_naive(). A template
    # differencing a naive-UTC column against the local clock would report an
    # hourly dispatcher as ~7 hours stale.
    summary['last_dispatch_age'] = (
        utcnow_naive() - summary['last_dispatch']
        if summary['last_dispatch'] else None)
    for state in CARD_STATES:
        summary[state] = by_state.get(state, 0)
    return summary


def count_stale_running(session: Session, *,
                        stale_lease_seconds: Optional[int] = None) -> int:
    """Rows still ``running`` past the lease horizon.

    Non-zero means a runner died mid-task: it claimed the slot and never wrote
    an outcome. Those rows become reclaimable at exactly this horizon, so this
    counter is how an operator learns the death happened at all.

    Deliberately **not** windowed, for the same reason ``count_stuck_queued``
    is not: a run stuck three days ago matters more than one stuck an hour
    ago, and windowing lets the oldest breakage age quietly off the card.
    """
    if stale_lease_seconds is None:
        stale_lease_seconds = _default_lease_seconds()
    horizon = utcnow_naive() - timedelta(seconds=stale_lease_seconds)
    return session.execute(
        select(func.count(TaskRun.task_run_id))
        .where(TaskRun.state == 'running', TaskRun.heartbeat_at < horizon)
    ).scalar_one()


def last_dispatch(session: Session) -> Optional[datetime]:
    """When the dispatcher most recently claimed anything, across all tasks.

    The card's "is the CronJob alive at all?" line. ``None`` means it has
    never run — which, before the first hourly wake, is the honest answer.
    """
    return session.execute(select(func.max(TaskRun.claimed_at))).scalar()


def get_recent_task_runs(session: Session, *,
                         since: Optional[datetime] = None,
                         task_names: Optional[Sequence[str]] = None,
                         states: Optional[Sequence[str]] = None,
                         triggers: Optional[Sequence[str]] = None,
                         search: Optional[str] = None,
                         limit: Optional[int] = 100,
                         offset: int = 0) -> List[TaskRun]:
    """The run-history table's rows, newest first.

    ``search`` is a substring match on task name **or** runner id — the two
    things an operator arrives knowing ("what did cleanup do", "what did pod
    samuel-tasks-29341234-abcde do").
    """
    return page_rows(session, SPEC, limit=limit, offset=offset,
                     since=since, task_names=task_names, states=states,
                     triggers=triggers, search=search)


def count_recent_task_runs(session: Session, **filters) -> int:
    """Total matching rows, for pagination."""
    return count_rows(session, SPEC, **filters)


def facet_task_runs(session: Session, dimension: str,
                    **filters) -> Dict[str, int]:
    """Counts for one facet dimension, **excluding that dimension's filter**.

    Args:
        dimension: ``'task_name'`` / ``'state'`` / ``'trigger_type'``.
    """
    return facet_counts(session, SPEC, dimension, **filters)


def observed_task_names(session: Session) -> List[str]:
    """Every task name the ledger has ever seen, sorted.

    The filter ``<select>``'s offer list. Read from the table rather than from
    ``scheduling.registry.TASKS`` on purpose: a task deleted from the registry
    still has history worth filtering to, and the registry is not what the
    rows say.
    """
    return [name for (name,) in session.execute(
        select(TaskRun.task_name).distinct().order_by(TaskRun.task_name)
    ).all()]


def _filters(*, since: Optional[datetime] = None,
             task_names: Optional[Sequence[str]] = None,
             states: Optional[Sequence[str]] = None,
             triggers: Optional[Sequence[str]] = None,
             search: Optional[str] = None) -> list:
    """The WHERE terms shared by the table, the count and the facets.

    One builder so a filter added to the table cannot be forgotten in the
    facet rollups — which would show counts the table does not honor.
    """
    conditions = []
    if since is not None:
        conditions.append(TaskRun.claimed_at >= since)
    if task_names:
        conditions.append(TaskRun.task_name.in_(list(task_names)))
    if states:
        conditions.append(TaskRun.state.in_(list(states)))
    if triggers:
        conditions.append(TaskRun.trigger_type.in_(list(triggers)))
    if search:
        term = f'%{search.strip()}%'
        conditions.append(or_(
            TaskRun.task_name.ilike(term),
            TaskRun.runner_id.ilike(term),
        ))
    return conditions


#: Binds this table to the shared helpers in ``querykit``. Declared last
#: because it closes over :func:`_filters`.
SPEC = LogSpec(
    model=TaskRun,
    id_column=TaskRun.task_run_id,
    order_columns=(TaskRun.claimed_at.desc(),),
    dimensions={
        'task_name': TaskRun.task_name,
        'state': TaskRun.state,
        'trigger_type': TaskRun.trigger_type,
    },
    owned_filter={'task_name': 'task_names', 'state': 'states',
                  'trigger_type': 'triggers'},
    build_filters=_filters,
)
