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
    generate_jobs_usage_pie_chart,
    generate_jobs_user_pie_chart,
)
from webapp.extensions import db
from webapp.jobs import service
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


def _scope_project(project) -> Project:
    """Resolve the ``?scope=`` child project, or fall back to *project*.

    Mirrors ``disk_scans/routes.py:_scope_project`` — an out-of-tree or
    unknown scope silently falls back to the root project so a fragment
    can never escape the project the decorator authorized.
    """
    scope = (request.args.get('scope') or '').strip()
    if not scope or scope == project.projcode:
        return project
    candidate = Project.get_by_projcode(db.session, scope)
    if candidate is None or candidate.tree_root != project.tree_root:
        return project
    return candidate


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
    account_projcodes = None
    user_account = None
    try:
        common = dict(
            limit=page['per_page'], offset=offset,
            sort_by=sort['sort_by'], sort_dir=sort['sort_dir'],
            columns=requested_cols,
            valid_qos_names=qos_options,
        )
        if mode == 'project':
            # Expand the (possibly ?scope=-re-rooted) project tree so a
            # parent's rows surface jobs charged to child projcodes —
            # mirrors the Historical Usage rollup.
            account_projcodes = [
                p.projcode
                for p in _scope_project(project).get_descendants(include_self=True)
            ]
            # `account` narrows WITHIN the server-derived tree (the
            # By Project drill on a parent project). An out-of-tree value
            # is ignored — the tree stays the security boundary, so a
            # client can never widen scope with this parameter.
            requested = (request.args.get('account') or '').strip() or None
            if requested and requested in account_projcodes:
                user_account = requested
                account_projcodes = [requested]
            rows = service.search_jobs(
                machine, project=project,
                account_projcodes=account_projcodes, **common, **filters,
            )
            total = service.count_jobs(
                machine, project=project,
                account_projcodes=account_projcodes,
                valid_qos_names=qos_options, **filters,
            )
        elif mode == 'user':
            # `account` narrows one's OWN jobs to a single projcode (the
            # By Project drill) — safe from the client in this mode only
            # because the username pin still applies.
            user_account = (request.args.get('account') or '').strip() or None
            rows = service.search_jobs_user(
                machine, pinned_user, account=user_account, **common, **filters,
            )
            total = service.count_jobs_user(
                machine, pinned_user, account=user_account,
                valid_qos_names=qos_options, **filters,
            )
        else:
            # `account` narrows the machine-wide view to one projcode
            # (the By Project drill) — only ever a restriction, and this
            # route family is gated on VIEW_ALL_JOB_DATA.
            user_account = (request.args.get('account') or '').strip() or None
            rows = service.search_jobs_machine(
                machine, account=user_account, **common, **filters,
            )
            total = service.count_jobs_machine(
                machine, account=user_account,
                valid_qos_names=qos_options, **filters,
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

    # Explorer chip strip (?chips=1): facet counts for the same filter set
    # this table shows, rendered as an hx-swap-oob block so chips and
    # table always refresh together (panel submit, chip click, sort,
    # pagination). Card/drill embeds never send chips=1. Degrades to no
    # chips on any facet failure — the table is the primary content.
    facet_chips = None
    if request.args.get('chips') == '1' and error is None:
        try:
            facet_chips = service.jobs_facets(
                machine,
                account_projcodes=(
                    [user_account] if user_account else account_projcodes),
                username=pinned_user,
                valid_qos_names=qos_options,
                **filters,
            )
        except Exception:
            from flask import current_app
            current_app.logger.exception(
                'jobs table: facets failed for mode=%s machine=%s',
                mode, machine,
            )
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
        facet_chips=facet_chips,
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
        column_specs={}, roundtrip_params={}, facet_chips=None,
        enabled=False, error=None,
    )


@bp.route('/<projcode>')
@login_required
@require_project_access
def jobs_fragment(project):
    """HTMX fragment: per-job table for *project* on the requested machine."""
    if not is_enabled():
        return _disabled_jobs_table(project=project)

    machine = (request.args.get('machine') or '').strip().lower()
    if machine not in _VALID_MACHINES:
        abort(400, f'machine must be one of {sorted(_VALID_MACHINES)}')

    return _jobs_table_response(
        mode='project', machine=machine,
        fragment_url=url_for('jobs.jobs_fragment', projcode=project.projcode),
        project=project,
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
    'scope', 'chips', 'account',
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


def _usage_other(usage) -> Optional[dict]:
    """The upstream limit's remainder: totals are pre-truncation, so any
    positive difference is real usage by entities beyond the row cap."""
    totals = usage.get('totals') or {}
    rows = usage.get('rows') or []
    rem = {
        k: (totals.get(k) or 0) - sum((r.get(k) or 0) for r in rows)
        for k in ('job_count', 'cpu_hours', 'gpu_hours')
    }
    return rem if any(v > 1e-9 for v in rem.values()) else None


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
            sort_by=_USAGE_SORT_BY[metric],
            account_projcodes=account_projcodes, **filters,
        )
    except Exception as exc:
        from flask import current_app
        current_app.logger.exception(
            'jobs by-user fragment failed: mode=%s machine=%s', mode, machine,
        )
        error = str(exc)

    pie_svg = generate_jobs_user_pie_chart(usage, metric=metric) \
        if usage else None
    other = _usage_other(usage) if usage else None

    # Same gate as the admin_dashboard.user_card route the username cells
    # link to — don't render click affordances that would 403.
    from flask_login import current_user
    can_view_users = has_permission_any_facility(
        current_user, Permission.VIEW_USERS)

    return render_template(
        template,
        enabled=True, error=error,
        mode=mode, machine=machine,
        usage=usage, other=other,
        metric=metric, pie_svg=pie_svg,
        fragment_url=fragment_url,
        jobs_fragment_url=jobs_fragment_url,
        target_id=target_id,
        can_view_users=can_view_users,
        params=_roundtrip_params(machine, target_id),
    )


def _render_by_project(*, mode, machine, fragment_url, jobs_fragment_url,
                       target_id, username=None, account_projcodes=None):
    """Shared renderer for the By Project tab (all three modes).

    Scoping mirrors the service: user mode pins ``username`` (and drops
    any client ``user`` filter — the pin owns that dimension), project
    mode passes the server-derived ``account_projcodes`` tree, machine
    mode passes neither (route gated on VIEW_ALL_JOB_DATA).
    """
    template = 'dashboards/user/partials/jobs_by_project.html'
    if not is_enabled():
        return render_template(template, enabled=False, error=None,
                               mode=mode, machine=None, target_id=target_id)

    filters = _parse_job_filters(include_user=(username is None))
    metric = _parse_metric(_DEFAULT_METRIC_PIE)

    usage = None
    error = None
    try:
        usage = service.jobs_usage_by_project(
            machine, username=username, limit=_BY_USER_LIMIT,
            sort_by=_USAGE_SORT_BY[metric],
            account_projcodes=account_projcodes, **filters,
        )
    except Exception as exc:
        from flask import current_app
        current_app.logger.exception(
            'jobs by-project fragment failed: mode=%s machine=%s',
            mode, machine,
        )
        error = str(exc)

    pie_svg = generate_jobs_usage_pie_chart(
        usage, metric=metric, sentinel_prefix='job-proj') if usage else None
    other = _usage_other(usage) if usage else None

    # Projcode cells link to user_dashboard.project_details_modal, gated by
    # require_project_access. In user mode the rows are the pinned user's
    # own projects (affiliation grants access), so the affordance always
    # renders; elsewhere require VIEW_PROJECTS so the click can't 403.
    from flask_login import current_user
    can_view_projects = (mode == 'user') or has_permission_any_facility(
        current_user, Permission.VIEW_PROJECTS)

    return render_template(
        template,
        enabled=True, error=error,
        mode=mode, machine=machine,
        usage=usage, other=other,
        metric=metric, pie_svg=pie_svg,
        fragment_url=fragment_url,
        jobs_fragment_url=jobs_fragment_url,
        target_id=target_id,
        can_view_projects=can_view_projects,
        params=_roundtrip_params(machine, target_id),
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

    filters = _parse_job_filters()
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
            # Both axes pinned ⇒ every band has exactly one owner, so
            # skip the grouping entirely: flat bars, and a band drills
            # straight to its jobs instead of through a one-row tier.
            owners_limit=_HIST_OWNERS_LIMIT if rel['owners_enabled'] else None,
            # Which top-N survives must follow the displayed metric —
            # hours-ranked owners cover ~1% of band GPU-hours (plugin
            # PR #100 review data), rendering a GPU stack as all-"Other".
            owners_sort_by=_USAGE_SORT_BY[metric],
            owners_by=owners_by,
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


@bp.route('/<projcode>/by-project')
@login_required
@require_project_access
def by_project_fragment(project):
    """HTMX fragment: per-projcode usage pie + rows across *project*'s tree.

    Only meaningful for parent projects whose account tree spans more
    than one projcode (the card gates the tab on that); the tree list is
    server-derived — the same security boundary as every project-mode
    fragment. Rows drill into the project jobs fragment narrowed by
    ``account=<projcode>`` (validated against the tree there).
    """
    if not is_enabled():
        return _render_by_project(mode='project', machine=None,
                                  fragment_url=None,
                                  jobs_fragment_url=None, target_id='')
    machine = _get_machine_or_400()
    target_id = (request.args.get('target_id') or '').strip() \
        or f'jobs-byproj-{project.projcode}-{machine}'
    return _render_by_project(
        mode='project', machine=machine,
        fragment_url=url_for('jobs.by_project_fragment',
                             projcode=project.projcode),
        jobs_fragment_url=url_for('jobs.jobs_fragment',
                                  projcode=project.projcode),
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
        jobs_fragment_url=url_for('jobs.jobs_fragment',
                                  projcode=project.projcode),
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


def _initial_jobs_url(fragment_url: str, machine: str, target_id: str,
                      panel: dict, scope: Optional[str] = None) -> str:
    """Fragment URL pre-loaded by the explorer page (carries current filters).

    The page's table container ``hx-get``s this on load so a deep-link /
    reload lands on the same filtered view the panel shows; subsequent panel
    submits re-fetch via the form's own fields.
    """
    from urllib.parse import urlencode
    params = {'machine': machine, 'target_id': target_id,
              'per_page': panel['per_page'], 'chips': '1'}
    if scope:
        params['scope'] = scope
    for key in ('start', 'end', 'queue', 'qos', 'exit_status', 'name'):
        if panel[key]:
            params[key] = panel[key]
    if panel['user']:
        params['user'] = panel['user']
    if panel['ignore_case']:
        params['ignore_case'] = '1'
    for key in ('min_nodes', 'max_nodes', 'min_cpus', 'max_cpus',
                'min_gpus', 'max_gpus', 'min_wait_hours', 'max_wait_hours',
                'min_elapsed_hours', 'max_elapsed_hours',
                'min_reqmem_gb', 'max_reqmem_gb'):
        if panel[key] is not None:
            params[key] = panel[key]
    return f'{fragment_url}?{urlencode(params)}'


def _user_search_url() -> str:
    """The fk-picker search endpoint for the user filter (context='fk')."""
    return url_for('admin_dashboard.htmx_search_users', context='fk')


@bp.route('/<projcode>/explore')
@login_required
@require_project_access
def explore_page(project):
    """Standalone full-page jobs explorer for *project* (project mode).

    Renders the filter panel + a table container that lazy-loads
    ``jobs_fragment`` with the panel's params. ``?scope=<child>`` re-roots
    to a subtree (same-tree validated); the reusable fragment is shared
    verbatim with the machine/user modes.
    """
    if not is_enabled():
        return render_template(
            'dashboards/user/jobs_explore_page.html',
            mode='project', enabled=False, project=project,
            scoped_project=project, machine=None,
        )
    machine = _get_machine_or_400()
    scoped = _scope_project(project)
    target_id = 'jobs-explore'
    fragment_url = url_for('jobs.jobs_fragment', projcode=project.projcode)
    panel = _panel_filters(machine)
    scope = scoped.projcode if scoped.projcode != project.projcode else None
    return render_template(
        'dashboards/user/jobs_explore_page.html',
        mode='project', enabled=True,
        project=project, scoped_project=scoped, machine=machine,
        scope=scope,
        fragment_url=fragment_url,
        initial_url=_initial_jobs_url(fragment_url, machine, target_id,
                                      panel, scope=scope),
        filters=panel, user_search_url=_user_search_url(),
        per_page_options=_PER_PAGE_OPTIONS, target_id=target_id,
    )


@bp.route('/machine/<machine>')
@login_required
@require_permission(Permission.VIEW_ALL_JOB_DATA)
def jobs_machine_fragment(machine):
    """HTMX fragment: per-job table across an ENTIRE machine (operator)."""
    if not is_enabled():
        return _disabled_jobs_table()
    machine = _machine_or_404(machine)
    return _jobs_table_response(
        mode='machine', machine=machine,
        fragment_url=url_for('jobs.jobs_machine_fragment', machine=machine),
    )


@bp.route('/machine/<machine>/by-user')
@login_required
@require_permission(Permission.VIEW_ALL_JOB_DATA)
def by_user_machine_fragment(machine):
    """HTMX fragment: machine-wide per-user usage pie + rows (operator)."""
    if not is_enabled():
        return _render_by_user(mode='machine', machine=None,
                               fragment_url=None, jobs_fragment_url=None,
                               target_id='')
    machine = _machine_or_404(machine)
    target_id = (request.args.get('target_id') or '').strip() \
        or f'jobs-byuser-machine-{machine}'
    return _render_by_user(
        mode='machine', machine=machine,
        fragment_url=url_for('jobs.by_user_machine_fragment', machine=machine),
        jobs_fragment_url=url_for('jobs.jobs_machine_fragment', machine=machine),
        target_id=target_id,
    )


@bp.route('/machine/<machine>/by-project')
@login_required
@require_permission(Permission.VIEW_ALL_JOB_DATA)
def by_project_machine_fragment(machine):
    """HTMX fragment: machine-wide per-project usage pie + rows (operator).

    The multi-project counterpart of By User; rows drill into the
    machine jobs fragment narrowed by ``account=<projcode>``.
    """
    if not is_enabled():
        return _render_by_project(mode='machine', machine=None,
                                  fragment_url=None,
                                  jobs_fragment_url=None, target_id='')
    machine = _machine_or_404(machine)
    target_id = (request.args.get('target_id') or '').strip() \
        or f'jobs-byproj-machine-{machine}'
    return _render_by_project(
        mode='machine', machine=machine,
        fragment_url=url_for('jobs.by_project_machine_fragment',
                             machine=machine),
        jobs_fragment_url=url_for('jobs.jobs_machine_fragment',
                                  machine=machine),
        target_id=target_id,
    )


def _machine_histogram(machine, *, dimension, dimension_toggle, endpoint):
    """Common body of the three machine-mode histogram routes."""
    if not is_enabled():
        return _render_histogram(mode='machine', machine=None,
                                 dimension=dimension,
                                 dimension_toggle=dimension_toggle,
                                 fragment_url=None, target_id='')
    machine = _machine_or_404(machine)
    target_id = (request.args.get('target_id') or '').strip() \
        or f'jobs-{dimension}-machine-{machine}'
    return _render_histogram(
        mode='machine', machine=machine,
        dimension=dimension, dimension_toggle=dimension_toggle,
        fragment_url=url_for(endpoint, machine=machine),
        jobs_fragment_url=url_for('jobs.jobs_machine_fragment',
                                  machine=machine),
        target_id=target_id,
    )


@bp.route('/machine/<machine>/wait-times')
@login_required
@require_permission(Permission.VIEW_ALL_JOB_DATA)
def wait_times_machine_fragment(machine):
    return _machine_histogram(machine, dimension='wait',
                              dimension_toggle=False,
                              endpoint='jobs.wait_times_machine_fragment')


@bp.route('/machine/<machine>/job-sizes')
@login_required
@require_permission(Permission.VIEW_ALL_JOB_DATA)
def job_sizes_machine_fragment(machine):
    dimension = (request.args.get('dimension') or '').strip()
    if dimension not in _SIZE_DIMENSIONS:
        dimension = _SIZE_DIMENSIONS[0]
    return _machine_histogram(machine, dimension=dimension,
                              dimension_toggle=True,
                              endpoint='jobs.job_sizes_machine_fragment')


@bp.route('/machine/<machine>/durations')
@login_required
@require_permission(Permission.VIEW_ALL_JOB_DATA)
def durations_machine_fragment(machine):
    return _machine_histogram(machine, dimension='duration',
                              dimension_toggle=False,
                              endpoint='jobs.durations_machine_fragment')


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
    target_id = 'jobs-explore'
    fragment_url = url_for('jobs.jobs_machine_fragment', machine=machine)
    panel = _panel_filters(machine)
    return render_template(
        'dashboards/user/jobs_explore_page.html',
        mode='machine', enabled=True, machine=machine,
        fragment_url=fragment_url,
        initial_url=_initial_jobs_url(fragment_url, machine, target_id, panel),
        filters=panel, user_search_url=_user_search_url(),
        per_page_options=_PER_PAGE_OPTIONS, target_id=target_id,
    )


# ---------------------------------------------------------------------------
# User family ("My Jobs") — hard-pinned to the logged-in user
# ---------------------------------------------------------------------------
#
# @login_required ONLY — no permission gate. Safe because every route pins
# user=current_user.username server-side (the service families raise on a
# caller-supplied user), so a client-appended ?user=<other> changes nothing.
# Mirror of the disk_scans pinned-owner rule.

@bp.route('/user/<machine>')
@login_required
def jobs_user_fragment(machine):
    """HTMX fragment: the logged-in user's per-job table on *machine*."""
    from flask_login import current_user
    if not is_enabled():
        return _disabled_jobs_table(username=current_user.username)
    machine = _machine_or_404(machine)
    return _jobs_table_response(
        mode='user', machine=machine,
        fragment_url=url_for('jobs.jobs_user_fragment', machine=machine),
        pinned_user=current_user.username,
    )


@bp.route('/user/<machine>/by-project')
@login_required
def by_project_user_fragment(machine):
    """HTMX fragment: the logged-in user's per-project usage pie + rows.

    The user-mode counterpart of By User (which is hidden there — a pie
    of one): which projects MY jobs charged. Username pinned server-side
    like every /user/ route; rows drill into the user-mode jobs fragment
    narrowed by ``account=<projcode>``.
    """
    from flask_login import current_user
    if not is_enabled():
        return _render_by_project(mode='user', machine=None,
                                  fragment_url=None,
                                  jobs_fragment_url=None, target_id='')
    machine = _machine_or_404(machine)
    target_id = (request.args.get('target_id') or '').strip() \
        or f'jobs-byproj-user-{machine}'
    return _render_by_project(
        mode='user', machine=machine,
        fragment_url=url_for('jobs.by_project_user_fragment', machine=machine),
        jobs_fragment_url=url_for('jobs.jobs_user_fragment', machine=machine),
        target_id=target_id,
        username=current_user.username,
    )


def _user_histogram(machine, *, dimension, dimension_toggle, endpoint):
    """Common body of the three user-mode histogram routes."""
    from flask_login import current_user
    if not is_enabled():
        return _render_histogram(mode='user', machine=None,
                                 dimension=dimension,
                                 dimension_toggle=dimension_toggle,
                                 fragment_url=None, target_id='')
    machine = _machine_or_404(machine)
    target_id = (request.args.get('target_id') or '').strip() \
        or f'jobs-{dimension}-user-{machine}'
    return _render_histogram(
        mode='user', machine=machine,
        dimension=dimension, dimension_toggle=dimension_toggle,
        fragment_url=url_for(endpoint, machine=machine),
        jobs_fragment_url=url_for('jobs.jobs_user_fragment',
                                  machine=machine),
        target_id=target_id,
        username=current_user.username,
    )


@bp.route('/user/<machine>/wait-times')
@login_required
def wait_times_user_fragment(machine):
    return _user_histogram(machine, dimension='wait',
                           dimension_toggle=False,
                           endpoint='jobs.wait_times_user_fragment')


@bp.route('/user/<machine>/job-sizes')
@login_required
def job_sizes_user_fragment(machine):
    dimension = (request.args.get('dimension') or '').strip()
    if dimension not in _SIZE_DIMENSIONS:
        dimension = _SIZE_DIMENSIONS[0]
    return _user_histogram(machine, dimension=dimension,
                           dimension_toggle=True,
                           endpoint='jobs.job_sizes_user_fragment')


@bp.route('/user/<machine>/durations')
@login_required
def durations_user_fragment(machine):
    return _user_histogram(machine, dimension='duration',
                           dimension_toggle=False,
                           endpoint='jobs.durations_user_fragment')


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
    target_id = 'jobs-explore'
    fragment_url = url_for('jobs.jobs_user_fragment', machine=machine)
    panel = _panel_filters(machine)
    return render_template(
        'dashboards/user/jobs_explore_page.html',
        mode='user', enabled=True, machine=machine,
        username=current_user.username,
        fragment_url=fragment_url,
        initial_url=_initial_jobs_url(fragment_url, machine, target_id, panel),
        filters=panel, user_search_url=_user_search_url(),
        per_page_options=_PER_PAGE_OPTIONS, target_id=target_id,
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


@bp.route('/<projcode>/card')
@login_required
@require_project_access
def jobs_card_fragment(project):
    """HTMX fragment: *project*'s card shell rebound to a new window."""
    return _render_card_shell(
        mode='project', machine=_get_machine_or_400(),
        projcode=project.projcode,
        panel_params={
            'scope': (request.args.get('scope') or '').strip() or None,
        },
        account_projcodes=_tree_projcodes(project),
    )


@bp.route('/machine/<machine>/card')
@login_required
@require_permission(Permission.VIEW_ALL_JOB_DATA)
def jobs_card_machine_fragment(machine):
    """HTMX fragment: the machine-wide card shell on a new window."""
    return _render_card_shell(mode='machine', machine=_machine_or_404(machine))


@bp.route('/user/<machine>/card')
@login_required
def jobs_card_user_fragment(machine):
    """HTMX fragment: the "My Jobs" card shell on a new window."""
    return _render_card_shell(mode='user', machine=_machine_or_404(machine))
