"""Bulk project state transitions.

One write function, shared by three callers that select their victims very
differently: the admin "Deactivate Expired" button, ``sam-admin project
--recent-expirations --deactivate``, and the monthly
``deactivate_expired_projects`` task. Before this module each of the first two
hand-rolled the loop, and they disagreed about the window, about whether
``inactivate_time`` was stamped at all, and about transaction handling.

Selection deliberately stays out. See :func:`sam.queries.expirations.unique_projects`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from sam.projects.projects import Project


@dataclass(frozen=True)
class DeactivationResult:
    """What a :func:`deactivate_projects` call actually did."""

    #: The projcodes deactivated, in the order they were passed.
    projcodes: Tuple[str, ...]

    #: The single ``inactivate_time`` stamp shared by the whole batch.
    when: datetime

    @property
    def count(self) -> int:
        return len(self.projcodes)


def deactivate_projects(
    session: Session,
    projects: Sequence[Project],
    *,
    when: Optional[datetime] = None,
) -> DeactivationResult:
    """Soft-deactivate every project in ``projects``, sharing one stamp.

    NOTE: This function does NOT commit. The caller is responsible — normally by
    wrapping it in ``management_transaction(session)``.

    WARNING: **A scheduled task must do NEITHER.** ``scheduling.runner`` owns the
    transaction: it commits via ``ctx.close_sessions(commit=True)`` and rolls
    back when ``ctx.dry_run``. A commit anywhere inside a task body — including
    one smuggled in by ``management_transaction`` — makes ``--dry-run`` write.

    Selection is the caller's business, and the callers genuinely differ: the
    admin button and the monthly task use
    ``sam.queries.expirations.DEACTIVATION_MIN_DAYS_EXPIRED`` with no ceiling,
    while ``sam-admin`` starts at 0 days and takes its ceiling from ``--since``
    because a human is reading the list. Passing an already-selected sequence is
    also what lets ``sam-admin`` prompt with a count that is guaranteed to be the
    count it then mutates.

    One flush for the batch rather than one per project: every row changes the
    same two columns, so SQLAlchemy can group them, and an all-or-nothing flush
    is what makes the returned projcode list true rather than a guess about how
    far the loop got.

    Args:
        session: SQLAlchemy session owning ``projects``. Used for the flush in
            preference to ``Project.session``, which is
            ``Session.object_session(obj)`` and therefore ``None`` for a
            detached instance.
        projects: Already-selected, already-deduplicated projects. Pass an
            expirations result through
            :func:`sam.queries.expirations.unique_projects` first — its shape is
            one row per (project, allocation), and duplicates here would stamp a
            project twice and inflate the reported count.
        when: The ``inactivate_time`` stamp, shared across the batch. Defaults to
            a single ``datetime.now()`` taken once here, never per project.
            **A scheduled task must pass this explicitly**, derived from
            ``ctx.occurrence`` via ``scheduling.schedules.to_local_naive``:
            ``ctx.occurrence`` is naive UTC and this column is naive Mountain, so
            the raw value would stamp 6-7 hours into the future relative to every
            other date in the schema.

    Returns:
        DeactivationResult carrying the projcodes and the stamp used.
    """
    stamp = when or datetime.now()

    for project in projects:
        project.deactivate(when=stamp, flush=False)

    session.flush()

    return DeactivationResult(
        projcodes=tuple(project.projcode for project in projects),
        when=stamp,
    )
