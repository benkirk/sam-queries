"""Service layer for hpc-usage-queries per-job rows.

Thin wrapper around ``JobQueries.jobs_search`` (and the companion
``jobs_count``) that always scopes results to a SAM project (via
``project.projcode`` → ``Job.account``) and runs inside a context-managed
session bound to the cached engine.

Auth is the route's job, not the service's — but the service refuses to
issue an unscoped query (no ``project``) so a caller can't accidentally
return cross-project rows by forgetting a filter.

Plugin-capability fallback: ``offset`` / ``sort_by`` / ``sort_dir`` /
``has_gpus`` are forwarded only when the loaded plugin advertises them
(probed in :func:`webapp.jobs.session.init_job_history`). Against an
older plugin the call still succeeds — pagination just degrades.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional, Sequence

from webapp.jobs.session import get_capabilities, get_module, job_history_session


def search_jobs(
    machine: str,
    *,
    project,
    start: Optional[date] = None,
    end: Optional[date] = None,
    user: Optional[str] = None,
    queue: Optional[str] = None,
    status: Optional[str] = None,
    has_gpus: Optional[bool] = None,
    columns: Optional[Sequence[str]] = None,
    limit: Optional[int] = None,
    offset: int = 0,
    sort_by: Optional[str] = None,
    sort_dir: str = 'desc',
    account_projcodes: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """Return per-job rows for *project* on *machine*.

    The PBS ``Job.account`` filter is always set so callers cannot leak
    rows from another project. By default it pins to ``project.projcode``
    (single value). Pass ``account_projcodes`` to broaden the filter to
    every projcode in a project tree (parent + descendants) — the route
    does this so child-projcode jobs show up under the parent's
    drill-down rows.

    Args:
        machine: Machine name (e.g. 'derecho', 'casper').
        project: SAM Project — supplies the default ``account`` filter
            via ``project.projcode``.
        start, end: Date range filter on ``Job.end``.
        user, queue, status: Optional plain-text filters.
        has_gpus: ``None`` ignore; ``True`` → GPU jobs only; ``False`` →
            CPU-only jobs. Forwarded to the plugin when supported.
        columns: Optional column projection. Default is the plugin's
            ``DEFAULT_COLUMNS`` set.
        limit: Optional server-side LIMIT.
        offset: Optional server-side OFFSET; silently dropped if the
            loaded plugin lacks ``offset=`` (logged once at startup).
        sort_by, sort_dir: Optional sort column + direction; silently
            dropped if the loaded plugin lacks ``sort_by=``.
        account_projcodes: Optional sequence of projcodes for tree-aware
            filtering. When provided, takes precedence over
            ``project.projcode`` — the upstream plugin applies
            ``Job.account IN (...)``.

    Returns:
        ``list[dict]`` ordered by *sort_by* (default ``Job.end DESC``);
        empty list if no matches.

    Raises:
        RuntimeError: if the plugin is not loaded — propagated from
            ``job_history_session``.
    """
    if project is None:
        raise ValueError('search_jobs requires a project (account filter).')

    mod = get_module()
    JobQueries = mod.JobQueries  # AttributeError here means stale plugin
    caps = get_capabilities()

    kwargs: Dict[str, Any] = {
        'start':   start,
        'end':     end,
        'account': list(account_projcodes) if account_projcodes is not None else project.projcode,
        'user':    user,
        'queue':   queue,
        'status':  status,
        'columns': columns,
        'limit':   limit,
    }
    if caps['offset']:
        kwargs['offset'] = offset
        kwargs['has_gpus'] = has_gpus
    if caps['sort'] and sort_by is not None:
        kwargs['sort_by']  = sort_by
        kwargs['sort_dir'] = sort_dir

    with job_history_session(machine) as session:
        return JobQueries(session, machine=machine).jobs_search(**kwargs)


def count_jobs(
    machine: str,
    *,
    project,
    start: Optional[date] = None,
    end: Optional[date] = None,
    user: Optional[str] = None,
    queue: Optional[str] = None,
    status: Optional[str] = None,
    has_gpus: Optional[bool] = None,
    account_projcodes: Optional[Sequence[str]] = None,
) -> Optional[int]:
    """Return the total number of jobs matching the search filters.

    Companion to :func:`search_jobs` for paginated UIs. Same projcode
    pinning + filter shape; ``account_projcodes`` broadens the filter
    to a project tree exactly like :func:`search_jobs`.

    **Source priority** — the per-job drill-down's filter shape
    (account/machine/queue/user/date) is exactly the unique key of
    SAM's ``comp_charge_summary``, so the count is sourced from there
    by default (small pre-aggregated table; sub-millisecond response
    against the production schema). Falls back to the plugin's
    ``JobQueries.jobs_count`` — a ``COUNT(*)`` over the raw ``job``
    table — only when a filter outside the summary key set is in play
    (``status``, ``has_gpus``). The two counts can disagree under
    ingester drift; SAM is treated as the source of truth for the
    displayed totalizer since it's the project's accounting authority.

    Returns:
        ``int`` total. ``None`` only on the plugin-fallback path when
        the plugin lacks ``jobs_count`` (older builds) — the route
        uses ``None`` as the signal to hide the pagination nav.
    """
    if project is None:
        raise ValueError('count_jobs requires a project (account filter).')

    projcodes = (list(account_projcodes) if account_projcodes is not None
                 else [project.projcode])

    # Fast path: SAM's daily summary covers every filter the drill-down
    # uses. Avoids a 1-second-plus COUNT(*) over the plugin's job table.
    if status is None and has_gpus is None:
        return _count_via_sam_summary(
            machine,
            projcodes=projcodes,
            start=start, end=end,
            user=user, queue=queue,
        )

    # Plugin fallback for filter shapes outside the summary's key set.
    if not get_capabilities()['count']:
        return None

    mod = get_module()
    JobQueries = mod.JobQueries

    with job_history_session(machine) as session:
        return JobQueries(session, machine=machine).jobs_count(
            start=start,
            end=end,
            account=projcodes if account_projcodes is not None else project.projcode,
            user=user,
            queue=queue,
            status=status,
            has_gpus=has_gpus,
        )


def _count_via_sam_summary(
    machine: str,
    *,
    projcodes: Sequence[str],
    start: Optional[date],
    end:   Optional[date],
    user:  Optional[str],
    queue: Optional[str],
) -> int:
    """``SUM(num_jobs)`` over ``comp_charge_summary`` for the drill-down filter shape.

    Plugin-side ``machine='derecho'`` covers SAM's ``Derecho`` and
    ``Derecho GPU`` resource rows (same physical machine, different
    SAM resource_name). An ILIKE prefix match captures both; the
    queue + user + project filters discriminate naturally between
    CPU and GPU rows because each queue belongs to one resource.

    ``projcodes`` is always a sequence — the caller normalizes singular
    vs. tree-wide inputs. An empty sequence yields zero (`IN ()`).
    """
    # Local imports avoid pulling Flask-SQLAlchemy into the module
    # namespace at import time — keeps `from webapp.jobs import service`
    # cheap for the test paths that don't need a live `db` session.
    from sqlalchemy import func
    from sam import CompChargeSummary
    from webapp.extensions import db

    q = db.session.query(func.coalesce(func.sum(CompChargeSummary.num_jobs), 0))
    q = q.filter(CompChargeSummary.act_projcode.in_(projcodes))
    q = q.filter(CompChargeSummary.machine.ilike(f'{machine}%'))
    if start is not None:
        q = q.filter(CompChargeSummary.activity_date >= start)
    if end is not None:
        q = q.filter(CompChargeSummary.activity_date <= end)
    if user:
        q = q.filter(CompChargeSummary.act_username == user)
    if queue:
        q = q.filter(CompChargeSummary.queue == queue)
    return int(q.scalar() or 0)
