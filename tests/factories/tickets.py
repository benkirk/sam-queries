"""Ticket-provider test doubles, shared by tickets/, webapp/ and tasks/ tests.

``FakeTicketProvider`` is dict-backed and records every call; set
``raise_with`` to make the next ``create``/``find``/``get`` raise.
``MinimalProvider`` implements only the abstract members: the proof that a
second provider needs nothing more.
"""
from typing import Any, Dict, List, Optional, Tuple

from sam.integration.tickets.base import (TicketDraft, TicketProvider,
                                          TicketRef)


class FakeTicketProvider(TicketProvider):
    name = 'fake'

    def __init__(self, *, configured: bool = True, write_configured: bool = True,
                 prefix: str = 'FAKE') -> None:
        self._configured = configured
        self._write = write_configured
        self.prefix = prefix
        self.tickets: Dict[str, TicketRef] = {}
        self.by_handle: Dict[str, str] = {}
        self.created: List[TicketDraft] = []
        self.comments: List[Tuple[str, str, bool]] = []
        self.calls: List[Tuple[str, str]] = []
        self.raise_with: Optional[Exception] = None
        self.raise_on: Tuple[str, ...] = ('create', 'find', 'get')

    @classmethod
    def from_environment(cls, *, interactive: bool = False) -> 'FakeTicketProvider':
        return cls()

    @property
    def configured(self) -> bool:
        return self._configured

    @property
    def write_configured(self) -> bool:
        return self._configured and self._write

    def summary(self) -> Dict[str, Any]:
        return {'provider': self.name, 'enabled': self._configured,
                'write_enabled': self._write, 'token_set': True}

    def browse_url(self, key: str) -> str:
        return f'https://tickets.example.invalid/browse/{key}'

    def _maybe_raise(self, op: str) -> None:
        self.calls.append((op, ''))
        if self.raise_with is not None and op in self.raise_on:
            raise self.raise_with

    def add(self, handle: str, *, key: Optional[str] = None, status: str = 'Open',
            closed: bool = False) -> TicketRef:
        """Seed a ticket as if a mail had filed it."""
        key = key or f'{self.prefix}-{len(self.tickets) + 1}'
        ref = TicketRef(key, self.browse_url(key), status=status, closed=closed)
        self.tickets[key] = ref
        self.by_handle.setdefault(handle, key)
        return ref

    def set_status(self, key: str, status: str, *, closed: bool) -> None:
        self.tickets[key] = TicketRef(key, self.browse_url(key), status=status, closed=closed)

    def create(self, draft: TicketDraft) -> TicketRef:
        self.guard_write()
        self._maybe_raise('create')
        self.created.append(draft)
        ref = self.add(draft.handle, status='Waiting for support')
        self.post_automation_note(ref.key, draft)
        return ref

    def comment(self, key: str, text: str, *, internal: bool = True) -> None:
        self.guard_write()
        self.comments.append((key, text, internal))

    def get(self, key: str) -> Optional[TicketRef]:
        self.guard_read()
        self._maybe_raise('get')
        return self.tickets.get(key)

    def find(self, handle: str) -> Optional[TicketRef]:
        self.guard_read()
        self._maybe_raise('find')
        key = self.by_handle.get(handle)
        return self.tickets.get(key) if key else None


class MinimalProvider(TicketProvider):
    """Only the abstract members; everything else is the base default."""

    name = 'minimal'

    @classmethod
    def from_environment(cls, *, interactive: bool = False) -> 'MinimalProvider':
        return cls()

    @property
    def configured(self) -> bool:
        return True

    @property
    def write_configured(self) -> bool:
        return True

    def summary(self) -> Dict[str, Any]:
        return {'provider': self.name}

    def create(self, draft: TicketDraft) -> TicketRef:
        self.post_automation_note('MIN-1', draft)
        return TicketRef('MIN-1', self.browse_url('MIN-1'))

    def get(self, key: str) -> Optional[TicketRef]:
        return None

    def browse_url(self, key: str) -> str:
        return f'https://minimal.example.invalid/{key}'


def make_external_ticket(session, *, entity_id, entity_type='account_request',
                         provider='jira-servicedesk', ticket_key=None,
                         origin='learned', requested_by='task:test', status=None,
                         closed=None, synced_at=None, when=None):
    """One ``external_ticket`` row via the model's ``create()``. The default key
    derives from ``entity_id`` plus a uuid tail, so xdist workers never collide."""
    import uuid

    from sam import ExternalTicket
    key = ticket_key or f'T{entity_id}-{uuid.uuid4().hex[:8].upper()}'
    return ExternalTicket.create(session, provider=provider, ticket_key=key,
                                 entity_type=entity_type, entity_id=entity_id,
                                 origin=origin, requested_by=requested_by,
                                 status=status, closed=closed, synced_at=synced_at,
                                 when=when)
