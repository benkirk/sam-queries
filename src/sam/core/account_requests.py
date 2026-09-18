"""HPC account requests: the record of a person who needs an account.

SAM never creates users (docs/xras/PROJECT_AND_ACCOUNT_LIFECYCLE.md section 2);
NUSD does. These two tables hold what SAM cannot re-derive from the mirror:
who asked, who vouched, whether NUSD was told, and who set a row aside. Whether
the account now EXISTS is derived from ``users`` at read time and stamped by
the reconcile pass -- never trusted from the row alone.

Design: docs/plans/ACCOUNT_REGISTRATION.md.
"""

import re
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Boolean, Column, Date, DateTime, Index, Integer, String, Text, UniqueConstraint, and_,
)
from sqlalchemy.ext.hybrid import hybrid_property

from ..base import ActiveFlagMixin, Base, SessionMixin


#: ``account_request.state``. Fulfilled is deliberately not a state: a row is
#: fulfilled when a matching ``users`` row exists, which is observed, not stored.
ACCOUNT_REQUEST_STATES = ('submitted', 'claimed', 'rejected', 'dismissed')

#: The states the queue works. ``requested_at`` is orthogonal to these, so a
#: claimed row can be included in a digest without losing its assignee.
OPEN_STATES = ('submitted', 'claimed')

#: ``account_request.purpose``. What happens on fulfillment:
#: ``standalone`` nothing further; ``enrollment`` joins ``project_id``;
#: ``submission`` merges ``xras_username`` (XRAS phase 3, not built).
ACCOUNT_REQUEST_PURPOSES = ('standalone', 'enrollment', 'submission')

CREATED_BY_SELF = 'self'
CREATED_BY_SWEEP = 'task:xras_sweep'

#: Upper-case, dash-separated, 3 to 32 characters: ``WRF-TUTORIAL-2026-10``.
EVENT_CODE_RE = re.compile(r'^[A-Z0-9][A-Z0-9-]{2,31}$')

_UNSET = object()


def _clean(value, *, width: Optional[int] = None) -> Optional[str]:
    """Strip; empty becomes None; optionally clip to a column width."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:width] if width else text


#----------------------------------------------------------------------------
class AccountRequestEvent(Base, ActiveFlagMixin, SessionMixin):
    """A cohort of requests sharing one project and one deadline.

    ``active`` is the kill switch; ``opens_at``/``closes_at`` is the schedule.
    They answer different questions, so both exist and :meth:`is_open_at`
    combines them.
    """
    __tablename__ = 'account_request_event'

    __table_args__ = (
        UniqueConstraint('event_code', name='account_request_event_code'),
        Index('account_request_event_project', 'project_id'),
    )

    account_request_event_id = Column(Integer, primary_key=True, autoincrement=True)
    event_code = Column(String(32), nullable=False)
    name = Column(String(128), nullable=False)
    #: Sponsor prose shown on the public form under the event name.
    instructions = Column(Text)
    project_id = Column(Integer, nullable=False)
    extra_sponsor_user_id = Column(Integer)
    accounts_needed_by = Column(Date, nullable=False)
    opens_at = Column(DateTime)
    closes_at = Column(DateTime)
    #: Opt-in public discoverability; an unlisted event is reachable by link only.
    listed = Column(Boolean, nullable=False, default=False)
    created_by = Column(String(35), nullable=False)
    #: App clock, never a DB default -- SAM's convention is naive-Mountain.
    creation_time = Column(DateTime, nullable=False)
    modified_time = Column(DateTime, nullable=False, default=datetime.now,
                           onupdate=datetime.now)

    def __str__(self):
        return f"{self.event_code} ({self.name}, by {self.accounts_needed_by})"

    def __repr__(self):
        return f"<AccountRequestEvent {self.event_code!r} project={self.project_id}>"

    def is_open_at(self, now: Optional[datetime] = None) -> bool:
        """Whether the public form accepts this code at ``now``."""
        now = now or datetime.now()
        if not self.active:
            return False
        if self.opens_at is not None and now < self.opens_at:
            return False
        if self.closes_at is not None and now >= self.closes_at:
            return False
        return True

    @staticmethod
    def normalize_code(code) -> str:
        """Upper-case and strip; raises ValueError when it does not fit the pattern."""
        text = str(code or '').strip().upper()
        if not EVENT_CODE_RE.match(text):
            raise ValueError(
                f'event code {code!r} must be 3-32 upper-case letters, digits '
                f'or dashes, starting with a letter or digit')
        return text

    @classmethod
    def create(cls, session, *, event_code, name, project_id, accounts_needed_by,
               created_by, instructions=None, extra_sponsor_user_id=None,
               opens_at=None, closes_at=None, listed=False, clock=None):
        """Flushes, does not commit; the caller owns the transaction."""
        name = _clean(name, width=128)
        if not name:
            raise ValueError('an event needs a name')
        if not isinstance(accounts_needed_by, date):
            raise ValueError('accounts_needed_by must be a date')
        if opens_at and closes_at and closes_at < opens_at:
            raise ValueError('closes_at must not precede opens_at')
        now = clock or datetime.now()
        event = cls(
            event_code=cls.normalize_code(event_code),
            name=name,
            instructions=_clean(instructions),
            project_id=int(project_id),
            extra_sponsor_user_id=extra_sponsor_user_id,
            accounts_needed_by=accounts_needed_by,
            opens_at=opens_at,
            closes_at=closes_at,
            listed=bool(listed),
            created_by=_clean(created_by, width=35),
            creation_time=now,
            modified_time=now,
        )
        if not event.created_by:
            raise ValueError('created_by is required')
        session.add(event)
        session.flush()
        return event

    def update(self, *, name=None, accounts_needed_by=None, instructions=_UNSET,
               opens_at=_UNSET, closes_at=_UNSET, extra_sponsor_user_id=_UNSET,
               listed=None):
        """Sentinel-gated so a caller can clear the window, sponsor or instructions."""
        if name is not None:
            cleaned = _clean(name, width=128)
            if not cleaned:
                raise ValueError('an event needs a name')
            self.name = cleaned
        if instructions is not _UNSET:
            self.instructions = _clean(instructions)
        if accounts_needed_by is not None:
            self.accounts_needed_by = accounts_needed_by
        if opens_at is not _UNSET:
            self.opens_at = opens_at
        if closes_at is not _UNSET:
            self.closes_at = closes_at
        if self.opens_at and self.closes_at and self.closes_at < self.opens_at:
            raise ValueError('closes_at must not precede opens_at')
        if extra_sponsor_user_id is not _UNSET:
            self.extra_sponsor_user_id = extra_sponsor_user_id
        if listed is not None:
            self.listed = bool(listed)
        self.session.flush()
        return self

    def close(self):
        self.active = False
        self.session.flush()
        return self

    def reopen(self):
        self.active = True
        self.session.flush()
        return self


#----------------------------------------------------------------------------
class AccountRequest(Base, SessionMixin):
    """One person who needs an account, and what SAM did about it.

    Invariants enforced in :meth:`create` because the columns are bare
    VARCHARs: ``enrollment`` needs a ``project_id``, ``submission`` needs an
    ``xras_username``, ``standalone`` has no project. ``email`` is lower-cased
    on the way in; it is the ONE key the reconcile pass matches on.
    ``desired_username`` is a hint for NUSD and never a match key -- a
    case-insensitive hit on a stranger's username is a plausible collision.

    Unlike ``XrasRemediationEvent`` a scheduled task MAY create rows here:
    the XRAS sweep derives ``submission`` rows from handoff rosters.
    """
    __tablename__ = 'account_request'

    # WARNING: on Postgres an index name shares one namespace with table
    # names, so no index here may be called ``account_request_event``.
    __table_args__ = (
        Index('account_request_state', 'state', 'verified_at'),
        Index('account_request_email', 'email'),
        Index('account_request_xras', 'xras_username'),
        Index('account_request_event_ref', 'event_id'),
        Index('account_request_project', 'project_id'),
        Index('account_request_origin', 'created_by', 'creation_time'),
    )

    account_request_id = Column(Integer, primary_key=True, autoincrement=True)

    # Identity claim -- the XRAS person field set.
    email = Column(String(255), nullable=False)
    first_name = Column(String(64), nullable=False)
    middle_name = Column(String(64))
    last_name = Column(String(64), nullable=False)
    organization = Column(String(128))
    academic_status = Column(String(64))
    residence_country = Column(String(64))
    orcid = Column(String(19))
    phone = Column(String(32))
    desired_username = Column(String(64))

    # Intent.
    purpose = Column(String(16), nullable=False)
    project_id = Column(Integer)
    sponsor_user_id = Column(Integer)
    event_id = Column(Integer)
    #: Stored lower-cased: a match key the sweep and the Pending Users card
    #: look up with a plain IN, so the index serves it on either backend.
    xras_username = Column(String(64))

    # Queue state.
    state = Column(String(16), nullable=False)
    assignee = Column(String(35))
    requested_at = Column(DateTime)
    closed_by = Column(String(35))
    closed_at = Column(DateTime)
    closed_reason = Column(String(255))
    comment = Column(Text)
    purpose_note = Column(String(500))

    # Provenance.
    created_by = Column(String(35), nullable=False)
    verified_at = Column(DateTime)
    verified_by = Column(String(35))
    verify_code_hash = Column(String(64))
    verify_expires_at = Column(DateTime)
    verify_sent_count = Column(Integer, nullable=False, default=0)
    source_ip = Column(String(45))
    creation_time = Column(DateTime, nullable=False)
    modified_time = Column(DateTime, nullable=False, default=datetime.now,
                           onupdate=datetime.now)

    # Fulfillment.
    user_id = Column(Integer)
    upid = Column(Integer)
    fulfilled_at = Column(DateTime)
    fulfill_error = Column(String(255))
    #: When the operator-chosen rejection notice left.
    closure_notified_at = Column(DateTime)
    #: Not written yet: the XRAS placeholder merge (phase 3).
    merged_at = Column(DateTime)

    def __str__(self):
        return f"{self.email} ({self.display_name}, {self.purpose}, {self.state})"

    def __repr__(self):
        return (f"<AccountRequest {self.account_request_id} {self.email!r} "
                f"{self.purpose}/{self.state}>")

    @property
    def display_name(self) -> str:
        return ' '.join(p for p in (self.first_name, self.last_name) if p)

    @property
    def is_fulfilled(self) -> bool:
        return self.user_id is not None

    @property
    def is_verified(self) -> bool:
        return self.verified_at is not None

    @hybrid_property
    def is_open(self) -> bool:
        """In the queue: an open state AND verified. Unverified rows are invisible."""
        return self.state in OPEN_STATES and self.verified_at is not None

    @is_open.expression
    def is_open(cls):
        return and_(cls.state.in_(OPEN_STATES), cls.verified_at.isnot(None))

    @classmethod
    def create(cls, session, *, email, first_name, last_name, purpose, created_by,
               middle_name=None, organization=None, academic_status=None,
               residence_country=None, orcid=None, phone=None,
               desired_username=None, project_id=None, sponsor_user_id=None,
               event_id=None, xras_username=None, comment=None, purpose_note=None,
               verified_by=None, source_ip=None, clock=None):
        """Flushes, does not commit.

        ``verified_by`` set means the row is visible to the queue at once: a
        sponsor or the sweep is vouching. The public form leaves it None and
        the row waits for the mail round trip.

        Raises:
            ValueError: unknown purpose, a purpose invariant broken, or a
                missing identity field.
        """
        if purpose not in ACCOUNT_REQUEST_PURPOSES:
            raise ValueError(
                f"unknown account_request.purpose {purpose!r}; "
                f"expected one of {', '.join(ACCOUNT_REQUEST_PURPOSES)}")
        if purpose == 'enrollment' and project_id is None:
            raise ValueError('an enrollment request needs a project_id')
        if purpose == 'submission' and not _clean(xras_username):
            raise ValueError('a submission request needs an xras_username')
        if purpose == 'standalone' and project_id is not None:
            raise ValueError('a standalone request carries no project_id')
        address = _clean(email, width=255)
        if not address or '@' not in address:
            raise ValueError(f'not an email address: {email!r}')
        first, last = _clean(first_name, width=64), _clean(last_name, width=64)
        if not first or not last:
            raise ValueError('first_name and last_name are required')
        creator = _clean(created_by, width=35)
        if not creator:
            raise ValueError('created_by is required')
        now = clock or datetime.now()
        placeholder = _clean(xras_username, width=64)
        row = cls(
            email=address.lower(),
            first_name=first,
            middle_name=_clean(middle_name, width=64),
            last_name=last,
            organization=_clean(organization, width=128),
            academic_status=_clean(academic_status, width=64),
            residence_country=_clean(residence_country, width=64),
            orcid=_clean(orcid, width=19),
            phone=_clean(phone, width=32),
            desired_username=_clean(desired_username, width=64),
            purpose=purpose,
            project_id=project_id,
            sponsor_user_id=sponsor_user_id,
            event_id=event_id,
            xras_username=placeholder.lower() if placeholder else None,
            state='submitted',
            comment=_clean(comment),
            purpose_note=_clean(purpose_note, width=500),
            created_by=creator,
            verified_at=now if verified_by else None,
            verified_by=_clean(verified_by, width=35),
            source_ip=_clean(source_ip, width=45),
            creation_time=now,
            modified_time=now,
        )
        session.add(row)
        session.flush()
        return row

    # -- queue transitions ---------------------------------------------------

    def _require_open(self, verb: str) -> None:
        if self.state not in OPEN_STATES:
            raise ValueError(f'cannot {verb} a {self.state} request')

    def claim(self, by):
        self._require_open('claim')
        self.state = 'claimed'
        self.assignee = _clean(by, width=35)
        self.session.flush()
        return self

    def unclaim(self):
        self._require_open('unclaim')
        self.state = 'submitted'
        self.assignee = None
        self.session.flush()
        return self

    def _close(self, state: str, verb: str, by, reason, clock=None):
        self._require_open(verb)
        cleaned = _clean(reason, width=255)
        if not cleaned:
            raise ValueError(f'a reason is required to {verb} a request')
        self.state = state
        self.assignee = None
        self.closed_by = _clean(by, width=35)
        self.closed_at = clock or datetime.now()
        self.closed_reason = cleaned
        self.session.flush()
        return self

    def dismiss(self, by, reason, clock=None):
        """Set aside: a duplicate, or a person who already has an account."""
        return self._close('dismissed', 'dismiss', by, reason, clock)

    def reject(self, by, reason, clock=None):
        """Refuse; the reason is recorded and, if the operator chose, mailed."""
        return self._close('rejected', 'reject', by, reason, clock)

    def mark_closure_notified(self, when=None):
        """The rejection notice reached the requester (send first, stamp second)."""
        self.closure_notified_at = when or datetime.now()
        self.session.flush()
        return self

    def reopen(self):
        if self.state in OPEN_STATES:
            raise ValueError('the request is already open')
        self.state = 'submitted'
        self.closed_by = None
        self.closed_at = None
        self.closed_reason = None
        self.closure_notified_at = None
        self.session.flush()
        return self

    # -- verification --------------------------------------------------------

    def set_verification(self, code_hash: str, expires_at: datetime):
        """One call per verification mail issued; the count is the abuse signal."""
        self.verify_code_hash = code_hash
        self.verify_expires_at = expires_at
        self.verify_sent_count = (self.verify_sent_count or 0) + 1
        self.session.flush()
        return self

    def mark_verified(self, by, clock=None):
        """``by`` is ``'self'`` for the mail round trip, else the vouching operator."""
        self.verified_at = clock or datetime.now()
        self.verified_by = _clean(by, width=35)
        self.verify_code_hash = None
        self.verify_expires_at = None
        self.session.flush()
        return self

    # -- what SAM did --------------------------------------------------------

    def mark_requested(self, when=None):
        """First told only: every later digest is in notification_log."""
        if self.requested_at is None:
            self.requested_at = when or datetime.now()
            self.session.flush()
        return self

    def fulfill(self, user, when=None):
        """Stamp the mirrored account. ``upid`` outlives a username change."""
        self.user_id = user.user_id
        self.upid = user.upid
        self.fulfilled_at = when or datetime.now()
        self.fulfill_error = None
        self.session.flush()
        return self

    def record_fulfill_error(self, message):
        self.fulfill_error = _clean(message, width=255)
        self.session.flush()
        return self


#: ``account_request_event_enrollment.source`` -- how the enrollment was made.
ENROLLMENT_SOURCES = ('self', 'invite', 'roster', 'reconcile')


#----------------------------------------------------------------------------
class EventEnrollment(Base, SessionMixin):
    """One user's enrollment in one event -- the durable event<->user tie.

    ``add_user_to_project`` links user<->project only; this records that the
    link was made for an event, so "who enrolled via X" and "which events am I
    in" are answerable. One row per ``(event_id, user_id)``; :meth:`upsert` is
    idempotent, which every enrollment path relies on.
    """
    __tablename__ = 'account_request_event_enrollment'

    __table_args__ = (
        UniqueConstraint('event_id', 'user_id',
                         name='account_request_event_enrollment_uk'),
        Index('account_request_event_enrollment_user', 'user_id'),
    )

    enrollment_id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(Integer, nullable=False)
    user_id = Column(Integer, nullable=False)
    #: ``users.upid`` -- outlives a username change, as on the request row.
    upid = Column(Integer)
    source = Column(String(16), nullable=False)
    created_by = Column(String(35), nullable=False)
    enrolled_at = Column(DateTime, nullable=False)
    creation_time = Column(DateTime, nullable=False)
    modified_time = Column(DateTime, nullable=False, default=datetime.now,
                           onupdate=datetime.now)

    def __str__(self):
        return f"user {self.user_id} in event {self.event_id} ({self.source})"

    def __repr__(self):
        return (f"<EventEnrollment event={self.event_id} user={self.user_id} "
                f"{self.source!r}>")

    @classmethod
    def upsert(cls, session, *, event, user, source, by, clock=None):
        """Record ``user`` in ``event`` once; a repeat is a no-op. Flushes."""
        if source not in ENROLLMENT_SOURCES:
            raise ValueError(f'unknown enrollment source {source!r}')
        event_id = event.account_request_event_id
        existing = (session.query(cls)
                    .filter(cls.event_id == event_id, cls.user_id == user.user_id)
                    .first())
        if existing is not None:
            return existing
        now = clock or datetime.now()
        row = cls(event_id=event_id, user_id=user.user_id, upid=user.upid,
                  source=source, created_by=_clean(by, width=35) or source,
                  enrolled_at=now, creation_time=now, modified_time=now)
        session.add(row)
        session.flush()
        return row
