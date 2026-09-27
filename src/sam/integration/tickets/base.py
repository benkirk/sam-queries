"""The ticket-provider contract: value types, exceptions, and the ABC.

Three outcomes, as in ``sam.integration.xras_api.base``: a value (the tracker
answered), ``None`` (it answered and has no such ticket), an exception (we
could not ask). ``TicketNotConfigured`` and ``TicketRejected`` both subclass
``TicketSourceUnavailable`` so a caller that degrades on "could not ask"
degrades on "may not ask" and "refused" without a second branch.
Design: docs/plans/implemented/TICKET_PROVIDER.md.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar, Dict, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Posted as an internal note on every ticket a provider creates, so the desk
#: knows no person typed it. Callers append the row-specific line.
DEFAULT_AUTOMATION_NOTE = (
    'Filed automatically by SAM (NSF NCAR Systems Accounting Manager) with an '
    'API token, not by hand; the reporter shown is the token owner. Replies '
    'here reach this queue, not SAM.')

#: The kinds of ticket SAM files. A provider keys its per-kind settings
#: (request type, desk, labels) on these names, like ``sam.notify`` families.
ACCOUNT_KIND = 'account'
KINDS: Tuple[str, ...] = (ACCOUNT_KIND,)


class TicketSourceUnavailable(Exception):
    """The tracker could not be reached, or did not answer usefully."""


class TicketNotConfigured(TicketSourceUnavailable):
    """A lever is off or the credential is missing; nothing was attempted."""


class TicketRejected(TicketSourceUnavailable):
    """The tracker understood the call and refused it (a 4xx). Retrying cannot fix it."""

    def __init__(self, message: str, *, status: int = 0,
                 errors: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.status = status
        self.errors = list(errors)


@dataclass(frozen=True)
class TicketDraft:
    """What a caller asks a provider to file. ``handle`` is the lookup token
    already in ``summary`` (``SAM-AR-12``); ``body`` is plain text; ``kind``
    is a :data:`KINDS` name and selects the provider's request type."""

    handle: str
    summary: str
    body: str
    kind: str = ACCOUNT_KIND
    link_url: str = ''
    labels: Tuple[str, ...] = ()
    on_behalf_of: str = ''
    automation_note: str = ''


@dataclass(frozen=True)
class TicketRef:
    """A ticket as the tracker reports it. ``closed`` is ``None`` when unknown."""

    key: str
    url: str = ''
    status: str = ''
    closed: Optional[bool] = None


class TicketProvider(ABC):
    """One tracker. Seven abstract members; ``find``, ``comment`` and ``check``
    are optional capabilities with safe defaults."""

    #: Registry key, stored in ``external_ticket.provider``; stable forever.
    name: ClassVar[str] = ''

    @classmethod
    @abstractmethod
    def from_environment(cls, *, interactive: bool = False) -> 'TicketProvider':
        """Build from config. Never raises; check :attr:`configured`.
        ``interactive`` asks for the short, single-attempt webapp budget."""

    @property
    @abstractmethod
    def configured(self) -> bool:
        """Reads may be attempted."""

    @property
    @abstractmethod
    def write_configured(self) -> bool:
        """Creates and comments may be attempted."""

    @abstractmethod
    def summary(self) -> Dict[str, Any]:
        """Config for the Admin > Configuration card. Never the credential."""

    @abstractmethod
    def create(self, draft: TicketDraft) -> TicketRef:
        """File one ticket, exactly one attempt, then post the automation note
        via :meth:`post_automation_note`. Raises on failure; never returns None."""

    @abstractmethod
    def get(self, key: str) -> Optional[TicketRef]:
        """Current status of ``key``; ``None`` when the tracker has no such ticket."""

    @abstractmethod
    def browse_url(self, key: str) -> str:
        """The human URL for ``key``. No I/O."""

    @property
    def destination(self) -> str:
        """Where filings land, for the ledger's recipient column."""
        return self.name

    def find(self, handle: str) -> Optional[TicketRef]:
        """The oldest ticket whose summary carries ``handle``; ``None`` if none."""
        return None

    def comment(self, key: str, text: str, *, internal: bool = True) -> None:
        raise NotImplementedError(f'{self.name} cannot comment')

    def check(self) -> Tuple[bool, str]:
        """Probe the credential without side effects, for the card."""
        return True, ''

    def guard_read(self) -> None:
        if not self.configured:
            raise TicketNotConfigured(f'{self.name}: reads are not configured')

    def guard_write(self) -> None:
        if not self.write_configured:
            raise TicketNotConfigured(f'{self.name}: writes are not configured')

    def post_automation_note(self, key: str, draft: TicketDraft) -> bool:
        """The create invariant: a failed note is logged and never fails the create."""
        note = draft.automation_note or DEFAULT_AUTOMATION_NOTE
        try:
            self.comment(key, note, internal=True)
            return True
        except Exception as exc:        # the ticket exists; the note is decoration
            logger.warning('%s: automation note on %s failed: %s',
                           self.name, key, exc)
            return False
