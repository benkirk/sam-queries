"""Mailing invitation links: the sponsor side of ``register_invite``.

One sender for the three sponsor actions (the invite form's checkbox, roster
paste, Resend). Send first, stamp second: ``invite_sent_at`` is written only
for a delivered message, and it is the stamp the link was signed with.
"""

from datetime import datetime
from typing import List, Sequence

from flask import current_app

from sam.core.account_requests import OPEN_STATES, AccountRequest
from sam.manage import management_transaction
from sam.queries.account_notices import build_invite_message
from sam.queries.account_requests import events_for, request_views
from webapp.extensions import db
from webapp.utils.notify import get_notifier, public_url_for

from . import tokens

DELIVERED = ('sent', 'redirected')
#: Reconnect to the relay every this many delivered messages (roster paste).
CHUNK_SIZE = 50


def can_send_invite(row: AccountRequest) -> bool:
    """An open, sponsor-made, not yet completed or fulfilled row with an address."""
    return (row.state in OPEN_STATES and row.completed_at is None
            and not row.is_fulfilled and bool(row.email)
            and row.sponsor_user_id is not None)


def send_invite_links(rows: Sequence[AccountRequest], *, requested_by: str) -> List:
    """Mail each row its link; returns one DeliveryResult per row, in order."""
    if not rows:
        return []
    sent_at = datetime.now().replace(microsecond=0)
    ttl_days = int(current_app.config.get('ACCOUNT_INVITE_TTL_DAYS', 30))
    views = request_views(db.session, rows, resolutions={},
                          events=events_for(db.session, rows))
    messages = [
        build_invite_message(
            v['row'], sent_at=sent_at, expires_days=ttl_days,
            invite_url=public_url_for('register_invite.page', token=tokens.invite_token(
                v['row'].account_request_id, sent_at)),
            sponsor_name=v['sponsor'].display_name if v['sponsor'] else '',
            projcode=v['project_code'], event=v['event'], requested_by=requested_by)
        for v in views]
    results = get_notifier().send_many(messages, chunk_size=CHUNK_SIZE)
    delivered = [row for row, result in zip(rows, results) if result.status in DELIVERED]
    if delivered:
        with management_transaction(db.session):
            for row in delivered:
                row.mark_invite_sent(sent_at)
    return results
