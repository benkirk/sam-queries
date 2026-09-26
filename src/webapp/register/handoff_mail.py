"""Mail that leaves when a request lands with NUSD: the invitee's receipt.

Sent after the write commits, outside ``management_transaction``, like every
send point; the ledger key is per row, so a repeat call is suppressed.
"""

import logging

from sam.core.account_requests import CREATED_BY_SELF, AccountRequest
from sam.queries.account_notices import build_receipt_message
from sam.queries.account_requests import events_for, request_views
from webapp.extensions import db
from webapp.utils.notify import get_notifier

from . import eula

logger = logging.getLogger(__name__)


def _view(row: AccountRequest) -> dict:
    return request_views(db.session, [row], resolutions={},
                         events=events_for(db.session, [row]))[0]


def send_receipt(row: AccountRequest):
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
