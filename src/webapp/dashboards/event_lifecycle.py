"""Account-request event create/edit/close/reopen, shared by two blueprints.

``project_invites`` (per project, dark behind ACCOUNT_INVITATIONS_ENABLED) and
Admin -> Events (``admin/events_routes.py``, always mounted) both call these,
so the admin page never depends on the invitations flag. Also owns the memoized
public listing, so every lifecycle write can invalidate it.
"""

from flask import request
from flask_login import current_user

from sam.core.account_requests import AccountRequestEvent
from sam.core.users import User
from sam.manage import management_transaction
from sam.queries.account_requests import upcoming_listed_events
from sam.schemas.forms import AccountRequestEventEditForm
from webapp.extensions import cache, db
from webapp.utils.form_handler import FormError, HtmxFormHandler
from webapp.utils.htmx import htmx_success_message
from webapp.utils.project_permissions import can_create_events

EVENT_FORM = 'project_members/fragments/event_form_htmx.html'


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
    """The event_code uniqueness check is global: a code is typed by strangers.
    Callers pass ``after_commit=lambda _: invalidate_upcoming_events()``."""
    code = data['event_code']
    if db.session.query(AccountRequestEvent).filter_by(event_code=code).first():
        raise FormError(f'The code {code} is already in use.')
    sponsor = resolve_sponsor(data.get('extra_sponsor_user_id'))
    return AccountRequestEvent.create(
        db.session, event_code=code, name=data['name'],
        instructions=data.get('instructions'),
        project_id=project.project_id,
        accounts_needed_by=data['accounts_needed_by'],
        opens_at=data.get('opens_at'), closes_at=data.get('closes_at'),
        extra_sponsor_user_id=sponsor.user_id if sponsor else None,
        listed=data.get('listed', False),
        created_by=current_user.username)


class EventEditHandler(HtmxFormHandler):
    """PUT: gated on the keys present in the ORIGINAL form, not the loaded
    output -- load_default fills absent fields with None and would clear them.
    Subclasses supply ``triggers`` and ``context()``."""
    schema_cls = AccountRequestEventEditForm
    template = EVENT_FORM
    partial = True
    triggers = {}

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
        # A checkbox: absent means unchecked, so presence cannot gate it. The
        # form draws it only for an operator, and only an operator may set it
        # -- a steward's edit must not publish (or unpublish) the event.
        if can_create_events(current_user, self.project):
            updates['listed'] = 'listed' in sent
        return self.event.update(**updates)

    def after_commit(self, result):
        invalidate_upcoming_events()

    def on_success(self, result):
        return htmx_success_message(self.triggers, f'Saved {self.event.event_code}.')


def switch_event(event, verb, triggers):
    with management_transaction(db.session):
        (event.close if verb == 'close' else event.reopen)()
    invalidate_upcoming_events()
    return htmx_success_message(
        triggers, f'{event.event_code} {"closed" if verb == "close" else "reopened"}.')
