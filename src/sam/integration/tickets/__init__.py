"""Help-desk tickets SAM files or learns (docs/plans/implemented/TICKET_PROVIDER.md).

Import-light on purpose: ``sam/__init__.py`` imports the model, so this file
pulls in only ``base`` and ``models``. ``registry``, ``jira`` and ``learn``
bring in ``requests`` and are imported by path.
"""

from sam.integration.tickets.base import (ACCOUNT_KIND, DEFAULT_AUTOMATION_NOTE,
                                          KINDS, TicketDraft,
                                          TicketNotConfigured, TicketProvider,
                                          TicketRef, TicketRejected,
                                          TicketSourceUnavailable)
from sam.integration.tickets.models import ExternalTicket

__all__ = [
    'ACCOUNT_KIND', 'DEFAULT_AUTOMATION_NOTE', 'ExternalTicket', 'KINDS', 'TicketDraft',
    'TicketNotConfigured',
    'TicketProvider', 'TicketRef', 'TicketRejected', 'TicketSourceUnavailable',
]
