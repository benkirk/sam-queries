"""The Invitations tab of Manage Project: invite a person who has no account.

Its own blueprint and URL prefix (sharing the members' prefix would let
that blueprint's ``/<projcode>`` rule answer these paths once unmounted),
mounted by ``create_app`` only when ``ACCOUNT_INVITATIONS_ENABLED`` is on --
the ``register`` idiom, so the tab ships dark in prod and every route 404s
there. Three ways to the same rows: one invitation, an event (a cohort with
a code and a deadline), a roster pasted under it. Guards: the project's
stewards (tree walked) or the event's extra sponsor, or
MANAGE_ACCOUNT_REQUESTS (MANAGE_EVENTS for an event's own lifecycle). Design: docs/plans/ACCOUNT_REGISTRATION.md 3.1.
"""

from flask import Blueprint, render_template, request, url_for
from flask_login import current_user, login_required

from sam.core.account_requests import OPEN_STATES, AccountRequest, AccountRequestEvent
from sam.core.users import User
from sam.manage.account_requests import (
    OUTCOME_ADDED,
    OUTCOME_DUPLICATE,
    OUTCOME_QUEUED,
    invite_user,
    parse_roster,
    paste_roster,
)
from sam.queries.account_requests import (
    enrollees_for_event, event_sponsors, events_for, request_views,
    resolve_requests,
)
from sam.schemas.forms import (
    AccountRequestEventForm,
    InviteUserForm,
    RosterPasteForm,
)
from webapp.api.access_control import (
    require_event_sponsor_access, require_project_facility_permission,
    require_project_permission,
)
from webapp.dashboards.event_lifecycle import (
    EVENT_FORM, EventEditHandler, create_event, invalidate_upcoming_events,
    sponsor_context, switch_event,
)
from webapp.extensions import db
from webapp.utils.form_handler import FormError, HtmxFormHandler
from webapp.utils.htmx import (
    handle_htmx_form_post, htmx_success, htmx_success_message, institution_options,
)
from webapp.utils.project_permissions import can_create_events
from webapp.utils.rbac import Permission, has_permission_any_facility

bp = Blueprint('project_invites', __name__, url_prefix='/project-invitations')

_TAB = 'project_members/fragments/invitations_tab_htmx.html'
_INVITE_FORM = 'project_members/fragments/invite_form_htmx.html'
_ROSTER_FORM = 'project_members/fragments/roster_form_htmx.html'
_ROSTER_RESULT = 'project_members/fragments/roster_result_htmx.html'
_FORM_TARGET = '#invitationFormContainer'
_TRIGGERS = {'closeActiveModal': {}, 'refreshInvitations': {}, 'refreshAccountQueue': {}}

_GUARD = require_project_permission(Permission.MANAGE_ACCOUNT_REQUESTS,
                                    include_ancestors=True)
# Rosters create account requests, so they stay on MANAGE_ACCOUNT_REQUESTS; the
# event's own lifecycle is MANAGE_EVENTS. Both admit a steward or the event's
# extra sponsor.
_EVENT_GUARD = require_event_sponsor_access(Permission.MANAGE_ACCOUNT_REQUESTS)
_EVENT_MANAGE_GUARD = require_event_sponsor_access(Permission.MANAGE_EVENTS)
# Creating an event (minting a code) is operator-only: MANAGE_EVENTS for the
# project's facility, NO lead/admin override -- a PI cannot open a code and
# surprise the operators.
_CREATE_GUARD = require_project_facility_permission(Permission.MANAGE_EVENTS)


def _sponsor():
    """The acting user's SAM row; the sponsor recorded on every row written here."""
    return db.session.get(User, current_user.user_id)


def _project_events(project):
    return (db.session.query(AccountRequestEvent)
            .filter(AccountRequestEvent.project_id == project.project_id)
            .order_by(AccountRequestEvent.active.desc(),
                      AccountRequestEvent.accounts_needed_by).all())


def _default_event_code(events):
    """Pre-select the sole active event so an invitation defaults into its
    cohort (the common case: one workshop running). Stay neutral (— none —)
    when there are zero or several active events, where a guess could misfile."""
    active = [e for e in events if e.active]
    return active[0].event_code if len(active) == 1 else ''


def _event_for(project, code):
    """This project's event by code, or None."""
    if not code:
        return None
    return (db.session.query(AccountRequestEvent)
            .filter(AccountRequestEvent.project_id == project.project_id,
                    AccountRequestEvent.event_code == code).first())


@bp.route('/<projcode>/invitations')
@login_required
@_GUARD
def invitations_fragment(project):
    """The tab: this project's events with progress, and its invitations."""
    rows = (db.session.query(AccountRequest)
            .filter(AccountRequest.project_id == project.project_id)
            .order_by(AccountRequest.creation_time.desc()).all())
    resolutions = resolve_requests(db.session, rows)
    events = _project_events(project)
    sponsors = event_sponsors(db.session, events)
    by_event = {}
    for row in rows:
        by_event.setdefault(row.event_id, []).append(row)
    base_url = request.url_root.rstrip('/')
    event_rows = []
    for event in events:
        members = by_event.get(event.account_request_event_id, [])
        event_rows.append({
            'event': event,
            'total': len(members),
            'fulfilled': sum(1 for r in members if r.is_fulfilled),
            'open': sum(1 for r in members if r.state in OPEN_STATES),
            'sponsor': sponsors.get(event.extra_sponsor_user_id),
            'enrollees': enrollees_for_event(db.session, event.account_request_event_id),
            # Built by hand, not url_for: the register blueprint is unmounted in
            # prod (ACCOUNT_REGISTRATION_ENABLED off), so url_for would BuildError.
            'reg_url': f'{base_url}/register/{event.event_code}',
        })
    views = request_views(db.session, rows, resolutions=resolutions,
                          events=events_for(db.session, rows))
    return render_template(_TAB, project=project, events=event_rows, rows=views,
                           can_view_users=has_permission_any_facility(
                               current_user, Permission.VIEW_USERS),
                           open_count=sum(1 for r in rows
                                          if r.is_open and r.user_id is None))


# -- invite one person --------------------------------------------------------

class _InviteUserHandler(HtmxFormHandler):
    schema_cls = InviteUserForm
    template = _INVITE_FORM

    def clean(self, data):
        self.event = None
        if data.get('event_code'):
            self.event = _event_for(self.project, data['event_code'])
            if self.event is None:
                raise FormError(f'{data["event_code"]} is not an event on '
                                f'{self.project.projcode}.')
        return data

    def perform(self, data):
        return invite_user(
            db.session, project_id=self.project.project_id, sponsor=_sponsor(),
            email=data['email'], first_name=data['first_name'],
            last_name=data['last_name'], note=data.get('note'),
            organization=data.get('organization'), event=self.event)

    def context(self):
        events = _project_events(self.project)
        return {'project': self.project, 'events': events,
                'default_event_code': _default_event_code(events),
                'post_url': url_for('project_invites.htmx_invite_user',
                                    projcode=self.project.projcode)}

    def on_success(self, result):
        outcome, obj = result
        if outcome == OUTCOME_ADDED:
            message = f'{obj.display_name} already had an account and is now a member.'
        elif outcome == OUTCOME_DUPLICATE:
            message = f'{obj.email} is already waiting on this project; nothing added.'
        else:
            message = f'Queued {obj.display_name} for an account; NUSD will be told.'
        return htmx_success_message(_TRIGGERS, message)


@bp.route('/institutions')
@login_required
def institutions_fragment():
    """Datalist options for the invite form's Institution field (login only:
    institution names are not sensitive, and a plain project lead uses this).
    The ``_fragment`` suffix keeps it out of the e2e page sweep (e2e/conftest.py)."""
    return institution_options()


@bp.route('/<projcode>/invite-form')
@login_required
@_GUARD
def htmx_invite_form(project):
    events = _project_events(project)
    return render_template(_INVITE_FORM, project=project, events=events,
                           default_event_code=_default_event_code(events),
                           post_url=url_for('project_invites.htmx_invite_user',
                                            projcode=project.projcode), errors=[])


@bp.route('/<projcode>/invite', methods=['POST'])
@login_required
@_GUARD
def htmx_invite_user(project):
    return _InviteUserHandler(project=project).handle()


# -- events -------------------------------------------------------------------
# The lifecycle itself lives in event_lifecycle.py, shared with Admin -> Events.

def _event_form_context(project, event=None):
    post_url = (url_for('project_invites.htmx_event_update', event_code=event.event_code)
                if event else
                url_for('project_invites.htmx_event_create', projcode=project.projcode))
    return {'project': project, 'event': event, 'post_url': post_url,
            'can_list': can_create_events(current_user, project)}


@bp.route('/<projcode>/events/new-form')
@login_required
@_CREATE_GUARD
def htmx_event_form(project):
    return render_template(EVENT_FORM, errors=[], **_event_form_context(project))


@bp.route('/<projcode>/events', methods=['POST'])
@login_required
@_CREATE_GUARD
def htmx_event_create(project):
    return handle_htmx_form_post(
        schema_cls=AccountRequestEventForm, template=EVENT_FORM,
        do_action=lambda data: create_event(data, project),
        success_triggers=_TRIGGERS,
        success_message='Event created. Hand the code out; registrations '
                        'and pasted rosters land under it.',
        error_prefix='Error creating event',
        extra_context=_event_form_context(project),
        after_commit=lambda _event: invalidate_upcoming_events(),
    )


class _EventEditHandler(EventEditHandler):
    triggers = _TRIGGERS

    def context(self):
        return _event_form_context(self.project, self.event)


@bp.route('/events/<event_code>/edit-form')
@login_required
@_EVENT_MANAGE_GUARD
def htmx_event_edit_form(event, project):
    return render_template(EVENT_FORM, errors=[], **sponsor_context(event),
                           **_event_form_context(project, event))


@bp.route('/events/<event_code>', methods=['POST', 'PUT'])
@login_required
@_EVENT_MANAGE_GUARD
def htmx_event_update(event, project):
    """Partial update; POST as well as PUT because the shared form wrapper
    emits hx-post. The gating on sent keys is what makes it a partial."""
    return _EventEditHandler(event=event, project=project).handle()


@bp.route('/events/<event_code>/close', methods=['POST'])
@login_required
@_EVENT_MANAGE_GUARD
def htmx_event_close(event, project):
    """Stop accepting the code on the public form; existing rows are untouched."""
    return switch_event(event, 'close', {'refreshInvitations': {}})


@bp.route('/events/<event_code>/reopen', methods=['POST'])
@login_required
@_EVENT_MANAGE_GUARD
def htmx_event_reopen(event, project):
    return switch_event(event, 'reopen', {'refreshInvitations': {}})


# -- roster -------------------------------------------------------------------

class _RosterHandler(HtmxFormHandler):
    schema_cls = RosterPasteForm
    template = _ROSTER_FORM

    def clean(self, data):
        entries, errors = parse_roster(data['roster'])
        if errors:
            raise FormError(*errors)
        if not entries:
            raise FormError('No people found; one "Name <email>" per line.')
        data['entries'] = entries
        return data

    def perform(self, data):
        return paste_roster(db.session, event=self.event, sponsor=_sponsor(),
                            entries=data['entries'])

    def context(self):
        return {'project': self.project, 'event': self.event,
                'post_url': url_for('project_invites.htmx_roster_paste',
                                    event_code=self.event.event_code)}

    def on_success(self, result):
        # The summary replaces the form inside the still-open modal: an
        # operator pasting thirty lines wants to see which three were known.
        counts = {k: len(v) for k, v in result.items()}
        return htmx_success(_ROSTER_RESULT,
                            {'refreshInvitations': {}, 'refreshAccountQueue': {}},
                            toast=(f"{counts[OUTCOME_QUEUED]} queued, "
                                   f"{counts[OUTCOME_ADDED]} added, "
                                   f"{counts[OUTCOME_DUPLICATE]} already waiting"),
                            event=self.event, project=self.project, outcomes=result)


@bp.route('/events/<event_code>/roster-form')
@login_required
@_EVENT_GUARD
def htmx_roster_form(event, project):
    return render_template(_ROSTER_FORM, project=project, event=event,
                           post_url=url_for('project_invites.htmx_roster_paste',
                                            event_code=event.event_code), errors=[])


@bp.route('/events/<event_code>/roster', methods=['POST'])
@login_required
@_EVENT_GUARD
def htmx_roster_paste(event, project):
    return _RosterHandler(event=event, project=project).handle()
