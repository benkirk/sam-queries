"""Help-desk tickets SAM files or learns (docs/plans/TICKET_PROVIDER.md).

Import-light on purpose: ``sam/__init__.py`` imports the model, so this file
pulls in only ``base`` and ``models``. ``registry``, ``jira`` and ``learn``
bring in ``requests`` and are imported by path.
"""

from sam.integration.tickets.base import (DEFAULT_AUTOMATION_NOTE, TicketDraft,
                                          TicketNotConfigured, TicketProvider,
                                          TicketRef, TicketRejected,
                                          TicketSourceUnavailable)
from sam.integration.tickets.models import ExternalTicket

__all__ = [
    'DEFAULT_AUTOMATION_NOTE', 'ExternalTicket', 'TicketDraft', 'TicketNotConfigured',
    'TicketProvider', 'TicketRef', 'TicketRejected', 'TicketSourceUnavailable',
]
