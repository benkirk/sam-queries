"""Service layer for hpc-usage-queries per-job rows.

Thin wrapper around ``JobQueries.jobs_search`` (and the companion
``jobs_count``) that always scopes results to a SAM project (via
``project.projcode`` → ``Job.account``) and runs inside a context-managed
session bound to the cached engine.

Auth is the route's job, not the service's — but the service refuses to
issue an unscoped query (no ``project``) so a caller can't accidentally
return cross-project rows by forgetting a filter.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional, Sequence

from webapp.jobs.session import get_module, job_history_session


def _resolve_queue_and_qos(
    queue: Optional[str],
    qos: Optional[str],
    valid_qos_names: Sequence[str] = (),
) -> tuple:
    """Resolve a possibly-legacy queue name into (queue, qos) for the plugin.

    TODO(legacy-queue-names): pre-2026-05-13 ingester runs wrote
    synthetic queue names like ``cpu-special`` / ``cpu-economy`` into
    ``comp_charge_summary``. The underlying ``Job.queue`` column never
    used these — the real queue is the substring before the first dash
    (``cpu``) and the suffix encodes the QoS / priority class. Before
    QoS was a first-class filter the suffix was discarded; now that
    ``Job.qos`` is a real column we can do better:

    1. Strip the suffix from the queue so it matches ``Job.queue``.
    2. If the caller didn't specify a QoS filter AND the dropped suffix
       is a known QoS name, promote the suffix to a QoS filter —
       surfacing the precision the legacy summary rows already encoded.

    Explicit ``qos`` always wins over inference. When
    ``valid_qos_names`` is empty (no QoS catalog available) the
    function falls back to legacy behavior: strip the suffix only.

    ``_count_via_sam_summary`` keeps the raw composite queue because
    the summary table IS the source of truth for itself; this resolver
    is only applied on the plugin path.

    Remove this helper and its call sites once the historical
    ``comp_charge_summary`` rows have been rewritten with canonical
    queue names.
    """
    if not queue or '-' not in queue:
        return queue, qos
    base, suffix = queue.split('-', 1)
    if qos is not None:
        return base, qos
    if suffix in valid_qos_names:
        return base, suffix
    return base, qos


def search_jobs(
    machine: str,
    *,
    project,
    start: Optional[date] = None,
    end: Optional[date] = None,
    user: Optional[str] = None,
    queue: Optional[str] = None,
    qos: Optional[str] = None,
    status: Optional[str] = None,
    has_gpus: Optional[bool] = None,
    columns: Optional[Sequence[str]] = None,
    limit: Optional[int] = None,
    offset: int = 0,
    sort_by: Optional[str] = None,
    sort_dir: str = 'desc',
    account_projcodes: Optional[Sequence[str]] = None,
    valid_qos_names: Sequence[str] = (),
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
        user, queue, qos, status: Optional plain-text filters.
            ``qos`` matches the canonical name in the plugin's
            ``job_qos`` lookup (e.g. ``'premium'``, ``'regular'``).
        has_gpus: ``None`` ignore; ``True`` → GPU jobs only; ``False`` →
            CPU-only jobs.
        columns: Optional column projection. Default is the plugin's
            ``DEFAULT_COLUMNS`` set.
        limit: Optional server-side LIMIT.
        offset: Optional server-side OFFSET.
        sort_by, sort_dir: Optional sort column + direction.
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
    JobQueries = mod.JobQueries

    # TODO(legacy-queue-names): see _resolve_queue_and_qos. Promotes
    # 'cpu-special' → queue='cpu', qos='special' when caller left qos
    # unset and the suffix matches a known QoS name.
    queue_norm, qos_norm = _resolve_queue_and_qos(queue, qos, valid_qos_names)

    kwargs: Dict[str, Any] = {
        'start':   start,
        'end':     end,
        'account': list(account_projcodes) if account_projcodes is not None else project.projcode,
        'user':    user,
        'queue':   queue_norm,
        'qos':     qos_norm,
        'status':  status,
        'columns': columns,
        'limit':   limit,
        'offset':  offset,
        'has_gpus': has_gpus,
    }
    if sort_by is not None:
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
    qos: Optional[str] = None,
    status: Optional[str] = None,
    has_gpus: Optional[bool] = None,
    account_projcodes: Optional[Sequence[str]] = None,
    valid_qos_names: Sequence[str] = (),
) -> int:
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
        ``int`` total.
    """
    if project is None:
        raise ValueError('count_jobs requires a project (account filter).')

    projcodes = (list(account_projcodes) if account_projcodes is not None
                 else [project.projcode])

    # Fast path: SAM's daily summary covers every filter the drill-down
    # uses. Avoids a 1-second-plus COUNT(*) over the plugin's job table.
    # `qos` is NOT in CompChargeSummary's key set today, so a QoS filter
    # falls back to the plugin path alongside status / has_gpus.
    if status is None and has_gpus is None and qos is None:
        return _count_via_sam_summary(
            machine,
            projcodes=projcodes,
            start=start, end=end,
            user=user, queue=queue,
        )

    # Plugin fallback for filter shapes outside the summary's key set.
    # TODO(legacy-queue-names): see _resolve_queue_and_qos. The fast
    # path above kept the raw composite queue (the summary stores it
    # that way); the plugin path needs the split + QoS inference.
    queue_norm, qos_norm = _resolve_queue_and_qos(queue, qos, valid_qos_names)

    mod = get_module()
    JobQueries = mod.JobQueries

    with job_history_session(machine) as session:
        return JobQueries(session, machine=machine).jobs_count(
            start=start,
            end=end,
            account=projcodes if account_projcodes is not None else project.projcode,
            user=user,
            queue=queue_norm,
            qos=qos_norm,
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


def list_qos_names(machine: str) -> List[str]:
    """Return active QoS names from the plugin's ``job_qos`` lookup table.

    Lets the route populate a QoS filter dropdown without hardcoding
    the canonical seed list (premium / regular / economy / uncharged /
    special) — if a new QoS row is seeded later it shows up here
    automatically. Per-machine because each compute system has its own
    plugin DB and the seed set could diverge.

    Returns an empty list if the plugin isn't loaded for this machine
    or the lookup table has no active rows.
    """
    mod = get_module()
    JobQueries = mod.JobQueries
    with job_history_session(machine) as session:
        return JobQueries(session, machine=machine).list_qos_names()
