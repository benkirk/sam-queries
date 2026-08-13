"""``run_due()`` — the entire scheduler-facing API.

One function. The CronJob calls it hourly through `sam-admin tasks --run-due`;
a future always-on daemon would call the same thing in a loop
(``while True: run_due(now=utcnow_naive(), ledger=ledger); sleep(60)``) and the
CronJob would demote to a values-flippable fallback. Nothing here imports
Click, Flask or kubernetes.

``now`` is **injected and never read from the clock inside**. That is what
makes a simulated week testable in milliseconds, and it is not a testing
convenience bolted on — a scheduler that reads the clock in three places is a
scheduler whose behaviour you cannot reason about.

See ``docs/plans/implemented/SCHEDULED_TASKS.md`` § 5 and § 6.4.
"""

from __future__ import annotations

import logging
import os
import traceback
from datetime import datetime, timedelta
from typing import Callable, Dict, Optional

from scheduling.ledger import TaskLedger, lease_for
from scheduling.registry import TASKS, CatchUp, Task, TaskContext, TaskResult
from scheduling.schedules import occurrence_key

logger = logging.getLogger(__name__)

#: Comma-separated task names to skip, without a code deploy. The P5 rollout
#: depends on this: ship the chart with the destructive task named here, prove
#: credentials/DNS/image for 24 h against a zero-blast-radius dispatcher, then
#: clear it in a second commit.
DISABLED_ENV = 'SAM_TASKS_DISABLED'

#: How far back the misfire backfill will walk before collapsing the rest into
#: one summary row, so a task disabled for a year cannot emit 365 INSERTs on
#: the day someone re-enables it.
MAX_BACKFILL_STEPS = 32

#: Truncation for a traceback stored in `detail`.
_TB_MAX = 4000


def disabled_tasks(env: Optional[dict] = None) -> set[str]:
    """Names listed in ``$SAM_TASKS_DISABLED``."""
    raw = (env or os.environ).get(DISABLED_ENV, '') or ''
    return {n.strip() for n in raw.split(',') if n.strip()}


def run_due(*, now: datetime,
            ledger: TaskLedger,
            registry: Optional[Dict[str, Task]] = None,
            only: Optional[str] = None,
            force: bool = False,
            dry_run: bool = False,
            runner_id: Optional[str] = None,
            sam_session_factory: Optional[Callable] = None,
            status_session_factory: Optional[Callable] = None,
            env: Optional[dict] = None) -> dict:
    """Dispatch every task whose slot is open.

    Args:
        now: the dispatch instant, naive UTC. Injected, never read here.
        ledger: a :class:`~scheduling.ledger.TaskLedger`.
        registry: defaults to the global ``TASKS``.
        only: run just this task, ignoring dueness. Its slot is still the real
            scheduled one unless ``force``.
        force: with ``only``, claim a **manual** occurrence key. See below.
        dry_run: compute everything, write no ledger rows at all.
        runner_id: the pod name, recorded so a row ties back to `kubectl logs`.

    Returns:
        A dict with ``now``, per-task ``results`` and a ``counts`` rollup —
        the shape the CLI turns into its ``task_dispatch`` envelope.

    Tasks run **strictly serially** in one process. With a handful of tasks and
    a two-minute worst case, concurrency is unjustifiable complexity; the
    ledger already makes parallel *dispatchers* safe, so if it ever becomes
    justifiable the answer is two CronJobs with disjoint ``--only`` sets, not
    a thread pool.
    """
    registry = TASKS if registry is None else registry
    disabled = disabled_tasks(env)
    results = []

    if only is not None and only not in registry:
        raise KeyError(only)

    names = [only] if only else list(registry)

    for name in names:
        task = registry[name]

        # 1. Kill switch. Checked before anything else, including dueness, so
        #    a disabled task costs one dict lookup.
        if name in disabled:
            results.append(_skip(ledger, task, now, dry_run,
                                 reason='disabled', runner_id=runner_id))
            continue

        # 2. Which slot are we filling?
        if force:
            occ = now.replace(microsecond=0)
            key = 'M' + occurrence_key(occ)
            trigger = 'manual'
        else:
            occ = task.schedule.last_occurrence(now)
            if occ is None:
                results.append({'task': name, 'occurrence': None,
                                'outcome': 'nothing_due'})
                continue
            key = occurrence_key(occ)
            trigger = 'manual' if only else 'schedule'

        # 3. Is this slot already settled? Asked BEFORE the dueness check, and
        #    the ordering is load-bearing.
        #
        #    A daily 02:15 task with a 6 h grace is "late" for eighteen hours
        #    of every day — from 08:15 until the next slot. Checking lateness
        #    first would therefore declare a misfire on every dispatch from
        #    15:07 onward for a slot that *already succeeded that morning*,
        #    and each one would re-walk the backfill. The history would fill
        #    with skips describing runs that happened.
        #
        #    A `running` row deliberately falls through: it may be stale, and
        #    the reclaim path below is what recovers a dead dispatcher's slot.
        settled = None if force else ledger.get(name, key)
        if settled is not None and settled['state'] != 'running':
            results.append({'task': name, 'occurrence': occ.isoformat(),
                            'outcome': 'already_claimed',
                            'state': settled['state']})
            continue

        # 4. Dueness. `--run <name>` deliberately bypasses the grace check:
        #    an operator asking for a run has already decided it is wanted.
        if only is None and settled is None:
            late_by = now - occ
            if late_by > task.misfire_grace:
                _backfill_misfires(ledger, task, now, occ, dry_run,
                                   runner_id=runner_id)
                results.append(_skip(ledger, task, now, dry_run,
                                     reason='misfire',
                                     occurrence=occ, key=key,
                                     late_by_s=int(late_by.total_seconds()),
                                     runner_id=runner_id))
                continue

        # 5. Claim, with one stale reclaim attempt per dispatch.
        if dry_run:
            existing = ledger.get(name, key)
            results.append({
                'task': name,
                'occurrence': occ.isoformat(),
                'outcome': 'would_claim' if existing is None else 'already_claimed',
            })
            continue

        run_id = ledger.claim(name, key, now=now, trigger=trigger,
                              runner_id=runner_id)
        if run_id is None:
            run_id = ledger.reclaim_stale(
                name, key, now=now,
                lease=lease_for(task.expected_runtime), runner_id=runner_id)
        if run_id is None:
            # The normal case for every dispatch after the first in a slot.
            # Deliberately not a warning: at hourly dispatch this is 23/24 of
            # all log lines for a daily task.
            results.append({'task': name, 'occurrence': occ.isoformat(),
                            'outcome': 'already_claimed'})
            continue

        # 6. Run it.
        results.append(_execute(ledger, task, run_id, now, occ, key, dry_run,
                                sam_session_factory, status_session_factory))

    counts: dict = {}
    for r in results:
        counts[r['outcome']] = counts.get(r['outcome'], 0) + 1

    return {'now': now.isoformat(), 'results': results, 'counts': counts}


def _execute(ledger: TaskLedger, task: Task, run_id: int, now: datetime,
             occ: datetime, key: str, dry_run: bool,
             sam_factory, status_factory) -> dict:
    """Run one task body, timed, and close out its ledger row."""
    ctx = TaskContext(
        now=now, occurrence=occ, occurrence_key=key, task_name=task.name,
        dry_run=dry_run,
        logger=logging.getLogger(f'sam.tasks.{task.name}'),
        _sam_session_factory=sam_factory if 'sam' in task.needs else None,
        _status_session_factory=status_factory if 'status' in task.needs else None,
    )

    started = _monotonic_ms()
    try:
        result = task.fn(ctx)
    except Exception as exc:
        # `Exception`, NOT `BaseException`. A KeyboardInterrupt or SystemExit
        # must propagate: when the pod's activeDeadlineSeconds kills a wedged
        # run we want the row left `running`-and-stale for the reclaim path,
        # not mislabelled `failed` by a handler that caught the kill.
        ctx.close_sessions(commit=False)
        duration = _monotonic_ms() - started
        detail = {'error': repr(exc),
                  'traceback': traceback.format_exc()[-_TB_MAX:]}
        # A task may attach structured context to the exception it raises.
        # `TaskResult` has no failed state — a task fails by raising — so
        # without this the only place a failure can say anything is inside
        # `repr(exc)`, and an operator ends up regex-ing a count out of a
        # string. The expiration send's cap uses it to report
        # {'audience': n, 'cap': c} as data.
        extra = getattr(exc, 'task_detail', None)
        if isinstance(extra, dict):
            detail.update(extra)
        ledger.finish(run_id, state='failed', now=now,
                      duration_ms=duration, detail=detail)
        logger.exception('task %s failed at occurrence %s', task.name, key)
        return {'task': task.name, 'occurrence': occ.isoformat(),
                'outcome': 'failed', 'error': repr(exc)}

    ctx.close_sessions(commit=True)
    duration = _monotonic_ms() - started

    if result is None:
        result = TaskResult()
    elif isinstance(result, dict):
        result = TaskResult(detail=result)

    state = result.state
    detail = dict(result.detail or {})
    if result.message:
        detail['message'] = result.message
    if result.partial_failures:
        detail['partial_failures'] = result.partial_failures

    ledger.finish(run_id, state=state, now=now, duration_ms=duration,
                  detail=detail or None)
    logger.info('task %s %s at occurrence %s in %dms',
                task.name, state, key, duration)
    return {'task': task.name, 'occurrence': occ.isoformat(),
            'outcome': state, 'duration_ms': duration, 'detail': detail}


def _skip(ledger: TaskLedger, task: Task, now: datetime, dry_run: bool, *,
          reason: str, occurrence: Optional[datetime] = None,
          key: Optional[str] = None, late_by_s: Optional[int] = None,
          runner_id: Optional[str] = None) -> dict:
    """Record (and report) one skipped occurrence."""
    if occurrence is None:
        occurrence = task.schedule.last_occurrence(now)
    if occurrence is None:
        return {'task': task.name, 'occurrence': None, 'outcome': 'nothing_due'}
    if key is None:
        key = occurrence_key(occurrence)

    detail = {'reason': reason}
    if late_by_s is not None:
        detail['late_by_s'] = late_by_s

    if not dry_run:
        ledger.record_skip(task.name, key, now=now, detail=detail,
                           runner_id=runner_id)
    return {'task': task.name, 'occurrence': occurrence.isoformat(),
            'outcome': 'skipped', 'reason': reason}


def _backfill_misfires(ledger: TaskLedger, task: Task, now: datetime,
                       newest_missed: datetime, dry_run: bool, *,
                       runner_id: Optional[str] = None) -> int:
    """Write `skipped` rows for the occurrences before this one, bounded.

    Costs a handful of rows and buys a real benefit: `--history` then shows an
    outage *as an outage*. The alternative — silence — makes a three-day gap
    look identical to a task that was never registered.

    Bounded at :data:`MAX_BACKFILL_STEPS`, after which one summary row stands
    in for the rest.
    """
    if dry_run:
        return 0

    written = 0
    probe = newest_missed
    for _ in range(MAX_BACKFILL_STEPS):
        earlier = task.schedule.last_occurrence(probe - timedelta(seconds=1))
        if earlier is None or earlier >= probe:
            break
        probe = earlier
        if now - probe <= task.misfire_grace:
            break
        if ledger.get(task.name, occurrence_key(probe)) is not None:
            break                       # we have reached recorded history
        ledger.record_skip(
            task.name, occurrence_key(probe), now=now,
            detail={'reason': 'misfire',
                    'late_by_s': int((now - probe).total_seconds())},
            runner_id=runner_id)
        written += 1
    return written


def _monotonic_ms() -> int:
    """Milliseconds from a monotonic source.

    Not derived from the injected `now`: a duration must be measured, and the
    wall clock can step. This is the one clock read in the module and it
    measures elapsed time only — it never decides what to run.
    """
    import time
    return int(time.monotonic() * 1000)
