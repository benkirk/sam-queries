"""HTMX fragment route for per-job rows on a project's resource-usage page.

Endpoint: ``GET /dashboards/user/jobs/<projcode>``

Query params (all optional unless noted):
  machine   (required) — 'derecho' or 'casper'
  start, end           — YYYY-MM-DD; filters on Job.end
  user                 — limit to a single PBS username
  queue                — limit to a single queue
  status               — limit to a single PBS exit status (e.g. 'F')
  page                 — int ≥ 1; default 1
  per_page             — int in [10, 200]; default 50
  sort_by              — one of {'start', 'elapsed', 'cpu_charges',
                         'gpu_charges'}; default None (plugin orders
                         by ``Job.end DESC``)
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

# Subset of plugin COLUMNS exposed as sortable headers in the UI. The
# plugin itself accepts any COLUMNS key; the whitelist keeps the URL
# surface tight and prevents surprising sort outcomes from typos.
_SORT_WHITELIST = {'start', 'elapsed', 'cpu_charges', 'gpu_charges'}

# Default columns shown when drilled into a user+queue row. user/queue/
# account are dropped because the row context already pins them.
_DEFAULT_COLS = (
    'job_id', 'name', 'start', 'elapsed',
    'numnodes', 'numcpus', 'numgpus',
    'cpu_charges', 'gpu_charges',
)

# Extra columns revealed in the per-row "expand" drawer. Order is the
# render order in the drawer.
_VERBOSE_EXTRAS = (
    'status', 'queue', 'user',
    'submit', 'end', 'walltime',
    'mpiprocs', 'ompthreads',
    'reqmem', 'memory', 'vmemory',
    'cputype', 'gputype', 'resources',
    'cpu_hours', 'gpu_hours', 'memory_hours',
    'qos_factor', 'memory_charges',
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
            **filters,
        )
        total = service.count_jobs(
            machine, project=project,
            account_projcodes=account_projcodes,
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
    column_specs = _load_column_specs()
    fragment_url = url_for('jobs.jobs_fragment', projcode=project.projcode)

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
        fragment_url=fragment_url,
        target_id=f'jobs-{project.projcode}-{machine}',
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
