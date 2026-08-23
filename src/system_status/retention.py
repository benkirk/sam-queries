"""Retention policy for `system_status` snapshot tables — the one knob.

Every consumer reads :data:`DEFAULT_RETENTION_DAYS` from here: the
``scripts/cleanup_status_data.py`` argparse default, :func:`cleanup_old_data`'s
own signature, and the ``cleanup_status_snapshots`` scheduled task. There is
deliberately no second constant anywhere else.

This module is the single home of the policy; it is deliberately not inlined
in ``scripts/cleanup_status_data.py``. That script is a hastily written, rarely
run utility — three commits, no tests, and nothing in the tree schedules
it — so it must be read as *evidence about the tables* rather than as a
specification. ``docs/plans/implemented/SCHEDULED_TASKS.md`` § 3.1 records the five
decisions that produced what is here; the four that changed behavior are:

1. **The cutoff is naive UTC.** The script used ``datetime.now()``, which is
   local, against ``timestamp`` columns that are naive **UTC**
   (``system_status.base.StatusTimestampMixin``). On a Denver host that pruned
   6-7 hours early, every run. See :func:`utcnow_naive`.
2. **Snapshot tables only.** ``system_outages`` and ``resource_reservations``
   are curated, human-authored incident records, not samples. The script
   pruned them in the same transaction as the snapshots, on two inconsistent
   and unexamined predicates. They are now out of scope: a scheduled job does
   not delete hand-written history.
3. **Spans are pruned explicitly.** ``user_proj_queue_status`` was absent from
   the script's table list entirely — it was reaped only transitively, by
   ``ondelete='CASCADE'`` from ``derecho_status`` / ``casper_status``. That
   semantic is kept ("spans that *started* in the pruned window go"), because
   ``timestamp`` is the span's first_seen, but it is now an explicit DELETE.
   See :data:`SNAPSHOT_TABLES` for why that matters.
4. **A return value instead of ``print``.** The task needs counts; an operator
   needs a log line. The script's entire body was ``print``.

The default is one year, deliberately conservative. A never-pruned production
database would otherwise meet its first automated run as a single multi-year
``DELETE`` against ``csg-postgres``; at a year, only data already older than a
year is in scope, so that hazard never arises and narrowing later is a
one-line ``values.yaml`` change reviewable on its own.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from .models import (
    CasperNodeTypeStatus,
    CasperStatus,
    DerechoStatus,
    FilesystemStatus,
    JupyterHubStatus,
    LoginNodeStatus,
    QueueStatus,
    UserProjQueueStatus,
)
from .timeutil import utcnow_naive

logger = logging.getLogger(__name__)

#: The one retention knob. Overridden per-deployment by ``$STATUS_RETENTION_DAYS``
#: (``helm/values.yaml`` sets it explicitly so the number is visible in GitOps).
DEFAULT_RETENTION_DAYS = 365

#: Per-table overrides, in days. **Empty on purpose.** The tables have wildly
#: different row rates, so a per-table horizon is very likely right eventually —
#: but nothing has measured `csg-postgres`, and a guessed number that looks
#: authoritative is worse than one obvious global. The first production run's
#: `deleted` breakdown is the measurement. Add rows HERE, not a second constant
#: somewhere else.
RETENTION_DAYS: dict[str, int] = {}

#: Rows are deleted in batches this size, so a prune never holds one long lock.
#: Hygiene, not mitigation — at a 365-day default the first run is small.
DEFAULT_CHUNK_SIZE = 10_000

#: The snapshot tables, **children before parents**.
#:
#: Order is load-bearing and the reason this is a list rather than a set.
#: ``login_node_status``, ``queue_status``, ``filesystem_status`` and
#: ``user_proj_queue_status`` all carry ``ondelete='CASCADE'`` FKs to
#: ``derecho_status`` / ``casper_status``. Deleting children first means the
#: outcome does not depend on whether the backend enforces CASCADE at all —
#: which matters more than it sounds: production is Postgres and enforces it,
#: but the test tier is SQLite, which does **not** enforce foreign keys unless
#: ``PRAGMA foreign_keys=ON`` is set (nothing in this repo sets it), and a bulk
#: ``query.delete()`` bypasses SQLAlchemy's ORM-level cascade too. Relying on
#: CASCADE would mean the behavior under test and the behavior in production
#: were different mechanisms.
#:
#: ``UserProjQueueStatus`` is here even though the old script omitted it. Its
#: ``timestamp`` is the span's *first_seen*, so ``timestamp < cutoff`` reproduces
#: exactly what CASCADE did — and also reaps any span whose parent FKs are both
#: NULL, which CASCADE never could.
SNAPSHOT_TABLES: list[tuple[type, str]] = [
    # children first
    (UserProjQueueStatus, 'user_proj_queue_status'),
    (LoginNodeStatus, 'login_node_status'),
    (QueueStatus, 'queue_status'),
    (FilesystemStatus, 'filesystem_status'),
    (CasperNodeTypeStatus, 'casper_node_type_status'),
    # parents last
    (DerechoStatus, 'derecho_status'),
    (CasperStatus, 'casper_status'),
    (JupyterHubStatus, 'jupyterhub_status'),
]


def resolve_cutoff(table_name: str, *, cutoff: datetime,
                   retention_days: int) -> datetime:
    """The cutoff for one table, applying any :data:`RETENTION_DAYS` override.

    With no override the shared ``cutoff`` is returned unchanged, so the common
    case is one instant across every table and a run is trivially reasoned
    about. An override shifts only that table.
    """
    override = RETENTION_DAYS.get(table_name)
    if override is None or override == retention_days:
        return cutoff
    return cutoff + timedelta(days=retention_days - override)


def _delete_chunked(session: Session, model: type, table_name: str,
                    cutoff: datetime, chunk_size: int) -> int:
    """Delete ``timestamp < cutoff`` rows in bounded batches. Returns the count.

    Each batch selects primary keys first and then deletes by key. A bare
    ``DELETE ... LIMIT`` is not portable (Postgres has no such clause), and
    ``delete()`` on a query carrying ``.limit()`` raises in SQLAlchemy.
    """
    pk = next(iter(model.__table__.primary_key.columns))
    pk_attr = getattr(model, pk.name)
    total = 0

    while True:
        keys = [
            row[0] for row in session.query(pk_attr)
            .filter(model.timestamp < cutoff)
            .limit(chunk_size)
            .all()
        ]
        if not keys:
            break
        deleted = (
            session.query(model)
            .filter(pk_attr.in_(keys))
            .delete(synchronize_session=False)
        )
        total += deleted
        # Defensive: without this a backend that reported 0 deleted for a
        # non-empty key list would spin forever on the same batch.
        if not deleted:
            logger.warning(
                'retention: %s returned 0 deleted for a batch of %d keys; '
                'stopping to avoid an unbounded loop', table_name, len(keys))
            break

    return total


def cleanup_old_data(retention_days: int = DEFAULT_RETENTION_DAYS,
                     dry_run: bool = False,
                     cutoff: Optional[datetime] = None,
                     session: Optional[Session] = None,
                     chunk_size: int = DEFAULT_CHUNK_SIZE) -> dict[str, int]:
    """Prune `system_status` snapshot rows older than the retention window.

    Args:
        retention_days: window width. Ignored when ``cutoff`` is given.
        dry_run: count what would go, delete nothing, commit nothing.
        cutoff: the exact instant to prune before, **naive UTC**. Injected by
            the scheduled task, which derives it from its *occurrence* rather
            than the wall clock — that is what makes a late run produce the
            same result as a punctual one. Defaults to
            ``utcnow_naive() - retention_days``.
        session: an open session to use. Injected by the task so the prune
            joins its transaction; when omitted this opens (and closes) its
            own, which is what the standalone script wants.
        chunk_size: rows per delete batch.

    Returns:
        ``{table_name: rows}`` for every table in :data:`SNAPSHOT_TABLES`,
        including zeros, so a caller can log a complete picture. Under
        ``dry_run`` the values are what *would* be deleted.

    Note:
        Outages and reservations are **not** touched. See the module docstring.
    """
    # Whether the window was derived here or handed in changes what the log
    # line may honestly claim: an injected cutoff need not be
    # `retention_days` old, and printing the default next to it reads as a
    # contradiction to whoever is holding the pager.
    derived = cutoff is None
    if derived:
        cutoff = utcnow_naive() - timedelta(days=retention_days)

    if session is not None:
        return _cleanup(session, cutoff, retention_days, dry_run, chunk_size,
                        derived=derived)

    from .session import create_status_engine, get_session
    _engine, SessionLocal = create_status_engine()
    with get_session(SessionLocal) as own_session:
        return _cleanup(own_session, cutoff, retention_days, dry_run,
                        chunk_size, derived=derived)


def _cleanup(session: Session, cutoff: datetime, retention_days: int,
             dry_run: bool, chunk_size: int, *,
             derived: bool = True) -> dict[str, int]:
    """The body, with a session in hand either way."""
    if derived:
        window = f'{retention_days}-day window'
    else:
        # `retention_days` still selects RETENTION_DAYS overrides, but it did
        # not produce this cutoff, so don't present it as the age.
        window = f'explicit cutoff; overrides relative to {retention_days}d'
    logger.info('retention: cutoff=%s (%s), mode=%s',
                cutoff.isoformat(), window,
                'dry-run' if dry_run else 'delete')

    counts: dict[str, int] = {}
    for model, table_name in SNAPSHOT_TABLES:
        table_cutoff = resolve_cutoff(table_name, cutoff=cutoff,
                                      retention_days=retention_days)
        if dry_run:
            counts[table_name] = (
                session.query(model)
                .filter(model.timestamp < table_cutoff)
                .count()
            )
        else:
            counts[table_name] = _delete_chunked(
                session, model, table_name, table_cutoff, chunk_size)

        if counts[table_name]:
            logger.info('retention: %s %s %d rows older than %s',
                        'would delete' if dry_run else 'deleted',
                        table_name, counts[table_name],
                        table_cutoff.isoformat())

    total = sum(counts.values())
    if dry_run:
        logger.info('retention: dry run complete, %d rows would be deleted', total)
    else:
        # The caller may be inside a larger transaction (the scheduled task is);
        # committing here would be wrong for it. Flush so counts are real, and
        # let whoever owns the session decide.
        session.flush()
        logger.info('retention: %d rows deleted (uncommitted)', total)

    return counts
