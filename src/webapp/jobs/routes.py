"""HTMX fragment route for per-job rows on a project's resource-usage page.

Endpoint: ``GET /dashboards/user/jobs/<projcode>``

Query params (all optional unless noted):
  machine   (required) — 'derecho' or 'casper'
  start, end           — YYYY-MM-DD; filters on Job.end
  user                 — limit to a single PBS username
  queue                — limit to a single queue
  qos                  — limit to a single QoS / priority class
                         (e.g. 'premium', 'regular', 'economy',
                         'uncharged', 'special')
  status               — limit to a single PBS exit status (e.g. 'F')
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
    'status', 'qos_factor',
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
        'status': (request.args.get('status') or '').strip() or None,
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
        from job_history.cli.search.columns import COLUMNS
        return COLUMNS
    except Exception:
        return {}
