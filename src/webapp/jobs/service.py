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

from webapp.jobs import cache as jobs_cache
from webapp.jobs.session import (
    get_engines,
    get_module,
    is_enabled,
    job_history_session,
)


def job_history_machines() -> List[str]:
    """Machines with a warmed job-history engine, sorted.

    Analogue of ``disk_scans.service.scan_capable_resources()`` — the
    single source for "which machines can the jobs UI offer?". Returns
    ``[]`` when the plugin is disabled, so nav items / tabs / route
    validation all degrade together.
    """
    if not is_enabled():
        return []
    return sorted(get_engines().keys())


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


def _plugin_filter_kwargs(
    *,
    start: Optional[date] = None,
    end: Optional[date] = None,
    user: Optional[str] = None,
    queue: Optional[str] = None,
    qos: Optional[str] = None,
    exit_status: Optional[str] = None,
    name=None,
    ignore_case: bool = False,
    min_eligible_secs: Optional[int] = None,
    max_eligible_secs: Optional[int] = None,
    min_nodes: Optional[int] = None,
    max_nodes: Optional[int] = None,
    min_cpus: Optional[int] = None,
    max_cpus: Optional[int] = None,
    min_gpus: Optional[int] = None,
    max_gpus: Optional[int] = None,
    min_elapsed: Optional[int] = None,
    max_elapsed: Optional[int] = None,
    min_reqmem: Optional[int] = None,
    max_reqmem: Optional[int] = None,
    min_memory_used: Optional[int] = None,
    max_memory_used: Optional[int] = None,
    min_memory_wasted: Optional[int] = None,
    max_memory_wasted: Optional[int] = None,
    valid_qos_names: Sequence[str] = (),
) -> Dict[str, Any]:
    """Normalize the flat SAM-side filter set into plugin kwargs.

    The single seam every query mode routes through: mirrors the
    plugin's keyword surface 1:1 (minus ``account``, which each mode
    pins itself), runs the legacy queue→QoS resolver, and — being
    keyword-only — rejects unknown filter names with a TypeError instead
    of silently dropping them. Range bounds are plugin-native units
    (seconds / bytes / counts), inclusive, NULL-strict. The
    ``memory_wasted`` pair (requested − used bytes) is signed: negative
    bounds select jobs that used MORE than they requested.
    """
    queue_norm, qos_norm = _resolve_queue_and_qos(queue, qos, valid_qos_names)
    return {
        'start': start,
        'end':   end,
        'user':  user,
        'queue': queue_norm,
        'qos':   qos_norm,
        'exit_status': exit_status,
        'name':  name,
        'ignore_case': bool(ignore_case),
        'min_eligible_secs': min_eligible_secs,
        'max_eligible_secs': max_eligible_secs,
        'min_nodes': min_nodes, 'max_nodes': max_nodes,
        'min_cpus':  min_cpus,  'max_cpus':  max_cpus,
        'min_gpus':  min_gpus,  'max_gpus':  max_gpus,
        'min_elapsed': min_elapsed, 'max_elapsed': max_elapsed,
        'min_reqmem':  min_reqmem,  'max_reqmem':  max_reqmem,
        'min_memory_used': min_memory_used,
        'max_memory_used': max_memory_used,
        'min_memory_wasted': min_memory_wasted,
        'max_memory_wasted': max_memory_wasted,
    }


def search_jobs(
    machine: str,
    *,
    project,
    columns: Optional[Sequence[str]] = None,
    limit: Optional[int] = None,
    offset: int = 0,
    sort_by: Optional[str] = None,
    sort_dir: str = 'desc',
    account_projcodes: Optional[Sequence[str]] = None,
    valid_qos_names: Sequence[str] = (),
    **filters,
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
        columns: Optional column projection. Default is the plugin's
            ``DEFAULT_COLUMNS`` set.
        limit: Optional server-side LIMIT.
        offset: Optional server-side OFFSET.
        sort_by, sort_dir: Optional sort column + direction.
        account_projcodes: Optional sequence of projcodes for tree-aware
            filtering. When provided, takes precedence over
            ``project.projcode`` — the upstream plugin applies
            ``Job.account IN (...)``.
        **filters: The flat filter set — see
            :func:`_plugin_filter_kwargs` (start/end, user, queue, qos,
            exit_status, name + ignore_case, and the inclusive min/max
            bounds). Unknown names raise TypeError.

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

    # TODO(legacy-queue-names): the normalizer runs _resolve_queue_and_qos,
    # promoting 'cpu-special' → queue='cpu', qos='special' when the caller
    # left qos unset and the suffix matches a known QoS name.
    kwargs = _plugin_filter_kwargs(valid_qos_names=valid_qos_names, **filters)
    kwargs.update({
        'account': list(account_projcodes) if account_projcodes is not None else project.projcode,
        'columns': columns,
        'limit':   limit,
        'offset':  offset,
    })
    if sort_by is not None:
        kwargs['sort_by']  = sort_by
        kwargs['sort_dir'] = sort_dir

    with job_history_session(machine) as session:
        return JobQueries(session, machine=machine).jobs_search(**kwargs)


def count_jobs(
    machine: str,
    *,
    project,
    account_projcodes: Optional[Sequence[str]] = None,
    valid_qos_names: Sequence[str] = (),
    **filters,
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
    table — whenever ANY filter outside the summary key set is in play
    (qos, exit_status, name, or any min/max bound). The two counts can
    disagree under ingester drift; SAM is treated as the source of
    truth for the displayed totalizer since it's the project's
    accounting authority.

    Returns:
        ``int`` total.
    """
    if project is None:
        raise ValueError('count_jobs requires a project (account filter).')

    projcodes = (list(account_projcodes) if account_projcodes is not None
                 else [project.projcode])

    # Fast path: SAM's daily summary covers every filter the drill-down
    # uses — but ONLY those. `qos` is NOT in CompChargeSummary's key set
    # today, so a QoS filter falls back to the plugin path alongside
    # exit_status / name / every range bound. Gate on the raw filter
    # names (not the normalized kwargs) so a falsy-but-real bound like
    # ``max_gpus=0`` still forces the plugin path. ``ignore_case`` is a
    # modifier on ``name`` (a bool, often explicitly False), not a
    # filter — without a name it changes nothing, so it never gates.
    _summary_keys = {'start', 'end', 'user', 'queue', 'ignore_case'}
    extended = {k: v for k, v in filters.items() if k not in _summary_keys}
    if all(v is None for v in extended.values()):
        return _count_via_sam_summary(
            machine,
            projcodes=projcodes,
            start=filters.get('start'), end=filters.get('end'),
            user=filters.get('user'), queue=filters.get('queue'),
        )

    # Plugin fallback for filter shapes outside the summary's key set.
    # TODO(legacy-queue-names): the normalizer runs _resolve_queue_and_qos.
    # The fast path above kept the raw composite queue (the summary
    # stores it that way); the plugin path needs the split + QoS
    # inference.
    kwargs = _plugin_filter_kwargs(valid_qos_names=valid_qos_names, **filters)
    kwargs['account'] = (projcodes if account_projcodes is not None
                         else project.projcode)

    mod = get_module()
    JobQueries = mod.JobQueries

    with job_history_session(machine) as session:
        return JobQueries(session, machine=machine).jobs_count(**kwargs)


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


def search_jobs_machine(
    machine: str,
    *,
    columns: Optional[Sequence[str]] = None,
    limit: Optional[int] = None,
    offset: int = 0,
    sort_by: Optional[str] = None,
    sort_dir: str = 'desc',
    valid_qos_names: Sequence[str] = (),
    **filters,
) -> List[Dict[str, Any]]:
    """Machine-wide per-job rows — NO account scoping.

    SECURITY: this deliberately issues an unscoped query (every user's
    jobs, cross-project). The caller MUST sit behind
    ``@require_permission(Permission.VIEW_ALL_JOB_DATA)`` — there is no
    fallback pinning here, unlike :func:`search_jobs` (project) and
    :func:`search_jobs_user` (session user).
    """
    mod = get_module()
    JobQueries = mod.JobQueries

    kwargs = _plugin_filter_kwargs(valid_qos_names=valid_qos_names, **filters)
    kwargs.update({'columns': columns, 'limit': limit, 'offset': offset})
    if sort_by is not None:
        kwargs['sort_by']  = sort_by
        kwargs['sort_dir'] = sort_dir

    with job_history_session(machine) as session:
        return JobQueries(session, machine=machine).jobs_search(**kwargs)


def count_jobs_machine(
    machine: str,
    *,
    valid_qos_names: Sequence[str] = (),
    **filters,
) -> int:
    """Machine-wide job count — NO account scoping (see search_jobs_machine).

    Always the plugin's ``jobs_count``: the SAM-summary fast path is a
    per-project accounting table, and machine-wide requests are already
    permission-gated, low-volume operator surfaces.
    """
    mod = get_module()
    JobQueries = mod.JobQueries

    kwargs = _plugin_filter_kwargs(valid_qos_names=valid_qos_names, **filters)

    with job_history_session(machine) as session:
        return JobQueries(session, machine=machine).jobs_count(**kwargs)


def search_jobs_user(
    machine: str,
    username: str,
    *,
    columns: Optional[Sequence[str]] = None,
    limit: Optional[int] = None,
    offset: int = 0,
    sort_by: Optional[str] = None,
    sort_dir: str = 'desc',
    valid_qos_names: Sequence[str] = (),
    **filters,
) -> List[Dict[str, Any]]:
    """Per-job rows hard-pinned to *username* ("My Jobs" mode).

    The pin is server-side and non-negotiable: an empty username raises,
    and a ``user`` key in *filters* raises rather than being silently
    overwritten — the route must never forward a client-supplied user
    into this mode (mirror of disk_scans' pinned-owner rule).
    """
    if not username:
        raise ValueError('search_jobs_user requires a username (user pin).')
    if 'user' in filters:
        raise ValueError(
            "search_jobs_user pins user server-side; "
            "remove the 'user' filter from the call."
        )

    mod = get_module()
    JobQueries = mod.JobQueries

    kwargs = _plugin_filter_kwargs(valid_qos_names=valid_qos_names, **filters)
    kwargs.update({
        'user':    username,
        'columns': columns,
        'limit':   limit,
        'offset':  offset,
    })
    if sort_by is not None:
        kwargs['sort_by']  = sort_by
        kwargs['sort_dir'] = sort_dir

    with job_history_session(machine) as session:
        return JobQueries(session, machine=machine).jobs_search(**kwargs)


def count_jobs_user(
    machine: str,
    username: str,
    *,
    valid_qos_names: Sequence[str] = (),
    **filters,
) -> int:
    """Job count hard-pinned to *username* (see search_jobs_user)."""
    if not username:
        raise ValueError('count_jobs_user requires a username (user pin).')
    if 'user' in filters:
        raise ValueError(
            "count_jobs_user pins user server-side; "
            "remove the 'user' filter from the call."
        )

    mod = get_module()
    JobQueries = mod.JobQueries

    kwargs = _plugin_filter_kwargs(valid_qos_names=valid_qos_names, **filters)
    kwargs['user'] = username

    with job_history_session(machine) as session:
        return JobQueries(session, machine=machine).jobs_count(**kwargs)


def jobs_histogram(
    machine: str,
    dimension: str,
    *,
    account_projcodes: Optional[Sequence[str]] = None,
    username: Optional[str] = None,
    valid_qos_names: Sequence[str] = (),
    **filters,
) -> Dict[str, Any]:
    """Cached bucket histogram for *dimension* (plugin envelope, verbatim).

    Scope is the caller's job: ``account_projcodes`` pins a project
    tree (project mode), ``username`` hard-pins user mode (overwriting
    any client-supplied ``user`` filter — the pin always wins), both
    ``None`` is machine-wide (caller must be VIEW_ALL_JOB_DATA-gated).

    Results go through the jobs TTL cache: closed windows (``end`` before
    today) land in the long-lived ``historical`` bucket, open ones in
    ``recent``. The envelope is self-describing (``min_param`` /
    ``max_param``) — use those for bar drill-downs, never a hardcoded
    dimension→kwarg map.
    """
    kwargs = _plugin_filter_kwargs(valid_qos_names=valid_qos_names, **filters)
    if username is not None:
        kwargs['user'] = username
    if account_projcodes is not None:
        kwargs['account'] = list(account_projcodes)

    def _compute():
        mod = get_module()
        JobQueries = mod.JobQueries
        with job_history_session(machine) as session:
            return JobQueries(session, machine=machine).jobs_histogram(
                dimension, **kwargs,
            )

    opts = dict(kwargs)
    opts['dimension'] = dimension
    return jobs_cache.cached_jobs_aggregation(
        'histogram', machine, opts, _compute,
        bucket=jobs_cache.bucket_for_window(kwargs.get('end')),
    )


def jobs_usage_by_user(
    machine: str,
    *,
    limit: Optional[int] = 50,
    account_projcodes: Optional[Sequence[str]] = None,
    valid_qos_names: Sequence[str] = (),
    **filters,
) -> Dict[str, Any]:
    """Cached per-user usage rollup (plugin ``jobs_usage_by('user')``).

    Backs the By User pie: rows are hours-desc, ``totals`` is computed
    upstream BEFORE the limit truncation, so the pie's "Other" slice is
    ``totals − Σ rows``. No self-exclusion of any filter — ``account``
    scoping always applies (it's the security boundary). Same scope and
    caching rules as :func:`jobs_histogram`.
    """
    kwargs = _plugin_filter_kwargs(valid_qos_names=valid_qos_names, **filters)
    if account_projcodes is not None:
        kwargs['account'] = list(account_projcodes)

    def _compute():
        mod = get_module()
        JobQueries = mod.JobQueries
        with job_history_session(machine) as session:
            return JobQueries(session, machine=machine).jobs_usage_by(
                'user', limit=limit, **kwargs,
            )

    opts = dict(kwargs)
    opts['limit'] = limit
    return jobs_cache.cached_jobs_aggregation(
        'usage_by_user', machine, opts, _compute,
        bucket=jobs_cache.bucket_for_window(kwargs.get('end')),
    )


def jobs_facets(
    machine: str,
    *,
    facets: Sequence[str] = ('queue', 'qos', 'exit_status'),
    limit: Optional[int] = 8,
    account_projcodes: Optional[Sequence[str]] = None,
    username: Optional[str] = None,
    valid_qos_names: Sequence[str] = (),
    **filters,
) -> Dict[str, List[Dict[str, Any]]]:
    """Cached per-dimension value counts (plugin ``jobs_facets``).

    Backs the explorer's filter chips: ``{dim: [{'value', 'count'}, …]}``
    with live counts for the current window + filters. The plugin's
    ``self_exclude`` default stays on — a dimension's own filter doesn't
    constrain its own counts, so the queue chips still list every queue
    while a queue filter is active (click-to-switch). ``limit`` caps each
    dimension's chip count; the tail is dropped, not folded.

    Same scope and caching rules as :func:`jobs_histogram` — ``username``
    hard-pins user mode (never self-excluded: 'user' isn't a requested
    facet dimension), ``account_projcodes`` pins a project tree, and
    ``account`` is never self-excluded upstream by design.
    """
    kwargs = _plugin_filter_kwargs(valid_qos_names=valid_qos_names, **filters)
    if username is not None:
        kwargs['user'] = username
    if account_projcodes is not None:
        kwargs['account'] = list(account_projcodes)

    def _compute():
        mod = get_module()
        JobQueries = mod.JobQueries
        with job_history_session(machine) as session:
            return JobQueries(session, machine=machine).jobs_facets(
                facets=tuple(facets), limit=limit, **kwargs,
            )

    opts = dict(kwargs)
    opts['facets'] = tuple(facets)
    opts['limit'] = limit
    return jobs_cache.cached_jobs_aggregation(
        'facets', machine, opts, _compute,
        bucket=jobs_cache.bucket_for_window(kwargs.get('end')),
    )


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
