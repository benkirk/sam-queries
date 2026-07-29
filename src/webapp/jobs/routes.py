"""HTMX fragment routes for the Job History card (per-job rows + aggregations).

Project-mode endpoints (url_prefix ``/dashboards/user/jobs``):

  GET /<projcode>             — per-job table (the Jobs tab; original route)
  GET /<projcode>/by-user     — per-user usage pie + drillable rows
  GET /<projcode>/wait-times  — wait-time histogram (dimension pinned 'wait')
  GET /<projcode>/job-sizes   — resource-needs histogram (?dimension=
                                nodes|cpus|gpus|memory)
  GET /<projcode>/durations   — elapsed-time histogram (pinned 'duration')
  GET /<projcode>/card        — the tab shell itself, re-rendered for a new
                                lookback (?days=); the period pills' target

Jobs-tab query params: ``GET /dashboards/user/jobs/<projcode>``

Query params (all optional unless noted):
  machine   (required) — 'derecho' or 'casper'
  start, end           — YYYY-MM-DD; filters on Job.end
  days                 — lookback in days, one of
                         service.JOBS_WINDOW_CHOICES; outranks start/end
                         (the card's period pills, which can only append
                         to a URL whose window was baked in at render
                         time). Normalized back to start= before anything
                         downstream sees it.
  user                 — limit to a single PBS username
  queue                — limit to a single queue
  qos                  — limit to a single QoS / priority class
                         (e.g. 'premium', 'regular', 'economy',
                         'uncharged', 'special')
  exit_status          — limit to a single PBS exit code as text
                         (e.g. '0' = success, '1', '271')
  page                 — int ≥ 1; default 1
  per_page             — int in [10, 200]; default 50
  sort_by              — any _DEFAULT_COLS key (e.g. 'user', 'start',
                         'elapsed', 'qos', 'cpu_charges'); default None
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

import re
from datetime import date, datetime, timedelta
from typing import Optional

from flask import Blueprint, abort, render_template, request, url_for
from flask_login import login_required

from sam.core.users import User
from sam.projects.projects import Project
from webapp.api.access_control import require_project_access
from webapp.dashboards.charts import (
    generate_jobs_histogram,
    generate_jobs_timeseries_stacked,
    generate_jobs_usage_pie_chart,
    generate_jobs_user_pie_chart,
)
from webapp.extensions import db
from webapp.jobs import service
from webapp.utils.fragments import (
    ModeSpec,
    PanelSpec,
    declare_panels,
    register_panels,
)
from webapp.jobs.scope import (
    MachineJobScope,
    ProjectJobScope,
    UserJobScope,
)
from webapp.utils.scope import resolve_scope_project as _scope_project
from webapp.jobs.session import is_enabled
from webapp.utils.htmx import read_flag
from webapp.utils.rbac import (
    Permission,
    has_permission_any_facility,
    require_permission,
)

bp = Blueprint('jobs', __name__)

# Allowed machine values — keep in lockstep with
# job_history.database.session.VALID_MACHINES. Hardcoded here so the
# route can reject bad input without touching the plugin.
_VALID_MACHINES = {'derecho', 'casper'}

# Default columns in the jobs table. queue/account are left to the drawer
# because the drill contexts pin them; `user` renders by default and is
# suppressed contextually instead (see _user_col_suppressed).
_DEFAULT_COLS = (
    'job_id', 'user', 'name', 'qos', 'start', 'elapsed',
    'numnodes', 'numcpus', 'numgpus',
    'cpu_charges', 'gpu_charges',
)

# Every column rendered as a table header is sortable. The plugin maps
# `job.*` / `charge.*` keys to their SQLAlchemy columns, lookup-backed
# keys (`user`) to a joined name column, and the computed `*_charges`
# keys to `hours × COALESCE(qos_factor, 1)`, so every key in
# _DEFAULT_COLS resolves to a valid ORDER BY at the SQL level. Built
# from _DEFAULT_COLS to stay in lockstep automatically.
_SORT_WHITELIST = set(_DEFAULT_COLS)

# Extra columns revealed in the per-row "expand" drawer. Order is the
# render order in the drawer. `qos_factor` is paired with the `qos` name
# column (now in the main table) so the multiplier sits next to status
# at the top of the drawer rather than buried beside memory_charges.
_VERBOSE_EXTRAS = (
    'exit_status', 'qos_factor',
    'queue',
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


def _parse_days() -> Optional[int]:
    """The card's ``?days=`` lookback pill, or None when not one of ours.

    Lenient like ``_parse_metric``: an unknown or malformed value means
    "no override", never a 400 — the value can arrive from a client's
    localStorage, which may outlive a change to the offered windows.
    """
    raw = (request.args.get('days') or '').strip()
    try:
        days = int(raw)
    except ValueError:
        return None
    return days if days in service.JOBS_WINDOW_CHOICES else None


def _days_start(days: int) -> date:
    """Window start for a ``?days=`` lookback (always relative to today)."""
    return date.today() - timedelta(days=days)


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


def _user_col_suppressed(*, pinned_user, filters, rows, total, per_page):
    """True when the User column would be single-valued noise.

    Three triggers: the mode pins a user (My Jobs), a ``user=`` filter is
    active (the By User / per-band drills), or the whole filtered result
    fits on one page and every row shares one username — the cheap exact
    case; a multi-page uniform view keeps the column rather than paying a
    distinct-count query to find out.
    """
    if pinned_user is not None or filters.get('user'):
        return True
    return bool(
        rows and total is not None and total <= per_page
        and len({r.get('user') for r in rows}) == 1
    )




def _resolve_user_filter() -> tuple:
    """(username, user_id, label) from ``?user=`` or the fk-picker's ``?user_id=``.

    The explorer's user picker stores a SAM user_id; the per-job data is
    keyed by PBS username, so resolve here. A raw ``?user=`` (drill-downs,
    deep links) wins over the picker.
    """
    raw = (request.args.get('user') or '').strip()
    if raw:
        return raw, None, raw
    uid_raw = (request.args.get('user_id') or '').strip()
    if uid_raw.isdigit():
        u = db.session.get(User, int(uid_raw))
        if u is not None:
            return u.username, int(uid_raw), u.username
    return None, None, ''


def _agg_scope(mode, *, username=None, account_projcodes=None):
    """JobScope for an aggregation panel, from the values its renderer holds.

    The per-job table has its own builder (:func:`_table_scope`) because it
    also resolves ``?account=``; the aggregation panels receive an
    already-resolved tree (or a pinned username) from their route.
    """
    if mode == 'project':
        return ProjectJobScope(account_projcodes=account_projcodes)
    if mode == 'user':
        return UserJobScope(username)
    return MachineJobScope()


def _table_scope(mode, *, project=None, pinned_user=None):
    """Build the JobScope for a fragment, plus the two display values around it.

    Returns ``(scope, account_projcodes, user_account)`` — the latter two
    only for the template (the resolved tree, and the narrowing projcode a
    By Project drill selected); the scope carries what the query needs.

    ``?account=`` is a NARROWING filter in every mode, never a widening one:

    * project — narrows within the server-derived tree; an out-of-tree value
      is ignored, so the tree stays the security boundary.
    * user — narrows one's OWN jobs, safe from the client here only because
      the username pin still applies on top.
    * machine — narrows an already VIEW_ALL_JOB_DATA-gated view.
    """
    requested = (request.args.get('account') or '').strip() or None

    if mode == 'project':
        # Expand the (possibly ?scope=-re-rooted) project tree so a parent's
        # rows surface jobs charged to child projcodes — mirrors the
        # Historical Usage rollup.
        account_projcodes = [
            p.projcode
            for p in _scope_project(project).get_descendants(include_self=True)
        ]
        user_account = None
        if requested and requested in account_projcodes:
            user_account = requested
            account_projcodes = [requested]
        return (ProjectJobScope(project, account_projcodes),
                account_projcodes, user_account)

    if mode == 'user':
        return UserJobScope(pinned_user, account=requested), None, requested

    return MachineJobScope(account=requested), None, requested


def _jobs_table_response(*, mode, machine, fragment_url,
                         project=None, pinned_user=None):
    """Shared body of the per-job table fragment across the three modes.

    ``mode`` selects the service family (and with it the scoping rule):
    'project' pins the account filter to *project*'s tree, 'user' hard-pins
    ``pinned_user`` (any client-supplied user is ignored), 'machine' is
    unscoped — its routes are VIEW_ALL_JOB_DATA-gated.
    """
    # Same parse as the aggregation fragments — one boundary, one unit
    # convention. User mode drops the user key entirely (the service
    # families raise if it sneaks in beside the server-side pin).
    filters = _parse_job_filters(include_user=(pinned_user is None))

    page = _parse_pagination()
    sort = _parse_sort()
    offset = (page['n'] - 1) * page['per_page']

    # Always request the verbose column superset so the per-row drawer
    # renders without a second fetch. Plugin still validates each key.
    requested_cols = tuple(_DEFAULT_COLS) + tuple(_VERBOSE_EXTRAS)

    # QoS options for the filter dropdown — sourced from the plugin's
    # job_qos lookup table so a future seed addition flows through
    # without a SAM-side change. Fetched BEFORE search/count so the
    # same list can also be threaded into the service as
    # ``valid_qos_names``: this lets the legacy queue normalizer promote
    # a 'cpu-special' drill-down's suffix to a real QoS filter. Degrades
    # to [] if the plugin call fails or the table is empty.
    try:
        qos_options = service.list_qos_names(machine)
    except Exception:
        from flask import current_app
        current_app.logger.exception(
            'jobs table: list_qos_names failed for machine=%s', machine,
        )
        qos_options = []

    error = None
    rows = []
    total: Optional[int] = None
    scope, account_projcodes, user_account = _table_scope(
        mode, project=project, pinned_user=pinned_user)
    try:
        rows = service.search_jobs(
            machine, scope,
            limit=page['per_page'], offset=offset,
            sort_by=sort['sort_by'], sort_dir=sort['sort_dir'],
            columns=requested_cols, valid_qos_names=qos_options, **filters,
        )
        total = service.count_jobs(
            machine, scope, valid_qos_names=qos_options, **filters,
        )
    except Exception as exc:
        # Catch-all so a transient plugin/DB issue degrades to a banner
        # rather than a 500 on the surrounding page. App logger captures
        # the full traceback for diagnosis.
        from flask import current_app
        current_app.logger.exception(
            'jobs table: search/count failed for mode=%s machine=%s',
            mode, machine,
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

    # Same single-value treatment for the User column. Only the
    # uniformity trigger needs the header badge: the pin/filter cases
    # already surface the name via the fragment heading or the `user:`
    # filter badge.
    user_badge = None
    if _user_col_suppressed(pinned_user=pinned_user, filters=filters,
                            rows=rows, total=total,
                            per_page=page['per_page']):
        visible_cols = [c for c in visible_cols if c != 'user']
        if pinned_user is None and not filters.get('user'):
            (shared_user,) = {r.get('user') for r in rows}
            if shared_user is not None:
                user_badge = {'name': shared_user}

    column_specs = _load_column_specs()

    if user_account:
        filters['account'] = user_account   # header badge (post-service)

    # The caller passes the id of the container that owns this fragment so
    # sort / pagination clicks can swap that same container's innerHTML.
    # Falls back to a generic id when called without one (legacy paths).
    scope_key = (project.projcode if project is not None
                 else (pinned_user or 'all'))
    target_id = (request.args.get('target_id') or '').strip() \
        or f'jobs-{scope_key}-{machine}'

    return render_template(
        'dashboards/user/partials/jobs_fragment.html',
        project=project,
        username=pinned_user,
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
        user_badge=user_badge,
        fragment_url=fragment_url,
        target_id=target_id,
        roundtrip_params=_roundtrip_params(machine, target_id),
        enabled=True,
        error=error,
    )


def _disabled_jobs_table(project=None, username=None):
    """The per-job table partial in disabled mode — never a 404, so a host
    page can hx-get it unconditionally."""
    return render_template(
        'dashboards/user/partials/jobs_fragment.html',
        project=project, username=username, machine=None, rows=[],
        filters={}, page={'n': 1, 'per_page': _DEFAULT_PER_PAGE},
        sort={'sort_by': None, 'sort_dir': 'desc'},
        total=None, visible_cols=[], verbose_extras=[],
        column_specs={}, roundtrip_params={},
        enabled=False, error=None,
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

# Chart metric pills shared by the aggregation tabs. 'charges' is the
# QoS-weighted counterpart of the hour metrics (hours x qos_factor, summed
# by the plugin) — one vocabulary across every panel, so the shared
# `metric:jobs` persist family stays valid. NOTE for consumers: charges are
# NOT proportional to hours. `qos_factor` is a genuine 0.0 for the
# 'uncharged' QoS, so a charges view legitimately shows an empty bar where
# an hours view shows work; templates carry a caption saying so.
_METRICS = ('jobs', 'cpu_hours', 'gpu_hours', 'charges')
_DEFAULT_METRIC_HIST = 'jobs'
_DEFAULT_METRIC_PIE = 'cpu_hours'

# Timeline (Jobs tab) granularity. We coarsen because 180 bars is already
# past what an 18in axis can show — a legibility limit, NOT a cost one. An
# earlier revision of this comment justified the cap with "+54% at 180 bands";
# that measurement timed the periods sequentially, so cache warming rode along
# with band count. Re-measured interleaved (plugin PR #102), 180 bands costs
# ~10% and 730 costs ~65%: real, but it does not bite at anything we render.
# The plugin's own cap is path-dependent — 400 bands on the jobs-scan path,
# 1200 on the daily_summary fast path, which has no CASE ladder at all.
_TIMELINE_PERIODS = ('day', 'week', 'month')
_MAX_TIMELINE_BARS = 120
# Days-per-band, used both to auto-select and to disable over-budget pills.
_PERIOD_DAYS = {'day': 1, 'week': 7, 'month': 30}
# Stack segments carried per band. Matches _HIST_OWNERS_LIMIT so the
# timeline and the histograms agree on how deep "top N" goes.
_TIMELINE_OWNERS_LIMIT = 10

# Job Sizes tab dimension pills; Wait Times / Durations pin their dimension.
# memory = REQUESTED (reqmem); memory_used = consumed (Job.memory);
# memory_wasted = requested − used (negative ⇒ used more than requested).
_SIZE_DIMENSIONS = ('nodes', 'cpus', 'gpus',
                    'memory', 'memory_used', 'memory_wasted')

# Rows shown in the By User table (the pie itself keeps at most 9 + Other).
_BY_USER_LIMIT = 25

# Metric pill → plugin jobs_usage_by sort_by key. Ranking must follow the
# viewed metric or the top-N cut hides e.g. pure-GPU users behind CPU-heavy
# ones (the Derecho GPU-Hours one-wedge bug).
_USAGE_SORT_BY = {
    'jobs': 'job_count',
    'cpu_hours': 'cpu_hours',
    'gpu_hours': 'gpu_hours',
    'charges': 'charges',
}

# Top-N users carried per histogram bucket (chart stack segments + the
# per-band user tier). Matches the fs_scans _AH_TOP_SEGMENTS cap.
_HIST_OWNERS_LIMIT = 10

# Filter query params round-tripped through pill/toggle re-fetches, and —
# where the per-job fragment understands them — carried into row drill-downs.
_ROUNDTRIP_KEYS = (
    'start', 'end', 'user', 'user_id', 'queue', 'qos', 'exit_status',
    'name', 'ignore_case',
    'min_nodes', 'max_nodes', 'min_cpus', 'max_cpus',
    'min_gpus', 'max_gpus', 'min_wait_hours', 'max_wait_hours',
    'min_elapsed_hours', 'max_elapsed_hours',
    'min_reqmem_gb', 'max_reqmem_gb',
    # Plugin-native bounds (bar-drill deep links / envelope replays).
    'min_eligible_secs', 'max_eligible_secs',
    'min_elapsed', 'max_elapsed', 'min_reqmem', 'max_reqmem',
    'min_memory_used', 'max_memory_used',
    'min_memory_wasted', 'max_memory_wasted',
    'scope', 'account',
)

_SECS_PER_HOUR = 3600

# 1 GB = 1024^3 bytes — the plugin's GB↔bytes convention (its CLI's
# _BYTES_PER_GB); the "GB" panel labels match its bucket-label vocabulary.
_BYTES_PER_GB = 1024 ** 3


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


def _parse_signed_int_arg(name: str) -> Optional[int]:
    """Like ``_parse_int_arg`` but negatives are legal (memory_wasted)."""
    raw = (request.args.get(name) or '').strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _parse_job_filters(include_user: bool = True) -> dict:
    """Whitelisted GET parse → service filter kwargs (plugin-native units).

    Human-facing units convert at this boundary and nowhere else:
    ``min/max_wait_hours`` and ``min/max_elapsed_hours`` (hours →
    seconds), ``min/max_reqmem_gb`` (GB → bytes, 1024³).
    Unknown params are ignored; malformed numbers degrade to "no filter".

    Plugin-native bound params (the names a histogram envelope's
    ``min_param``/``max_param`` announce) pass through verbatim — the bar
    drill replays a clicked band without re-deriving display units. They
    parse AFTER the human-unit forms, so if both spell the same bound the
    native one wins. The ``memory_wasted`` pair is signed: negative bounds
    select over-request jobs and must not be clamped.

    ``include_user=False`` omits the ``user`` key entirely — the user-mode
    service family raises if a user filter arrives beside its server-side
    pin.
    """
    f: dict = {
        'start': _parse_date(request.args.get('start')),
        'end':   _parse_date(request.args.get('end')),
        'queue': (request.args.get('queue') or '').strip() or None,
        'qos':   (request.args.get('qos') or '').strip() or None,
        'exit_status': (request.args.get('exit_status') or '').strip() or None,
        'name':  (request.args.get('name') or '').strip() or None,
    }
    days = _parse_days()
    if days is not None:
        # The card's period pill outranks any window baked into the panel
        # URL at render time: the client-side persistence layer can only
        # append ``days`` to a request, never rewrite the ``start`` already
        # in the path, so precedence has to be settled here.
        f['start'] = _days_start(days)
        f['end'] = None
    if include_user:
        f['user'] = _resolve_user_filter()[0]
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
    for arg, target, factor in (
            ('min_elapsed_hours', 'min_elapsed', _SECS_PER_HOUR),
            ('max_elapsed_hours', 'max_elapsed', _SECS_PER_HOUR),
            ('min_reqmem_gb',     'min_reqmem',  _BYTES_PER_GB),
            ('max_reqmem_gb',     'max_reqmem',  _BYTES_PER_GB)):
        v = _parse_float_arg(arg)
        if v is not None:
            f[target] = int(v * factor)
    for key in ('min_eligible_secs', 'max_eligible_secs',
                'min_elapsed', 'max_elapsed',
                'min_reqmem', 'max_reqmem',
                'min_memory_used', 'max_memory_used'):
        v = _parse_int_arg(key)
        if v is not None:
            f[key] = v
    for key in ('min_memory_wasted', 'max_memory_wasted'):
        v = _parse_signed_int_arg(key)
        if v is not None:
            f[key] = v
    return f


def _roundtrip_params(machine: str, target_id: str) -> dict:
    """Raw (display-unit) query params to carry through re-fetches."""
    params = {
        k: request.args.get(k) for k in _ROUNDTRIP_KEYS
        if (request.args.get(k) or '').strip()
    }
    days = _parse_days()
    if days is not None:
        # Normalize the pill to a plain start= at the fragment boundary:
        # in-panel pills, bar drills and explorer deep links all keep
        # speaking start/end, so `days` stays out of _ROUNDTRIP_KEYS and
        # never has to be understood past this point.
        params['start'] = _days_start(days).isoformat()
        params.pop('end', None)
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


def _parse_log() -> bool:
    """``?log=`` — the histograms' log y-axis switch.

    Same predicate as the filesystem-scan distribution histogram's switch
    (both go through ``utils.htmx.is_truthy``), so the two can't drift on
    what counts as checked; unlike that one it is offered on every
    histogram tab, since every job distribution is skewed enough to want it.
    """
    return read_flag(request.args, 'log')


def _period_bar_count(period: str, span_days: int) -> int:
    """Bars a *period* would produce over a *span_days* window."""
    per = _PERIOD_DAYS[period]
    return max(1, -(-span_days // per))   # ceil


def _auto_period(span_days: int) -> str:
    """Coarsest-to-finest: the finest granularity that stays in budget.

    Picked server-side rather than defaulted to 'day' because the explorer
    permits an unbounded window (clearing both date fields is a documented
    opt-in to full history — ~1,500+ days on derecho). A fixed 'day'
    default would either blow the bar budget or hit the plugin's 400-band
    ValueError, both of which read as a broken panel.
    """
    for period in _TIMELINE_PERIODS:      # day, week, month
        if _period_bar_count(period, span_days) <= _MAX_TIMELINE_BARS:
            return period
    return _TIMELINE_PERIODS[-1]


def _parse_period(span_days: int) -> str:
    """``?period=`` for the timeline, clamped to what the window affords.

    An explicit choice wins **only while it fits**: a stale 'day' arriving
    from localStorage against a 5-year window must not be honoured. Lenient
    like ``_parse_metric`` — unknown values mean "no override", never a 400.
    """
    raw = (request.args.get('period') or '').strip()
    if raw in _TIMELINE_PERIODS and \
            _period_bar_count(raw, span_days) <= _MAX_TIMELINE_BARS:
        return raw
    return _auto_period(span_days)


def _period_choices(span_days: int) -> list[dict]:
    """Pill descriptors: which granularities this window can afford.

    Over-budget choices are rendered **disabled with a reason** rather than
    hidden — a pill that silently disappears reads as a bug, and the reason
    ("1,827 bars") is the explanation an analyst needs.
    """
    out = []
    for period in _TIMELINE_PERIODS:
        bars = _period_bar_count(period, span_days)
        out.append({
            'key': period,
            'label': period.capitalize(),
            'bars': bars,
            'enabled': bars <= _MAX_TIMELINE_BARS,
        })
    return out


def _parse_group_by() -> str:
    """Owner dimension for the histograms' User|Project pill.

    ``group_by=user|project`` is the app-wide spelling — the same one the
    status dashboard's queue-load chart uses, which is what lets the
    choice ride the shared view-preference bucket and mean the same thing
    on both. ``owners_by=user|account`` (the plugin's own vocabulary, and
    what this pill emitted before) is still honoured so in-flight links
    and bookmarks keep working. Anything else falls back to 'user'.
    """
    raw = (request.args.get('group_by') or '').strip()
    if raw:
        return 'project' if raw == 'project' else 'user'
    legacy = (request.args.get('owners_by') or '').strip()
    return 'project' if legacy == 'account' else 'user'


def _tree_projcodes(project) -> list:
    """(Scoped) parent + descendants — same tree expansion as jobs_fragment.

    Honors ``?scope=`` re-rooting so every card tab and the explorer agree
    on which slice of the tree they aggregate.
    """
    return [
        p.projcode
        for p in _scope_project(project).get_descendants(include_self=True)
    ]


def panel_relevance(*, mode: str, user_filter=None, account_filter=None,
                    account_projcodes=None) -> dict:
    """Which panels and owner axes can actually vary in this scope.

    A pie of one is noise, and so is a stacked bar whose every band has a
    single owner. Both fall out of one question asked twice: can the
    scope vary along the **user** axis, and along the **project** axis?

    The answer comes only from statically-known pins — the mode's
    server-side pin, the filters baked into the panel URLs, and the
    server-derived project set — never from query results. So the tab
    strip and the panel internals are decided before any query runs, and
    cannot disagree with each other.

    Pure by design (no ``request`` access): callers pass the values that
    actually reach the panels, which is not the same as whatever is in
    ``request.args``. A ``?user=`` on a host *page* URL must not hide the
    By User tab when the card's panels were never filtered by it.

    Args:
        mode: ``'project'`` | ``'machine'`` | ``'user'``. User mode pins
            the username server-side, so its user axis is always fixed.
        user_filter: username the panels are filtered to, if any.
        account_filter: single projcode the panels are narrowed to, if
            any (the By Project row drill).
        account_projcodes: server-derived project set — the (scoped) tree
            in project mode, ``None`` in machine and user mode where the
            scope spans every project the viewer may see.

    Returns a dict of:
        ``show_by_user`` / ``show_by_project`` — render that tab at all.
        ``owners_toggle`` — offer the histograms' User|Project pill.
        ``default_group_by`` — which axis owns the stacked segments when
            the pill isn't offered: the one that can still vary.
        ``owners_enabled`` — group the histogram by owner at all. False
            when both axes are pinned, which is what turns the bars flat
            and lets a band drill straight to its jobs.
    """
    user_pinned = (mode == 'user') or bool(user_filter)
    project_pinned = bool(account_filter) or (
        account_projcodes is not None and len(account_projcodes) <= 1)
    return {
        'show_by_user': not user_pinned,
        'show_by_project': not project_pinned,
        'owners_toggle': not user_pinned and not project_pinned,
        'default_group_by': 'project' if user_pinned else 'user',
        'owners_enabled': not (user_pinned and project_pinned),
    }


#: Plugin metric keys carried on every usage row / totals dict. The full
#: vector, so the remainder below can be shown in whichever metric the panel
#: is ranked by — a charges view whose "Other" row has no charges figure is
#: the defect this replaced.
_USAGE_METRIC_KEYS = ('job_count', 'cpu_hours', 'gpu_hours',
                      'cpu_charges', 'gpu_charges')


def _usage_other(usage) -> Optional[dict]:
    """The upstream limit's remainder: totals are pre-truncation, so any
    positive difference is real usage by entities beyond the row cap.

    Visibility is gated on the count/hours keys only. Charges cannot be the
    sole evidence of a truncated tail — an all-``uncharged``-QoS tail has
    charges 0.0 with real hours, and no row can carry charges without hours —
    so folding them into the test would only add float noise.
    """
    totals = usage.get('totals') or {}
    rows = usage.get('rows') or []
    rem = {
        k: (totals.get(k) or 0) - sum((r.get(k) or 0) for r in rows)
        for k in _USAGE_METRIC_KEYS
    }
    visible = ('job_count', 'cpu_hours', 'gpu_hours')
    return rem if any(rem[k] > 1e-9 for k in visible) else None


#: The two usage rollups are the same panel over a different entity. Each
#: spec is the complete set of things that differ — the template and the
#: renderer below are shared verbatim.
_USAGE_ENTITIES = {
    'user': {
        'key':            'user',
        'label':          'User',
        'form_key':       'byuser',
        'row_prefix':     '-u',
        'sentinel':       'job-user',
        'sentinel_attr':  'data-job-user',
        'drill_param':    'user',
        'modal_endpoint': 'admin_dashboard.user_card',
        'modal_arg':      'username',
        'modal_target':   'userDetailsModal',
        'modal_title':    'View user details',
        'unknown_hint':   'jobs with no recorded user',
        'loading_suffix': "'s jobs",
        'service':        'jobs_usage_by_user',
        'target_stem':    'byuser',
    },
    'project': {
        'key':            'project',
        'label':          'Project',
        'form_key':       'byproj',
        'row_prefix':     '-p',
        'sentinel':       'job-proj',
        'sentinel_attr':  'data-job-project',
        'drill_param':    'account',
        'modal_endpoint': 'user_dashboard.project_details_modal',
        'modal_arg':      'projcode',
        'modal_target':   'projectDetailsModal',
        'modal_title':    'View project details',
        'unknown_hint':   'jobs with no recorded project',
        'loading_suffix': ' jobs',
        'service':        'jobs_usage_by_project',
        'target_stem':    'byproj',
    },
}


def _usage_affordance_permission(entity_key: str, mode: str) -> bool:
    """Whether to render the entity quick-view modal link.

    Never render an affordance that would 403. Projects get one extra
    allowance: in user mode the rows are the pinned user's OWN projects, and
    affiliation already passes the modal route's ``require_project_access``.
    """
    from flask_login import current_user
    if entity_key == 'user':
        return has_permission_any_facility(current_user, Permission.VIEW_USERS)
    return (mode == 'user') or has_permission_any_facility(
        current_user, Permission.VIEW_PROJECTS)


def _render_usage_panel(*, entity_key, mode, machine, fragment_url,
                        jobs_fragment_url, target_id,
                        username=None, account_projcodes=None):
    """Shared renderer for the By User / By Project tabs (all modes).

    Scoping is the scope object's job; what differs here is only the entity
    the rollup groups by, which ``_USAGE_ENTITIES`` carries.
    """
    entity = _USAGE_ENTITIES[entity_key]
    template = 'dashboards/user/partials/jobs_usage_panel.html'
    if not is_enabled():
        return render_template(template, enabled=False, error=None,
                               mode=mode, machine=None, target_id=target_id,
                               entity=entity)

    filters = _parse_job_filters(include_user=(username is None))
    metric = _parse_metric(_DEFAULT_METRIC_PIE)

    usage = None
    error = None
    try:
        usage = getattr(service, entity['service'])(
            machine,
            _agg_scope(mode, username=username,
                       account_projcodes=account_projcodes),
            limit=_BY_USER_LIMIT, sort_by=_USAGE_SORT_BY[metric], **filters,
        )
    except Exception as exc:
        from flask import current_app
        current_app.logger.exception(
            'jobs by-%s fragment failed: mode=%s machine=%s',
            entity_key, mode, machine,
        )
        error = str(exc)

    pie_svg = generate_jobs_usage_pie_chart(
        usage, metric=metric, sentinel_prefix=entity['sentinel']) if usage else None
    other = _usage_other(usage) if usage else None

    return render_template(
        template,
        enabled=True, error=error,
        mode=mode, machine=machine,
        usage=usage, other=other,
        metric=metric, pie_svg=pie_svg,
        fragment_url=fragment_url,
        jobs_fragment_url=jobs_fragment_url,
        target_id=target_id,
        can_view_entity=_usage_affordance_permission(entity_key, mode),
        params=_roundtrip_params(machine, target_id),
        entity=entity,
    )


def _bucket_drill_url(jobs_fragment_url: str, hist: dict, bucket: dict,
                      roundtrip: dict) -> Optional[str]:
    """Per-band URL for the mode's jobs fragment, or None for empty bands.

    Replays the envelope's self-describing bounds verbatim
    (``{min_param: lo, max_param: hi}``, omitting a ``None`` end — the
    open side of an unbounded band, including the wasted dimension's
    negative 'over request' band) plus the pane's round-trip filters.
    A pane param spelling the same native bound as the band is dropped —
    the clicked band's meaning wins. The template appends its own
    ``target_id``.
    """
    if not bucket.get('job_count'):
        return None
    from urllib.parse import urlencode
    params = {}
    if bucket.get('lo') is not None:
        params[hist['min_param']] = bucket['lo']
    if bucket.get('hi') is not None:
        params[hist['max_param']] = bucket['hi']
    for k, v in roundtrip.items():
        if k != 'target_id' and k not in params:
            params[k] = v
    return f'{jobs_fragment_url}?{urlencode(params)}'


def _trim_empty_edge_bands(hist):
    """Drop leading AND trailing all-zero bands from a histogram envelope.

    The plugin returns a complete, ordered bucket vector — zeros included —
    which is what keeps the x-axis stable as filters change, and that's
    worth preserving *inside* a distribution: an interior zero is a gap in
    the data, a finding in its own right, and it stays.

    The edges are different, and structural rather than a filter artifact:

    * Leading. On Job Sizes every job uses at least one node and one CPU,
      so those dimensions can never fill their 0 band. GPUs are the
      exception that keeps the rule honest — there the 0 band holds the
      CPU-only jobs, so it survives on its own merit.
    * Trailing. The bucket tables are sized for the largest machine the
      plugin serves (``CPU_HIST_BUCKETS`` runs to >32768), so a few-hundred
      node machine spends the top of every node/CPU/GPU/memory axis on
      bands nothing can ever land in.

    Emptiness is judged on ``job_count`` alone, never the displayed metric,
    so flipping Jobs / CPU-hours / GPU-hours can't shift the axis under the
    viewer (a band of real jobs charging no GPU-hours stays put).

    An all-zero vector trims to **nothing**: there is no distribution, and
    the caller renders an empty state rather than an axis with no bars.

    The tradeoff, recorded because it partly reverses the zero-filled
    vector's original intent: two panes side by side (Derecho vs Casper
    subtabs, or before/after a filter change) can now have different axes.
    Preserving interior zeros is what keeps the shape *within* the
    populated range comparable.

    Returns a shallow copy — the envelope is a shared cache entry and must
    never be mutated — or *hist* itself when there's nothing to trim.
    """
    buckets = (hist or {}).get('buckets') or []
    lead = 0
    while lead < len(buckets) and not (buckets[lead].get('job_count') or 0):
        lead += 1
    if lead == len(buckets):
        return dict(hist, buckets=[]) if buckets else hist
    tail = len(buckets)
    while tail > lead and not (buckets[tail - 1].get('job_count') or 0):
        tail -= 1
    if not lead and tail == len(buckets):
        return hist
    return dict(hist, buckets=buckets[lead:tail])


def _filter_span_days(filters) -> Optional[int]:
    """Window width in days from the parsed filters, or None if open-ended.

    None is the explorer's "both date fields cleared" case — a documented
    opt-in to full history, which is ~1,500+ days on derecho. The caller
    treats it as the widest possible window so the granularity starts
    coarse; the real span is recovered from the envelope afterwards.
    """
    start, end = filters.get('start'), filters.get('end')
    if not start:
        return None
    try:
        start_d = date.fromisoformat(str(start)[:10])
        end_d = date.fromisoformat(str(end)[:10]) if end else date.today()
    except ValueError:
        return None
    return max(1, (end_d - start_d).days + 1)


def _band_drill_url(jobs_fragment_url, band, roundtrip):
    """Per-band URL for the mode's jobs fragment, or None for empty bands.

    Unlike a histogram band — which replays through the envelope's
    ``min_param``/``max_param`` — a time band replays through ``start`` and
    ``end``, because the window filters ARE this dimension. The band's own
    (window-clipped) dates therefore OVERRIDE the pane's, rather than
    narrowing alongside them.
    """
    if not band.get('job_count'):
        return None
    from urllib.parse import urlencode
    params = dict(roundtrip)
    params['start'] = band['start']
    params['end'] = band['end']
    return f'{jobs_fragment_url}?{urlencode(params)}'


def _render_timeline(*, mode, machine, fragment_url, target_id,
                     jobs_fragment_url=None,
                     account_projcodes=None, username=None):
    """Renderer for the Jobs tab's activity timeline.

    The one panel with a time axis. Upstream cost swings ~500x on whether
    the filter set lets the plugin serve it from ``daily_summary`` instead
    of scanning ``jobs`` (see ``service.jobs_timeseries``), and the filters
    that force the scan are the explorer's — so the cards keep it behind a
    collapse rather than firing it with the table, while the explorer, whose
    whole point is the filter panel, opens it.
    """
    template = 'dashboards/user/partials/jobs_timeline.html'
    if not is_enabled():
        return render_template(template, enabled=False, error=None,
                               mode=mode, machine=None, target_id=target_id)

    filters = _parse_job_filters(include_user=(username is None))
    metric = _parse_metric(_DEFAULT_METRIC_HIST)
    # An open-ended window starts coarse; the true span comes back in the
    # envelope and drives the pills below.
    span_days = _filter_span_days(filters)
    period = _parse_period(span_days if span_days is not None else 10 ** 6)

    # Same relevance rule as the histograms, so the stack can never be keyed
    # on an axis the scope has already pinned to a single value.
    rel = panel_relevance(
        mode=mode,
        user_filter=username or filters.get('user'),
        account_filter=(request.args.get('account') or '').strip() or None,
        account_projcodes=account_projcodes,
    )
    owners_toggle = rel['owners_toggle']
    group_by = _parse_group_by() if owners_toggle else rel['default_group_by']
    owners_by = 'account' if group_by == 'project' else 'user'
    entity = _USAGE_ENTITIES[group_by]

    ts = None
    error = None
    try:
        ts = service.jobs_timeseries(
            machine, period,
            _agg_scope(mode, username=username,
                       account_projcodes=account_projcodes),
            owners_limit=(_TIMELINE_OWNERS_LIMIT
                          if rel['owners_enabled'] else None),
            owners_sort_by=_USAGE_SORT_BY[metric],
            owners_by=owners_by,
            **filters,
        )
    except Exception as exc:
        from flask import current_app
        current_app.logger.exception(
            'jobs timeline fragment failed: mode=%s machine=%s period=%s',
            mode, machine, period,
        )
        error = str(exc)

    bands = (ts or {}).get('bands') or []
    has_bands = any(b.get('job_count') for b in bands)

    # Recover the REAL span from the resolved window so the pills reflect
    # what this window can actually afford, even when the caller supplied
    # no dates at all.
    if ts and ts.get('start') and ts.get('end'):
        try:
            span_days = (date.fromisoformat(ts['end'])
                         - date.fromisoformat(ts['start'])).days + 1
        except ValueError:
            pass

    # Legend entries open the entity's quick-view MODAL, not a row sentinel:
    # this chart lives in the Jobs pane while the By User / By Project rows
    # live in their own lazily-loaded panes, and openEntityRow scopes its
    # lookup to the clicked chart's pane — so a row sentinel here is a
    # silent no-op. Gate on the same affordance permission the By User /
    # By Project tables use, so we never render a link that would 403.
    from flask_login import current_user
    if group_by == 'user':
        link_entities = has_permission_any_facility(
            current_user, Permission.VIEW_USERS)
    else:
        link_entities = (mode == 'user') or has_permission_any_facility(
            current_user, Permission.VIEW_PROJECTS)
    chart_svg = (generate_jobs_timeseries_stacked(
        ts, metric=metric, period=period,
        entity_kind=group_by,
        link_entities=link_entities) if has_bands else None)

    params = _roundtrip_params(machine, target_id)
    band_drills = None
    if has_bands and jobs_fragment_url:
        band_drills = [_band_drill_url(jobs_fragment_url, b, params)
                       for b in bands]

    # After the drill URLs — the jobs fragment understands neither.
    if group_by != 'user':
        params = dict(params, group_by=group_by)
    params = dict(params, period=period)

    return render_template(
        template,
        enabled=True, error=error,
        mode=mode, machine=machine,
        ts=ts, bands=bands, chart_svg=chart_svg,
        metric=metric, metrics=_METRICS,
        period=period, period_choices=_period_choices(span_days or 1),
        max_bars=_MAX_TIMELINE_BARS,
        group_by=group_by, owners_toggle=owners_toggle,
        entity=entity, link_entities=link_entities,
        fragment_url=fragment_url,
        band_drills=band_drills,
        target_id=target_id,
        params=params,
    )


def _render_histogram(*, mode, machine, dimension, dimension_toggle,
                      fragment_url, target_id,
                      jobs_fragment_url=None,
                      account_projcodes=None, username=None):
    """Shared renderer for the Wait Times / Job Sizes / Durations tabs."""
    template = 'dashboards/user/partials/jobs_histogram.html'
    if not is_enabled():
        return render_template(template, enabled=False, error=None,
                               mode=mode, machine=None, target_id=target_id,
                               dimension=dimension,
                               dimension_toggle=dimension_toggle)

    # User mode drops the user key entirely rather than letting the scope
    # overwrite it: the pin owns that dimension, and a crafted ?user= must
    # be ignored, not rejected with a 500 on the surrounding page. Same
    # convention as the table and By Project fragments.
    filters = _parse_job_filters(include_user=(username is None))
    metric = _parse_metric(_DEFAULT_METRIC_HIST)
    log_on = _parse_log()

    # Who owns the stacked segments and the per-band tier — the same
    # relevance rule that decides the tab strip, so a pane can never
    # stack by an axis its scope has pinned to a single value. The pill
    # is offered only where BOTH axes can vary; elsewhere the param is
    # ignored, so a crafted URL can't flip a single-project pane into a
    # redundant per-project breakdown.
    rel = panel_relevance(
        mode=mode,
        user_filter=username or filters.get('user'),
        account_filter=(request.args.get('account') or '').strip() or None,
        account_projcodes=account_projcodes,
    )
    owners_toggle = rel['owners_toggle']
    group_by = _parse_group_by() if owners_toggle else rel['default_group_by']
    # The plugin's word for a project owner is 'account'; the URL and the
    # shared view-preference bucket speak 'project'. Translate here, at
    # the one boundary between the two vocabularies.
    owners_by = 'account' if group_by == 'project' else 'user'

    hist = None
    error = None
    try:
        hist = service.jobs_histogram(
            machine, dimension,
            _agg_scope(mode, username=username,
                       account_projcodes=account_projcodes),
            # Both axes pinned ⇒ every band has exactly one owner, so
            # skip the grouping entirely: flat bars, and a band drills
            # straight to its jobs instead of through a one-row tier.
            owners_limit=_HIST_OWNERS_LIMIT if rel['owners_enabled'] else None,
            # Which top-N survives must follow the displayed metric —
            # hours-ranked owners cover ~1% of band GPU-hours (plugin
            # PR #100 review data), rendering a GPU stack as all-"Other".
            owners_sort_by=_USAGE_SORT_BY[metric],
            owners_by=owners_by,
            **filters,
        )
    except Exception as exc:
        from flask import current_app
        current_app.logger.exception(
            'jobs histogram fragment failed: mode=%s machine=%s dimension=%s',
            mode, machine, dimension,
        )
        error = str(exc)

    # Trim BEFORE the chart and the drill list: the bar sentinels
    # (#jh-bar-<i>) and the table's data-jh-bucket indices are both
    # positions in this bucket vector, so all three have to see the
    # same one. An all-zero distribution trims to no bands at all, which
    # is how the template knows to render one empty state instead of a
    # bar-less axis over a table of zeros.
    hist = _trim_empty_edge_bands(hist)
    has_bands = bool((hist or {}).get('buckets'))

    chart_svg = (generate_jobs_histogram(hist, metric=metric, log_y=log_on)
                 if has_bands else None)
    params = _roundtrip_params(machine, target_id)

    # One drill URL per band (None for empty bands) — computed here, not
    # in the template, so the envelope's min_param/max_param replay stays
    # in one place. A parallel list rather than mutating hist: the
    # envelope is a shared cache entry.
    bucket_drills = None
    if has_bands and jobs_fragment_url:
        bucket_drills = [
            _bucket_drill_url(jobs_fragment_url, hist, b, params)
            for b in hist.get('buckets') or []
        ]

    # Round-trip the non-default owner dimension and y-scale through the
    # metric / dimension pills' hx-include form (AFTER the drill URLs — the
    # jobs fragments take neither). The switch itself spells ?log= out in
    # its own URL, which wins over this stale copy: Werkzeug reads the
    # first value and htmx appends included params after the hx-get query.
    if group_by != 'user':
        params = dict(params, group_by=group_by)
    if log_on:
        params = dict(params, log='1')

    # Same affordance gates as the By User / By Project tables — never
    # render an entity quick-view link that would 403.
    from flask_login import current_user
    can_view_users = has_permission_any_facility(
        current_user, Permission.VIEW_USERS)
    can_view_projects = (mode == 'user') or has_permission_any_facility(
        current_user, Permission.VIEW_PROJECTS)

    return render_template(
        template,
        enabled=True, error=error,
        mode=mode, machine=machine,
        hist=hist, chart_svg=chart_svg,
        metric=metric, log_on=log_on,
        dimension=dimension, dimension_toggle=dimension_toggle,
        size_dimensions=_SIZE_DIMENSIONS,
        group_by=group_by, owners_toggle=owners_toggle,
        can_view_users=can_view_users,
        can_view_projects=can_view_projects,
        fragment_url=fragment_url,
        bucket_drills=bucket_drills,
        target_id=target_id,
        params=params,
    )


# ---------------------------------------------------------------------------
# Explorer full view (project mode) + machine-wide family (operator surfaces)
# ---------------------------------------------------------------------------

# Row-count choices offered by the explorer's per-page selector.
_PER_PAGE_OPTIONS = (25, 50, 100, 200)


def _machine_or_404(machine: str) -> str:
    """Validate a path ``<machine>`` against the warmed engines → 404 unknown.

    Dynamic (not the static _VALID_MACHINES): the machine-wide routes only
    make sense for machines the plugin actually serves right now.
    """
    m = (machine or '').strip().lower()
    if m not in service.job_history_machines():
        abort(404)
    return m


def _qos_options_safe(machine: str) -> list:
    """QoS names for the explorer's select; [] on any plugin hiccup."""
    try:
        return service.list_qos_names(machine)
    except Exception:
        from flask import current_app
        current_app.logger.exception(
            'jobs explorer: list_qos_names failed for machine=%s', machine,
        )
        return []


def _panel_filters(machine: str) -> dict:
    """Raw (display-unit) filter values for the explorer's sidebar panel."""
    username, user_id, user_label = _resolve_user_filter()
    per_page = _parse_pagination()['per_page']
    start = (request.args.get('start') or '').strip()
    end = (request.args.get('end') or '').strip()
    if not start and not end:
        # Unbounded windows are the expensive path (~200 s machine-wide
        # vs ~0.6 s per month) — default the explorer to the same window
        # the cards use. The card's "Open full view" link hands its
        # current pill over as ?days=, so the explorer opens on the window
        # the user was already looking at. The field is visible in the
        # panel; clearing it opts into the full history explicitly.
        start = _days_start(
            _parse_days() or service.DEFAULT_JOBS_WINDOW_DAYS
        ).isoformat()
    return {
        'start': start,
        'end':   end,
        'user': username or '',
        'user_id': user_id or '',
        'user_label': user_label,
        'queue': (request.args.get('queue') or '').strip(),
        'qos':   (request.args.get('qos') or '').strip(),
        'exit_status': (request.args.get('exit_status') or '').strip(),
        'name':  (request.args.get('name') or '').strip(),
        'ignore_case': request.args.get('ignore_case') in ('1', 'true', 'on'),
        'min_nodes': _parse_int_arg('min_nodes'),
        'max_nodes': _parse_int_arg('max_nodes'),
        'min_cpus':  _parse_int_arg('min_cpus'),
        'max_cpus':  _parse_int_arg('max_cpus'),
        'min_gpus':  _parse_int_arg('min_gpus'),
        'max_gpus':  _parse_int_arg('max_gpus'),
        'min_wait_hours': _parse_float_arg('min_wait_hours'),
        'max_wait_hours': _parse_float_arg('max_wait_hours'),
        'min_elapsed_hours': _parse_float_arg('min_elapsed_hours'),
        'max_elapsed_hours': _parse_float_arg('max_elapsed_hours'),
        'min_reqmem_gb': _parse_float_arg('min_reqmem_gb'),
        'max_reqmem_gb': _parse_float_arg('max_reqmem_gb'),
        'per_page': per_page,
        'qos_options': _qos_options_safe(machine),
    }


# Element ids for the explorer's card. Fixed rather than request-supplied:
# the page render and every filter-submit re-render must agree on them, and
# the table's container id (``<cid>-jobs``) is what jobs_fragment.html
# derives the chip placeholder and panel-form ids from.
_EXPLORER_CID = 'jobs-explore'
_EXPLORER_TABLIST = 'jobsExploreTabs'

# Tab keys the card understands, in strip order. Whitelisted because the
# value picks which panel fires its query on render.
_CARD_TABS = ('jobs', 'byuser', 'byproj', 'wait', 'sizes', 'durations')


def _parse_active_tab() -> str:
    """``?active_tab=`` — which card tab the viewer has open.

    Server-side input rather than something the client restores after the
    swap: the explorer re-renders the whole card on every Apply, and a
    card that always came back on Jobs would fetch the chart the viewer
    asked for *and* a per-job table nobody wants (16 s+ machine-wide on
    Casper). Unknown values fall back to Jobs.
    """
    tab = (request.args.get('active_tab') or '').strip()
    return tab if tab in _CARD_TABS else 'jobs'

# Panel-shaping filters the explorer bakes into every panel URL, in the
# display units _parse_job_filters reads. `ignore_case` rides along only
# with a name glob; `machine`, `target_id` and `projcode` are the macro's
# to supply. `account` is deliberately absent: it narrows the per-job
# table but not the aggregations, so baking it in would hide the By
# Project tab while its neighbours still counted every project.
_EXPLORER_PANEL_KEYS = (
    'start', 'end', 'user', 'queue', 'qos', 'exit_status', 'name',
    'min_nodes', 'max_nodes', 'min_cpus', 'max_cpus', 'min_gpus', 'max_gpus',
    'min_wait_hours', 'max_wait_hours',
    'min_elapsed_hours', 'max_elapsed_hours',
    'min_reqmem_gb', 'max_reqmem_gb',
)


def _explorer_panel_params(panel: dict, scope: Optional[str] = None,
                           include_user: bool = True) -> dict:
    """The filter panel's current values, as panel-URL query params.

    ``include_user=False`` in user mode: the username is pinned
    server-side on every fragment, so a client-supplied one is already
    overwritten. Baking it into the panel URLs would put a parameter on
    screen that looks like it filters and does not.
    """
    params = {k: panel[k] for k in _EXPLORER_PANEL_KEYS
              if panel.get(k) not in (None, '')
              and (include_user or k != 'user')}
    if panel.get('name') and panel.get('ignore_case'):
        params['ignore_case'] = '1'
    if scope:
        params['scope'] = scope
    return params


def _explorer_facets(mode: str, machine: str, panel: dict, project=None,
                     username=None) -> Optional[dict]:
    """Facet counts for the chip strip, under the current filter set.

    Computed by the shell rather than out-of-band from the table, because
    the table is one tab of six now: a viewer who applies a filter while
    looking at a chart would otherwise be left with chip counts from the
    previous filter set. It also stops the strip recomputing on a sort or
    page click, neither of which can change a facet count.

    Degrades to no chips on any failure — the panels are the content.
    """
    try:
        return service.jobs_facets(
            machine,
            _agg_scope(mode, username=username,
                       account_projcodes=(_tree_projcodes(project)
                                          if project is not None else None)),
            valid_qos_names=panel.get('qos_options') or (),
            **_parse_job_filters(include_user=(username is None)),
        )
    except Exception:
        from flask import current_app
        current_app.logger.exception(
            'jobs explorer: facets failed for mode=%s machine=%s',
            mode, machine,
        )
        return None


def _explorer_card_context(*, mode: str, machine: str, project=None,
                           scope: Optional[str] = None) -> tuple:
    """(panel, card context) for the explorer, in every mode.

    Shared by the three ``explore_*`` page routes and the three ``/card``
    routes when the filter panel submits to them, so a deep link and an
    Apply produce the same card. The panels are lazy, so a visit that
    only reads the table costs exactly what it did before the charts
    moved in.
    """
    from flask_login import current_user
    username = current_user.username if mode == 'user' else None
    panel = _panel_filters(machine)
    panel_params = _explorer_panel_params(panel, scope,
                                          include_user=(username is None))
    active_tab = _parse_active_tab()
    panel['active_tab'] = active_tab      # the form round-trips it back
    return panel, _card_context(
        active_tab=active_tab,
        mode=mode, machine=machine,
        cid=_EXPLORER_CID, tablist_id=_EXPLORER_TABLIST,
        projcode=(project.projcode if project is not None else None),
        panel_params=panel_params,
        # The table takes one param the aggregations have no use for.
        jobs_params=dict(panel_params, per_page=panel['per_page']),
        account_projcodes=(_tree_projcodes(project)
                           if project is not None else None),
        facet_chips=_explorer_facets(mode, machine, panel,
                                     project=project, username=username),
        facet_filters=_parse_job_filters(include_user=(username is None)),
        facet_form_id=f'jobs-filters-panel-{_EXPLORER_CID}-jobs',
        # The filter panel's own date fields own the window here; a pill
        # group beside them would be a second control for one setting.
        days=None,
        days_persist_id=None,
        show_pills=False,
        show_explore_link=False,
        # Open on the explorer: this is the page whose whole point is the
        # filter panel, so the chart that responds to it should be visible
        # without a click. The cards keep it collapsed — see jobs_card.html.
        timeline_open=True,
        load_trigger='load once',
    )


def _user_search_url() -> str:
    """The fk-picker search endpoint for the user filter (context='fk')."""
    return url_for('admin_dashboard.htmx_search_users', context='fk')


def _explorer_card_url(mode: str, machine: str, *, projcode=None,
                       scope=None) -> str:
    """Where the explorer's filter form submits.

    An Apply re-renders the card shell, which is the only way the six
    panels pick up a new filter set: each bakes its own into its hx-get
    at render time. Exactly what a period pill does on the cards, with a
    bigger param set. ``surface=explorer`` is how the shell route knows
    to read the filter panel instead of a ``?days=`` lookback.
    """
    if mode == 'machine':
        return url_for('jobs.jobs_card_machine_fragment', machine=machine,
                       surface='explorer')
    if mode == 'user':
        return url_for('jobs.jobs_card_user_fragment', machine=machine,
                       surface='explorer')
    return url_for('jobs.jobs_card_fragment', projcode=projcode,
                   machine=machine, scope=scope, surface='explorer')


@bp.route('/<projcode>/explore')
@login_required
@require_project_access
def explore_page(project):
    """Standalone full-page jobs explorer for *project* (project mode).

    The filter panel above the card; the card's six tabs below it, each
    lazy-loading its fragment with the panel's params — its Jobs tab is
    the per-job table, so nothing is duplicated. ``?scope=<child>``
    re-roots to a subtree (same-tree validated).
    """
    if not is_enabled():
        return render_template(
            'dashboards/user/jobs_explore_page.html',
            mode='project', enabled=False, project=project,
            scoped_project=project, machine=None,
        )
    machine = _get_machine_or_400()
    scoped = _scope_project(project)
    scope = scoped.projcode if scoped.projcode != project.projcode else None
    panel, card = _explorer_card_context(
        mode='project', machine=machine, project=project, scope=scope,
    )
    return render_template(
        'dashboards/user/jobs_explore_page.html',
        mode='project', enabled=True,
        project=project, scoped_project=scoped, machine=machine,
        scope=scope,
        card_url=_explorer_card_url('project', machine,
                                    projcode=project.projcode, scope=scope),
        filters=panel, user_search_url=_user_search_url(),
        per_page_options=_PER_PAGE_OPTIONS, card=card,
    )


@bp.route('/machine/<machine>/explore')
@login_required
@require_permission(Permission.VIEW_ALL_JOB_DATA)
def explore_machine_page(machine):
    """Standalone full-page jobs explorer across an ENTIRE machine.

    Machine mode — unscoped, elevated. Same page template as project mode,
    parameterized by ``mode='machine'`` + the machine fragment URL.
    """
    if not is_enabled():
        return render_template(
            'dashboards/user/jobs_explore_page.html',
            mode='machine', enabled=False, machine=machine,
        )
    machine = _machine_or_404(machine)
    panel, card = _explorer_card_context(mode='machine', machine=machine)
    return render_template(
        'dashboards/user/jobs_explore_page.html',
        mode='machine', enabled=True, machine=machine,
        card_url=_explorer_card_url('machine', machine),
        filters=panel, user_search_url=_user_search_url(),
        per_page_options=_PER_PAGE_OPTIONS, card=card,
    )


# ---------------------------------------------------------------------------
# User family ("My Jobs") — hard-pinned to the logged-in user
# ---------------------------------------------------------------------------
#
# @login_required ONLY — no permission gate. Safe because every route pins
# user=current_user.username server-side (the service families raise on a
# caller-supplied user), so a client-appended ?user=<other> changes nothing.
# Mirror of the disk_scans pinned-owner rule.

@bp.route('/user/<machine>/explore')
@login_required
def explore_user_page(machine):
    """Standalone jobs explorer pinned to the logged-in user ("My Jobs").

    Same page template, ``mode='user'``: the filter panel omits the user
    picker, and the fragment routes re-pin the username server-side on
    every fetch — a hand-edited ?user= in the URL changes nothing.
    """
    from flask_login import current_user
    if not is_enabled():
        return render_template(
            'dashboards/user/jobs_explore_page.html',
            mode='user', enabled=False, machine=machine,
        )
    machine = _machine_or_404(machine)
    panel, card = _explorer_card_context(mode='user', machine=machine)
    return render_template(
        'dashboards/user/jobs_explore_page.html',
        mode='user', enabled=True, machine=machine,
        username=current_user.username,
        card_url=_explorer_card_url('user', machine),
        filters=panel, user_search_url=_user_search_url(),
        per_page_options=_PER_PAGE_OPTIONS, card=card,
    )


# ---------------------------------------------------------------------------
# Card shell (period pills) — one route per mode
# ---------------------------------------------------------------------------
#
# Each panel bakes its window into its own hx-get URL at render time, so
# changing the period means re-rendering the shell that owns those URLs.
# These routes do that and nothing else — no plugin queries — so a pill
# click costs one cheap render and the panels re-fetch lazily as they are
# shown. Gating mirrors each mode's panel family.

# Shell params echoed straight into element ids and hx-target selectors.
_ID_ARG_RE = re.compile(r'^[A-Za-z0-9_-]{1,64}$')


def _id_arg(name: str, default: Optional[str] = None) -> Optional[str]:
    """Read an id-shaped query arg; 400 on anything that isn't id-safe."""
    raw = (request.args.get(name) or '').strip()
    if not raw:
        return default
    if not _ID_ARG_RE.match(raw):
        abort(400, f'{name} must match {_ID_ARG_RE.pattern}')
    return raw


def _card_context(*, mode: str, machine: str, panel_params=None,
                  account_projcodes=None, **extra) -> dict:
    """Template context for the jobs card shell.

    The id-shaped args are echoed straight back into element ids and
    hx-target selectors, so they go through ``_id_arg``. Everything that
    shapes a panel URL travels in ``panel_params`` / ``jobs_params``,
    which the caller owns — see ``_render_card_shell`` (period pills) and
    ``_explorer_card_context`` (the full-view filter panel).

    Tab visibility is derived from ``panel_params``, not ``request.args``:
    what a panel is filtered by is exactly what its URL carries.
    """
    panel_params = panel_params or {}
    rel = panel_relevance(
        mode=mode,
        user_filter=panel_params.get('user'),
        account_filter=panel_params.get('account'),
        account_projcodes=account_projcodes,
    )
    ctx = {
        'mode': mode,
        'machine': machine,
        'cid': _id_arg('cid', 'jobs-card'),
        'tablist_id': _id_arg('tablist_id', 'jobsCardTabs'),
        'days_persist_id': _id_arg('days_persist_id'),
        'panel_params': panel_params,
        'show_by_user': rel['show_by_user'],
        'show_by_project': rel['show_by_project'],
    }
    ctx.update(extra)
    return ctx


def _render_card_shell(*, mode: str, machine: str, **extra):
    """Re-render the jobs card bound to the requested ``?days=`` window."""
    days = _parse_days() or service.DEFAULT_JOBS_WINDOW_DAYS
    # A pill is a pure lookback from today, so it drops any end date the
    # host page baked in rather than re-anchoring the window inside it.
    panel_params = dict(extra.pop('panel_params', None) or {})
    panel_params['start'] = _days_start(days).isoformat()
    panel_params.pop('end', None)
    return render_template(
        'dashboards/user/partials/jobs_card_shell.html',
        **_card_context(
            mode=mode, machine=machine,
            days=days,
            panel_params=panel_params,
            account_projcodes=extra.pop('account_projcodes', None),
            # The clicked card is on screen, so this fires straight away; a
            # sibling card refreshed inside a hidden machine subtab waits
            # until it is actually shown instead of querying for nobody.
            load_trigger='intersect once',
            **extra,
        ),
    )


def _is_explorer_surface() -> bool:
    """``surface=explorer`` — the filter panel submitted, not a pill.

    Both re-render the same shell; they differ only in where the window
    (and, on the explorer, the rest of the filter set) comes from.
    """
    return (request.args.get('surface') or '').strip() == 'explorer'


def _render_explorer_shell(*, mode: str, machine: str, project=None,
                           scope: Optional[str] = None):
    _panel, card = _explorer_card_context(
        mode=mode, machine=machine, project=project, scope=scope,
    )
    return render_template(
        'dashboards/user/partials/jobs_card_shell.html', **card,
    )


# ---------------------------------------------------------------------------
# Per-mode request context
# ---------------------------------------------------------------------------

def _machine_for(mode: str, arg) -> Optional[str]:
    """Resolve the machine for *mode*, or ``None`` when the plugin is off.

    Project mode carries it in ``?machine=`` (one card, many resources);
    the path-``<machine>`` families carry it in the URL and validate against
    the warmed engines. When the plugin is disabled we return ``None``
    WITHOUT validating — otherwise a disabled deployment would 400/404
    instead of rendering the "unavailable" banner the card expects.
    """
    if not is_enabled():
        return None
    return _get_machine_or_400() if mode == 'project' else _machine_or_404(arg)


def _jobs_ctx(mode: str, arg) -> dict:
    """The per-request facts every jobs panel needs.

    ``target_suffix`` is the tail of each panel's default ``target_id``; the
    panel supplies the stem (``byuser`` / ``byproj`` / the histogram
    dimension). Keeping the two apart is what lets one spec table serve
    ids like ``jobs-byuser-SCSG0001-derecho`` and ``jobs-wait-machine-casper``.
    """
    machine = _machine_for(mode, arg)

    if mode == 'project':
        return {
            'machine': machine,
            'project': arg,
            'username': None,
            'account_projcodes': _tree_projcodes(arg),
            'scope': (request.args.get('scope') or '').strip() or None,
            'target_suffix': f'{arg.projcode}-{machine}',
        }

    from flask_login import current_user
    username = current_user.username if mode == 'user' else None
    return {
        'machine': machine,
        'project': None,
        'username': username,
        'account_projcodes': None,
        'scope': None,
        'target_suffix': f'{mode}-{machine}',
    }


def _target_id(ctx: dict, stem: str) -> str:
    """``?target_id=`` round-tripped, else the panel's default for this mode."""
    return ((request.args.get('target_id') or '').strip()
            or f"jobs-{stem}-{ctx['target_suffix']}")


def _panel_dimension(default: str, toggle: bool) -> str:
    """The histogram dimension: a whitelisted ``?dimension=`` when the panel
    offers the pills, else the panel's fixed one."""
    if not toggle:
        return default
    dimension = (request.args.get('dimension') or '').strip()
    return dimension if dimension in _SIZE_DIMENSIONS else _SIZE_DIMENSIONS[0]


# ---------------------------------------------------------------------------
# Panel adapters — registrar calling convention over the shared renderers
# ---------------------------------------------------------------------------
#
# The registrar hands every panel the same five arguments; these translate
# that into what each renderer already wanted. They are also where the
# "plugin disabled" degradation lives: machine is None, and each renderer
# already knows to draw its unavailable banner rather than query.

def _panel_jobs_table(ctx, fragment_url, *, mode, scope_for, log_label, **_kw):
    """HTMX fragment: the per-job table."""
    if ctx['machine'] is None:
        return _disabled_jobs_table(project=ctx['project'],
                                    username=ctx['username'])
    return _jobs_table_response(
        mode=mode, machine=ctx['machine'], fragment_url=fragment_url,
        project=ctx['project'], pinned_user=ctx['username'],
    )


def _panel_usage(ctx, fragment_url, *, mode, scope_for, log_label,
                 entity_key, jobs_fragment_url=None, **_kw):
    """HTMX fragment: a per-entity usage pie + drillable rows."""
    entity = _USAGE_ENTITIES[entity_key]
    if ctx['machine'] is None:
        return _render_usage_panel(entity_key=entity_key, mode=mode,
                                   machine=None, fragment_url=None,
                                   jobs_fragment_url=None, target_id='')
    return _render_usage_panel(
        entity_key=entity_key, mode=mode, machine=ctx['machine'],
        fragment_url=fragment_url, jobs_fragment_url=jobs_fragment_url,
        target_id=_target_id(ctx, entity['target_stem']),
        username=ctx['username'],
        account_projcodes=ctx['account_projcodes'],
    )


def _panel_histogram(ctx, fragment_url, *, mode, scope_for, log_label,
                     dimension, dimension_toggle=False,
                     jobs_fragment_url=None, **_kw):
    """HTMX fragment: one of the three distribution histograms."""
    dim = _panel_dimension(dimension, dimension_toggle)
    if ctx['machine'] is None:
        return _render_histogram(mode=mode, machine=None, dimension=dim,
                                 dimension_toggle=dimension_toggle,
                                 fragment_url=None, target_id='')
    return _render_histogram(
        mode=mode, machine=ctx['machine'],
        dimension=dim, dimension_toggle=dimension_toggle,
        fragment_url=fragment_url, jobs_fragment_url=jobs_fragment_url,
        target_id=_target_id(ctx, dim),
        account_projcodes=ctx['account_projcodes'],
        username=ctx['username'],
    )


def _panel_timeline(ctx, fragment_url, *, mode, scope_for, log_label,
                    jobs_fragment_url=None, **_kw):
    """HTMX fragment: the Jobs tab's activity timeline."""
    if ctx['machine'] is None:
        return _render_timeline(mode=mode, machine=None,
                                fragment_url=None, target_id='')
    return _render_timeline(
        mode=mode, machine=ctx['machine'],
        fragment_url=fragment_url, jobs_fragment_url=jobs_fragment_url,
        target_id=_target_id(ctx, 'timeline'),
        account_projcodes=ctx['account_projcodes'],
        username=ctx['username'],
    )


def _panel_card(ctx, fragment_url, *, mode, scope_for, log_label, **_kw):
    """HTMX fragment: the card shell, re-rendered on a new window or filters.

    Both surfaces render the same shell; they differ only in where the
    window (and, on the explorer, the rest of the filter set) comes from.
    """
    machine = ctx['machine'] or _get_machine_or_400()
    if _is_explorer_surface():
        return _render_explorer_shell(mode=mode, machine=machine,
                                      project=ctx['project'],
                                      scope=ctx['scope'])
    extra = {}
    if mode == 'project':
        extra = {
            'projcode': ctx['project'].projcode,
            'panel_params': {'scope': ctx['scope']},
            'account_projcodes': ctx['account_projcodes'],
        }
    return _render_card_shell(mode=mode, machine=machine, **extra)


# ---------------------------------------------------------------------------
# Route registration — 20 fragment routes from two tables
# ---------------------------------------------------------------------------
#
# Each was the same shape: resolve the machine, build the fragment URL and a
# default target_id, call the shared renderer with this mode's scoping
# arguments. `register_panels` generates them; the endpoint names it derives
# are pinned by tests/unit/test_route_map_parity.py.
#
# The three `explore` PAGES stay hand-written above — they build a
# page-level context (filter panel, facet chips, scope panel) the fragments
# don't have, which is more than the spec expresses.

_MODES = (
    ModeSpec(
        mode='project', url_prefix='/<projcode>', url_param='projcode',
        endpoint_suffix='',
        decorators=(login_required, require_project_access),
        # require_project_access resolves the projcode to a Project and
        # passes the object; url_for needs the code back.
        url_value=lambda project: project.projcode,
        context=lambda project: _jobs_ctx('project', project),
    ),
    ModeSpec(
        # Machine-wide: every user's jobs, cross-project. The permission
        # here IS the access control — the service will not second-guess it.
        mode='machine', url_prefix='/machine/<machine>', url_param='machine',
        endpoint_suffix='_machine',
        decorators=(login_required,
                    require_permission(Permission.VIEW_ALL_JOB_DATA)),
        context=lambda machine: _jobs_ctx('machine', machine),
    ),
    ModeSpec(
        # "My Jobs" — @login_required ONLY. Safe because every panel pins
        # user=current_user.username server-side (UserJobScope raises on a
        # caller-supplied user), so a client-appended ?user=<other> changes
        # nothing. Mirror of the disk_scans pinned-owner rule.
        mode='user', url_prefix='/user/<machine>', url_param='machine',
        endpoint_suffix='_user',
        decorators=(login_required,),
        context=lambda machine: _jobs_ctx('user', machine),
    ),
)

_PANELS = declare_panels((
    # The table lives at the mode prefix itself — hence the empty rule.
    PanelSpec(key='jobs', rule='', render=_panel_jobs_table),
    PanelSpec(
        key='by_user', rule='/by-user', render=_panel_usage,
        kwargs={'entity_key': 'user'},
        # No user mode: By User there would be a pie of one, so By Project
        # takes its slot.
        modes=('project', 'machine'),
        siblings={'jobs_fragment_url': 'jobs'},
    ),
    PanelSpec(key='by_project', rule='/by-project', render=_panel_usage,
              kwargs={'entity_key': 'project'},
              siblings={'jobs_fragment_url': 'jobs'}),
    PanelSpec(key='wait_times', rule='/wait-times', render=_panel_histogram,
              kwargs={'dimension': 'wait', 'dimension_toggle': False},
              siblings={'jobs_fragment_url': 'jobs'}),
    PanelSpec(key='job_sizes', rule='/job-sizes', render=_panel_histogram,
              # The only panel offering the dimension pills, so the
              # dimension comes from the request rather than the spec.
              kwargs={'dimension': _SIZE_DIMENSIONS[0], 'dimension_toggle': True},
              siblings={'jobs_fragment_url': 'jobs'}),
    PanelSpec(key='durations', rule='/durations', render=_panel_histogram,
              kwargs={'dimension': 'duration', 'dimension_toggle': False},
              siblings={'jobs_fragment_url': 'jobs'}),
    # Not a tab: renders INSIDE the Jobs pane, above the table, behind a
    # collapse. Its own fragment so a metric/period pill re-fetches only the
    # chart and a sort/page click re-fetches only the table.
    PanelSpec(key='timeline', rule='/timeline', render=_panel_timeline,
              siblings={'jobs_fragment_url': 'jobs'}),
    PanelSpec(key='jobs_card', rule='/card', render=_panel_card),
))

register_panels(bp, modes=_MODES, panels=_PANELS)
