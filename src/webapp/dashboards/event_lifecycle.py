"""Account-request event lifecycle and roster paste, shared by two blueprints.

``project_invites`` (per project, dark behind ACCOUNT_INVITATIONS_ENABLED) and
Admin -> Events (``admin/events_routes.py``, always mounted) both call these,
so the admin page never depends on the invitations flag. Also owns the memoized
public listing, so every lifecycle write can invalidate it. Every write logs
one line naming the actor: the table has no ``modified_by``.
"""

import logging
from datetime import date, datetime

from flask import current_app, request, url_for
from flask_login import current_user
from marshmallow import ValidationError

from sam.core.account_requests import AccountRequest, AccountRequestEvent
from sam.core.users import User
from sam.manage import management_transaction
from sam.manage.account_requests import (
    OUTCOME_ADDED, OUTCOME_AMBIGUOUS, OUTCOME_DUPLICATE, OUTCOME_QUEUED,
    invite_outcomes, parse_roster, paste_roster,
)
from sam.queries.account_requests import upcoming_listed_events
from sam.schemas.forms import (
    AccountRequestEventEditForm, AccountRequestEventForm, RosterPasteForm,
)
from sam.schemas.forms.account_requests import PAST_DEADLINE_MSG, deadline_notice
from webapp.extensions import cache, db
from webapp.utils.email_preview import render_email_preview, render_preview_info
from webapp.utils.form_handler import FormError, HtmxFormHandler
from webapp.utils.htmx import htmx_success, htmx_success_message
from webapp.utils.project_permissions import can_create_events

logger = logging.getLogger(__name__)

EVENT_FORM = 'project_members/fragments/event_form_htmx.html'
ROSTER_FORM = 'project_members/fragments/roster_form_htmx.html'
ROSTER_RESULT = 'project_members/fragments/roster_result_htmx.html'


def _actor():
    return getattr(current_user, 'username', None)


def resolve_sponsor(user_id):
    """The extra sponsor's SAM row (picked from the user search), or a FormError."""
    if user_id is None:
        return None
    user = db.session.get(User, user_id)
    if user is None or not user.is_active:
        raise FormError('That sponsor is not an active SAM user.')
    return user


def sponsor_context(event):
    """The edit form's pre-selected sponsor picker values."""
    sponsor = (db.session.get(User, event.extra_sponsor_user_id)
               if event.extra_sponsor_user_id else None)
    return {'sponsor_id': sponsor.user_id if sponsor else '',
            'sponsor_label': f'{sponsor.display_name} ({sponsor.username})' if sponsor else ''}


@cache.memoize()
def upcoming_events_data():
    """Plain dicts, never ORM rows: this is pickled into Redis and shared by
    every visitor of the public status page."""
    return upcoming_listed_events(db.session)


def invalidate_upcoming_events():
    cache.delete_memoized(upcoming_events_data)


def create_event(data, project):
    """The event_code uniqueness check is global: a code is typed by strangers."""
    if not project.is_active:
        raise FormError(f'{project.projcode} is not an active project.')
    code = data['event_code']
    if db.session.query(AccountRequestEvent).filter_by(event_code=code).first():
        raise FormError(f'The code {code} is already in use.')
    sponsor = resolve_sponsor(data.get('extra_sponsor_user_id'))
    logger.info('event create: code=%s project=%s listed=%s invite_only=%s by=%s', code,
                project.projcode, bool(data.get('listed')), bool(data.get('invite_only')),
                _actor())
    return AccountRequestEvent.create(
        db.session, event_code=code, name=data['name'],
        instructions=data.get('instructions'),
        project_id=project.project_id,
        accounts_needed_by=data['accounts_needed_by'],
        opens_at=data.get('opens_at'), closes_at=data.get('closes_at'),
        extra_sponsor_user_id=sponsor.user_id if sponsor else None,
        listed=data.get('listed', False),
        invite_only=data.get('invite_only', False),
        created_by=current_user.username)


def date_floor(event=None) -> str:
    """The date picker's ``min``: today, or an old event's own date so it stays editable."""
    today = date.today()
    return min(today, event.accounts_needed_by).isoformat() if event else today.isoformat()


def saved_response(triggers, message, event):
    """The success toast; a short-notice deadline turns it into a warning."""
    notice = deadline_notice(event.accounts_needed_by)
    if notice is None:
        return htmx_success_message(triggers, message)
    return htmx_success('dashboards/fragments/htmx_success.html', triggers,
                        toast=f'{message} {notice}', toast_variant='warning',
                        message=message, detail=notice)


class EventCreateHandler(HtmxFormHandler):
    """Create an event. Subclasses supply ``triggers``, ``context()`` and, for a
    picked project, ``target_project(data)``."""
    schema_cls = AccountRequestEventForm
    template = EVENT_FORM
    error_prefix = 'Error creating event'
    triggers = {}
    message = 'Event created.'

    def target_project(self, data):
        return self.project

    def perform(self, data):
        return create_event(data, self.target_project(data))

    def after_commit(self, result):
        invalidate_upcoming_events()

    def on_success(self, event):
        return saved_response(self.triggers, self.message, event)


class EventEditHandler(HtmxFormHandler):
    """PUT: gated on the keys present in the ORIGINAL form, not the loaded
    output -- load_default fills absent fields with None and would clear them.
    Subclasses supply ``triggers`` and ``context()``."""
    schema_cls = AccountRequestEventEditForm
    template = EVENT_FORM
    partial = True
    triggers = {}

    def clean(self, data):
        # Only a changed date is checked, so an old event stays editable.
        needed_by = data.get('accounts_needed_by')
        if (needed_by and needed_by != self.event.accounts_needed_by
                and needed_by < date.today()):
            raise ValidationError({'accounts_needed_by': [PAST_DEADLINE_MSG]})
        return data

    def perform(self, data):
        sent = request.form
        updates = {}
        if 'name' in sent:
            updates['name'] = data.get('name')
        if 'instructions' in sent:
            updates['instructions'] = data.get('instructions')
        if 'accounts_needed_by' in sent and data.get('accounts_needed_by'):
            updates['accounts_needed_by'] = data['accounts_needed_by']
        if 'opens_at' in sent:
            updates['opens_at'] = data.get('opens_at')
        if 'closes_at' in sent:
            updates['closes_at'] = data.get('closes_at')
        # The picker's hidden input is always posted: empty clears the sponsor.
        if 'extra_sponsor_user_id' in sent:
            sponsor = resolve_sponsor(data.get('extra_sponsor_user_id'))
            updates['extra_sponsor_user_id'] = sponsor.user_id if sponsor else None
        # A checkbox: absent means unchecked, so its own key cannot gate it.
        # The form posts ``listed_present`` beside it, so a caller that never
        # drew the box cannot unpublish the event. Operator-only either way:
        # a steward's edit must not publish (or unpublish).
        if 'listed_present' in sent and can_create_events(current_user, self.project):
            updates['listed'] = 'listed' in sent
        if 'invite_only_present' in sent:
            updates['invite_only'] = 'invite_only' in sent
        logger.info('event edit: code=%s fields=%s listed=%s->%s by=%s',
                    self.event.event_code, sorted(updates), self.event.listed,
                    updates.get('listed', self.event.listed), _actor())
        return self.event.update(**updates)

    def after_commit(self, result):
        invalidate_upcoming_events()

    def on_success(self, result):
        return saved_response(self.triggers, f'Saved {self.event.event_code}.', self.event)


def switch_event(event, verb, triggers):
    with management_transaction(db.session):
        (event.close if verb == 'close' else event.reopen)()
    logger.info('event %s: code=%s by=%s', verb, event.event_code, _actor())
    invalidate_upcoming_events()
    return htmx_success_message(
        triggers, f'{event.event_code} {"closed" if verb == "close" else "reopened"}.')


def roster_preview(event, preview_url):
    """The invitation a pasted roster would send, for the picked new person.

    Read-only: classifies with ``invite_outcomes`` and renders transient rows,
    so nothing is created, enrolled or signed.
    """
    from webapp.register.invite_mail import (   # webapp.register imports this module
        invite_messages, placeholder_link, preview_invite_rows)
    if not current_app.config.get('ACCOUNT_INVITATIONS_ENABLED', False):
        return render_preview_info('Invitation links are off on this deployment: '
                                   'a pasted roster is queued and nobody is mailed.')
    try:
        data = RosterPasteForm().load(request.form)
    except ValidationError:
        return render_preview_info('Paste at least one "Name <email>" line to preview.')
    entries, errors = parse_roster(data['roster'])
    if errors:
        return render_preview_info('Fix these lines first: ' + '; '.join(errors[:5]),
                                   'warning')
    if not entries:
        return render_preview_info('No people found; one "Name <email>" per line.')
    outcomes = invite_outcomes(db.session, event.project_id, [e['email'] for e in entries])
    by = {}
    for e in entries:
        by.setdefault(outcomes[e['email']][0], []).append(e)
    queued = by.get(OUTCOME_QUEUED, [])
    parts = [f'{len(queued)} get a link']
    if by.get(OUTCOME_ADDED):
        parts.append(f'{len(by[OUTCOME_ADDED])} already known (enrolled, no email)')
    if by.get(OUTCOME_DUPLICATE):
        parts.append(f'{len(by[OUTCOME_DUPLICATE])} already waiting')
    if by.get(OUTCOME_AMBIGUOUS):
        parts.append(f'{len(by[OUTCOME_AMBIGUOUS])} on more than one account (refused)')
    header = f'{len(entries)} people: ' + ', '.join(parts) + '.'
    if not queued:
        return render_preview_info(header + ' Nobody new gets a link.')
    rows = preview_invite_rows(queued, project_id=event.project_id,
                               sponsor=db.session.get(User, current_user.user_id),
                               event=event)
    notes = [header, 'Each link is created when you click Invite everyone.']
    if not data.get('send_invite'):
        notes.append('The box is unticked: Invite everyone queues them and mails nobody.')
    return render_email_preview(
        invite_messages(rows, sent_at=datetime.now(), requested_by=_actor(),
                        link_for=placeholder_link),
        id_prefix='rosterPreview', pane_id='rosterPreviewPane',
        picker_url=preview_url, notes=notes)


class RosterHandler(HtmxFormHandler):
    """Paste a roster under ``self.event``. Subclasses name ``post_endpoint``
    and ``preview_endpoint`` (both take ``event_code``) and the ``triggers``."""
    schema_cls = RosterPasteForm
    template = ROSTER_FORM
    post_endpoint = None
    preview_endpoint = None
    triggers = {}

    def clean(self, data):
        entries, errors = parse_roster(data['roster'])
        if errors:
            raise FormError(*errors)
        if not entries:
            raise FormError('No people found; one "Name <email>" per line.')
        data['entries'] = entries
        return data

    def perform(self, data):
        # The acting user is the sponsor recorded on every row written here.
        sponsor = db.session.get(User, current_user.user_id)
        self.send_invite = (bool(data.get('send_invite'))
                            and current_app.config.get('ACCOUNT_INVITATIONS_ENABLED', False))
        return paste_roster(db.session, event=self.event, sponsor=sponsor,
                            entries=data['entries'])

    def after_commit(self, result):
        """One invitation link per newly queued person, or NUSD's ticket for
        each when no link was asked for; known users got neither."""
        self.invites = None
        # Here, not at the top: webapp.register imports this module.
        from webapp.register.handoff_mail import send_ticket
        from webapp.register.invite_mail import (
            DELIVERED, can_send_invite, send_invite_links)
        queued = result[OUTCOME_QUEUED]
        rows = (db.session.query(AccountRequest)
                .filter(AccountRequest.event_id == self.event.account_request_event_id,
                        AccountRequest.email.in_(queued)).all()) if queued else []
        if not self.send_invite:
            for row in rows:
                send_ticket(row, requested_by=_actor())
            return
        rows = [r for r in rows if can_send_invite(r) and r.invite_sent_at is None]
        results = send_invite_links(rows, requested_by=_actor())
        self.invites = {
            'delivered': [r.recipient for r in results if r.status in DELIVERED],
            'undelivered': [(r.recipient, r.detail or r.status)
                            for r in results if r.status not in DELIVERED]}

    def context(self):
        return {'project': self.project, 'event': self.event,
                'post_url': url_for(self.post_endpoint,
                                    event_code=self.event.event_code),
                'preview_url': url_for(self.preview_endpoint,
                                       event_code=self.event.event_code)}

    def on_success(self, result):
        # The summary replaces the form inside the still-open modal: an
        # operator pasting thirty lines wants to see which three were known.
        counts = {k: len(v) for k, v in result.items()}
        links = len(self.invites['delivered']) if self.invites else 0
        logger.info('event roster: code=%s queued=%d added=%d duplicate=%d links=%d by=%s',
                    self.event.event_code, counts[OUTCOME_QUEUED], counts[OUTCOME_ADDED],
                    counts[OUTCOME_DUPLICATE], links, _actor())
        toast = (f"{counts[OUTCOME_QUEUED]} queued, {counts[OUTCOME_ADDED]} added, "
                 f"{counts[OUTCOME_DUPLICATE]} already waiting")
        if self.invites is not None:
            toast += f", {links} invitation link(s) sent"
        return htmx_success(ROSTER_RESULT, self.triggers, toast=toast,
                            event=self.event, project=self.project, outcomes=result,
                            invites=self.invites)
