"""Admin -> Events: every account-request event, across projects.

Gated on MANAGE_EVENTS and always mounted, unlike the per-project Invitations
tab (dark behind ACCOUNT_INVITATIONS_ENABLED). The lifecycle is the shared
``dashboards/event_lifecycle.py``.
"""

from flask import abort, render_template, request, url_for
from flask_login import current_user, login_required

from sam.core.account_requests import AccountRequestEvent
from sam.projects.projects import Project
from sam.queries.account_requests import all_events
from sam.schemas.forms import AccountRequestEventAdminForm
from webapp.dashboards.event_lifecycle import (
    EVENT_FORM, EventEditHandler, create_event, invalidate_upcoming_events,
    sponsor_context, switch_event,
)
from webapp.extensions import db
from webapp.utils.form_handler import FormError
from webapp.utils.htmx import (
    handle_htmx_form_post, read_active_only, register_typeahead,
)
from webapp.utils.rbac import (
    Permission, has_permission_any_facility, require_permission,
)

from .blueprint import bp

_TRIGGERS = {'closeActiveModal': {}, 'refreshEvents': {}}
_GUARD = require_permission(Permission.MANAGE_EVENTS)


def _event_or_404(event_code):
    code = str(event_code or '').strip().upper()
    event = db.session.query(AccountRequestEvent).filter_by(event_code=code).first()
    if event is None:
        abort(404)
    return event


def _form_context(event=None):
    project = db.session.get(Project, event.project_id) if event else None
    return {
        'event': event, 'project': project, 'can_list': True,
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
    rows = all_events(db.session)
    if active_only:
        rows = [r for r in rows if r['event'].is_active]
    base_url = request.url_root.rstrip('/')
    for r in rows:
        # By hand, not url_for: the register blueprint is unmounted in prod.
        r['reg_url'] = f'{base_url}/register/{r["event"].event_code}'
    return render_template(
        'dashboards/admin/fragments/events_card.html', rows=rows,
        active_only=active_only,
        can_view_projects=has_permission_any_facility(current_user, Permission.VIEW_PROJECTS),
        can_view_users=has_permission_any_facility(current_user, Permission.VIEW_USERS))


@bp.route('/htmx/events/new-form')
@login_required
@_GUARD
def htmx_admin_event_form():
    return render_template(EVENT_FORM, errors=[], **_form_context())


@bp.route('/htmx/events/new', methods=['POST'])
@login_required
@_GUARD
def htmx_admin_event_create():
    def _create(data):
        project = db.session.get(Project, data['project_id'])
        if project is None:
            raise FormError('That project does not exist.')
        return create_event(data, project)

    return handle_htmx_form_post(
        schema_cls=AccountRequestEventAdminForm, template=EVENT_FORM,
        do_action=_create, success_triggers=_TRIGGERS,
        success_message='Event created. Hand the code out.',
        error_prefix='Error creating event',
        extra_context=_form_context(),
        after_commit=lambda _event: invalidate_upcoming_events(),
    )


class _AdminEventEditHandler(EventEditHandler):
    triggers = _TRIGGERS

    def context(self):
        return _form_context(self.event)


@bp.route('/events/<event_code>/edit-form')
@login_required
@_GUARD
def htmx_admin_event_edit_form(event_code):
    event = _event_or_404(event_code)
    return render_template(EVENT_FORM, errors=[], **sponsor_context(event),
                           **_form_context(event))


@bp.route('/events/<event_code>', methods=['POST', 'PUT'])
@login_required
@_GUARD
def htmx_admin_event_update(event_code):
    event = _event_or_404(event_code)
    project = db.session.get(Project, event.project_id)
    return _AdminEventEditHandler(event=event, project=project).handle()


@bp.route('/events/<event_code>/close', methods=['POST'])
@login_required
@_GUARD
def htmx_admin_event_close(event_code):
    return switch_event(_event_or_404(event_code), 'close', {'refreshEvents': {}})


@bp.route('/events/<event_code>/reopen', methods=['POST'])
@login_required
@_GUARD
def htmx_admin_event_reopen(event_code):
    return switch_event(_event_or_404(event_code), 'reopen', {'refreshEvents': {}})


def _search_projects(q, active_only):
    from sam.queries.projects import search_projects_by_code_or_title
    return search_projects_by_code_or_title(db.session, q, active=True)[:10]


register_typeahead(
    bp, rule='/htmx/events/project-search',
    endpoint='htmx_project_search_for_event',
    permission=Permission.MANAGE_EVENTS,
    search=_search_projects,
    template='dashboards/admin/fragments/project_search_results_fk_htmx.html',
    ctx_key='projects', min_len=1,
)
