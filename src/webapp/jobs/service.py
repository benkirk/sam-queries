"""Service layer for hpc-usage-queries per-job rows.

Thin wrapper around ``JobQueries.jobs_search`` that always scopes results
to a SAM project (via ``project.projcode`` → ``Job.account``) and runs
inside a context-managed session bound to the cached engine.

Auth is the route's job, not the service's — but the service refuses to
issue an unscoped query (no ``project``) so a caller can't accidentally
return cross-project rows by forgetting a filter.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional, Sequence

from webapp.jobs.session import get_module, job_history_session


def search_jobs(
    machine: str,
    *,
    project,
    start: Optional[date] = None,
    end: Optional[date] = None,
    user: Optional[str] = None,
    queue: Optional[str] = None,
    status: Optional[str] = None,
    columns: Optional[Sequence[str]] = None,
    limit: Optional[int] = None,
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
        columns: Optional column projection. Default is the plugin's
            ``DEFAULT_COLUMNS`` set.
        limit: Optional server-side LIMIT.

    Returns:
        ``list[dict]`` ordered by ``Job.end DESC``; empty list if no
        matches. Empty list (not a 404) when the plugin is enabled but
        the project has no jobs.

    Raises:
        RuntimeError: if the plugin is not loaded — propagated from
            ``job_history_session``.
    """
    if project is None:
        raise ValueError('search_jobs requires a project (account filter).')

    mod = get_module()
    JobQueries = mod.JobQueries  # AttributeError here means stale plugin

    with job_history_session(machine) as session:
        return JobQueries(session, machine=machine).jobs_search(
            start=start,
            end=end,
            account=project.projcode,
            user=user,
            queue=queue,
            status=status,
            columns=columns,
            limit=limit,
        )
