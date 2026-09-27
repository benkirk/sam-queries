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
from datetime import datetime
from typing import Optional

from flask import current_app, g
from sqlalchemy.orm import Session

from sam.core.account_requests import CREATED_BY_SELF, AccountRequest
from sam.integration.tickets import (ACCOUNT_KIND, DEFAULT_AUTOMATION_NOTE,
                                     ExternalTicket, TicketDraft,
                                     TicketNotConfigured, TicketProvider,
                                     TicketRef, TicketRejected,
                                     TicketSourceUnavailable)
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
    ledger = get_notifier().ledger
    link = _queue_link(row)
    # The link rides outside the body (the provider makes it clickable).
    message = build_ticket_message(row, view=_view(row), recipient=provider.destination,
                                   queue_url='', requested_by=requested_by)
    message = replace(message, recipient=replace(message.recipient, channel=Channel.TICKET))
    if ledger.already_sent(message.dedup_key):
        logger.info('request %s: ticket already filed under %s', rid, message.dedup_key)
        return DeliveryResult(ok=True, status='suppressed', message=message,
                              detail=f'already sent under {message.dedup_key}')
    handle = ticket_handle(rid)
    try:
        found = provider.find(handle)
        if found is not None:
            # A mail from before the switch already filed it.
            _link_ticket(provider, found, rid, 'learned', requested_by)
            log_id = ledger.record(message, status='sent', transport=provider.name,
                                   detail=f'found {found.key}')
            logger.info('request %s: ticket %s already existed', rid, found.key)
            return DeliveryResult(ok=True, status='sent', message=message,
                                  detail=found.key, log_id=log_id)
        rendered = get_notifier(read_only=True).preview(message)
        log_id = ledger.record(message, status='queued', transport=provider.name,
                               rendered=rendered)
    except TicketSourceUnavailable as exc:
        return _provider_down(rid, provider, exc)
    except Exception:
        logger.exception('request %s: %s filing could not start; mailing', rid, provider.name)
        return None

    draft = TicketDraft(
        handle=handle, kind=ACCOUNT_KIND, summary=message.subject, body=rendered.text,
        link_url=link,
        automation_note=f'{DEFAULT_AUTOMATION_NOTE} The request lives at {link}')
    try:
        ref = provider.create(draft)
    except Exception as exc:        # TicketSourceUnavailable, or a provider bug
        ref = _filed_anyway(provider, handle, exc)
        if ref is None:
            ledger.resolve(log_id, status='failed', detail=f'{type(exc).__name__}: {exc}')
            return _provider_down(rid, provider, exc)
        logger.warning('request %s: create failed (%s) but %s carries %s; not mailing',
                       rid, exc, ref.key, handle)
    ledger.resolve(log_id, status='sent', detail=ref.key)
    _link_ticket(provider, ref, rid, 'created', requested_by)
    logger.info('request %s: filed %s through %s', rid, ref.key, provider.name)
    return DeliveryResult(ok=True, status='sent', message=message, detail=ref.key,
                          log_id=log_id)


def _filed_anyway(provider: TicketProvider, handle: str, exc: Exception
                  ) -> Optional[TicketRef]:
    """A create that timed out may have filed: the desk commits, then the 5 s
    budget expires. Mailing on that would file a duplicate, so ask once. A 4xx
    (``TicketRejected``) or a provider bug means nothing was filed."""
    if not isinstance(exc, TicketSourceUnavailable) or isinstance(exc, TicketRejected):
        return None
    try:
        return provider.find(handle)
    except Exception:
        return None


def _provider_down(rid: int, provider: TicketProvider, exc: Exception) -> None:
    """Log, and send the rest of this request's filings to mail."""
    logger.warning('request %s: %s unavailable (%s); mailing the ticket',
                   rid, provider.name, exc)
    g.ticket_provider_down = True
    return None


def _link_ticket(provider: TicketProvider, ref: TicketRef, request_id: int,
                 origin: str, requested_by: str) -> None:
    """Its own short session: a filed ticket must survive any later rollback.
    A failure is logged; the hourly learn links it from the ledger row."""
    try:
        with Session(db.engine) as session:
            ExternalTicket.create(session, provider=provider.name, ticket_key=ref.key,
                                  entity_type='account_request', entity_id=request_id,
                                  origin=origin, requested_by=requested_by,
                                  status=ref.status, closed=ref.closed,
                                  synced_at=datetime.now())
            session.commit()
    except Exception:
        logger.exception('request %s: could not link %s', request_id, ref.key)


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
