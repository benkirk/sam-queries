"""HTMX fragment routes for filesystem-scan analytics on the disk page.

Endpoints (url_prefix ``/dashboards/user/disk-scans``):

  GET /<projcode>/directories     — largest directories (sortable)
  GET /<projcode>/entities        — owner|group rollups (?kind=)
  GET /<projcode>/access-history  — access-time histogram (SVG)
  GET /<projcode>/file-sizes      — file-size histogram (SVG)

Each fragment is lazy-loaded on first tab show (see
``resource_details_disk.html``). Access control mirrors the rest of the
project-scoped UI: ``require_project_access`` looks the project up by
``projcode``, verifies the user can see it, and hands the route a Project.
The service layer additionally refuses any *unscoped* query, so a fragment
can only ever surface the project's own directories.

Scope follows the disk page's selection:

  * ``?scope=<projcode>`` re-roots to a child project — hits the plugin's
    pre-computed whole-collection-root fast path (5-10s).
  * ``?fileset=<path>`` drills into a single fileset — the inherently slow
    on-the-fly scan (30-200s); the template lazy-loads + shows a spinner.

``?resource=`` carries the disk resource being viewed (Campaign_Store
today). Like ``jobs_fragment``, a plugin/DB hiccup degrades to an inline
``error`` banner rather than 500-ing the surrounding page.
"""

from __future__ import annotations

from typing import Optional, Tuple

from flask import Blueprint, current_app, render_template, request, url_for
from flask_login import login_required

from sam.projects.projects import Project
from webapp.api.access_control import require_project_access
from webapp.dashboards.charts import generate_distribution_histogram
from webapp.disk_scans import service
from webapp.disk_scans.session import is_enabled
from webapp.extensions import db

bp = Blueprint('disk_scans', __name__)

# scan_directories sort keys the facade understands (see
# fs_scans/queries/facade.py:_DIR_SORT_KEYS). The facade fixes the sort
# direction per key (size/files/atime/dirs descending, path ascending),
# so the UI only switches the active key, not a direction. 'dirs' maps to
# the recursive subdirectory count (dir_count_r).
_DIR_SORT_WHITELIST = {'size', 'files', 'atime_r', 'path', 'dirs'}
_DEFAULT_DIR_SORT = 'size'

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 500


def _scope_project(project) -> Project:
    """Resolve the ``?scope=`` child project, or fall back to *project*.

    Mirrors the validation in
    ``dashboards/user/blueprint.py:_render_disk_resource_details`` — an
    out-of-tree or unknown scope silently falls back to the root project so
    the fragment can never escape the project the decorator authorized.
    """
    scope = (request.args.get('scope') or '').strip()
    if not scope or scope == project.projcode:
        return project
    candidate = Project.get_by_projcode(db.session, scope)
    if candidate is None or candidate.tree_root != project.tree_root:
        return project
    return candidate


def _common_ctx(project) -> dict:
    """Shared template context: scoped project, resource, fileset, ids.

    ``fragment_url`` + ``target_id`` round-trip so a sort / toggle click
    re-fetches into the same container this fragment was swapped into.
    """
    resource_name = (request.args.get('resource') or '').strip()
    scoped = _scope_project(project)
    fileset = (request.args.get('fileset') or '').strip() or None
    target_id = (request.args.get('target_id') or '').strip()
    return {
        'project': project,
        'scoped_project': scoped,
        'resource_name': resource_name,
        'scope': scoped.projcode,
        'fileset': fileset,
        'target_id': target_id,
    }


def _limit() -> int:
    """Read ``?limit=`` with a defensive default + ceiling."""
    try:
        n = int(request.args.get('limit', _DEFAULT_LIMIT))
    except (TypeError, ValueError):
        n = _DEFAULT_LIMIT
    return max(1, min(n, _MAX_LIMIT))


@bp.route('/<projcode>/directories')
@login_required
@require_project_access
def directories_fragment(project):
    """HTMX fragment: largest directories for *project* (sortable)."""
    ctx = _common_ctx(project)
    if not is_enabled() or not ctx['resource_name']:
        return render_template(
            'dashboards/user/partials/disk_scans_directories.html',
            rows=[], sort={'sort_by': _DEFAULT_DIR_SORT},
            sortable_columns=sorted(_DIR_SORT_WHITELIST),
            fragment_url=url_for('disk_scans.directories_fragment',
                                 projcode=project.projcode),
            enabled=is_enabled(), error=None, **ctx,
        )

    sort_by = request.args.get('sort_by') or _DEFAULT_DIR_SORT
    if sort_by not in _DIR_SORT_WHITELIST:
        sort_by = _DEFAULT_DIR_SORT

    rows, error = [], None
    try:
        rows = service.scan_directories(
            db.session, ctx['scoped_project'], ctx['resource_name'],
            sort_by=sort_by, limit=_limit(), subpath=ctx['fileset'],
        )
    except Exception as exc:
        current_app.logger.exception(
            'disk_scans.directories: scan failed for project=%s resource=%s',
            ctx['scoped_project'].projcode, ctx['resource_name'],
        )
        error = str(exc)

    return render_template(
        'dashboards/user/partials/disk_scans_directories.html',
        rows=rows, sort={'sort_by': sort_by},
        sortable_columns=sorted(_DIR_SORT_WHITELIST),
        fragment_url=url_for('disk_scans.directories_fragment',
                             projcode=project.projcode),
        enabled=True, error=error, **ctx,
    )


@bp.route('/<projcode>/entities')
@login_required
@require_project_access
def entities_fragment(project):
    """HTMX fragment: per-owner or per-group rollup (``?kind=owner|group``)."""
    ctx = _common_ctx(project)
    kind = (request.args.get('kind') or 'owner').strip().lower()
    if kind not in ('owner', 'group'):
        kind = 'owner'

    if not is_enabled() or not ctx['resource_name']:
        return render_template(
            'dashboards/user/partials/disk_scans_entities.html',
            rows=[], kind=kind,
            fragment_url=url_for('disk_scans.entities_fragment',
                                 projcode=project.projcode),
            enabled=is_enabled(), error=None, **ctx,
        )

    rows, error = [], None
    try:
        fn = service.scan_owner_summary if kind == 'owner' else service.scan_group_summary
        rows = fn(
            db.session, ctx['scoped_project'], ctx['resource_name'],
            limit=_limit(), subpath=ctx['fileset'],
        )
    except Exception as exc:
        current_app.logger.exception(
            'disk_scans.entities: %s scan failed for project=%s resource=%s',
            kind, ctx['scoped_project'].projcode, ctx['resource_name'],
        )
        error = str(exc)

    return render_template(
        'dashboards/user/partials/disk_scans_entities.html',
        rows=rows, kind=kind,
        fragment_url=url_for('disk_scans.entities_fragment',
                             projcode=project.projcode),
        enabled=True, error=error, **ctx,
    )


_METRIC_WHITELIST = {'data', 'files'}


def _truthy(v) -> bool:
    return (v or '').strip().lower() in ('1', 'true', 'on', 'yes')


def _render_distribution(project, *, service_fn, endpoint, kind,
                         bucket_header, metric_toggle=False, log_toggle=False):
    """Shared body for the two distribution histogram fragments.

    The Access-history and File-size tabs are identical end to end — same
    ``{bucket_labels, buckets{...}, owners, username_map}`` shape, same
    template, same chart — differing only in the service query, the bucket
    column header, and (file-sizes only) a Data ↔ Files metric pill plus a
    Log-scale switch, both of which re-fetch the pane via ``?metric=`` /
    ``?log=``. ``kind`` is used only for logging.

    A log y-axis can't represent a stack, so ``log_y`` renders solid bars
    (no per-user gradient); it's offered only where the metric is skewed
    enough to need it (file-sizes), hence gated on *log_toggle*.
    """
    ctx = _common_ctx(project)

    metric = (request.args.get('metric') or 'data').strip().lower()
    if not metric_toggle or metric not in _METRIC_WHITELIST:
        metric = 'data'
    log_on = log_toggle and _truthy(request.args.get('log'))
    # urls the pill / switch re-fetch (carry scope/resource/fileset via the
    # pane's hidden form + hx-include, exactly like the owner↔group toggle).
    fragment_url = url_for(endpoint, projcode=project.projcode)
    extra = dict(metric=metric, metric_toggle=metric_toggle,
                 log_on=log_on, log_toggle=log_toggle,
                 bucket_header=bucket_header, fragment_url=fragment_url)

    if not is_enabled() or not ctx['resource_name']:
        return render_template(
            'dashboards/user/partials/disk_scans_distribution.html',
            hist=None, chart_svg=None,
            enabled=is_enabled(), error=None, **ctx, **extra,
        )

    owner_uid = request.args.get('owner_uid', type=int)
    hist, chart_svg, error = None, None, None
    try:
        hist = service_fn(
            db.session, ctx['scoped_project'], ctx['resource_name'],
            owner_uid=owner_uid, subpath=ctx['fileset'],
        )
        if hist:
            chart_svg = generate_distribution_histogram(
                hist, log_y=log_on, metric=metric)
    except Exception as exc:
        current_app.logger.exception(
            'disk_scans.%s: scan failed for project=%s resource=%s',
            kind, ctx['scoped_project'].projcode, ctx['resource_name'],
        )
        error = str(exc)

    return render_template(
        'dashboards/user/partials/disk_scans_distribution.html',
        hist=hist, chart_svg=chart_svg,
        enabled=True, error=error, **ctx, **extra,
    )


@bp.route('/<projcode>/access-history')
@login_required
@require_project_access
def access_history_fragment(project):
    """HTMX fragment: access-time distribution histogram (server-rendered SVG)."""
    return _render_distribution(
        project, service_fn=service.scan_access_history,
        endpoint='disk_scans.access_history_fragment', kind='access_history',
        bucket_header='Last accessed',
    )


@bp.route('/<projcode>/file-sizes')
@login_required
@require_project_access
def file_sizes_fragment(project):
    """HTMX fragment: file-size distribution histogram (server-rendered SVG).

    Carries a Data ↔ Files metric pill (``?metric=``) since a file-size
    distribution is equally meaningful by volume or by file count, plus a
    Log-scale switch (``?log=``) because file-size *data* spans many orders
    of magnitude — at the cost of the per-user stack gradient.
    """
    return _render_distribution(
        project, service_fn=service.scan_file_sizes,
        endpoint='disk_scans.file_sizes_fragment', kind='file_sizes',
        bucket_header='File size', metric_toggle=True, log_toggle=True,
    )
