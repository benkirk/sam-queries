"""NUSD's ticket for a batch of adhoc group masks (docs/plans/UNIX_GROUP_MASK.md § 5), the
``register/handoff_mail.py`` shape: through the ticket provider when one is armed,
else (and on any provider failure) one plain-text mail to ``NOTIFY_ACCOUNT_TICKET_TO``;
both paths share the ledger key ``hpc_group_ticket:<head id>`` and the link on the head
mask. Called after the write commits, never inside ``management_transaction``. A withdraw
posts one internal comment on the batch's ticket.
"""

import logging
from dataclasses import replace
from typing import Dict, Optional, Sequence

from flask import g
from sqlalchemy.orm import Session

from sam.core.groups import UnixGroupMask
from sam.core.users import User
from sam.integration.tickets import (DEFAULT_AUTOMATION_NOTE, HPC_GROUP_KIND, TicketDraft,
                                     TicketProvider)
from sam.integration.tickets.filing import LinkTarget, file_once
from sam.integration.tickets.queries import tickets_for
from sam.notify.base import Channel, DeliveryResult
from sam.queries.group_masks import (MASK_ENTITY, batch_siblings_open, build_mask_ticket_message,
                                     mask_ticket_handle)
from webapp.extensions import db
from webapp.register.handoff_mail import ticket_provider, ticket_settings
from webapp.utils.notify import get_notifier, public_url_for

logger = logging.getLogger(__name__)


def _masks_link(group_name: str) -> str:
    return public_url_for('admin_dashboard.users_group_masks', q=group_name)


def _display_names(usernames: Sequence[str]) -> Dict[str, str]:
    return {u.username: u.display_name for u in
            db.session.query(User).filter(User.username.in_(list(usernames)))}


def _label(masks: Sequence[UnixGroupMask]) -> str:
    return f'group mask {masks[0].mask_id}' + (f' (+{len(masks) - 1})' if len(masks) > 1 else '')


def send_mask_ticket(masks: Sequence[UnixGroupMask], *, group_name: str, branches,
                     requested_by: str) -> Optional[DeliveryResult]:
    """File NUSD's ticket for one batch (head first); ``None`` when neither path is configured."""
    provider = ticket_provider()
    if provider is not None:
        result = _file(provider, masks, group_name, branches, requested_by)
        if result is not None:
            return result
    return _mail(masks, group_name, branches, requested_by)


def _mail(masks, group_name, branches, requested_by) -> Optional[DeliveryResult]:
    to, sender = ticket_settings()
    if not to:
        return None
    message = build_mask_ticket_message(
        masks, group_name=group_name, branches=branches,
        display_names=_display_names([m.username for m in masks]),
        recipient=to, sender=sender, masks_url=_masks_link(group_name), requested_by=requested_by)
    result = get_notifier().send(message)
    logger.info('%s: ticket to %s %s', _label(masks), to, result.status)
    return result


def _file(provider: TicketProvider, masks, group_name, branches, requested_by
          ) -> Optional[DeliveryResult]:
    """File through ``provider``; ``None`` sends the caller to the mail path."""
    mid = masks[0].mask_id
    link = _masks_link(group_name)
    message = build_mask_ticket_message(
        masks, group_name=group_name, branches=branches,
        display_names=_display_names([m.username for m in masks]),
        recipient=provider.destination, masks_url='', requested_by=requested_by)
    message = replace(message, recipient=replace(message.recipient, channel=Channel.TICKET))

    def draft(rendered) -> TicketDraft:
        return TicketDraft(
            handle=mask_ticket_handle(mid), kind=HPC_GROUP_KIND, summary=message.subject,
            body=rendered.text, link_url=link,
            automation_note=f'{DEFAULT_AUTOMATION_NOTE} The change lives at {link}')

    filing = file_once(
        provider, get_notifier().ledger, message, handle=mask_ticket_handle(mid),
        render=lambda: get_notifier(read_only=True).preview(message), draft=draft,
        target=LinkTarget(MASK_ENTITY, mid, requested_by),
        session_factory=lambda: Session(db.engine), label=_label(masks))
    if filing.status == 'suppressed':
        return DeliveryResult(ok=True, status='suppressed', message=message,
                              detail=f'already sent under {message.dedup_key}')
    if filing.status == 'unavailable':
        logger.warning('group mask %s: %s unavailable (%s); mailing the ticket',
                       mid, provider.name, filing.error)
        g.ticket_provider_down = True
        return None
    if filing.status == 'error':
        return None
    return DeliveryResult(ok=True, status='sent', message=message, detail=filing.ref.key,
                          log_id=filing.log_id)


def comment_withdrawn(mask: UnixGroupMask, *, by: str, session=None) -> bool:
    """One internal comment on the batch's ticket, when the provider can; never raises."""
    provider = ticket_provider()
    if provider is None:
        return False
    session = session or db.session
    rows = tickets_for(session, MASK_ENTITY, [mask.head_id]).get(mask.head_id, [])
    others = batch_siblings_open(session, mask) if rows else 0
    rest = (f'The other {others} member(s) on this ticket still stand.' if others
            else 'Nothing more is needed upstream.')
    posted = False
    for row in rows:
        if row.provider != provider.name:
            continue
        try:
            provider.comment(row.ticket_key,
                             f"Withdrawn in SAM by {by}: the {mask.op} of '{mask.username}' "
                             f"was reversed on SAM's side. {rest}",
                             internal=True)
            posted = True
        except Exception:        # noqa: BLE001 - the ticket is NUSD's; a lost comment is logged
            logger.warning('group mask %s: comment on %s failed', mask.mask_id, row.ticket_key,
                           exc_info=True)
    return posted
