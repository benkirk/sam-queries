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
) -> List[Dict[str, Any]]:
    """Return per-job rows for *project* on *machine*.

    The PBS ``Job.account`` filter is always set to ``project.projcode``
    so callers cannot leak rows from another project. All other filters
    are optional and compose via AND inside the plugin.

    Args:
        machine: Machine name (e.g. 'derecho', 'casper').
        project: SAM Project — supplies the ``account`` filter via
            ``project.projcode``.
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
        'account': project.projcode,
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
) -> Optional[int]:
    """Return the total number of jobs matching the search filters.

    Companion to :func:`search_jobs` for paginated UIs. Same projcode
    pinning + filter shape.

    Returns:
        ``int`` total when the plugin advertises ``JobQueries.jobs_count``,
        else ``None`` — the route uses ``None`` as the signal to hide
        the pagination nav (graceful degradation on older plugins).
    """
    if project is None:
        raise ValueError('count_jobs requires a project (account filter).')

    if not get_capabilities()['count']:
        return None

    mod = get_module()
    JobQueries = mod.JobQueries

    with job_history_session(machine) as session:
        return JobQueries(session, machine=machine).jobs_count(
            start=start,
            end=end,
            account=project.projcode,
            user=user,
            queue=queue,
            status=status,
            has_gpus=has_gpus,
        )
