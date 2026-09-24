"""Mailing invitation links: the sponsor side of ``register_invite``.

One sender for the three sponsor actions (the invite form's checkbox, roster
paste, Resend). Send first, stamp second: ``invite_sent_at`` is written only
for a delivered message, and it is the stamp the link was signed with.
Previews build through the same :func:`invite_messages` with a placeholder link.
"""

from datetime import datetime
from typing import Callable, Iterable, List, Optional, Sequence

from flask import current_app

from sam.core.account_requests import OPEN_STATES, AccountRequest, AccountRequestEvent
from sam.core.users import User
from sam.manage import management_transaction
from sam.queries.account_notices import build_invite_message
from sam.queries.account_requests import events_for, request_views
from webapp.extensions import db
from webapp.utils.notify import get_notifier, public_url_for, public_url_root

from . import tokens

DELIVERED = ('sent', 'redirected')
#: Reconnect to the relay every this many delivered messages (roster paste).
CHUNK_SIZE = 50
#: The link a preview shows; no token is signed until the send.
INVITE_LINK_PLACEHOLDER = 'register/invite/PREVIEW-link-is-created-when-sent'


def can_send_invite(row: AccountRequest) -> bool:
    """An open, sponsor-made, not yet completed or fulfilled row with an address."""
    return (row.state in OPEN_STATES and row.completed_at is None
            and not row.is_fulfilled and bool(row.email)
            and row.sponsor_user_id is not None)


def invite_messages(rows: Sequence[AccountRequest], *, sent_at: datetime,
                    requested_by: str, link_for: Callable[[AccountRequest], str]) -> List:
    """One invitation per row; the send signs a real link, a preview a placeholder."""
    ttl_days = int(current_app.config.get('ACCOUNT_INVITE_TTL_DAYS', 30))
    views = request_views(db.session, rows, resolutions={},
                          events=events_for(db.session, rows))
    return [
        build_invite_message(
            v['row'], sent_at=sent_at, expires_days=ttl_days, invite_url=link_for(v['row']),
            sponsor_name=v['sponsor'].display_name if v['sponsor'] else '',
            projcode=v['project_code'], event=v['event'], requested_by=requested_by)
        for v in views]


def placeholder_link(_row=None) -> str:
    """Not url_for: the invite blueprint may be unmounted. Resolves as an invalid link."""
    return public_url_root() + INVITE_LINK_PLACEHOLDER


def preview_invite_rows(entries: Iterable[dict], *, project_id: int, sponsor: User,
                        event: Optional[AccountRequestEvent] = None) -> List[AccountRequest]:
    """TRANSIENT rows for a preview: ``account_request_id`` is None.

    WARNING: never add these to a session; autoflush would insert them. The
    model has no relationships, so only an explicit add can do that.
    """
    now = datetime.now()
    return [AccountRequest(
        email=e['email'], first_name=e['first_name'], last_name=e['last_name'],
        organization=e.get('organization'), purpose='enrollment', state='submitted',
        project_id=project_id, sponsor_user_id=sponsor.user_id,
        event_id=event.account_request_event_id if event else None,
        created_by=sponsor.username, verified_by=sponsor.username,
        verified_at=now, creation_time=now) for e in entries]


def send_invite_links(rows: Sequence[AccountRequest], *, requested_by: str) -> List:
    """Mail each row its link; returns one DeliveryResult per row, in order."""
    if not rows:
        return []
    sent_at = datetime.now().replace(microsecond=0)
    messages = invite_messages(
        rows, sent_at=sent_at, requested_by=requested_by,
        link_for=lambda row: public_url_for('register_invite.page', token=tokens.invite_token(
            row.account_request_id, sent_at)))
    results = get_notifier().send_many(messages, chunk_size=CHUNK_SIZE)
    delivered = [row for row, result in zip(rows, results) if result.status in DELIVERED]
    if delivered:
        with management_transaction(db.session):
            for row in delivered:
                row.mark_invite_sent(sent_at)
    return results
