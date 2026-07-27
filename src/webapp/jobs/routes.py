"""HTMX fragment routes for the Job History card (per-job rows + aggregations).

Project-mode endpoints (url_prefix ``/dashboards/user/jobs``):

  GET /<projcode>             — per-job table (the Jobs tab; original route)
  GET /<projcode>/by-user     — per-user usage pie + drillable rows
  GET /<projcode>/wait-times  — wait-time histogram (dimension pinned 'wait')
  GET /<projcode>/job-sizes   — resource-needs histogram (?dimension=
                                nodes|cpus|gpus|memory)
  GET /<projcode>/durations   — elapsed-time histogram (pinned 'duration')

Jobs-tab query params: ``GET /dashboards/user/jobs/<projcode>``

Query params (all optional unless noted):
  machine   (required) — 'derecho' or 'casper'
  start, end           — YYYY-MM-DD; filters on Job.end
  user                 — limit to a single PBS username
  queue                — limit to a single queue
  qos                  — limit to a single QoS / priority class
                         (e.g. 'premium', 'regular', 'economy',
                         'uncharged', 'special')
  exit_status          — limit to a single PBS exit code as text
                         (e.g. '0' = success, '1', '271')
  page                 — int ≥ 1; default 1
  per_page             — int in [10, 200]; default 50
  sort_by              — one of {'start', 'elapsed', 'qos',
                         'cpu_charges', 'gpu_charges'}; default None
                         (plugin orders by ``Job.end DESC``)
  sort_dir             — 'asc' | 'desc'; default 'desc'

Access control mirrors the rest of the project-scoped UI: the
``require_project_access`` decorator looks the project up by ``projcode``,
verifies the user can see it (VIEW_PROJECTS permission OR project
membership), then hands the route a Project object. The service layer
additionally pins ``Job.account = project.projcode`` so a malformed
filter cannot leak cross-project rows.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from flask import Blueprint, abort, render_template, request, url_for
from flask_login import login_required

from webapp.api.access_control import require_project_access
from webapp.dashboards.charts import (
    generate_jobs_histogram,
    generate_jobs_user_pie_chart,
)
from webapp.jobs import service
from webapp.jobs.session import is_enabled

bp = Blueprint('jobs', __name__)

# Allowed machine values — keep in lockstep with
# job_history.database.session.VALID_MACHINES. Hardcoded here so the
# route can reject bad input without touching the plugin.
_VALID_MACHINES = {'derecho', 'casper'}

# Default columns shown when drilled into a user+queue row. user/queue/
# account are dropped because the row context already pins them.
_DEFAULT_COLS = (
    'job_id', 'name', 'qos', 'start', 'elapsed',
    'numnodes', 'numcpus', 'numgpus',
    'cpu_charges', 'gpu_charges',
)

# Every column rendered as a table header is sortable. The plugin maps
# `job.*` / `charge.*` keys to their SQLAlchemy columns and the
# computed `*_charges` keys to `hours × COALESCE(qos_factor, 1)`, so
# every key in _DEFAULT_COLS resolves to a valid ORDER BY at the SQL
# level. Built from _DEFAULT_COLS to stay in lockstep automatically.
_SORT_WHITELIST = set(_DEFAULT_COLS)

# Extra columns revealed in the per-row "expand" drawer. Order is the
# render order in the drawer. `qos_factor` is paired with the `qos` name
# column (now in the main table) so the multiplier sits next to status
# at the top of the drawer rather than buried beside memory_charges.
_VERBOSE_EXTRAS = (
    'exit_status', 'qos_factor',
    'queue', 'user',
    'submit', 'end', 'walltime',
    'mpiprocs', 'ompthreads',
    'reqmem', 'memory', 'vmemory',
    'cputype', 'gputype', 'resources',
    'cpu_hours', 'gpu_hours', 'memory_hours',
    'memory_charges',
)

# Numeric columns subject to all-zero auto-suppression in the table.
_SUPPRESSIBLE = {
    'numgpus', 'numnodes', 'numcpus',
    'elapsed',
    'cpu_hours', 'gpu_hours', 'memory_hours',
    'cpu_charges', 'gpu_charges', 'memory_charges',
}

_DEFAULT_PER_PAGE = 50
_MIN_PER_PAGE = 10
_MAX_PER_PAGE = 200


def _parse_date(raw: Optional[str]) -> Optional[date]:
    """Parse YYYY-MM-DD; return None for empty/invalid."""
    if not raw:
        return None
    try:
        return datetime.strptime(raw, '%Y-%m-%d').date()
    except ValueError:
        return None


def _parse_pagination():
    """Read page + per_page query args with defensive defaults."""
    try:
        page_n = max(1, int(request.args.get('page', 1)))
    except (TypeError, ValueError):
        page_n = 1
    try:
        per_page = int(request.args.get('per_page', _DEFAULT_PER_PAGE))
    except (TypeError, ValueError):
        per_page = _DEFAULT_PER_PAGE
    per_page = max(_MIN_PER_PAGE, min(per_page, _MAX_PER_PAGE))
    return {'n': page_n, 'per_page': per_page}


def _parse_sort():
    """Read sort_by + sort_dir; whitelist sort_by, default to no sort."""
    sort_by = request.args.get('sort_by') or None
    if sort_by and sort_by not in _SORT_WHITELIST:
        sort_by = None
    sort_dir = request.args.get('sort_dir', 'desc')
    if sort_dir not in ('asc', 'desc'):
        sort_dir = 'desc'
    return {'sort_by': sort_by, 'sort_dir': sort_dir}


def _visible_cols(default_cols, rows):
    """Drop columns from *default_cols* where every row's value is 0/None.

    Suppression only applies to numeric columns the plugin defines as
    "always present"; string/identity columns are passed through. Empty
    *rows* → no suppression so headers still render correctly above a
    "No jobs match" message.
    """
    if not rows:
        return list(default_cols)
    return [
        c for c in default_cols
        if c not in _SUPPRESSIBLE
        or any((r.get(c) or 0) != 0 for r in rows)
    ]


@bp.route('/<projcode>')
@login_required
@require_project_access
def jobs_fragment(project):
    """HTMX fragment: per-job table for *project* on the requested machine."""
    if not is_enabled():
        # Render the partial in disabled mode rather than 404 — the
        # resource-details page may include the hx-get unconditionally
        # and we want a graceful "feature not available" cell.
        return render_template(
            'dashboards/user/partials/jobs_fragment.html',
            project=project, machine=None, rows=[],
            filters={}, page={'n': 1, 'per_page': _DEFAULT_PER_PAGE},
            sort={'sort_by': None, 'sort_dir': 'desc'},
            total=None, visible_cols=[], verbose_extras=[],
            column_specs={},
            enabled=False, error=None,
        )

    machine = (request.args.get('machine') or '').strip().lower()
    if machine not in _VALID_MACHINES:
        abort(400, f'machine must be one of {sorted(_VALID_MACHINES)}')

    filters = {
        'start':  _parse_date(request.args.get('start')),
        'end':    _parse_date(request.args.get('end')),
        'user':   (request.args.get('user') or '').strip() or None,
        'queue':  (request.args.get('queue') or '').strip() or None,
        'qos':    (request.args.get('qos') or '').strip() or None,
        'exit_status': (request.args.get('exit_status') or '').strip() or None,
    }
    page = _parse_pagination()
    sort = _parse_sort()
    offset = (page['n'] - 1) * page['per_page']

    # Always request the verbose column superset so the per-row drawer
    # renders without a second fetch. Plugin still validates each key.
    requested_cols = tuple(_DEFAULT_COLS) + tuple(_VERBOSE_EXTRAS)

    # Expand the project tree so a parent's drill-down rows surface jobs
    # charged to child projcodes. Mirrors the Historical Usage rollup in
    # webapp/dashboards/user/blueprint.py — `get_descendants(include_self=True)`
    # returns just [project] for non-tree projects, so single-project
    # callers get the same effective filter as before.
    account_projcodes = [
        p.projcode for p in project.get_descendants(include_self=True)
    ]

    # QoS options for the filter dropdown — sourced from the plugin's
    # job_qos lookup table so a future seed addition flows through
    # without a SAM-side change. Fetched BEFORE search/count so the
    # same list can also be threaded into service.search_jobs /
    # count_jobs as ``valid_qos_names``: this lets the legacy queue
    # normalizer promote a 'cpu-special' drill-down's suffix to a real
    # QoS filter (was previously discarded). Degrades to [] if the
    # plugin call fails or the table is empty.
    try:
        qos_options = service.list_qos_names(machine)
    except Exception:
        from flask import current_app
        current_app.logger.exception(
            'jobs_fragment: list_qos_names failed for machine=%s', machine,
        )
        qos_options = []

    error = None
    rows = []
    total: Optional[int] = None
    try:
        rows = service.search_jobs(
            machine, project=project,
            limit=page['per_page'], offset=offset,
            sort_by=sort['sort_by'], sort_dir=sort['sort_dir'],
            columns=requested_cols,
            account_projcodes=account_projcodes,
            valid_qos_names=qos_options,
            **filters,
        )
        total = service.count_jobs(
            machine, project=project,
            account_projcodes=account_projcodes,
            valid_qos_names=qos_options,
            **filters,
        )
    except Exception as exc:
        # Catch-all so a transient plugin/DB issue degrades to a banner
        # rather than a 500 on the surrounding page. App logger captures
        # the full traceback for diagnosis.
        from flask import current_app
        current_app.logger.exception(
            'jobs_fragment: search/count failed for project=%s machine=%s',
            project.projcode, machine,
        )
        error = str(exc)

    visible_cols = _visible_cols(_DEFAULT_COLS, rows)

    # Suppress the QoS column when every visible row has the same QoS
    # value — a single-valued column is just noise. None counts as a
    # distinct value so a mix of (premium / legacy-NULL) still renders
    # the column. The dropdown follows the same rule, with one
    # exception: when the user explicitly picked a QoS via ``?qos=``
    # the dropdown stays visible so they can change or reset their
    # selection (the column still goes away because all rows match).
    qos_in_rows = {r.get('qos') for r in rows}
    qos_has_variation = len(qos_in_rows) >= 2
    if not qos_has_variation and 'qos' in visible_cols:
        visible_cols = [c for c in visible_cols if c != 'qos']
    template_qos_options = (
        qos_options
        if (qos_has_variation or filters.get('qos'))
        else []
    )

    # When the column is suppressed because all rows share one QoS value,
    # surface that value (and its charging factor) as a header badge so the
    # single-value case isn't silent — this is exactly the case (uniform
    # economy / premium) where the multiplier matters most. None
    # (uncharacterized) gets no badge.
    qos_badge = None
    if rows and not qos_has_variation:
        (shared_qos,) = qos_in_rows         # exactly one element when rows non-empty
        if shared_qos is not None:
            factors = {r.get('qos_factor') for r in rows
                       if r.get('qos_factor') is not None}
            qos_badge = {
                'name': shared_qos,
                'factor': next(iter(factors)) if len(factors) == 1 else None,
            }

    column_specs = _load_column_specs()
    fragment_url = url_for('jobs.jobs_fragment', projcode=project.projcode)

    # The caller passes the id of the container that owns this fragment so
    # sort / pagination clicks can swap that same container's innerHTML.
    # Falls back to a generic id when called without one (legacy paths).
    target_id = (request.args.get('target_id') or '').strip() \
        or f'jobs-{project.projcode}-{machine}'

    return render_template(
        'dashboards/user/partials/jobs_fragment.html',
        project=project,
        machine=machine,
        rows=rows,
        filters=filters,
        page=page,
        sort=sort,
        total=total,
        visible_cols=visible_cols,
        verbose_extras=list(_VERBOSE_EXTRAS),
        column_specs=column_specs,
        sortable_columns=sorted(_SORT_WHITELIST),
        qos_options=template_qos_options,
        qos_badge=qos_badge,
        fragment_url=fragment_url,
        target_id=target_id,
        enabled=True,
        error=error,
    )


def _load_column_specs():
    """Return the plugin's COLUMNS dict, or an empty stub if not loaded.

    The template reads ``column_specs[col]['header']`` for every visible
    or verbose column. An empty dict is a safe fallback: the template
    falls back to the raw column key as the header.
    """
    try:
        from job_history import COLUMNS
        return COLUMNS
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Aggregation fragments (By User / Wait Times / Job Sizes / Durations)
# ---------------------------------------------------------------------------

# Chart metric pills shared by the aggregation tabs.
_METRICS = ('jobs', 'cpu_hours', 'gpu_hours')
_DEFAULT_METRIC_HIST = 'jobs'
_DEFAULT_METRIC_PIE = 'cpu_hours'

# Job Sizes tab dimension pills; Wait Times / Durations pin their dimension.
_SIZE_DIMENSIONS = ('nodes', 'cpus', 'gpus', 'memory')

# Rows shown in the By User table (the pie itself keeps at most 9 + Other).
_BY_USER_LIMIT = 25

# Filter query params round-tripped through pill/toggle re-fetches, and —
# where the per-job fragment understands them — carried into row drill-downs.
_ROUNDTRIP_KEYS = (
    'start', 'end', 'user', 'queue', 'qos', 'exit_status',
    'name', 'ignore_case',
    'min_nodes', 'max_nodes', 'min_cpus', 'max_cpus',
    'min_gpus', 'max_gpus', 'min_wait_hours', 'max_wait_hours',
)

_SECS_PER_HOUR = 3600


def _parse_int_arg(name: str) -> Optional[int]:
    raw = (request.args.get(name) or '').strip()
    if not raw:
        return None
    try:
        return max(0, int(raw))
    except ValueError:
        return None


def _parse_float_arg(name: str) -> Optional[float]:
    raw = (request.args.get(name) or '').strip()
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def _parse_job_filters() -> dict:
    """Whitelisted GET parse → service filter kwargs (plugin-native units).

    Human-facing units convert at this boundary and nowhere else:
    ``min/max_wait_hours`` (hours) → ``min/max_eligible_secs`` (seconds).
    Unknown params are ignored; malformed numbers degrade to "no filter".
    """
    f: dict = {
        'start': _parse_date(request.args.get('start')),
        'end':   _parse_date(request.args.get('end')),
        'user':  (request.args.get('user') or '').strip() or None,
        'queue': (request.args.get('queue') or '').strip() or None,
        'qos':   (request.args.get('qos') or '').strip() or None,
        'exit_status': (request.args.get('exit_status') or '').strip() or None,
        'name':  (request.args.get('name') or '').strip() or None,
    }
    if f['name'] is not None:
        f['ignore_case'] = request.args.get('ignore_case') in ('1', 'true', 'on')
    for key in ('min_nodes', 'max_nodes', 'min_cpus', 'max_cpus',
                'min_gpus', 'max_gpus'):
        v = _parse_int_arg(key)
        if v is not None:
            f[key] = v
    min_wait = _parse_float_arg('min_wait_hours')
    max_wait = _parse_float_arg('max_wait_hours')
    if min_wait is not None:
        f['min_eligible_secs'] = int(min_wait * _SECS_PER_HOUR)
    if max_wait is not None:
        f['max_eligible_secs'] = int(max_wait * _SECS_PER_HOUR)
    return f


def _roundtrip_params(machine: str, target_id: str) -> dict:
    """Raw (display-unit) query params to carry through re-fetches."""
    params = {
        k: request.args.get(k) for k in _ROUNDTRIP_KEYS
        if (request.args.get(k) or '').strip()
    }
    params['machine'] = machine
    params['target_id'] = target_id
    return params


def _get_machine_or_400() -> str:
    machine = (request.args.get('machine') or '').strip().lower()
    if machine not in _VALID_MACHINES:
        abort(400, f'machine must be one of {sorted(_VALID_MACHINES)}')
    return machine


def _parse_metric(default: str) -> str:
    metric = (request.args.get('metric') or '').strip()
    return metric if metric in _METRICS else default


def _tree_projcodes(project) -> list:
    """Parent + descendants — same tree expansion as jobs_fragment."""
    return [p.projcode for p in project.get_descendants(include_self=True)]


def _render_by_user(*, mode, machine, fragment_url, jobs_fragment_url,
                    target_id, account_projcodes=None):
    """Shared renderer for the By User tab (project + machine modes)."""
    template = 'dashboards/user/partials/jobs_by_user.html'
    if not is_enabled():
        return render_template(template, enabled=False, error=None,
                               mode=mode, machine=None, target_id=target_id)

    filters = _parse_job_filters()
    metric = _parse_metric(_DEFAULT_METRIC_PIE)

    usage = None
    error = None
    try:
        usage = service.jobs_usage_by_user(
            machine, limit=_BY_USER_LIMIT,
            account_projcodes=account_projcodes, **filters,
        )
    except Exception as exc:
        from flask import current_app
        current_app.logger.exception(
            'jobs by-user fragment failed: mode=%s machine=%s', mode, machine,
        )
        error = str(exc)

    pie_svg = None
    other = None
    if usage:
        pie_svg = generate_jobs_user_pie_chart(usage, metric=metric)
        totals = usage.get('totals') or {}
        rows = usage.get('rows') or []
        # The upstream limit's remainder: totals are pre-truncation, so any
        # positive difference is real usage by users beyond the row cap.
        rem = {
            k: (totals.get(k) or 0) - sum((r.get(k) or 0) for r in rows)
            for k in ('job_count', 'cpu_hours', 'gpu_hours')
        }
        if any(v > 1e-9 for v in rem.values()):
            other = rem

    return render_template(
        template,
        enabled=True, error=error,
        mode=mode, machine=machine,
        usage=usage, other=other,
        metric=metric, pie_svg=pie_svg,
        fragment_url=fragment_url,
        jobs_fragment_url=jobs_fragment_url,
        target_id=target_id,
        params=_roundtrip_params(machine, target_id),
    )


def _render_histogram(*, mode, machine, dimension, dimension_toggle,
                      fragment_url, target_id,
                      account_projcodes=None, username=None):
    """Shared renderer for the Wait Times / Job Sizes / Durations tabs."""
    template = 'dashboards/user/partials/jobs_histogram.html'
    if not is_enabled():
        return render_template(template, enabled=False, error=None,
                               mode=mode, machine=None, target_id=target_id,
                               dimension=dimension,
                               dimension_toggle=dimension_toggle)

    filters = _parse_job_filters()
    metric = _parse_metric(_DEFAULT_METRIC_HIST)

    hist = None
    error = None
    try:
        hist = service.jobs_histogram(
            machine, dimension,
            account_projcodes=account_projcodes, username=username,
            **filters,
        )
    except Exception as exc:
        from flask import current_app
        current_app.logger.exception(
            'jobs histogram fragment failed: mode=%s machine=%s dimension=%s',
            mode, machine, dimension,
        )
        error = str(exc)

    chart_svg = generate_jobs_histogram(hist, metric=metric) if hist else None

    return render_template(
        template,
        enabled=True, error=error,
        mode=mode, machine=machine,
        hist=hist, chart_svg=chart_svg,
        metric=metric,
        dimension=dimension, dimension_toggle=dimension_toggle,
        size_dimensions=_SIZE_DIMENSIONS,
        fragment_url=fragment_url,
        target_id=target_id,
        params=_roundtrip_params(machine, target_id),
    )


@bp.route('/<projcode>/by-user')
@login_required
@require_project_access
def by_user_fragment(project):
    """HTMX fragment: per-user usage pie + drillable rows for *project*."""
    if not is_enabled():
        return _render_by_user(mode='project', machine=None,
                               fragment_url=None, jobs_fragment_url=None,
                               target_id='')
    machine = _get_machine_or_400()
    target_id = (request.args.get('target_id') or '').strip() \
        or f'jobs-byuser-{project.projcode}-{machine}'
    return _render_by_user(
        mode='project', machine=machine,
        fragment_url=url_for('jobs.by_user_fragment', projcode=project.projcode),
        jobs_fragment_url=url_for('jobs.jobs_fragment', projcode=project.projcode),
        target_id=target_id,
        account_projcodes=_tree_projcodes(project),
    )


def _project_histogram(project, *, dimension, dimension_toggle, endpoint):
    """Common body of the three project-mode histogram routes."""
    if not is_enabled():
        return _render_histogram(mode='project', machine=None,
                                 dimension=dimension,
                                 dimension_toggle=dimension_toggle,
                                 fragment_url=None, target_id='')
    machine = _get_machine_or_400()
    target_id = (request.args.get('target_id') or '').strip() \
        or f'jobs-{dimension}-{project.projcode}-{machine}'
    return _render_histogram(
        mode='project', machine=machine,
        dimension=dimension, dimension_toggle=dimension_toggle,
        fragment_url=url_for(endpoint, projcode=project.projcode),
        target_id=target_id,
        account_projcodes=_tree_projcodes(project),
    )


@bp.route('/<projcode>/wait-times')
@login_required
@require_project_access
def wait_times_fragment(project):
    """HTMX fragment: queue-wait histogram (dimension pinned to 'wait')."""
    return _project_histogram(project, dimension='wait',
                              dimension_toggle=False,
                              endpoint='jobs.wait_times_fragment')


@bp.route('/<projcode>/job-sizes')
@login_required
@require_project_access
def job_sizes_fragment(project):
    """HTMX fragment: resource-needs histogram with dimension pills."""
    dimension = (request.args.get('dimension') or '').strip()
    if dimension not in _SIZE_DIMENSIONS:
        dimension = _SIZE_DIMENSIONS[0]
    return _project_histogram(project, dimension=dimension,
                              dimension_toggle=True,
                              endpoint='jobs.job_sizes_fragment')


@bp.route('/<projcode>/durations')
@login_required
@require_project_access
def durations_fragment(project):
    """HTMX fragment: elapsed-time histogram (pinned to 'duration')."""
    return _project_histogram(project, dimension='duration',
                              dimension_toggle=False,
                              endpoint='jobs.durations_fragment')
