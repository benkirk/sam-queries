"""``NotificationTemplateOverride`` — an operator's edit of one shipped template.

One row per template file name (``expiration-WNA.txt``). The package file
stays the baseline: the renderer prefers a row when one exists, and
deleting the row is "reset to default". DDL:
``scripts/create_notification_template_override.sql``.

Only sqlalchemy and ``sam.base`` here: ``sam/__init__.py`` imports this
module eagerly, so anything heavier would reach every ORM consumer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint

from sam.base import Base, SessionMixin

_NAME_MAX = 64
_USER_MAX = 35


class NotificationTemplateOverride(Base, SessionMixin):
    """One edited template body, replacing the shipped file of the same name."""

    __tablename__ = 'notification_template_override'
    __table_args__ = (
        UniqueConstraint('name', name='notification_template_override_name'),
    )

    notification_template_override_id = Column(Integer, primary_key=True,
                                               autoincrement=True)
    name = Column(String(_NAME_MAX), nullable=False)
    body = Column(Text, nullable=False)
    modified_by = Column(String(_USER_MAX), nullable=False)
    # App clock, naive-Mountain, no server_default: a CURRENT_TIMESTAMP
    # default resolves in the MySQL server's zone (UTC in the containers).
    modified_time = Column(DateTime, nullable=False)

    @classmethod
    def get_by_name(cls, session, name: str) -> Optional['NotificationTemplateOverride']:
        return session.query(cls).filter(cls.name == name).one_or_none()

    @classmethod
    def create(cls, session, *, name: str, body: str,
               modified_by: str) -> 'NotificationTemplateOverride':
        _require(name, 'name', _NAME_MAX)
        _require(modified_by, 'modified_by', _USER_MAX)
        if not isinstance(body, str):
            raise ValueError('body must be a string')
        row = cls(name=name, body=body, modified_by=modified_by,
                  modified_time=datetime.now())
        session.add(row)
        session.flush()
        return row

    def update(self, *, body: str, modified_by: str) -> 'NotificationTemplateOverride':
        _require(modified_by, 'modified_by', _USER_MAX)
        if not isinstance(body, str):
            raise ValueError('body must be a string')
        self.body = body
        self.modified_by = modified_by
        self.modified_time = datetime.now()
        self.session.flush()
        return self

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return (f'<NotificationTemplateOverride {self.name} '
                f'by {self.modified_by} at {self.modified_time}>')


def _require(value, field: str, max_len: int) -> None:
    if not value or not str(value).strip():
        raise ValueError(f'{field} is required')
    if len(value) > max_len:
        raise ValueError(f'{field} exceeds {max_len} characters')
