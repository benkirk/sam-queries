"""HTMX fragment route for per-job rows on a project's resource-usage page.

Endpoint: ``GET /dashboards/user/jobs/<projcode>``

Query params (all optional unless noted):
  machine   (required) — 'derecho' or 'casper'
  start, end           — YYYY-MM-DD; filters on Job.end
  user                 — limit to a single PBS username
  queue                — limit to a single queue
  status               — limit to a single PBS exit status (e.g. 'F')
  limit                — int, 1..1000 (default 200)

Access control mirrors the rest of the project-scoped UI: the
``require_project_access`` decorator looks the project up by ``projcode``,
verifies the user can see it (VIEW_PROJECTS permission OR project
membership), then hands the route a Project object. The service layer
additionally pins ``Job.account = project.projcode`` so a malformed
filter cannot leak cross-project rows.

The template ``partials/jobs_fragment.html`` is a minimal starting
point — the resource-details page will issue ``hx-get`` to this URL
once the template wire-in lands.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from flask import Blueprint, abort, render_template, request
from flask_login import login_required

from webapp.api.access_control import require_project_access
from webapp.jobs import service
from webapp.jobs.session import is_enabled

bp = Blueprint('jobs', __name__)

# Allowed machine values — keep in lockstep with
# job_history.database.session.VALID_MACHINES. Hardcoded here so the
# route can reject bad input without touching the plugin.
_VALID_MACHINES = {'derecho', 'casper'}

_DEFAULT_LIMIT = 200
_MAX_LIMIT = 1000


def _parse_date(raw: Optional[str]) -> Optional[date]:
    """Parse YYYY-MM-DD; return None for empty/invalid."""
    if not raw:
        return None
    try:
        return datetime.strptime(raw, '%Y-%m-%d').date()
    except ValueError:
        return None


def _parse_limit(raw: Optional[str]) -> int:
    try:
        n = int(raw) if raw else _DEFAULT_LIMIT
    except ValueError:
        n = _DEFAULT_LIMIT
    return max(1, min(n, _MAX_LIMIT))


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
            filters={}, limit=_DEFAULT_LIMIT,
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
    limit = _parse_limit(request.args.get('limit'))

    try:
        rows = service.search_jobs(machine, project=project, limit=limit, **filters)
        error = None
    except Exception as exc:
        # Catch-all so a transient plugin/DB issue degrades to a banner
        # rather than a 500 on the surrounding page. App logger captures
        # the full traceback for diagnosis.
        from flask import current_app
        current_app.logger.exception(
            'jobs_fragment: search_jobs failed for project=%s machine=%s',
            project.projcode, machine,
        )
        rows, error = [], str(exc)

    return render_template(
        'dashboards/user/partials/jobs_fragment.html',
        project=project,
        machine=machine,
        rows=rows,
        filters=filters,
        limit=limit,
        enabled=True,
        error=error,
    )
