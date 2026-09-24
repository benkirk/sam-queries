"""Admin -> Events: every account-request event, across projects.

Gated on MANAGE_EVENTS and always mounted, unlike the per-project Invitations
tab (dark behind ACCOUNT_INVITATIONS_ENABLED). The lifecycle is the shared
``dashboards/event_lifecycle.py``. A facility-scoped holder sees and acts on
only the events whose project sits in their facilities. A roster creates
account requests, so its two routes are MANAGE_ACCOUNT_REQUESTS.
"""

from flask import abort, render_template, request, url_for
from flask_login import current_user, login_required

from sam.core.account_requests import AccountRequestEvent
from sam.projects.projects import Project
from sam.queries.account_requests import all_events, enrollees_for_event
from sam.schemas.forms import AccountRequestEventAdminForm
from webapp.dashboards.event_lifecycle import (
    EVENT_FORM, ROSTER_FORM, EventCreateHandler, EventEditHandler, RosterHandler,
    date_floor, sponsor_context, switch_event,
)
from webapp.extensions import db
from webapp.utils.form_handler import FormError
from webapp.utils.htmx import read_active_only, register_typeahead
from webapp.utils.rbac import (
    Permission, has_permission_any_facility, has_permission_for_facility,
    require_permission_any_facility, user_facility_scope,
)

from .blueprint import bp

_TRIGGERS = {'closeActiveModal': {}, 'refreshEvents': {}}
_GUARD = require_permission_any_facility(Permission.MANAGE_EVENTS)
_ROSTER_GUARD = require_permission_any_facility(Permission.MANAGE_ACCOUNT_REQUESTS)


def _in_scope(project, permission):
    return project is not None and has_permission_for_facility(
        current_user, permission, project.facility_name)


def _event_or_404(event_code, permission=Permission.MANAGE_EVENTS):
    """``(event, project)``; 403 when the project is outside the caller's facilities."""
    code = str(event_code or '').strip().upper()
    event = db.session.query(AccountRequestEvent).filter_by(event_code=code).first()
    if event is None:
        abort(404)
    project = db.session.get(Project, event.project_id)
    # A vanished project has no facility: only an unscoped holder may act.
    if not has_permission_for_facility(
            current_user, permission, project.facility_name if project else None):
        abort(403)
    return event, project


def _form_context(event=None, project=None):
    return {
        'event': event, 'project': project, 'can_list': True,
        'date_floor': date_floor(event),
        'project_search_url': (None if event else
                               url_for('admin_dashboard.htmx_project_search_for_event')),
        'post_url': (url_for('admin_dashboard.htmx_admin_event_update',
                             event_code=event.event_code) if event else
                     url_for('admin_dashboard.htmx_admin_event_create')),
    }


@bp.route('/events')
@login_required
@_GUARD
def events():
    """The Events page: one card, loaded by htmx."""
    return render_template('dashboards/admin/events.html')


@bp.route('/events/fragment')
@login_required
@_GUARD
def events_fragment():
    active_only = read_active_only(request.args)
    scope = user_facility_scope(current_user, Permission.MANAGE_EVENTS)
    rows = all_events(db.session, facility_names=scope)
    if active_only:
        rows = [r for r in rows if r['event'].is_active]
    base_url = request.url_root.rstrip('/')
    for r in rows:
        # By hand, not url_for: the register blueprint is unmounted in prod.
        r['reg_url'] = f'{base_url}/register/{r["event"].event_code}'
    return render_template(
        'dashboards/admin/fragments/events_card.html', rows=rows,
        active_only=active_only,
        can_roster=has_permission_any_facility(
            current_user, Permission.MANAGE_ACCOUNT_REQUESTS),
        can_view_projects=has_permission_any_facility(current_user, Permission.VIEW_PROJECTS),
        can_view_users=has_permission_any_facility(current_user, Permission.VIEW_USERS))


@bp.route('/htmx/events/new-form')
@login_required
@_GUARD
def htmx_admin_event_form():
    return render_template(EVENT_FORM, errors=[], **_form_context())


class _AdminEventCreateHandler(EventCreateHandler):
    schema_cls = AccountRequestEventAdminForm
    triggers = _TRIGGERS
    message = 'Event created. Hand the code out.'

    def target_project(self, data):
        project = db.session.get(Project, data['project_id'])
        if project is None:
            raise FormError('That project does not exist.')
        if not _in_scope(project, Permission.MANAGE_EVENTS):
            raise FormError(f'{project.projcode} is outside your facilities.')
        return project

    def context(self):
        return _form_context()


@bp.route('/htmx/events/new', methods=['POST'])
@login_required
@_GUARD
def htmx_admin_event_create():
    return _AdminEventCreateHandler().handle()


class _AdminEventEditHandler(EventEditHandler):
    triggers = _TRIGGERS

    def context(self):
        return _form_context(self.event, self.project)


@bp.route('/events/<event_code>/edit-form')
@login_required
@_GUARD
def htmx_admin_event_edit_form(event_code):
    event, project = _event_or_404(event_code)
    return render_template(EVENT_FORM, errors=[], **sponsor_context(event),
                           **_form_context(event, project))


@bp.route('/events/<event_code>', methods=['POST', 'PUT'])
@login_required
@_GUARD
def htmx_admin_event_update(event_code):
    event, project = _event_or_404(event_code)
    return _AdminEventEditHandler(event=event, project=project).handle()


@bp.route('/events/<event_code>/close', methods=['POST'])
@login_required
@_GUARD
def htmx_admin_event_close(event_code):
    return switch_event(_event_or_404(event_code)[0], 'close', {'refreshEvents': {}})


@bp.route('/events/<event_code>/reopen', methods=['POST'])
@login_required
@_GUARD
def htmx_admin_event_reopen(event_code):
    return switch_event(_event_or_404(event_code)[0], 'reopen', {'refreshEvents': {}})


# -- enrollees and roster -----------------------------------------------------
# Rules carry /htmx/: the e2e console sweep treats any other GET as a page.

@bp.route('/htmx/events/<event_code>/enrollees')
@login_required
@_GUARD
def htmx_admin_event_enrollees(event_code):
    event, _project = _event_or_404(event_code)
    return render_template(
        'project_members/fragments/event_enrollees_htmx.html',
        enrollees=enrollees_for_event(db.session, event.account_request_event_id),
        can_view_users=has_permission_any_facility(current_user, Permission.VIEW_USERS))


class _AdminRosterHandler(RosterHandler):
    post_endpoint = 'admin_dashboard.htmx_admin_event_roster'
    triggers = {'refreshEvents': {}, 'refreshAccountQueue': {}}


def _roster_target(event_code):
    event, project = _event_or_404(event_code, Permission.MANAGE_ACCOUNT_REQUESTS)
    if project is None:
        abort(404)
    return event, project


@bp.route('/htmx/events/<event_code>/roster-form')
@login_required
@_ROSTER_GUARD
def htmx_admin_event_roster_form(event_code):
    event, project = _roster_target(event_code)
    return render_template(
        ROSTER_FORM, project=project, event=event, errors=[],
        post_url=url_for('admin_dashboard.htmx_admin_event_roster',
                         event_code=event.event_code))


@bp.route('/htmx/events/<event_code>/roster', methods=['POST'])
@login_required
@_ROSTER_GUARD
def htmx_admin_event_roster(event_code):
    event, project = _roster_target(event_code)
    return _AdminRosterHandler(event=event, project=project).handle()


def _search_projects(q, active_only):
    from sam.queries.projects import search_projects_by_code_or_title
    projects = search_projects_by_code_or_title(db.session, q, active=True)
    return [p for p in projects if _in_scope(p, Permission.MANAGE_EVENTS)][:10]


register_typeahead(
    bp, rule='/htmx/events/project-search',
    endpoint='htmx_project_search_for_event',
    permission=Permission.MANAGE_EVENTS, any_facility=True,
    search=_search_projects,
    template='dashboards/admin/fragments/project_search_results_fk_htmx.html',
    ctx_key='projects', min_len=1,
)
