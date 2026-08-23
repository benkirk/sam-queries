"""Service layer for hpc-usage-queries per-job rows and aggregations.

Thin wrappers around ``JobQueries`` that run inside a context-managed
session bound to the cached engine, and that take a
:class:`webapp.jobs.scope.JobScope` saying which jobs the caller may see.

Auth is the route's job, not the service's. What the service guarantees is
that *some* scope was named: the scope object owns the pinning rule for its
mode and can reject a filter combination before it reaches the plugin (see
``webapp/jobs/scope.py`` for the per-mode table). Passing
``MachineJobScope`` is how a caller says "unscoped, and I am gated on
``VIEW_ALL_JOB_DATA``" — it can't happen by forgetting an argument.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence

from webapp.jobs import cache as jobs_cache
from webapp.jobs.scope import JobScope, ProjectJobScope
from webapp.jobs.session import (
    get_engines,
    get_module,
    is_enabled,
    job_history_session,
)

# Lookback applied when a host doesn't ask for one. Unbounded windows are
# the expensive path — a machine-wide aggregation measures ~200 s against
# the plugin PG vs ~0.6 s per month-window — so every surface bounds by
# default and widens deliberately.
DEFAULT_JOBS_WINDOW_DAYS = 90

# (days, label) for the card's period pills, exposed to templates as the
# ``jobs_window_pills`` Jinja global. The ``?days=`` whitelist derives from
# it so the UI can never offer a window the route would reject.
JOBS_WINDOW_PILLS = ((30, '30d'), (60, '60d'), (90, '90d'), (365, '1 yr'))

# Accepted ``?days=`` values. Anything else degrades to the default rather
# than 400ing, so a stale localStorage value from an older pill set can't
# break a card.
JOBS_WINDOW_CHOICES = tuple(days for days, _ in JOBS_WINDOW_PILLS)

# Age ladder for the explorer's job-age range control — the same shape as the
# fs-scans ``ATIME_BUCKETS`` (cumulative upper bound in days, ``None`` closing
# the last band), consumed by ``webapp.utils.age_bands``.
#
# Deliberately shorter and finer at the near end than the disk ladder: job
# history is asked about in days and weeks far more often than in years, and
# beyond ~2 years the question is almost always "everything older", not which
# year. It is NOT the ``?days=`` pill set and shares no whitelist with it —
# the pills set ``days``, whereas this control writes ``start``/``end``
# directly, so it needs no entry in ``JOBS_WINDOW_CHOICES`` and cannot be
# rejected by ``_parse_days``.
JOBS_AGE_BANDS = (
    ('< 1 Week', 7),
    ('1-4 Weeks', 30),
    ('1-3 Months', 90),
    ('3-6 Months', 180),
    ('6-12 Months', 365),
    ('1-2 Years', 730),
    ('2+ Years', None),
)


def default_jobs_window_start() -> str:
    """ISO date DEFAULT_JOBS_WINDOW_DAYS ago — the cards' default window."""
    return (date.today() - timedelta(days=DEFAULT_JOBS_WINDOW_DAYS)).isoformat()


def job_history_machines() -> List[str]:
    """Machines with a warmed job-history engine, in ``JOB_HISTORY_MACHINES`` order.

    Analogue of ``disk_scans.service.scan_capable_resources()`` — the
    single source for "which machines can the jobs UI offer?". Returns
    ``[]`` when the plugin is disabled, so nav items / tabs / route
    validation all degrade together.

    The order is the deployment's, not the alphabet's: ``_warm()`` inserts
    engines while iterating ``JOB_HISTORY_MACHINES`` (default
    ``derecho,casper``), and dicts keep insertion order, so config order
    survives — a machine whose engine failed to open just drops out. This
    is what puts Derecho first in every subtab strip; sorting here used to
    throw that away and lead with Casper.
    """
    if not is_enabled():
        return []
    return list(get_engines().keys())


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
    scope: JobScope,
    *,
    columns: Optional[Sequence[str]] = None,
    limit: Optional[int] = None,
    offset: int = 0,
    sort_by: Optional[str] = None,
    sort_dir: str = 'desc',
    valid_qos_names: Sequence[str] = (),
    **filters,
) -> List[Dict[str, Any]]:
    """Return per-job rows for *machine*, restricted by *scope*.

    Args:
        machine: Machine name (e.g. 'derecho', 'casper').
        scope: a :class:`~webapp.jobs.scope.JobScope` — it owns the
            ``account`` / ``user`` pinning rule for its mode, and vets
            *filters* before they reach the plugin.
        columns: Optional column projection. Default is the plugin's
            ``DEFAULT_COLUMNS`` set.
        limit: Optional server-side LIMIT.
        offset: Optional server-side OFFSET.
        sort_by, sort_dir: Optional sort column + direction.
        **filters: The flat filter set — see
            :func:`_plugin_filter_kwargs` (start/end, user, queue, qos,
            exit_status, name + ignore_case, and the inclusive min/max
            bounds). Unknown names raise TypeError.

    Returns:
        ``list[dict]`` ordered by *sort_by* (default ``Job.end DESC``);
        empty list if no matches.

    Raises:
        ValueError: if *scope* forbids one of the supplied filters.
        RuntimeError: if the plugin is not loaded — propagated from
            ``job_history_session``.
    """
    scope.check_filters(filters)

    # TODO(legacy-queue-names): the normalizer runs _resolve_queue_and_qos,
    # promoting 'cpu-special' → queue='cpu', qos='special' when the caller
    # left qos unset and the suffix matches a known QoS name.
    kwargs = _plugin_filter_kwargs(valid_qos_names=valid_qos_names, **filters)
    scope.apply(kwargs)
    kwargs.update({'columns': columns, 'limit': limit, 'offset': offset})
    if sort_by is not None:
        kwargs['sort_by']  = sort_by
        kwargs['sort_dir'] = sort_dir

    JobQueries = get_module().JobQueries
    with job_history_session(machine) as session:
        return JobQueries(session, machine=machine).jobs_search(**kwargs)


def count_jobs(
    machine: str,
    scope: JobScope,
    *,
    valid_qos_names: Sequence[str] = (),
    **filters,
) -> int:
    """Return the total number of jobs matching the search filters.

    Companion to :func:`search_jobs` for paginated UIs — same scope and
    filter shape.

    **Source priority** — a project scope's filter shape
    (account/machine/queue/user/date) is exactly the unique key of SAM's
    ``comp_charge_summary``, so its count is sourced from there by default
    (small pre-aggregated table; sub-millisecond against the production
    schema). Falls back to the plugin's ``JobQueries.jobs_count`` — a
    ``COUNT(*)`` over the raw ``job`` table — whenever ANY filter outside
    the summary key set is in play (qos, exit_status, name, or any min/max
    bound), and always for the machine and user scopes (the summary is a
    per-project accounting table, and those surfaces are permission-gated
    and low-volume). The two counts can disagree under ingester drift; SAM
    is treated as the source of truth for the displayed totalizer since
    it's the project's accounting authority.

    Returns:
        ``int`` total.
    """
    scope.check_filters(filters)

    # Fast path: SAM's daily summary covers every filter the drill-down
    # uses — but ONLY those. `qos` is NOT in CompChargeSummary's key set
    # today, so a QoS filter falls back to the plugin path alongside
    # exit_status / name / every range bound. Gate on the raw filter
    # names (not the normalized kwargs) so a falsy-but-real bound like
    # ``max_gpus=0`` still forces the plugin path. ``ignore_case`` is a
    # modifier on ``name`` (a bool, often explicitly False), not a
    # filter — without a name it changes nothing, so it never gates.
    projcodes = scope.summary_projcodes
    if projcodes is not None:
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
    scope.apply(kwargs)

    JobQueries = get_module().JobQueries
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


def _cached_aggregation(
    query_type: str,
    machine: str,
    kwargs: Dict[str, Any],
    call,
    **extra_opts,
) -> Any:
    """Run a cached ``JobQueries`` aggregation. The shared half of all four.

    Args:
        query_type: cache key family — distinct per aggregation, so a
            by-project rollup never satisfies (or is satisfied by) a
            by-user one with the same filter set.
        machine: joins the cache key; also selects the engine.
        kwargs: the fully-resolved plugin kwargs, scope pins already
            applied. Every one of them shapes the result, so they all go
            into the cache key.
        call: ``JobQueries -> result``. Runs inside a fresh session only
            on a cache miss, and must return the FINAL caller-facing value
            (the plugin's self-describing envelope) so a hit reproduces it
            exactly.
        **extra_opts: SAM-side knobs that shape the result but aren't
            plugin kwargs (dimension, limit, facets).

    Bucket follows the window: a window whose ``end`` is before today is
    closed and lands in the long-lived ``historical`` bucket; one that
    touches today keeps collecting jobs, so it lands in ``recent``.
    """
    def _compute():
        JobQueries = get_module().JobQueries
        with job_history_session(machine) as session:
            return call(JobQueries(session, machine=machine))

    return jobs_cache.cached_jobs_aggregation(
        query_type, machine, {**kwargs, **extra_opts}, _compute,
        bucket=jobs_cache.bucket_for_window(kwargs.get('end')),
    )


def jobs_histogram(
    machine: str,
    dimension: str,
    scope: JobScope,
    *,
    owners_limit: Optional[int] = None,
    owners_sort_by: Optional[str] = None,
    owners_by: Optional[str] = None,
    valid_qos_names: Sequence[str] = (),
    **filters,
) -> Dict[str, Any]:
    """Cached bucket histogram for *dimension* (plugin envelope, verbatim).

    ``owners_limit`` forwards to the plugin: each bucket gains a top-N
    per-owner ``owners`` mapping (stacked chart segments + the per-band
    owner tier). ``owners_sort_by`` picks WHICH top-N survives — it must
    follow the displayed metric or a GPU-hours view gets owners ranked
    by combined hours (top-5 measured to cover ~1% of band GPU-hours
    machine-wide). ``owners_by='account'`` switches the owner dimension
    from users to projects; it is forwarded ONLY when set and non-default
    so a pre-`owners_by` plugin never sees the kwarg — the User pill path
    keeps working against an older container, only the Project pill
    degrades. All three join the cache ``opts`` so variants never alias
    — which also naturally busts pre-upgrade cache entries.

    The envelope is self-describing (``min_param`` / ``max_param``) — use
    those for bar drill-downs, never a hardcoded dimension→kwarg map.
    """
    scope.check_filters(filters)
    kwargs = _plugin_filter_kwargs(valid_qos_names=valid_qos_names, **filters)
    scope.apply(kwargs)
    if owners_limit is not None:
        kwargs['owners_limit'] = owners_limit
    if owners_sort_by is not None:
        kwargs['owners_sort_by'] = owners_sort_by
    if owners_by is not None and owners_by != 'user':
        kwargs['owners_by'] = owners_by

    return _cached_aggregation(
        'histogram', machine, kwargs,
        lambda q: q.jobs_histogram(dimension, **kwargs),
        dimension=dimension,
    )


def jobs_timeseries(
    machine: str,
    period: str,
    scope: JobScope,
    *,
    owners_limit: Optional[int] = None,
    owners_sort_by: Optional[str] = None,
    owners_by: Optional[str] = None,
    valid_qos_names: Sequence[str] = (),
    **filters,
) -> Dict[str, Any]:
    """Cached per-period activity series (plugin envelope, verbatim).

    The time axis none of the distribution panels offer, and the only
    per-period plugin query that honors the filter set — ``usage_history``
    / ``jobs_by_entity_period`` / ``daily_summary_report`` all take dates
    only, so a chart on those would ignore queue / size / exit-status
    filters while sitting above a table that respects them.

    ``period`` is ``'day'``/``'week'``/``'month'`` and joins the cache
    ``opts`` so granularities never alias. Unlike ``jobs_histogram``, the
    owner top-N is ranked **once over the whole window** and every band
    carries the same keys in the same order — a stacked chart assigns
    colors once and trusts a series never to move or vanish mid-axis.

    Bands replay through ``start``/``end`` rather than
    ``min_param``/``max_param``: the window filters *are* this dimension.

    Cost is path-dependent upstream. The plugin serves this off its
    pre-aggregated ``daily_summary`` whenever the filter set is expressible
    in ``(date, user_id, account_id, queue_id)`` — i.e. dates plus
    user/account/queue, which is exactly a dashboard card's scope — and
    scans ``jobs`` otherwise. ``qos``, ``exit_status``, ``job_id``, ``name``
    and every ``min_*``/``max_*`` bound force the scan, so this is cheap on
    the cards and expensive under precisely the explorer filters that make
    it interesting. On the scan path it costs two statements when
    *owners_limit* is set (rank, then series) against one for a histogram.
    Measured on our own containers, a 180-band daily series over the card's
    scope is **~65 ms** on either machine.

    The envelope is identical on both paths, so the *response* cannot tell
    you which ran — but the plugin logs the routing decision and the day
    coverage at DEBUG. ``webapp/logging_config.py`` wires the ``job_history``
    logger up deliberately so ``LOG_LEVEL=DEBUG`` surfaces it; without that
    it inherits an unconfigured root and is silently dropped.

    Hosts differ deliberately: the cards keep it behind a collapse, the
    explorer opens it (``timeline_open``) — see ``jobs_card.html``.
    """
    scope.check_filters(filters)
    kwargs = _plugin_filter_kwargs(valid_qos_names=valid_qos_names, **filters)
    scope.apply(kwargs)
    if owners_limit is not None:
        kwargs['owners_limit'] = owners_limit
    if owners_sort_by is not None:
        kwargs['owners_sort_by'] = owners_sort_by
    if owners_by is not None and owners_by != 'user':
        kwargs['owners_by'] = owners_by

    return _cached_aggregation(
        'timeseries', machine, kwargs,
        lambda q: q.jobs_timeseries(period, **kwargs),
        period=period,
    )


def jobs_usage_by_user(
    machine: str,
    scope: JobScope,
    *,
    limit: Optional[int] = 50,
    sort_by: Optional[str] = None,
    valid_qos_names: Sequence[str] = (),
    **filters,
) -> Dict[str, Any]:
    """Cached per-user usage rollup (plugin ``jobs_usage_by('user')``).

    Backs the By User pie: rows are ranked by ``sort_by`` (the plugin's
    combined-hours default when ``None``) BEFORE the limit truncation, so
    the surviving top-N follows the viewed metric; ``totals`` is likewise
    pre-truncation, so the pie's "Other" slice is ``totals − Σ rows``.
    ``sort_by`` joins the cache ``opts`` — different rankings are
    different result sets. No self-exclusion of any filter — the scope's
    ``account`` pin always applies (it's the security boundary).
    """
    scope.check_filters(filters)
    kwargs = _plugin_filter_kwargs(valid_qos_names=valid_qos_names, **filters)
    scope.apply(kwargs)
    if sort_by is not None:
        kwargs['sort_by'] = sort_by

    return _cached_aggregation(
        'usage_by_user', machine, kwargs,
        lambda q: q.jobs_usage_by('user', limit=limit, **kwargs),
        limit=limit,
    )


def jobs_usage_by_project(
    machine: str,
    scope: JobScope,
    *,
    limit: Optional[int] = 25,
    sort_by: Optional[str] = None,
    valid_qos_names: Sequence[str] = (),
    **filters,
) -> Dict[str, Any]:
    """Cached per-project usage rollup (plugin ``jobs_usage_by('account')``).

    Backs every "By Project" pie. Scoping is the scope object's job, with
    the same rules By User gets — notably a user scope pins server-side and
    rejects a client ``user`` filter, so this can never aggregate anyone
    else's jobs.

    Rows are ranked by ``sort_by`` (plugin combined-hours default when
    ``None``) BEFORE the limit truncation; ``totals`` is pre-truncation,
    so "Other" is ``totals − Σ rows``. Cached as query type
    ``'usage_by_account'`` — its own key family, so it never aliases with
    a By User call over the same window.
    """
    scope.check_filters(filters)
    kwargs = _plugin_filter_kwargs(valid_qos_names=valid_qos_names, **filters)
    scope.apply(kwargs)
    if sort_by is not None:
        kwargs['sort_by'] = sort_by

    return _cached_aggregation(
        'usage_by_account', machine, kwargs,
        lambda q: q.jobs_usage_by('account', limit=limit, **kwargs),
        limit=limit,
    )


def jobs_facets(
    machine: str,
    scope: JobScope,
    *,
    facets: Sequence[str] = ('queue', 'qos', 'exit_status'),
    limit: Optional[int] = 8,
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

    The scope's pins are never self-excluded: 'user' isn't a requested
    facet dimension, and 'account' is never self-excluded upstream by
    design — both are security boundaries, not user-adjustable filters.
    """
    scope.check_filters(filters)
    kwargs = _plugin_filter_kwargs(valid_qos_names=valid_qos_names, **filters)
    scope.apply(kwargs)

    return _cached_aggregation(
        'facets', machine, kwargs,
        lambda q: q.jobs_facets(facets=tuple(facets), limit=limit, **kwargs),
        facets=tuple(facets), limit=limit,
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
