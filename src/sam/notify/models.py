"""``NotificationLog`` — the delivery ledger.

This is an actual database TABLE (not a view). DDL and the full rationale:
``containers/sam-sql-dev/initdb.d/zz-92-notification_log.sql`` and
``docs/plans/implemented/NOTIFICATION_FRAMEWORK.md`` § 5.

It lives under ``sam/notify/`` rather than in a domain module because it is
part of the framework's contract, not of any one domain's model graph — and
because it deliberately has **no relationships**: see ``entity_type`` below.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, Index, Integer, String, Text

from sam.base import Base, SessionMixin
from sam.notify.base import NOTIFICATION_STATUSES

#: Longest value each free-text column can hold, so a relay's 4 KB rejection
#: message truncates in Python rather than raising 1406 in MySQL — losing the
#: record of a mail we sent is strictly worse than losing the tail of an error.
_ERROR_MAX = 4000
_SUBJECT_MAX = 255
_NAME_MAX = 255


class NotificationLog(Base, SessionMixin):
    """One delivery **attempt**, for any channel.

    **Append-only, with exactly one permitted transition.** A retry is a *new*
    row sharing the same :attr:`dedup_key`, never an edit — the discipline
    ``XrasActivationEvent`` keeps. The sole exception is
    ``queued → sent | failed`` via :meth:`resolve`, which is this row's own
    outcome rather than a state overwrite. That is what makes the table
    outbox-ready: a drain can be added later with no DDL and no caller change.

    A process that dies between the two writes leaves the row ``queued``,
    which reads as an honest "we do not know" rather than a silent loss.

    **Transaction discipline — the opposite of the route next to it.**
    ``webapp/dashboards/allocations/blueprint.py`` writes its activation event
    *inside* ``management_transaction``, on purpose, because a decision that
    did not take effect must not survive. A ledger row is the inverse: **mail
    handed to a relay cannot be un-sent by a rollback**, so it must survive
    one. :class:`~sam.notify.ledger.NotificationLedger` therefore commits on
    its own short-lived session, mirroring ``webapp/api/xras/replay.py``. The
    two disciplines sit two screens apart; a reader who has just met one will
    expect the other answer.

    **No relationships and no foreign keys, deliberately.** A notification is
    about whatever prompted it — a project today, an allocation or a user
    tomorrow, and for an unmapped XRAS path nothing at all. A column per
    entity is a forest of nullable FKs that grows with every kind, and each
    addition is a DBA ticket. The trade is no referential integrity, which is
    right for an append-only historical record: a deleted parent must not
    cascade the evidence away.
    """

    __tablename__ = 'notification_log'

    __table_args__ = (
        # The suppression query: "has this key been used", newest first.
        Index('notification_log_dedup', 'dedup_key', 'creation_time'),
        # Facet chips and the admin card's counts.
        Index('notification_log_kind', 'kind', 'creation_time'),
        Index('notification_log_status', 'status', 'creation_time'),
        Index('notification_log_recipient', 'recipient', 'creation_time'),
        Index('notification_log_projcode', 'projcode', 'creation_time'),
        Index('notification_log_entity', 'entity_type', 'entity_id'),
    )

    notification_log_id = Column(Integer, primary_key=True, autoincrement=True)

    #: A :data:`sam.notify.kinds.NOTIFICATION_KINDS` key.
    kind = Column(String(32), nullable=False)
    #: ``email`` today; ``slack`` is declared with no transport behind it.
    channel = Column(String(16), nullable=False)
    #: Which transport handled it. Recorded rather than derived from config,
    #: because config changes and this row is evidence about the past.
    transport = Column(String(16), nullable=False)
    #: One of :data:`sam.notify.base.NOTIFICATION_STATUSES`.
    status = Column(String(16), nullable=False)

    #: The address actually handed to the transport (post-redirect).
    recipient = Column(String(255), nullable=False)
    #: Set **only** when a redirect happened. ``None`` means "``recipient``
    #: is the subject".
    intended_recipient = Column(String(255))
    recipient_name = Column(String(255))
    recipient_role = Column(String(16))

    subject = Column(String(255))
    #: The *text* template actually chosen, e.g. ``expiration-WNA.txt`` —
    #: which is what makes the facility fallback auditable after the fact.
    #: Bodies are not stored, so there is nothing else to answer "which
    #: letter did this PI actually get".
    template = Column(String(64))

    entity_type = Column(String(32))
    entity_id = Column(Integer)
    #: Denormalized. ⚠️ utf8mb3 in MySQL is load-bearing — it is compared
    #: against ``project.projcode``; see the DDL header.
    projcode = Column(String(30))

    #: Built by the caller from the **intended** recipient. ``None`` means
    #: "never suppress".
    dedup_key = Column(String(128))
    error = Column(Text)

    requested_by = Column(String(35), nullable=False)

    #: Stamped from the *app* clock, never a DB default. ``TimestampMixin`` is
    #: deliberately not used: its ``server_default=CURRENT_TIMESTAMP``
    #: resolves in the MySQL server's timezone (UTC in the containers) while
    #: SAM's convention is naive-Mountain, and MySQL rounds fractional seconds
    #: rather than truncating. The XRAS tables make the same choice.
    creation_time = Column(DateTime, nullable=False)
    #: When the outcome was learned. ``None`` while ``queued``.
    sent_time = Column(DateTime)

    # ----------------------------------------------------------------- write
    @classmethod
    def create(cls, session, *, kind, channel, transport, status, recipient,
               requested_by, intended_recipient=None, recipient_name=None,
               recipient_role=None, subject=None, template=None,
               entity_type=None, entity_id=None, projcode=None,
               dedup_key=None, error=None, when=None):
        """Append one attempt.

        There is no ``update()``. The one legitimate mutation is
        :meth:`resolve`, and it is named for what it does rather than left as
        a general-purpose setter.

        Args:
            session: the session to add to. Usually the ledger's own
                short-lived one — see the class docstring.
            status: one of :data:`~sam.notify.base.NOTIFICATION_STATUSES`.
            when: overrides the timestamp. Tests use it to age a row past the
                staleness horizon; nothing in production passes it.

        Raises:
            ValueError: on an unknown ``status``. The column is a bare
                ``VARCHAR`` by design, so this is the only thing standing
                between a typo and a row no facet chip will ever match.
        """
        if status not in NOTIFICATION_STATUSES:
            raise ValueError(
                f'unknown notification_log.status {status!r}; expected one of '
                f'{", ".join(NOTIFICATION_STATUSES)}')

        now = when or datetime.now()
        row = cls(
            kind=kind,
            channel=channel,
            transport=transport,
            status=status,
            recipient=recipient[:255],
            intended_recipient=intended_recipient[:255] if intended_recipient else None,
            recipient_name=_clip(recipient_name, _NAME_MAX),
            recipient_role=_clip(recipient_role, 16),
            subject=_clip(subject, _SUBJECT_MAX),
            template=_clip(template, 64),
            entity_type=_clip(entity_type, 32),
            entity_id=entity_id,
            projcode=_clip(projcode, 30),
            dedup_key=_clip(dedup_key, 128),
            error=_clip(error, _ERROR_MAX),
            requested_by=(requested_by or 'system')[:35],
            creation_time=now,
            # A terminal status is learned at the moment it is written; only
            # `queued` is genuinely pending.
            sent_time=None if status == 'queued' else now,
        )
        session.add(row)
        session.flush()
        return row

    def resolve(self, *, status: str, error: Optional[str] = None,
                when: Optional[datetime] = None) -> 'NotificationLog':
        """Close out a ``queued`` row with its own outcome.

        The single permitted transition on an append-only table. Anything
        else is a new row.

        Raises:
            ValueError: on an unknown status, or on resolving a row that is
                not ``queued`` — which would be a state overwrite, and the
                one thing this table's design rules out.
        """
        if status not in NOTIFICATION_STATUSES:
            raise ValueError(
                f'unknown notification_log.status {status!r}; expected one of '
                f'{", ".join(NOTIFICATION_STATUSES)}')
        if self.status != 'queued':
            raise ValueError(
                f'notification_log {self.notification_log_id} is '
                f'{self.status!r}, not queued; this log is append-only and a '
                f'retry is a new row sharing the dedup_key')

        self.status = status
        self.sent_time = when or datetime.now()
        if error is not None:
            self.error = _clip(error, _ERROR_MAX)
        self.session.flush()
        return self

    # ------------------------------------------------------------------ repr
    def __str__(self):
        return f'{self.kind} → {self.recipient} ({self.status})'

    def __repr__(self):
        return (f'<NotificationLog(id={self.notification_log_id}, '
                f'kind={self.kind!r}, status={self.status!r}, '
                f'recipient={self.recipient!r})>')


def _clip(value: Optional[str], limit: int) -> Optional[str]:
    """Truncate to the column width rather than let MySQL raise 1406.

    Losing the tail of a relay's rejection message is strictly better than
    losing the record that we tried to mail somebody.
    """
    if value is None:
        return None
    return str(value)[:limit]
