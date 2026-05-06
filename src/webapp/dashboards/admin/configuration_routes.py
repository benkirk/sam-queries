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

from flask import render_template, current_app
from flask_login import login_required

from webapp.extensions import db
from webapp.utils.rbac import require_permission, Permission
from webapp.utils.config_inspect import gather_runtime_state, gather_server_info

from .blueprint import bp


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
