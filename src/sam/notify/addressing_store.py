"""``NotificationAddressing`` — an operator-added cc or bcc for one scope.

One address per row. ``scope`` is a family key (``xras``), a kind key
(``xras_supplement``) or a facility variant stem (``expiration-WNA``): the
vocabulary is :func:`sam.notify.kinds.addressing_scopes`. Rows add to the
``NOTIFY_<FAMILY>_*`` deployment defaults; deleting one removes it. DDL:
``scripts/create_notification_addressing.sql``.

Only sqlalchemy, ``sam.base`` and ``sam.notify.kinds`` here: ``sam/__init__.py``
imports this module eagerly.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint

from sam.base import Base, SessionMixin
from sam.notify.kinds import scope_family

FIELDS = ('cc', 'bcc')
_SCOPE_MAX = 48
_ADDRESS_MAX = 255
_USER_MAX = 35


class NotificationAddressing(Base, SessionMixin):
    """One extra copy recipient for every message a scope matches."""

    __tablename__ = 'notification_addressing'
    __table_args__ = (
        UniqueConstraint('scope', 'field', 'address',
                         name='notification_addressing_entry'),
    )

    notification_addressing_id = Column(Integer, primary_key=True,
                                        autoincrement=True)
    scope = Column(String(_SCOPE_MAX), nullable=False)
    field = Column(String(8), nullable=False)
    address = Column(String(_ADDRESS_MAX), nullable=False)
    created_by = Column(String(_USER_MAX), nullable=False)
    # App clock, naive-Mountain, no server_default (see template_store.py).
    creation_time = Column(DateTime, nullable=False)

    @property
    def family(self) -> str:
        return scope_family(self.scope)

    @classmethod
    def get_by_entry(cls, session, *, scope: str, field: str,
                     address: str) -> Optional['NotificationAddressing']:
        return session.query(cls).filter(
            cls.scope == scope, cls.field == field,
            cls.address == normalize_address(address)).one_or_none()

    @classmethod
    def create(cls, session, *, scope: str, field: str, address: str,
               created_by: str) -> 'NotificationAddressing':
        """Raises ``ValueError`` on an unknown scope or field, or a bad address."""
        scope_family(scope)
        if field not in FIELDS:
            raise ValueError(f'field must be one of {", ".join(FIELDS)}')
        _require(created_by, 'created_by', _USER_MAX)
        row = cls(scope=scope, field=field, address=normalize_address(address),
                  created_by=created_by, creation_time=datetime.now())
        session.add(row)
        session.flush()
        return row

    def __str__(self) -> str:
        return f'{self.field}:{self.address} ({self.scope})'

    def __repr__(self) -> str:
        return (f'<NotificationAddressing {self.scope} {self.field} '
                f'{self.address} by {self.created_by}>')


def normalize_address(address) -> str:
    """Trimmed and lower-cased; raises ``ValueError`` unless it looks like a mailbox."""
    value = (address or '').strip().lower()
    _require(value, 'address', _ADDRESS_MAX)
    local, sep, domain = value.partition('@')
    if not (local and sep and domain) or any(c.isspace() or c == ',' for c in value):
        raise ValueError(f'{address!r} is not an email address')
    return value


def _require(value, field: str, max_len: int) -> None:
    if not value or not str(value).strip():
        raise ValueError(f'{field} is required')
    if len(value) > max_len:
        raise ValueError(f'{field} exceeds {max_len} characters')
