"""Mail that leaves when a request lands with NUSD: their ticket, and the
invitee's receipt.

The ticket is one plain-text mail per request into Jira-by-email
(``NOTIFY_ACCOUNT_TICKET_TO``; empty leaves it off) from a real person
(``NOTIFY_ACCOUNT_TICKET_FROM``), sent at the "ready" moments: the public
address verified, the invitee's link completed, a sponsor queuing someone
without a link, an operator vouching. Its ledger key is the row alone, so
every site may call ``send_ticket`` and only the first send leaves. Sends
run after the write commits, outside ``management_transaction``.
"""

import logging
import os
from typing import Optional

from flask import current_app

from sam.core.account_requests import CREATED_BY_SELF, AccountRequest
from sam.notify.base import DeliveryResult
from sam.queries.account_notices import build_receipt_message, build_ticket_message
from sam.queries.account_requests import events_for, request_views
from webapp.extensions import db
from webapp.utils.notify import get_notifier

from . import eula

logger = logging.getLogger(__name__)


def _setting(key: str) -> str:
    # A key present in app.config wins even when empty: a test or a deployment
    # that blanks it must not fall through to the developer's .env.
    value = current_app.config[key] if key in current_app.config else os.environ.get(key)
    return str(value or '').strip()


def ticket_settings() -> tuple[str, str, str]:
    """``(to, sender, queue_url)``; an empty ``to`` means no ticket is filed."""
    return (_setting('NOTIFY_ACCOUNT_TICKET_TO'), _setting('NOTIFY_ACCOUNT_TICKET_FROM'),
            _setting('NOTIFY_ACCOUNT_QUEUE_URL'))


def _view(row: AccountRequest) -> dict:
    return request_views(db.session, [row], resolutions={},
                         events=events_for(db.session, [row]))[0]


def send_ticket(row: AccountRequest, *, requested_by: str = CREATED_BY_SELF
                ) -> Optional[DeliveryResult]:
    """File NUSD's ticket for ``row``; ``None`` when no address is configured."""
    to, sender, queue_url = ticket_settings()
    if not to:
        return None
    message = build_ticket_message(row, view=_view(row), recipient=to, sender=sender,
                                   queue_url=queue_url, requested_by=requested_by)
    result = get_notifier().send(message)
    logger.info('request %s: ticket to %s %s', row.account_request_id, to, result.status)
    return result


def send_receipt(row: AccountRequest) -> DeliveryResult:
    """The invitee's receipt, with the agreement they accepted appended."""
    v = _view(row)
    message = build_receipt_message(
        row, project_code=v['project_code'],
        event_name=v['event'].name if v['event'] else '',
        sponsor_name=v['sponsor'].display_name if v['sponsor'] else '',
        eula_text=eula.eula_text(), eula_html=eula.eula_html(),
        requested_by=CREATED_BY_SELF)
    result = get_notifier().send(message)
    logger.info('request %s: receipt to %s %s', row.account_request_id, row.email,
                result.status)
    return result


def send_completion_mail(row: AccountRequest) -> None:
    """The invitee finished their link: their receipt, then NUSD's ticket."""
    send_receipt(row)
    send_ticket(row)
