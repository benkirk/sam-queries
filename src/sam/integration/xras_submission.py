"""``xras_submission``: one row per XRAS action SAM files for a user.

A draft exists before XRAS assigns anything, so the key is a surrogate; ``action_id`` is
unique once known and ``request_id`` is the line it hangs on. ``state`` records only the
steps SAM owns (``draft`` through ``submitted``, ``aborted``) plus ``applied``, stamped
when the post is processed; between submit and apply XRAS owns the status and this row
does not copy it. No foreign keys (two dialects, the external_ticket convention).
DDL: ``scripts/sql/create_xras_submission.sql`` and its ``_pg`` twin. Design:
``docs/plans/XRAS_SUBMISSION.md`` § 4.2.
"""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import Column, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.ext.hybrid import hybrid_property

from sam.base import Base, SessionMixin

#: Every legal ``state``. A bare ``VARCHAR`` in both dialects, so this tuple is the check.
XRAS_SUBMISSION_STATES = ('draft', 'created', 'populated', 'submitted', 'aborted', 'applied')

#: The flow's own in-flight states: resume continues from these, abort ends them.
OPEN_STATES = ('draft', 'created', 'populated')

#: ``applied`` from ``created`` or ``populated``: an operator's unsubmitted action that was
#: submitted in XRAS instead, then approved and posted.
_NEXT = {
    'draft': ('created', 'aborted'),
    'created': ('populated', 'submitted', 'aborted', 'applied'),
    'populated': ('submitted', 'aborted', 'applied'),
    'submitted': ('applied',),
    'aborted': (),
    'applied': (),
}

_STAMP = {'submitted': 'submitted_time', 'applied': 'applied_time',
          'aborted': 'aborted_time'}

#: The status of a recorded step whose audit row cannot be read: the audit write failed
#: (``_open_event`` logs and returns ``None``) or the row is gone. The write may have
#: landed, so a resume treats it like an unverified one.
UNKNOWN_STEP = 'unknown'


def _dumps(value: Any) -> Optional[str]:
    return None if value is None else json.dumps(value, default=str, sort_keys=True)


class XrasSubmission(Base, SessionMixin):
    """One action SAM files, its resolved SAM state, and the evidence of each step."""

    __tablename__ = 'xras_submission'
    __table_args__ = (
        UniqueConstraint('action_id', name='xras_submission_action'),
        Index('xras_submission_line', 'request_id'),
        Index('xras_submission_project', 'project_id'),
        Index('xras_submission_pi', 'user_id', 'state'),
        Index('xras_submission_by', 'created_by', 'state'),
    )

    xras_submission_id = Column(Integer, primary_key=True, autoincrement=True)
    action_id = Column(Integer)
    request_id = Column(Integer)
    request_number = Column(String(128))
    project_id = Column(Integer)

    action_type = Column(String(24), nullable=False)
    request_type = Column(String(16))
    opportunity_id = Column(Integer)

    user_id = Column(Integer)
    admin_user_id = Column(Integer)
    facility_id = Column(Integer)
    mnemonic_code_id = Column(Integer)
    allocation_type_id = Column(Integer)
    contract_id = Column(Integer)
    registration_id = Column(Integer)

    eula_sha = Column(String(40), nullable=False)
    eula_accepted_at = Column(DateTime, nullable=False)

    state = Column(String(16), nullable=False)
    created_by = Column(String(35), nullable=False)
    xa_user = Column(String(64), nullable=False)
    # App clock, naive-Mountain, no server_default.
    creation_time = Column(DateTime, nullable=False)
    modified_time = Column(DateTime)
    submitted_time = Column(DateTime)
    applied_time = Column(DateTime)
    aborted_time = Column(DateTime)
    last_error = Column(Text)

    payload = Column(Text)
    xras_snapshot = Column(Text)
    step_events = Column(Text)

    @hybrid_property
    def is_active(self) -> bool:
        """Still in flight: resumable, abortable, and a block on a second open row."""
        return self.state in OPEN_STATES

    @is_active.expression
    def is_active(cls):
        return cls.state.in_(OPEN_STATES)

    @classmethod
    def create(cls, session, *, action_type: str, created_by: str, xa_user: str,
               eula_sha: str, eula_accepted_at: datetime, payload: Dict[str, Any],
               **keys: Any) -> 'XrasSubmission':
        """Open a ``draft`` row. *keys* are the nullable key and SAM-reference columns.

        Raises ``ValueError`` on an unknown action type, a missing agreement, or an unknown key.
        """
        from sam.integration.xras_api.vocabulary import SUBMIT_ACTION_TYPES
        if action_type not in SUBMIT_ACTION_TYPES:
            raise ValueError(f'action_type must be one of {", ".join(SUBMIT_ACTION_TYPES)}')
        if not eula_sha or eula_accepted_at is None:
            raise ValueError('every submission records the agreement it was filed under')
        unknown = set(keys) - _KEY_COLUMNS
        if unknown:
            raise ValueError(f'unknown xras_submission columns: {sorted(unknown)}')
        now = datetime.now()
        row = cls(action_type=action_type, created_by=str(created_by)[:35],
                  xa_user=str(xa_user), eula_sha=eula_sha,
                  eula_accepted_at=eula_accepted_at, state='draft',
                  payload=_dumps(payload), step_events=_dumps({}),
                  creation_time=now, modified_time=now, **keys)
        session.add(row)
        session.flush()
        return row

    def advance(self, state: str) -> 'XrasSubmission':
        """Move to *state*, stamping its time and clearing ``last_error``; ``ValueError`` if illegal."""
        if state not in XRAS_SUBMISSION_STATES:
            raise ValueError(f'unknown xras_submission state {state!r}')
        if state not in _NEXT[self.state]:
            raise ValueError(f'xras_submission {self.xras_submission_id}: '
                             f'{self.state} -> {state} is not a legal step')
        now = datetime.now()
        self.state = state
        self.last_error = None
        self.modified_time = now
        if state in _STAMP:
            setattr(self, _STAMP[state], now)
        self.session.flush()
        return self

    def record_step(self, step: str, event_id: Optional[int]) -> 'XrasSubmission':
        """Remember the ``xras_remediation_event`` row behind *step*."""
        events = self.steps
        events[step] = event_id
        self.step_events = _dumps(events)
        self.modified_time = datetime.now()
        self.session.flush()
        return self

    def set_keys(self, *, request_id=None, action_id=None, request_number=None,
                 snapshot=None) -> 'XrasSubmission':
        """Record the XRAS ids as they are learned; ``None`` leaves a column as it is."""
        for name, value in (('request_id', request_id), ('action_id', action_id),
                            ('request_number', request_number)):
            if value is not None:
                setattr(self, name, value)
        if snapshot is not None:
            self.xras_snapshot = _dumps(snapshot)
        self.modified_time = datetime.now()
        self.session.flush()
        return self

    def stop(self, error: Any) -> 'XrasSubmission':
        """Stamp why a step stopped; the state stays where it was, so resume can continue."""
        self.last_error = error if isinstance(error, str) else _dumps(error)
        self.modified_time = datetime.now()
        self.session.flush()
        return self

    def mark_applied(self, project_id: Optional[int] = None) -> 'XrasSubmission':
        """The post was processed: ``applied``, with the project a New created."""
        if project_id is not None:
            self.project_id = project_id
        return self.advance('applied')

    @property
    def payload_dict(self) -> Dict[str, Any]:
        return json.loads(self.payload) if self.payload else {}

    @property
    def snapshot_dict(self) -> Dict[str, Any]:
        return json.loads(self.xras_snapshot) if self.xras_snapshot else {}

    @property
    def steps(self) -> Dict[str, Optional[int]]:
        return json.loads(self.step_events) if self.step_events else {}

    def __str__(self):
        return (f'{self.action_type} {self.state} '
                f'(request {self.request_number or self.request_id or "unminted"})')

    def __repr__(self):
        return (f'<XrasSubmission({self.xras_submission_id}, {self.action_type}, '
                f'{self.state}, request_id={self.request_id}, action_id={self.action_id})>')


_KEY_COLUMNS = frozenset({
    'action_id', 'request_id', 'request_number', 'project_id', 'request_type',
    'opportunity_id', 'user_id', 'admin_user_id', 'facility_id', 'mnemonic_code_id',
    'allocation_type_id', 'contract_id', 'registration_id'})


def step_statuses(session, steps: Dict[str, Optional[int]]) -> Dict[str, str]:
    """``{step: audit status}`` for every recorded step, in one query; see ``UNKNOWN_STEP``."""
    from sam.integration.xras import XrasRemediationEvent
    ids = [i for i in steps.values() if i]
    found: Dict[int, str] = {}
    if ids:
        found = dict(session.query(XrasRemediationEvent.xras_remediation_event_id,
                                   XrasRemediationEvent.status)
                     .filter(XrasRemediationEvent.xras_remediation_event_id.in_(ids)))
    return {step: found.get(i, UNKNOWN_STEP) if i else UNKNOWN_STEP
            for step, i in steps.items()}


def documents_to_reattach(statuses: Dict[str, str], filenames) -> List[str]:
    """The *filenames* a resume must be handed again: never sent, or refused by XRAS (which
    then stored nothing). SAM keeps a document's name and digest, never its bytes. One whose
    outcome is unknown is not among them: the flow will not re-send it, as it may have landed.
    """
    return [name for name in filenames
            if statuses.get(f'document:{name}') in (None, 'rejected')]


def lookup_submission_by_request_id(session, request_id) -> Optional[XrasSubmission]:
    """The newest non-aborted row on line *request_id*, or ``None``: the New handler's consult key."""
    if not request_id:
        return None
    return (session.query(XrasSubmission)
            .filter(XrasSubmission.request_id == int(request_id))
            .filter(XrasSubmission.state != 'aborted')
            .order_by(XrasSubmission.xras_submission_id.desc())
            .first())


def new_submissions_by_request_id(session) -> Dict[int, XrasSubmission]:
    """``{request_id: row}`` for every non-aborted New SAM filed, newest winning, in one query."""
    out: Dict[int, XrasSubmission] = {}
    for row in (session.query(XrasSubmission)
                .filter(XrasSubmission.action_type == 'New')
                .filter(XrasSubmission.state != 'aborted')
                .filter(XrasSubmission.request_id.isnot(None))
                .order_by(XrasSubmission.xras_submission_id)):
        out[row.request_id] = row
    return out


def submission_by_action_id(session, action_id) -> Optional[XrasSubmission]:
    if not action_id:
        return None
    return (session.query(XrasSubmission)
            .filter(XrasSubmission.action_id == int(action_id)).one_or_none())


def open_submissions_for_user(session, username: str):
    """In-flight rows *username* filed or is impersonated on, newest first."""
    return (session.query(XrasSubmission)
            .filter(XrasSubmission.is_active)
            .filter((XrasSubmission.created_by == username)
                    | (XrasSubmission.xa_user == username))
            .order_by(XrasSubmission.xras_submission_id.desc())
            .all())


def open_submission_for(session, *, request_id, action_type: str,
                        created_by: str) -> Optional[XrasSubmission]:
    """The open row this actor already holds for ``(request_id, action_type)``: the second-open guard."""
    if not request_id:
        return None
    return (session.query(XrasSubmission)
            .filter(XrasSubmission.is_active)
            .filter(XrasSubmission.request_id == int(request_id))
            .filter(XrasSubmission.action_type == action_type)
            .filter(XrasSubmission.created_by == created_by)
            .first())
