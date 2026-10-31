"""What leaves when a request lands with NUSD: their ticket, and the invitee's
receipt.

The ticket goes through the ticket provider when ``TICKET_PROVIDER`` names one
and ``JIRA_WRITE_ENABLED`` is on, else (and on any provider failure) as one
plain-text mail into Jira-by-email (``NOTIFY_ACCOUNT_TICKET_TO``; empty leaves
mail off). Both paths share the ledger key ``account_ticket:<id>``, so every
"ready" site may call ``send_ticket`` and only the first filing, in either
era, leaves. Calls run after the write commits, never inside
``management_transaction``. Design: docs/plans/implemented/TICKET_PROVIDER.md § 8.5.
"""

import logging
import os
from dataclasses import replace
from typing import Optional

from flask import current_app, g
from sqlalchemy.orm import Session

from sam.core.account_requests import CREATED_BY_SELF, AccountRequest
from sam.integration.tickets import (ACCOUNT_KIND, DEFAULT_AUTOMATION_NOTE,
                                     TicketDraft, TicketNotConfigured, TicketProvider)
from sam.integration.tickets.filing import LinkTarget, file_once
from sam.integration.tickets.queries import ACCOUNT_REQUEST
from sam.notify.base import Channel, DeliveryResult
from sam.queries.account_notices import (build_receipt_message, build_ticket_message,
                                         ticket_handle)
from sam.queries.account_requests import events_for, request_views
from webapp.extensions import db
from webapp.utils.notify import get_notifier, public_url_for

from . import eula

logger = logging.getLogger(__name__)


def _setting(key: str) -> str:
    # A key present in app.config wins even when empty: a test or a deployment
    # that blanks it must not fall through to the developer's .env.
    value = current_app.config[key] if key in current_app.config else os.environ.get(key)
    return str(value or '').strip()


def ticket_settings() -> tuple[str, str]:
    """``(to, sender)``; an empty ``to`` means no ticket is mailed."""
    return _setting('NOTIFY_ACCOUNT_TICKET_TO'), _setting('NOTIFY_ACCOUNT_TICKET_FROM')


def _view(row: AccountRequest) -> dict:
    return request_views(db.session, [row], resolutions={},
                         events=events_for(db.session, [row]))[0]


def _queue_link(row: AccountRequest) -> str:
    """The row's own page (Admin > Accounts pinned to it), on the public hostname."""
    return public_url_for('admin_dashboard.account_requests', request=row.account_request_id)


def send_ticket(row: AccountRequest, *, requested_by: str = CREATED_BY_SELF
                ) -> Optional[DeliveryResult]:
    """File NUSD's ticket for ``row``; ``None`` when neither path is configured."""
    provider = ticket_provider()
    if provider is not None:
        result = _file_ticket(provider, row, requested_by)
        if result is not None:
            return result
    return _mail_ticket(row, requested_by)


def comment_request_tickets(request_ids, text: str) -> int:
    """An internal comment on each XRAS request's ticket SAM filed; best-effort, a count."""
    from sam.integration.tickets.queries import tickets_for
    from sam.queries.xras_request_tickets import ENTITY_TYPE
    provider = ticket_provider()
    if provider is None or not request_ids:
        return 0
    linked = tickets_for(db.session, ENTITY_TYPE, list(request_ids))
    done = 0
    for rid in request_ids:
        if rid not in linked:
            continue
        try:
            provider.comment(linked[rid][-1].ticket_key, text, internal=True)
            done += 1
        except Exception as exc:                        # noqa: BLE001
            logger.warning('request %s: comment failed: %s', rid, exc)
    return done


def ticket_provider() -> Optional[TicketProvider]:
    """The provider armed for filing, or ``None`` for the mail path. Built once
    per request (a roster files many, on one HTTP session); after one outage
    the rest of the request goes to mail."""
    if g.get('ticket_provider_down'):
        return None
    if 'ticket_provider' in g:
        return g.ticket_provider
    # By path: the registry imports requests (sam/integration/tickets/__init__.py).
    from sam.integration.tickets.registry import provider_from_environment
    try:
        provider = provider_from_environment(interactive=True)
    except TicketNotConfigured as exc:
        logger.error('ticket provider: %s; filing by mail', exc)
        provider = None
    if provider is not None and not provider.write_configured:
        provider = None
    g.ticket_provider = provider
    return provider


def _mail_ticket(row: AccountRequest, requested_by: str) -> Optional[DeliveryResult]:
    to, sender = ticket_settings()
    if not to:
        return None
    message = build_ticket_message(row, view=_view(row), recipient=to, sender=sender,
                                   queue_url=_queue_link(row), requested_by=requested_by)
    result = get_notifier().send(message)
    logger.info('request %s: ticket to %s %s', row.account_request_id, to, result.status)
    return result


def _file_ticket(provider: TicketProvider, row: AccountRequest,
                 requested_by: str) -> Optional[DeliveryResult]:
    """File through ``provider``; ``None`` sends the caller to the mail path."""
    rid = row.account_request_id
    link = _queue_link(row)
    # The link rides outside the body (the provider makes it clickable).
    message = build_ticket_message(row, view=_view(row), recipient=provider.destination,
                                   queue_url='', requested_by=requested_by)
    message = replace(message, recipient=replace(message.recipient, channel=Channel.TICKET))

    def draft(rendered) -> TicketDraft:
        return TicketDraft(
            handle=ticket_handle(rid), kind=ACCOUNT_KIND, summary=message.subject,
            body=rendered.text, link_url=link,
            automation_note=f'{DEFAULT_AUTOMATION_NOTE} The request lives at {link}')

    filing = file_once(
        provider, get_notifier().ledger, message, handle=ticket_handle(rid),
        render=lambda: get_notifier(read_only=True).preview(message), draft=draft,
        target=LinkTarget(ACCOUNT_REQUEST, rid, requested_by),
        session_factory=lambda: Session(db.engine), label=f'request {rid}')
    if filing.status == 'suppressed':
        return DeliveryResult(ok=True, status='suppressed', message=message,
                              detail=f'already sent under {message.dedup_key}')
    if filing.status == 'unavailable':
        return _provider_down(rid, provider, filing.error)
    if filing.status == 'error':
        return None
    return DeliveryResult(ok=True, status='sent', message=message, detail=filing.ref.key,
                          log_id=filing.log_id)


def _provider_down(rid: int, provider: TicketProvider, exc: Exception) -> None:
    """Log, and send the rest of this request's filings to mail."""
    logger.warning('request %s: %s unavailable (%s); mailing the ticket',
                   rid, provider.name, exc)
    g.ticket_provider_down = True
    return None


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
