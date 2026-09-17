"""The Invitations tab of Manage Project: invite a person who has no account.

Routes on the ``project_members`` blueprint (imported at the bottom of that
module), because the surface is project-scoped like the members table and
reachable from the same page. Three ways to the same rows: one invitation,
an event (a cohort with a code and a deadline), a roster pasted under it.
Guards: the project's stewards (tree walked) or the event's extra sponsor,
or MANAGE_ACCOUNT_REQUESTS. Design: docs/plans/ACCOUNT_REGISTRATION.md 3.1.
"""

from datetime import datetime

from flask import render_template, request, url_for
from flask_login import current_user, login_required

from sam.core.account_requests import AccountRequest, AccountRequestEvent
from sam.core.users import User
from sam.manage import management_transaction
from sam.manage.account_requests import (
    OUTCOME_ADDED,
    OUTCOME_DUPLICATE,
    OUTCOME_QUEUED,
    invite_user,
    parse_roster,
    paste_roster,
)
from sam.queries.account_requests import events_for, readiness_of, resolve_requests, waiting_days
from sam.schemas.forms import (
    AccountRequestEventEditForm,
    AccountRequestEventForm,
    InviteUserForm,
    RosterPasteForm,
)
from webapp.api.access_control import require_event_sponsor_access, require_project_permission
from webapp.extensions import db
from webapp.utils.form_handler import FormError, HtmxFormHandler
from webapp.utils.htmx import handle_htmx_form_post, htmx_success, htmx_success_message
from webapp.utils.rbac import Permission

from .project_members import bp

_TAB = 'project_members/fragments/invitations_tab_htmx.html'
_INVITE_FORM = 'project_members/fragments/invite_form_htmx.html'
_EVENT_FORM = 'project_members/fragments/event_form_htmx.html'
_ROSTER_FORM = 'project_members/fragments/roster_form_htmx.html'
_ROSTER_RESULT = 'project_members/fragments/roster_result_htmx.html'
_FORM_TARGET = '#invitationFormContainer'
_TRIGGERS = {'closeActiveModal': {}, 'refreshInvitations': {}, 'refreshAccountQueue': {}}

_GUARD = require_project_permission(Permission.MANAGE_ACCOUNT_REQUESTS,
                                    include_ancestors=True)
_EVENT_GUARD = require_event_sponsor_access(Permission.MANAGE_ACCOUNT_REQUESTS)


def _sponsor():
    """The acting user's SAM row; the sponsor recorded on every row written here."""
    return db.session.get(User, current_user.user_id)


def _project_events(project):
    return (db.session.query(AccountRequestEvent)
            .filter(AccountRequestEvent.project_id == project.project_id)
            .order_by(AccountRequestEvent.active.desc(),
                      AccountRequestEvent.accounts_needed_by).all())


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
    today = datetime.now().date()
    by_event = {}
    for row in rows:
        by_event.setdefault(row.event_id, []).append(row)
    event_rows = []
    for event in events:
        members = by_event.get(event.account_request_event_id, [])
        open_members = [r for r in members if r.state in ('submitted', 'claimed')]
        event_rows.append({
            'event': event,
            'total': len(members),
            'fulfilled': sum(1 for r in members if r.is_fulfilled),
            'open': len(open_members),
            'sponsor': (db.session.get(User, event.extra_sponsor_user_id)
                        if event.extra_sponsor_user_id else None),
        })
    all_events = events_for(db.session, rows)
    views = [{
        'row': r,
        'readiness': readiness_of(r, resolutions.get(r.account_request_id)),
        'event': all_events.get(r.event_id) if r.event_id else None,
        'waiting_days': waiting_days(r, today=today),
    } for r in rows]
    return render_template(_TAB, project=project, events=event_rows, rows=views,
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
        return {'project': self.project, 'events': _project_events(self.project),
                'post_url': url_for('project_members.htmx_invite_user',
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


@bp.route('/<projcode>/invite-form')
@login_required
@_GUARD
def htmx_invite_form(project):
    return render_template(_INVITE_FORM, project=project, events=_project_events(project),
                           post_url=url_for('project_members.htmx_invite_user',
                                            projcode=project.projcode), errors=[])


@bp.route('/<projcode>/invite', methods=['POST'])
@login_required
@_GUARD
def htmx_invite_user(project):
    return _InviteUserHandler(project=project).handle()


# -- events -------------------------------------------------------------------

def _resolve_sponsor(username):
    """The extra sponsor's SAM row, or a FormError naming the problem."""
    if not username:
        return None
    user = User.get_by_username(db.session, username)
    if user is None or not user.is_active:
        raise FormError(f'No active SAM user named {username}.')
    return user


@bp.route('/<projcode>/events/new-form')
@login_required
@_GUARD
def htmx_event_form(project):
    return render_template(_EVENT_FORM, project=project, event=None,
                           post_url=url_for('project_members.htmx_event_create',
                                            projcode=project.projcode), errors=[])


@bp.route('/<projcode>/events', methods=['POST'])
@login_required
@_GUARD
def htmx_event_create(project):
    def _create(data):
        code = data['event_code']
        if db.session.query(AccountRequestEvent).filter_by(event_code=code).first():
            raise FormError(f'The code {code} is already in use.')
        sponsor = _resolve_sponsor(data.get('extra_sponsor_username'))
        return AccountRequestEvent.create(
            db.session, event_code=code, name=data['name'],
            project_id=project.project_id,
            accounts_needed_by=data['accounts_needed_by'],
            opens_at=data.get('opens_at'), closes_at=data.get('closes_at'),
            extra_sponsor_user_id=sponsor.user_id if sponsor else None,
            created_by=current_user.username)

    return handle_htmx_form_post(
        schema_cls=AccountRequestEventForm, template=_EVENT_FORM,
        do_action=_create, success_triggers=_TRIGGERS,
        success_message='Event created. Hand the code out; registrations '
                        'and pasted rosters land under it.',
        error_prefix='Error creating event',
        extra_context={'project': project, 'event': None,
                       'post_url': url_for('project_members.htmx_event_create',
                                           projcode=project.projcode)},
    )


class _EventEditHandler(HtmxFormHandler):
    """PUT: gated on the keys present in the ORIGINAL form, not the loaded
    output -- load_default fills absent fields with None and would clear them."""
    schema_cls = AccountRequestEventEditForm
    template = _EVENT_FORM
    partial = True

    def perform(self, data):
        sent = request.form
        updates = {}
        if 'name' in sent:
            updates['name'] = data.get('name')
        if 'accounts_needed_by' in sent and data.get('accounts_needed_by'):
            updates['accounts_needed_by'] = data['accounts_needed_by']
        if 'opens_at' in sent:
            updates['opens_at'] = data.get('opens_at')
        if 'closes_at' in sent:
            updates['closes_at'] = data.get('closes_at')
        if 'extra_sponsor_username' in sent:
            sponsor = _resolve_sponsor(data.get('extra_sponsor_username'))
            updates['extra_sponsor_user_id'] = sponsor.user_id if sponsor else None
        return self.event.update(**updates)

    def context(self):
        return {'project': self.project, 'event': self.event,
                'post_url': url_for('project_members.htmx_event_update',
                                    event_code=self.event.event_code)}

    def on_success(self, result):
        return htmx_success_message(_TRIGGERS, f'Saved {self.event.event_code}.')


@bp.route('/events/<event_code>/edit-form')
@login_required
@_EVENT_GUARD
def htmx_event_edit_form(event, project):
    sponsor = db.session.get(User, event.extra_sponsor_user_id) if event.extra_sponsor_user_id else None
    return render_template(_EVENT_FORM, project=project, event=event,
                           sponsor_username=sponsor.username if sponsor else '',
                           post_url=url_for('project_members.htmx_event_update',
                                            event_code=event.event_code), errors=[])


@bp.route('/events/<event_code>', methods=['POST', 'PUT'])
@login_required
@_EVENT_GUARD
def htmx_event_update(event, project):
    """Partial update; POST as well as PUT because the shared form wrapper
    emits hx-post. The gating on sent keys is what makes it a partial."""
    return _EventEditHandler(event=event, project=project).handle()


def _switch(event, verb):
    with management_transaction(db.session):
        (event.close if verb == 'close' else event.reopen)()
    return htmx_success_message(
        {'refreshInvitations': {}},
        f'{event.event_code} {"closed" if verb == "close" else "reopened"}.')


@bp.route('/events/<event_code>/close', methods=['POST'])
@login_required
@_EVENT_GUARD
def htmx_event_close(event, project):
    """Stop accepting the code on the public form; existing rows are untouched."""
    return _switch(event, 'close')


@bp.route('/events/<event_code>/reopen', methods=['POST'])
@login_required
@_EVENT_GUARD
def htmx_event_reopen(event, project):
    return _switch(event, 'reopen')


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
                'post_url': url_for('project_members.htmx_roster_paste',
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
                           post_url=url_for('project_members.htmx_roster_paste',
                                            event_code=event.event_code), errors=[])


@bp.route('/events/<event_code>/roster', methods=['POST'])
@login_required
@_EVENT_GUARD
def htmx_roster_paste(event, project):
    return _RosterHandler(event=event, project=project).handle()
