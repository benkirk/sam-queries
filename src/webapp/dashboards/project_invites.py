"""The Invitations tab of Manage Project: invite a person who has no account.

Its own blueprint and URL prefix (sharing the members' prefix would let
that blueprint's ``/<projcode>`` rule answer these paths once unmounted),
mounted by ``create_app`` only when ``ACCOUNT_INVITATIONS_ENABLED`` is on --
the ``register`` idiom, so the tab ships dark in prod and every route 404s
there. Three ways to the same rows: one invitation, an event (a cohort with
a code and a deadline), a roster pasted under it. Guards: the project's
stewards (tree walked) or the event's extra sponsor, or
MANAGE_ACCOUNT_REQUESTS (MANAGE_EVENTS for an event's own lifecycle). Design: docs/plans/implemented/ACCOUNT_REGISTRATION.md 3.1.
"""

import logging
from datetime import datetime

from flask import Blueprint, current_app, render_template, request, url_for
from flask_login import current_user, login_required
from marshmallow import ValidationError

from sam import fmt
from sam.core.account_requests import OPEN_STATES, AccountRequest, AccountRequestEvent
from sam.core.users import User
from sam.manage.account_requests import (
    OUTCOME_ADDED,
    OUTCOME_AMBIGUOUS,
    OUTCOME_DUPLICATE,
    OUTCOME_QUEUED,
    invite_outcomes,
    invite_user,
)
from sam.queries.account_requests import (
    enrollees_for_event, event_sponsors, events_for, request_views,
    resolve_requests,
)
from sam.schemas.forms import InviteUserForm
from webapp.api.access_control import (
    require_event_sponsor_access, require_project_facility_permission,
    require_project_permission,
)
from webapp.dashboards.event_lifecycle import (
    EVENT_FORM, ROSTER_FORM, EventCreateHandler, EventEditHandler, RosterHandler,
    date_floor, roster_preview, sponsor_context, switch_event,
)
from webapp.extensions import db
from webapp.register.handoff_mail import send_ticket
from webapp.register.invite_mail import (
    DELIVERED, can_send_invite, invite_messages, placeholder_link, preview_invite_rows,
    send_invite_links,
)
from webapp.utils.email_preview import (
    email_preview_context, render_email_preview, render_preview_info,
)
from webapp.utils.form_handler import FormError, HtmxFormHandler
from webapp.utils.htmx import (
    htmx_not_found, htmx_success, htmx_success_message, institution_options,
)
from webapp.utils.notify import public_url_for
from webapp.utils.project_permissions import can_create_events
from webapp.utils.rbac import Permission, has_permission_any_facility

logger = logging.getLogger(__name__)
bp = Blueprint('project_invites', __name__, url_prefix='/project-invitations')

_TAB = 'project_members/fragments/invitations_tab_htmx.html'
_INVITE_FORM = 'project_members/fragments/invite_form_htmx.html'
_RESEND_PREVIEW = 'project_members/fragments/resend_invite_preview_htmx.html'
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


def _ttl_days():
    return int(current_app.config.get('ACCOUNT_INVITE_TTL_DAYS', 30))


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
            'reg_url': public_url_for('register_events.form_for_event',
                                      event_code=event.event_code),
        })
    views = request_views(db.session, rows, resolutions=resolutions,
                          events=events_for(db.session, rows))
    for v in views:
        v['can_invite'] = can_send_invite(v['row'])
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
        self.send_invite = bool(data.get('send_invite'))
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

    def after_commit(self, result):
        """With a link, NUSD's ticket waits for the invitee; without, it goes now."""
        outcome, obj = result
        self.invite = None
        if outcome != OUTCOME_QUEUED:
            return
        if self.send_invite:
            self.invite, = send_invite_links([obj], requested_by=current_user.username)
        else:
            send_ticket(obj, requested_by=current_user.username)

    def context(self):
        events = _project_events(self.project)
        return {'project': self.project, 'events': events, 'invite_ttl_days': _ttl_days(),
                'default_event_code': _default_event_code(events),
                'post_url': url_for('project_invites.htmx_invite_user',
                                    projcode=self.project.projcode),
                'preview_url': url_for('project_invites.htmx_invite_preview',
                                       projcode=self.project.projcode)}

    def on_success(self, result):
        outcome, obj = result
        if outcome == OUTCOME_ADDED:
            message = f'{obj.display_name} already had an account and is now a member.'
        elif outcome == OUTCOME_DUPLICATE:
            message = f'{obj.email} is already waiting on this project; nothing added.'
        else:
            message = f'Queued {obj.display_name} for an account; NUSD will be told.'
        return htmx_success_message(_TRIGGERS, message, detail=_invite_detail(self.invite, obj))


def _invite_detail(result, row):
    """One line on the invitation link, or None when none was asked for."""
    if result is None:
        return None
    if result.status in DELIVERED:
        return f'Invitation link {result.status} to {row.email}.'
    if result.status == 'suppressed':
        return 'No invitation link sent: mail is off on this deployment.'
    return f'No invitation link sent: {result.detail or result.status}.'


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
                           invite_ttl_days=_ttl_days(),
                           default_event_code=_default_event_code(events),
                           post_url=url_for('project_invites.htmx_invite_user',
                                            projcode=project.projcode),
                           preview_url=url_for('project_invites.htmx_invite_preview',
                                               projcode=project.projcode), errors=[])


@bp.route('/<projcode>/invite', methods=['POST'])
@login_required
@_GUARD
def htmx_invite_user(project):
    return _InviteUserHandler(project=project).handle()


_NO_INVITE_MAIL = {
    OUTCOME_ADDED: ('{email} already has an active account ({username}): Invite '
                    'adds them to {projcode} at once and sends no email.', 'info'),
    OUTCOME_DUPLICATE: ('{email} is already waiting on {projcode}: Invite adds '
                        'nothing and sends no email.', 'info'),
    OUTCOME_AMBIGUOUS: ('{email} belongs to more than one active SAM user: add '
                        'the member by username instead.', 'warning'),
}


@bp.route('/<projcode>/invite-preview', methods=['POST'])
@login_required
@_GUARD
def htmx_invite_preview(project):
    """The invitation Invite would send, from the form as typed. Writes nothing."""
    try:
        data = InviteUserForm().load(request.form)
    except ValidationError:
        return render_preview_info('Fill in the email and both names to preview '
                                   'the invitation.')
    event = _event_for(project, data.get('event_code'))
    if data.get('event_code') and event is None:
        return render_preview_info(f'{data["event_code"]} is not an event on '
                                   f'{project.projcode}.', 'warning')
    email = data['email']
    outcome, username = invite_outcomes(db.session, project.project_id, [email])[email]
    if outcome != OUTCOME_QUEUED:
        text, level = _NO_INVITE_MAIL[outcome]
        return render_preview_info(
            text.format(email=email, username=username, projcode=project.projcode), level)
    rows = preview_invite_rows([data], project_id=project.project_id,
                               sponsor=_sponsor(), event=event)
    notes = [f'The link is created when you click Invite and is valid for '
             f'{_ttl_days()} days.']
    if not data.get('send_invite'):
        notes.append('The box is unticked: Invite queues the request and sends no email.')
    return render_email_preview(
        invite_messages(rows, sent_at=datetime.now(), requested_by=current_user.username,
                        link_for=placeholder_link),
        id_prefix='invitePreview', pane_id='invitePreviewPane', notes=notes)


def _resend_target(project, request_id):
    row = db.session.get(AccountRequest, request_id)
    return row if row is not None and row.project_id == project.project_id else None


@bp.route('/<projcode>/requests/<int:request_id>/invite-preview')
@login_required
@_GUARD
def htmx_resend_invite_preview(project, request_id):
    """Modal body: the link mail as Send link would mail it, plus Send."""
    row = _resend_target(project, request_id)
    pane = None
    if row is not None and can_send_invite(row):
        notes = ([f'The link sent {fmt.date_str(row.invite_sent_at)} stops working '
                  'when you send this one.'] if row.invite_sent_at else [])
        pane = email_preview_context(
            invite_messages([row], sent_at=datetime.now(),
                            requested_by=current_user.username,
                            link_for=placeholder_link),
            id_prefix='resendPreview', pane_id='resendPreviewPane', notes=notes)
    return render_template(_RESEND_PREVIEW, project=project, row=row, **(pane or {}))


@bp.route('/<projcode>/requests/<int:request_id>/resend-invite', methods=['POST'])
@login_required
@_GUARD
def htmx_resend_invite(project, request_id):
    """Mail a fresh link; re-stamping ``invite_sent_at`` voids every older one."""
    row = _resend_target(project, request_id)
    if row is None:
        return htmx_not_found('Invitation')
    if not can_send_invite(row):
        return _toast_error(f'{row.email} cannot be sent an invitation link now.')
    result, = send_invite_links([row], requested_by=current_user.username)
    logger.info('invite link for request %s: %s by %s', row.account_request_id,
                result.status, current_user.username)
    if result.status in DELIVERED:
        return htmx_success_message(
            {'closeActiveModal': {}, 'refreshInvitations': {}, 'refreshAccountQueue': {}},
            f'Invitation link {result.status} to {row.email}.')
    return _toast_error(_invite_detail(result, row))


def _toast_error(message):
    """A danger toast for an ``hx-swap="none"`` button; no reload trigger."""
    return htmx_success('dashboards/fragments/htmx_success.html', {},
                        toast=message, toast_variant='danger', message=message)


# -- events -------------------------------------------------------------------
# The lifecycle itself lives in event_lifecycle.py, shared with Admin -> Events.

def _event_form_context(project, event=None):
    post_url = (url_for('project_invites.htmx_event_update', event_code=event.event_code)
                if event else
                url_for('project_invites.htmx_event_create', projcode=project.projcode))
    return {'project': project, 'event': event, 'post_url': post_url,
            'can_list': can_create_events(current_user, project),
            'date_floor': date_floor(event)}


@bp.route('/<projcode>/events/new-form')
@login_required
@_CREATE_GUARD
def htmx_event_form(project):
    return render_template(EVENT_FORM, errors=[], **_event_form_context(project))


class _EventCreateHandler(EventCreateHandler):
    triggers = _TRIGGERS
    message = 'Event created. Hand the code out; registrations and pasted rosters land under it.'

    def context(self):
        return _event_form_context(self.project)


@bp.route('/<projcode>/events', methods=['POST'])
@login_required
@_CREATE_GUARD
def htmx_event_create(project):
    return _EventCreateHandler(project=project).handle()


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

class _RosterHandler(RosterHandler):
    post_endpoint = 'project_invites.htmx_roster_paste'
    preview_endpoint = 'project_invites.htmx_roster_preview'
    triggers = {'refreshInvitations': {}, 'refreshAccountQueue': {}}


@bp.route('/events/<event_code>/roster-form')
@login_required
@_EVENT_GUARD
def htmx_roster_form(event, project):
    return render_template(ROSTER_FORM, project=project, event=event,
                           post_url=url_for('project_invites.htmx_roster_paste',
                                            event_code=event.event_code),
                           preview_url=url_for('project_invites.htmx_roster_preview',
                                               event_code=event.event_code), errors=[])


@bp.route('/events/<event_code>/roster', methods=['POST'])
@login_required
@_EVENT_GUARD
def htmx_roster_paste(event, project):
    return _RosterHandler(event=event, project=project).handle()


@bp.route('/events/<event_code>/roster-preview', methods=['POST'])
@login_required
@_EVENT_GUARD
def htmx_roster_preview(event, project):
    return roster_preview(event, url_for('project_invites.htmx_roster_preview',
                                         event_code=event.event_code))
