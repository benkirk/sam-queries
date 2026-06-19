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

from datetime import datetime
from typing import Optional, Tuple
from urllib.parse import urlencode

from flask import Blueprint, current_app, render_template, request, url_for
from flask_login import login_required

from sam.core.users import User
from sam.projects.projects import Project
from webapp.api.access_control import require_project_access
from webapp.dashboards.charts import generate_distribution_histogram
from webapp.disk_scans import service
from webapp.disk_scans.session import is_enabled
from webapp.extensions import db
from webapp.utils.rbac import Permission, require_permission

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
# Row-count choices offered by the explorer page's limit selector.
_LIMIT_OPTIONS = (50, 100, 250, 500)


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


def _truthy(v) -> bool:
    return (v or '').strip().lower() in ('1', 'true', 'on', 'yes')


def _query_date(name: str) -> Optional[datetime]:
    """Parse a ``?<name>=YYYY-MM-DD`` query arg to a datetime (or ``None``).

    GET-only filter parsing — the forms-schema rule is for POST/PUT mutations;
    here we mirror the lightweight ``request.args`` coercion already used for
    ``owner_uid``/``leaves_only`` and the ``?metric=``/``?log=`` toggles.
    """
    raw = (request.args.get(name) or '').strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, '%Y-%m-%d')
    except ValueError:
        return None


def _dir_filters() -> dict:
    """Parse the shared Large-directories filter params (GET, whitelisted).

    Used by all four directory routes. Carries both the service-ready values
    (``sort_by/limit/owner_uid/leaves_only`` + parsed ``accessed_before/after``
    datetimes) and the raw date strings (``*_str``) the hidden form / filter
    panel echo back so a sort re-fetch or page reload preserves them.
    """
    sort_by = request.args.get('sort_by') or _DEFAULT_DIR_SORT
    if sort_by not in _DIR_SORT_WHITELIST:
        sort_by = _DEFAULT_DIR_SORT

    owner_uid, owner_user_id, owner_label = _resolve_owner()

    return {
        'sort_by': sort_by,
        'limit': _limit(),
        'owner_uid': owner_uid,
        'owner_user_id': owner_user_id,
        'owner_label': owner_label,
        'accessed_before': _query_date('accessed_before'),
        'accessed_after': _query_date('accessed_after'),
        'accessed_before_str': (request.args.get('accessed_before') or '').strip(),
        'accessed_after_str': (request.args.get('accessed_after') or '').strip(),
        'leaves_only': _truthy(request.args.get('leaves_only')),
    }


def _resolve_owner() -> Tuple[Optional[int], Optional[int], str]:
    """Resolve the owner filter to ``(owner_uid, owner_user_id, label)``.

    Two entry points feed the same filter: the per-user drill-down passes a
    unix ``?owner_uid=`` directly, while the filter-panel user picker (the
    shared ``fk_search_field``, which stores a SAM ``user_id``) passes
    ``?owner_user_id=``. ``owner_uid`` is the canonical wire param the fragment
    echoes back across sort re-fetches, so it wins when both are present. The
    label (``"Display Name (username)"``) repopulates the picker badge / chip
    on reload and is best-effort — an unresolved UID just shows numerically.
    """
    owner_uid = request.args.get('owner_uid', type=int)
    owner_user_id = request.args.get('owner_user_id', type=int)
    user = None
    if owner_user_id is not None:
        user = db.session.get(User, owner_user_id)
        if user is not None and owner_uid is None:
            owner_uid = user.unix_uid
    if user is None and owner_uid is not None:
        user = db.session.query(User).filter_by(unix_uid=owner_uid).first()
    label = f'{user.display_name} ({user.username})' if user is not None else ''
    return owner_uid, owner_user_id, label


def _user_search_url() -> str:
    """The fk-picker search endpoint for the owner user picker (context='fk')."""
    return url_for('admin_dashboard.htmx_search_users', context='fk')


def _initial_fragment_url(fragment_url: str, ctx: dict, flt: dict) -> str:
    """Fragment URL pre-loaded by the explorer page (carries current filters).

    The page's table container ``hx-get``s this on load so a deep-link / reload
    lands on the same filtered view the panel shows. Subsequent panel submits
    and in-table sorts re-fetch via ``hx-include`` of the live forms.
    """
    params = {
        'resource': ctx['resource_name'],
        'target_id': ctx['target_id'],
        'sort_by': flt['sort_by'],
        'limit': flt['limit'],
    }
    if ctx.get('scope'):
        params['scope'] = ctx['scope']
    if ctx.get('fileset'):
        params['fileset'] = ctx['fileset']
    if flt['owner_uid'] is not None:
        params['owner_uid'] = flt['owner_uid']
    if flt['leaves_only']:
        params['leaves_only'] = '1'
    if flt['accessed_before_str']:
        params['accessed_before'] = flt['accessed_before_str']
    if flt['accessed_after_str']:
        params['accessed_after'] = flt['accessed_after_str']
    return f'{fragment_url}?{urlencode(params)}'


def _resource_ctx(resource_name: str) -> dict:
    """``_common_ctx`` analogue for resource mode (no project scoping).

    Resource mode browses the whole resource, so there is no project /
    scoped_project and ``scope`` is empty; the fragment template treats those
    as optional. ``fileset`` still drills into a single collection sub-path.
    """
    fileset = (request.args.get('fileset') or '').strip() or None
    target_id = (request.args.get('target_id') or '').strip() or 'disk-scans-explore'
    return {
        'project': None,
        'scoped_project': None,
        'resource_name': resource_name,
        'scope': '',
        'fileset': fileset,
        'target_id': target_id,
    }


def _render_directories_fragment(ctx, fragment_url, *, mode, scan_call, log_label):
    """Shared body for the project + resource directory fragments.

    Both render the *same* ``disk_scans_directories.html`` partial; they differ
    only in scope context, which fragment URL the sort pill / headers re-fetch,
    and the (already scope-resolved) ``scan_call``. A plugin/DB hiccup degrades
    to an inline error banner, never a 500.
    """
    flt = _dir_filters()
    base = dict(
        sort={'sort_by': flt['sort_by']},
        sortable_columns=sorted(_DIR_SORT_WHITELIST),
        fragment_url=fragment_url, filters=flt, mode=mode, **ctx,
    )
    if not is_enabled() or not ctx['resource_name']:
        return render_template(
            'dashboards/user/partials/disk_scans_directories.html',
            rows=[], enabled=is_enabled(), error=None, **base,
        )

    rows, error = [], None
    try:
        rows = scan_call(flt)
    except Exception as exc:
        current_app.logger.exception(
            'disk_scans.directories(%s): scan failed for %s resource=%s',
            mode, log_label, ctx['resource_name'],
        )
        error = str(exc)

    return render_template(
        'dashboards/user/partials/disk_scans_directories.html',
        rows=rows, enabled=True, error=error, **base,
    )


@bp.route('/<projcode>/directories')
@login_required
@require_project_access
def directories_fragment(project):
    """HTMX fragment: largest directories for *project* (sortable, filterable)."""
    ctx = _common_ctx(project)
    fragment_url = url_for('disk_scans.directories_fragment',
                           projcode=project.projcode)

    def _scan(flt):
        return service.scan_directories(
            db.session, ctx['scoped_project'], ctx['resource_name'],
            sort_by=flt['sort_by'], limit=flt['limit'],
            owner_uid=flt['owner_uid'],
            accessed_before=flt['accessed_before'],
            accessed_after=flt['accessed_after'],
            leaves_only=flt['leaves_only'],
            subpath=ctx['fileset'],
        )

    return _render_directories_fragment(
        ctx, fragment_url, mode='project', scan_call=_scan,
        log_label=f"project={ctx['scoped_project'].projcode}",
    )


@bp.route('/<projcode>/directories/explore')
@login_required
@require_project_access
def directories_page(project):
    """Standalone full-page directory explorer for *project* (project mode).

    Renders the filters panel + a table container that lazy-loads
    ``directories_fragment`` with the panel's params. The reusable fragment is
    shared verbatim with resource mode; only the header + fragment URL differ.
    """
    ctx = _common_ctx(project)
    ctx['target_id'] = ctx['target_id'] or 'disk-scans-explore'
    flt = _dir_filters()
    fragment_url = url_for('disk_scans.directories_fragment',
                           projcode=project.projcode)
    return render_template(
        'dashboards/user/disk_scans_directories_page.html',
        mode='project', fragment_url=fragment_url,
        initial_url=_initial_fragment_url(fragment_url, ctx, flt),
        filters=flt, user_search_url=_user_search_url(),
        limit_options=_LIMIT_OPTIONS, **ctx,
    )


@bp.route('/resource/<resource>/directories')
@login_required
@require_permission(Permission.VIEW_ALL_FILESYSTEM_DATA)
def directories_resource_fragment(resource):
    """HTMX fragment: largest directories across an ENTIRE disk resource.

    Resource mode — unscoped, elevated. Gated by ``VIEW_ALL_FILESYSTEM_DATA``
    at the route (not just the template link), since it exposes every user's
    paths/sizes across the resource. ``?fileset=`` drills into a sub-path.
    """
    ctx = _resource_ctx(resource)
    fragment_url = url_for('disk_scans.directories_resource_fragment',
                           resource=resource)

    def _scan(flt):
        return service.scan_directories_resource(
            ctx['resource_name'], subpath=ctx['fileset'],
            sort_by=flt['sort_by'], limit=flt['limit'],
            owner_uid=flt['owner_uid'],
            accessed_before=flt['accessed_before'],
            accessed_after=flt['accessed_after'],
            leaves_only=flt['leaves_only'],
        )

    return _render_directories_fragment(
        ctx, fragment_url, mode='resource', scan_call=_scan,
        log_label='resource-wide',
    )


@bp.route('/resource/<resource>/explore')
@login_required
@require_permission(Permission.VIEW_ALL_FILESYSTEM_DATA)
def directories_resource_page(resource):
    """Standalone full-page directory explorer for an entire resource.

    Resource mode — the file-browser entry point. Same page template as
    project mode, parameterized by ``mode='resource'`` + the resource fragment
    URL; header shows the resource and a fileset breadcrumb. Elevated route.
    """
    ctx = _resource_ctx(resource)
    flt = _dir_filters()
    fragment_url = url_for('disk_scans.directories_resource_fragment',
                           resource=resource)
    return render_template(
        'dashboards/user/disk_scans_directories_page.html',
        mode='resource', fragment_url=fragment_url,
        initial_url=_initial_fragment_url(fragment_url, ctx, flt),
        filters=flt, user_search_url=_user_search_url(),
        limit_options=_LIMIT_OPTIONS, **ctx,
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
