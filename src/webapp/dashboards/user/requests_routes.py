"""User dashboard -> My Requests: the user's XRAS requests, and what each project may file.

The page renders from SAM's tables and the sweep's index, never a live XRAS read; each
project's picker reads XRAS lazily when opened (the family, then the user's offers).
Design: ``docs/plans/XRAS_SUBMISSION.md`` § 4.0, routing § 4.4.
"""

from flask import current_app, render_template, request
from flask_login import current_user, login_required

from sam.integration.xras_api import XrasSourceUnavailable
from sam.queries.xras_my_requests import led_projects, my_requests
from sam.xras.routing import action_offers, request_routing, route_for
from webapp.api.access_control import require_project_permission
from webapp.extensions import db
from webapp.utils.rbac import Permission

from .blueprint import _page_context, bp


def _config():
    from sam.integration.xras_api.config import XrasApiConfig
    return XrasApiConfig.from_environment().interactive()


def _index_rows():
    from sam.integration.xras_api.cache import load_requests_index
    return (load_requests_index() or {}).get('rows') or []


def _type_name(project):
    return project.allocation_type.allocation_type if project.allocation_type else None


@bp.route('/requests')
@login_required
def my_requests_page():
    """My Requests: the status of every request, then one picker per led or administered project."""
    ctx = _page_context()
    user = ctx['user']
    projects = led_projects(db.session, user)
    return render_template(
        'dashboards/user/my_requests.html',
        requests=my_requests(db.session, user.username, _index_rows()),
        projects=[(p, route_for(_type_name(p))) for p in projects],
        open_projcode=(request.args.get('projcode') or '').strip().upper() or None,
        submit_configured=_config().submit_configured,
        **ctx)


@bp.route('/requests/<projcode>/actions')
@login_required
@require_project_permission(Permission.EDIT_PROJECTS)
def my_requests_actions(project):
    """One project's offers, each enabled or disabled with its reason."""
    from sam.integration.xras_api import XrasApiClient
    from sam.integration.xras_api.admin_client import XrasAdminClient

    config = _config()
    username = current_user.username
    armed = config.submit_configured
    family, available, outage = None, [], None
    if armed and route_for(_type_name(project)) == 'xras':
        try:
            family = XrasApiClient.from_environment(config).get_request_family_by_number(
                project.projcode)
        except XrasSourceUnavailable as exc:
            current_app.logger.warning('my requests family read %s: %s', project.projcode, exc)
            outage = 'XRAS is not answering, so this project\'s actions cannot be offered now.'
    decision = request_routing(db.session, project, family, username, armed=armed) \
        if outage is None else None
    if decision is not None and decision.allowed:
        try:
            available = XrasAdminClient.from_environment(config).available_action_requests(
                username)
        except XrasSourceUnavailable as exc:
            current_app.logger.warning('my requests offers read %s: %s', username, exc)
            available = None
    return render_template(
        'dashboards/user/fragments/request_actions_htmx.html',
        project=project, decision=decision, outage=outage,
        offers=action_offers(decision, available, config.submit_allows) if decision else [])
