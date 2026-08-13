"""The task registry: what exists, when it runs, and what it needs.

A ``@task`` decorator rather than a list of dataclasses somewhere central,
because the schedule belongs next to the function it schedules — the
alternative puts the two halves of every task in different files and
guarantees drift. Discoverability is recovered the way
``system_status/models/__init__.py`` already does it: an explicit list of
side-effect imports in ``scheduling/tasks/__init__.py``, which is the one place
to grep for "what tasks exist".

See ``docs/plans/SCHEDULED_TASKS.md`` § 6.2 and § 6.3.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Optional, Sequence

logger = logging.getLogger(__name__)


class CatchUp(enum.Enum):
    """What to do about occurrences that were missed entirely."""

    #: Only the most recent occurrence is a candidate. The default, and right
    #: for anything whose work is cumulative — tonight's prune deletes what
    #: last night's would have, plus one more day.
    SKIP = 'skip'

    #: Replay every missed occurrence, oldest first, stopping at the first
    #: failure. For tasks whose occurrences do genuinely distinct work.
    ALL = 'all'


#: Which databases a task needs. The runner opens only these.
VALID_NEEDS = ('sam', 'status')


@dataclass(frozen=True)
class Task:
    """One registered task."""

    #: The ledger key. **Stable forever** — renaming orphans the history.
    name: str
    schedule: Any                       # a scheduling.schedules.Schedule
    fn: Callable[['TaskContext'], Any]

    #: Subset of :data:`VALID_NEEDS`. Drives which sessions get opened, which
    #: is what lets a status-only task survive a SAM MySQL outage.
    needs: Sequence[str] = ('status',)

    catchup: CatchUp = CatchUp.SKIP

    #: Past this much lateness an occurrence is recorded `skipped` rather than
    #: run. Six hours: long enough to absorb a cluster restart, short enough
    #: that a nightly job never runs in business hours.
    misfire_grace: timedelta = timedelta(hours=6)

    #: Drives the lease. Not a timeout — nothing kills a task for exceeding it.
    expected_runtime: Optional[timedelta] = None

    #: Only meaningful under :attr:`CatchUp.ALL`.
    max_catchup: int = 7

    #: Reserved for a task that wants a background heartbeat thread. Nothing
    #: declares it; a heartbeat thread for a 20-second DELETE is ceremony.
    long_running: bool = False

    description: str = ''

    def __post_init__(self):
        bad = [n for n in self.needs if n not in VALID_NEEDS]
        if bad:
            raise ValueError(
                f'task {self.name!r}: unknown needs {bad}; '
                f'valid values are {VALID_NEEDS}')
        if not self.name:
            raise ValueError('a task needs a name — it is the ledger key')


#: Every registered task, keyed by name. Populated by import side effects.
TASKS: Dict[str, Task] = {}


def task(*, name: str, schedule, **kwargs) -> Callable:
    """Register a function as a scheduled task.

    Raises:
        ValueError: on a duplicate name. Two tasks sharing a name would share
            ledger rows and silently suppress each other — the failure mode
            worth being loud about.
    """
    def decorate(fn: Callable) -> Callable:
        if name in TASKS:
            raise ValueError(
                f'duplicate task name {name!r} (already registered from '
                f'{TASKS[name].fn.__module__}). The name is the ledger key: '
                f'two tasks sharing one would claim each other\'s slots.')
        TASKS[name] = Task(name=name, schedule=schedule, fn=fn,
                           description=kwargs.pop('description', '')
                           or (fn.__doc__ or '').strip().split('\n')[0],
                           **kwargs)
        return fn
    return decorate


@dataclass
class TaskContext:
    """What a task body receives."""

    #: Naive UTC, the dispatch instant.
    now: datetime

    #: Naive UTC, the slot being filled. **Compute from this, never from the
    #: wall clock** — that is what makes a late run produce the same result as
    #: a punctual one, and it is the single easiest thing for a task author to
    #: get wrong.
    occurrence: datetime

    occurrence_key: str
    task_name: str
    dry_run: bool = False
    logger: logging.Logger = field(default_factory=lambda: logger)

    #: Set by the runner; opened lazily on first access.
    _sam_session_factory: Optional[Callable] = None
    _status_session_factory: Optional[Callable] = None
    _sam_session: Any = None
    _status_session: Any = None

    @property
    def sam_session(self):
        """The SAM MySQL session. Raises if the task did not declare it."""
        if self._sam_session is None:
            if self._sam_session_factory is None:
                raise RuntimeError(
                    f'task {self.task_name!r} touched sam_session but did not '
                    f"declare needs=('sam', ...)")
            self._sam_session = self._sam_session_factory()
        return self._sam_session

    @property
    def status_session(self):
        """The `system_status` session. Raises if the task did not declare it."""
        if self._status_session is None:
            if self._status_session_factory is None:
                raise RuntimeError(
                    f'task {self.task_name!r} touched status_session but did '
                    f"not declare needs=('status', ...)")
            self._status_session = self._status_session_factory()
        return self._status_session

    def close_sessions(self, *, commit: bool) -> None:
        """Commit or roll back whatever the task actually opened.

        The task's sessions are closed out independently of the ledger's, so a
        task that rolls back its data changes still leaves an honest record
        that it ran and failed.
        """
        for session in (self._sam_session, self._status_session):
            if session is None:
                continue
            try:
                if commit and not self.dry_run:
                    session.commit()
                else:
                    session.rollback()
            except Exception:
                logger.exception('task %s: error closing a session',
                                 self.task_name)
            finally:
                session.close()
        self._sam_session = None
        self._status_session = None


@dataclass
class TaskResult:
    """What a task body returns.

    ``partial_failures`` is a real state rather than a flag inside ``detail``
    because the expiration task will genuinely produce "23 of 25 emails sent",
    and an operator scanning a status column should not have to open JSON to
    notice.
    """

    detail: Optional[dict] = None
    message: Optional[str] = None
    partial_failures: int = 0

    @property
    def state(self) -> str:
        return 'partial' if self.partial_failures else 'succeeded'
