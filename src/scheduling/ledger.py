"""``TaskLedger`` — claim a slot, hold it, close it out.

Two locking primitives, chosen because they are the only ones that work
everywhere this code runs. Production is Postgres (`csg-postgres`), the config
default is MySQL, and tests are SQLite — so ``SELECT ... FOR UPDATE SKIP
LOCKED`` (absent on SQLite), MySQL's ``GET_LOCK`` and Postgres advisory locks
are all out. What remains is portable and, happily, sufficient:

**A — claim a new occurrence: INSERT, catch the unique violation.** Whoever
wins the ``uq_task_run_task_name_occurrence_key`` race owns the slot; the loser
gets ``None`` and moves on.

**B — reclaim a stale one: conditional UPDATE, check ``rowcount``.** A
single-statement compare-and-swap is atomic under every isolation level on all
three backends, needs no explicit locking syntax, and gives exactly what
``SELECT ... FOR UPDATE`` would with none of the portability cost.

⚠️ **No dialect-specific SQL may enter this module** — no ``ON CONFLICT``, no
``INSERT IGNORE``, no ``ON DUPLICATE KEY``. CI runs SQLite and production runs
Postgres; ``tests/unit/test_task_ledger.py`` has an AST guard that is the only
thing standing between those two facts.

**Session discipline, copied from ``sam.notify.ledger.NotificationLedger``.**
Every method opens its own short-lived session from a ``session_factory``,
commits, and closes — never enrolling in the caller's transaction. That is not
stylistic here: on Postgres an ``IntegrityError`` aborts the entire
transaction, and every subsequent statement on that connection fails with
``InFailedSqlTransaction``. Claiming on the session the task body will then use
would poison it. The ledger and the task's own work are deliberately separate
transactions, so a task that rolls back its data changes still leaves an honest
record that it ran and failed.

See ``docs/plans/SCHEDULED_TASKS.md`` § 4.4 and § 4.5.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Callable, Optional

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from system_status.models import TaskRun

logger = logging.getLogger(__name__)

#: Floor on a task's lease, regardless of how quick it claims to be. Below
#: this, ordinary scheduling jitter would let a healthy run be stolen.
MIN_LEASE = timedelta(seconds=900)

#: A run's lease is this multiple of its expected runtime, floored at
#: :data:`MIN_LEASE`. Generous on purpose: stealing a live run is far worse
#: than waiting out a dead one, because the next dispatch is only an hour away.
LEASE_FACTOR = 3

#: ``detail`` is TEXT; a runaway traceback should truncate here rather than
#: raise at the driver.
_DETAIL_MAX = 60_000


def lease_for(expected_runtime: Optional[timedelta]) -> timedelta:
    """How long a claim stays valid without a heartbeat."""
    if expected_runtime is None:
        return MIN_LEASE
    return max(LEASE_FACTOR * expected_runtime, MIN_LEASE)


def _encode(detail) -> Optional[str]:
    """JSON-encode ``detail``, never raising on unserializable input.

    A task that returns something odd must still get a ledger row. Losing the
    shape of the detail is survivable; losing the record that the task ran is
    not.
    """
    if detail is None:
        return None
    try:
        text = json.dumps(detail, default=str, sort_keys=False)
    except Exception:                                   # pragma: no cover
        text = json.dumps({'unserializable': repr(detail)[:1000]})
    return text[:_DETAIL_MAX]


class TaskLedger:
    """Reads and writes ``task_run``.

    Args:
        session_factory: a zero-argument callable returning a **new**
            ``Session``. Called once per operation; the session is closed
            straight after, so a ledger write never holds a connection across
            a task body.
    """

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self.session_factory = session_factory

    # ------------------------------------------------------------- primitive A
    def claim(self, task_name: str, occurrence_key: str, *, now: datetime,
              trigger: str = 'schedule',
              runner_id: Optional[str] = None) -> Optional[int]:
        """Try to own ``(task_name, occurrence_key)``.

        Returns the new ``task_run_id``, or ``None`` if somebody already holds
        the slot — which is the *normal* outcome for every dispatch after the
        first one in a slot, and must therefore not be logged as a warning.
        """
        with self.session_factory() as session:
            row = TaskRun(
                task_name=task_name, occurrence_key=occurrence_key,
                state='running', trigger_type=trigger, attempt=1,
                claimed_at=now, heartbeat_at=now, runner_id=runner_id,
            )
            session.add(row)
            try:
                session.commit()
            except IntegrityError:
                # Somebody else won the race. Roll back so the session can be
                # closed cleanly — on Postgres the transaction is already
                # aborted and nothing else could run on it anyway.
                session.rollback()
                logger.debug('ledger: %s@%s already claimed',
                             task_name, occurrence_key)
                return None
            return row.task_run_id

    # ------------------------------------------------------------- primitive B
    def reclaim_stale(self, task_name: str, occurrence_key: str, *,
                      now: datetime, lease: timedelta,
                      runner_id: Optional[str] = None) -> Optional[int]:
        """Steal a ``running`` row whose lease has expired.

        A single conditional UPDATE: ``rowcount == 1`` means we won, ``0``
        means somebody beat us to it or the row heartbeated back to life
        between our read and our write. There is no read — that is the point.
        """
        cutoff = now - lease
        with self.session_factory() as session:
            result = session.execute(
                update(TaskRun)
                .where(
                    TaskRun.task_name == task_name,
                    TaskRun.occurrence_key == occurrence_key,
                    TaskRun.state == 'running',
                    TaskRun.heartbeat_at < cutoff,
                )
                .values(
                    state='running',
                    runner_id=runner_id,
                    attempt=TaskRun.attempt + 1,
                    claimed_at=now,
                    heartbeat_at=now,
                    finished_at=None,
                )
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                session.rollback()
                return None
            session.commit()

            row_id = session.execute(
                select(TaskRun.task_run_id).where(
                    TaskRun.task_name == task_name,
                    TaskRun.occurrence_key == occurrence_key)
            ).scalar_one()
            logger.warning(
                'ledger: reclaimed stale run %s@%s (no heartbeat since %s)',
                task_name, occurrence_key, cutoff.isoformat())
            return row_id

    # ------------------------------------------------------------------ lifecycle
    def heartbeat(self, task_run_id: int, *, now: datetime) -> None:
        """Push the lease out. Swallows failures — see below.

        A missed heartbeat costs at worst a spurious reclaim on some later
        dispatch; raising here would kill a task that is doing its job
        perfectly well.
        """
        try:
            with self.session_factory() as session:
                session.execute(
                    update(TaskRun)
                    .where(TaskRun.task_run_id == task_run_id,
                           TaskRun.state == 'running')
                    .values(heartbeat_at=now)
                    .execution_options(synchronize_session=False)
                )
                session.commit()
        except Exception:
            logger.warning('ledger: heartbeat failed for task_run %s',
                           task_run_id, exc_info=True)

    def finish(self, task_run_id: int, *, state: str, now: datetime,
               duration_ms: Optional[int] = None, detail=None) -> None:
        """Close a run out with its outcome.

        Unconditional on the current state, deliberately: if a slow run was
        reclaimed out from under us we still want *our* outcome recorded
        rather than a row stuck at ``running``. The ``attempt`` counter is what
        distinguishes the two runs.
        """
        with self.session_factory() as session:
            session.execute(
                update(TaskRun)
                .where(TaskRun.task_run_id == task_run_id)
                .values(state=state, finished_at=now,
                        duration_ms=duration_ms, detail=_encode(detail))
                .execution_options(synchronize_session=False)
            )
            session.commit()

    def record_skip(self, task_name: str, occurrence_key: str, *,
                    now: datetime, detail=None,
                    trigger: str = 'schedule',
                    runner_id: Optional[str] = None) -> Optional[int]:
        """Write a terminal ``skipped`` row for an occurrence we will not run.

        Misfires and kill-switched tasks land here. Backfilling them costs a
        handful of rows and buys a real benefit: `--history` then shows an
        outage *as an outage*. Silence would make a three-day gap look
        identical to a task that was never registered.

        Returns ``None`` if the slot is already taken — a skip must never
        displace a real run.
        """
        with self.session_factory() as session:
            row = TaskRun(
                task_name=task_name, occurrence_key=occurrence_key,
                state='skipped', trigger_type=trigger, attempt=1,
                claimed_at=now, heartbeat_at=now, finished_at=now,
                duration_ms=0, runner_id=runner_id, detail=_encode(detail),
            )
            session.add(row)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                return None
            return row.task_run_id

    # ----------------------------------------------------------------- reads
    def get(self, task_name: str, occurrence_key: str) -> Optional[dict]:
        """One run as a plain dict, or ``None``.

        Dicts rather than ORM objects because the session closes here, and a
        detached instance whose attributes expire on access is a trap for
        every caller.
        """
        with self.session_factory() as session:
            row = session.execute(
                select(TaskRun).where(
                    TaskRun.task_name == task_name,
                    TaskRun.occurrence_key == occurrence_key)
            ).scalar_one_or_none()
            return _as_dict(row)

    def latest(self, task_name: str) -> Optional[dict]:
        """The most recently claimed run of a task."""
        with self.session_factory() as session:
            row = session.execute(
                select(TaskRun)
                .where(TaskRun.task_name == task_name)
                .order_by(TaskRun.claimed_at.desc(), TaskRun.task_run_id.desc())
                .limit(1)
            ).scalar_one_or_none()
            return _as_dict(row)

    def history(self, *, task_name: Optional[str] = None,
                limit: int = 20) -> list[dict]:
        """Recent runs, newest first."""
        with self.session_factory() as session:
            stmt = select(TaskRun).order_by(
                TaskRun.claimed_at.desc(), TaskRun.task_run_id.desc()).limit(limit)
            if task_name:
                stmt = stmt.where(TaskRun.task_name == task_name)
            return [_as_dict(r) for r in session.execute(stmt).scalars().all()]

    def stale_running(self, *, now: datetime,
                      lease: timedelta = MIN_LEASE) -> list[dict]:
        """Rows still ``running`` past their lease — the watchdog's input."""
        cutoff = now - lease
        with self.session_factory() as session:
            rows = session.execute(
                select(TaskRun)
                .where(TaskRun.state == 'running',
                       TaskRun.heartbeat_at < cutoff)
                .order_by(TaskRun.claimed_at)
            ).scalars().all()
            return [_as_dict(r) for r in rows]

    # ----------------------------------------------------------------- prune
    def prune(self, *, older_than: datetime) -> int:
        """Delete finished rows older than ``older_than``. Returns the count.

        ⚠️ Guarded on ``finished_at IS NOT NULL``, which is what stops a run
        from deleting its own live row — the pruning task is itself a row in
        this table, still ``running`` at the moment it prunes.
        """
        with self.session_factory() as session:
            deleted = (
                session.query(TaskRun)
                .filter(TaskRun.finished_at.isnot(None),
                        TaskRun.finished_at < older_than)
                .delete(synchronize_session=False)
            )
            session.commit()
            if deleted:
                logger.info('ledger: pruned %d task_run rows older than %s',
                            deleted, older_than.isoformat())
            return deleted


def _as_dict(row: Optional[TaskRun]) -> Optional[dict]:
    """A detached-safe snapshot. ``detail`` is decoded back to an object."""
    if row is None:
        return None
    detail = row.detail
    if detail:
        try:
            detail = json.loads(detail)
        except (ValueError, TypeError):
            pass                    # keep the raw string; it is still evidence
    return {
        'task_run_id': row.task_run_id,
        'task_name': row.task_name,
        'occurrence_key': row.occurrence_key,
        'state': row.state,
        'trigger': row.trigger_type,
        'attempt': row.attempt,
        'claimed_at': row.claimed_at,
        'heartbeat_at': row.heartbeat_at,
        'finished_at': row.finished_at,
        'duration_ms': row.duration_ms,
        'runner_id': row.runner_id,
        'detail': detail,
    }
