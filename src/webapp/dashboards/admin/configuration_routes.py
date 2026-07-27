"""
Admin dashboard — Configuration tab.

Read-only surface for sysadmins to inspect the running webapp's
runtime state: active config class, DB binds and pool stats, auth
provider, cache TTLs, audit settings, build SHA, recent audit
entries.

Gated on ``Permission.VIEW_SYSTEM_CONFIG`` so it can be granted
independently of the broader ``SYSTEM_ADMIN`` tier in the future.
The page contains no buttons that mutate, reload, or invalidate
state — anything write-shaped belongs on a separate page.

All values flow through ``webapp.utils.config_inspect`` which is the
sole place that reads from ``app.config`` / ``os.environ`` /
extensions; secrets are masked there before reaching the template.
"""

from flask import render_template, current_app, request
from flask_login import login_required

from webapp.extensions import db
from webapp.caching import caching
from webapp.utils.rbac import require_permission, Permission
from webapp.utils.config_inspect import gather_runtime_state, gather_server_info

from .blueprint import bp

# Categories accepted by caching.clear(); mirrors the JSON API's set
# (webapp.api.v1.admin). None (omitted) clears everything.
_VALID_CACHE_CATEGORIES = {'flask', 'chart', 'usage', 'scans', 'jobs'}


@bp.route('/htmx/configuration', methods=['GET'])
@login_required
@require_permission(Permission.VIEW_SYSTEM_CONFIG)
def htmx_configuration_card():
    """Render the read-only Configuration card fragment for the
    Admin dashboard's Configuration tab.
    """
    state = gather_runtime_state(current_app, db)
    return render_template(
        'dashboards/admin/fragments/configuration_card.html',
        state=state,
    )


@bp.route('/htmx/server', methods=['GET'])
@login_required
@require_permission(Permission.VIEW_SYSTEM_CONFIG)
def htmx_server_card():
    """Render just the Server Information card body. Used by the
    refresh button so admins can re-poll without rebuilding the entire
    Configuration tab — and, since the LB will likely route the refresh
    to a different worker, naturally surfaces the per-worker view.
    """
    return render_template(
        'dashboards/admin/fragments/server_card_body.html',
        server=gather_server_info(),
    )


@bp.route('/htmx/cache/clear', methods=['POST'])
@login_required
@require_permission(Permission.SYSTEM_ADMIN)
def htmx_clear_cache():
    """Clear caches from the Configuration tab's Caching card and re-render
    just that card body with fresh stats + a summary of what was cleared.

    Gated one tier above the read-only card (SYSTEM_ADMIN vs
    VIEW_SYSTEM_CONFIG) because it mutates runtime state. ``?category=`` (one
    of flask|chart|usage|scans) scopes the clear; omit it to clear everything.

    Runs in-process, so it clears *this* worker's caches directly (no HTTP
    round-trip). With Redis configured the shared stores clear globally; with
    the in-process fallback only the worker that served this POST is cleared —
    same worker-affinity caveat as the Server card's refresh button.
    """
    category = request.args.get('category') or None
    if category is not None and category not in _VALID_CACHE_CATEGORIES:
        category = None  # ignore a bad param rather than 500 the fragment
    cleared = caching.clear(category)
    state = gather_runtime_state(current_app, db)
    return render_template(
        'dashboards/admin/fragments/caching_card_body.html',
        cache_state=state['caching'],
        cleared=cleared,
    )
