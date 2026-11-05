"""Contracts built from people and awards: the one place a PI or monitor is placed.

A person who is a SAM user goes on the contract's user FK; one who is not becomes an
``ExternalContact`` (docs/plans/implemented/EXTERNAL_CONTACTS.md). ``plan_contract_from_award`` turns a
fetched award into a contract plan, refusing when the award does not fit the request;
``apply_contract_plan`` writes it. The XRAS post, the Remediations Create button and the
create form share this. Nothing here commits.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional, Tuple

from sqlalchemy import func

from sam.core.users import User
from sam.integration.awards.base import AwardRecord, PersonRef
from sam.integration.awards.people import _by_email, resolve_person
from sam.projects.contracts import Contract, ContractSource, ExternalContact, NSFProgram

#: Refusal reasons from :func:`plan_contract_from_award`.
INCOMPLETE_AWARD = 'incomplete_award'
NUMBER_TAKEN = 'number_taken'
NO_PI = 'no_pi'
PI_MISMATCH = 'pi_mismatch'

_ROLE_COLUMNS = {'pi': ('principal_investigator_user_id', 'pi_contact_id'),
                 'monitor': ('contract_monitor_user_id', 'monitor_contact_id')}


def person_columns(session, role: str, *, user_id: Optional[int] = None,
                   name: Optional[str] = None, email: Optional[str] = None,
                   organization: Optional[str] = None) -> dict:
    """Contract column kwargs for one person: the user FK, else a found-or-created contact.

    A typed contact whose email a SAM user holds goes on the user FK instead.
    """
    user_col, contact_col = _ROLE_COLUMNS[role]
    if user_id:
        return {user_col: user_id}
    if not (name or '').strip():
        return {}
    user = _by_email(session, email)
    if user is not None:
        return {user_col: user.user_id}
    contact = ExternalContact.find_or_create(session, name=name, email=email,
                                             organization=organization)
    if contact.user_id:
        return {user_col: contact.user_id, contact_col: contact.external_contact_id}
    return {contact_col: contact.external_contact_id}


def _fold(text: Optional[str]) -> list:
    """Lower-case name tokens with accents and punctuation removed."""
    plain = unicodedata.normalize('NFKD', text or '').encode('ascii', 'ignore').decode()
    return [t for t in ''.join(c if c.isalnum() else ' ' for c in plain).lower().split() if t]


def same_surname(a: Optional[str], b: Optional[str]) -> bool:
    """Whether two free-text names share a last name ("C Spencer Jones" / "Spencer Jones")."""
    left, right = _fold(a), _fold(b)
    if not left or not right:
        return False
    return left[-1] == right[-1] or left[-1] in right or right[-1] in left


@dataclass(frozen=True)
class PlannedPerson:
    """A contract person to write: a SAM user, or the agency's person as a new contact."""
    user: Optional[User]
    ref: Optional[PersonRef]

    @property
    def label(self) -> str:
        return self.user.display_name if self.user is not None else self.ref.label

    @property
    def is_user(self) -> bool:
        return self.user is not None


@dataclass(frozen=True)
class ContractPlan:
    """What :func:`apply_contract_plan` will create, for a preflight to show."""
    record: AwardRecord
    contract_source_id: int
    pi: PlannedPerson
    monitor: Optional[PlannedPerson]
    nsf_program_id: Optional[int]

    @property
    def contract_number(self) -> str:
        return self.record.contract_number


def _roster_match(person: PersonRef, roster_users: Iterable[User]) -> Optional[User]:
    """The request's own user this agency person is: by email, else one first+surname match,
    else the one roster user with that surname (NSF writes "C Spencer Jones" for Spencer Jones)."""
    users = [u for u in roster_users if u is not None]
    address = (person.email or '').strip().lower()
    if address:
        for user in users:
            if address in {e.lower() for e in (user.all_emails or ())}:
                return user
    tokens = _fold(person.name)
    if len(tokens) < 2:
        return None
    hits = [u for u in users
            if _fold(u.last_name)[-1:] == tokens[-1:] and _fold(u.first_name)[:1] == tokens[:1]]
    if len(hits) == 1:
        return hits[0]
    hits = [u for u in users if _fold(u.last_name)[-1:] == tokens[-1:]]
    return hits[0] if len(hits) == 1 else None


def _plan_person(session, person: Optional[PersonRef], roster_users) -> Optional[PlannedPerson]:
    if not person:
        return None
    user = _roster_match(person, roster_users) or resolve_person(session, person)
    return PlannedPerson(user=user, ref=None if user is not None else person)


def plan_contract_from_award(session, record: Optional[AwardRecord], *, source: ContractSource,
                             wire_pi_name: Optional[str] = None,
                             roster_users: Iterable[User] = ()
                             ) -> Tuple[Optional[ContractPlan], Optional[str]]:
    """``(plan, None)`` or ``(None, reason)``; never writes.

    People resolve against *roster_users* first, then any SAM user, then become contacts. A
    *wire_pi_name* (the request's PI for the grant) with another surname means another award.
    """
    from sam.xras.extractors import contract_candidates, extract_core_number
    if (record is None or not record.contract_number or not (record.title or '').strip()
            or record.start_date is None):
        return None, INCOMPLETE_AWARD
    if (Contract.get_by_number(session, record.contract_number) is not None
            or contract_candidates(session, extract_core_number(record.contract_number))):
        return None, NUMBER_TAKEN
    if not record.pi or not (record.pi.name or '').strip():
        return None, NO_PI
    if wire_pi_name and not same_surname(wire_pi_name, record.pi.name):
        return None, PI_MISMATCH
    roster = list(roster_users)
    monitor = record.monitor if record.monitor and (record.monitor.name or '').strip() else None
    program_id = None
    if record.program_name:
        program = (session.query(NSFProgram)
                   .filter(func.lower(NSFProgram.nsf_program_name) == record.program_name.lower())
                   .first())
        program_id = program.nsf_program_id if program else None
    return ContractPlan(record=record, contract_source_id=source.contract_source_id,
                        pi=_plan_person(session, record.pi, roster),
                        monitor=_plan_person(session, monitor, roster),
                        nsf_program_id=program_id), None


def _columns_for(session, role: str, planned: Optional[PlannedPerson]) -> dict:
    if planned is None:
        return {}
    if planned.is_user:
        return person_columns(session, role, user_id=planned.user.user_id)
    return person_columns(session, role, name=planned.ref.name, email=planned.ref.email)


def apply_contract_plan(session, plan: ContractPlan) -> Contract:
    """Create the contacts and the contract a plan describes."""
    record = plan.record
    kwargs = {**_columns_for(session, 'pi', plan.pi), **_columns_for(session, 'monitor', plan.monitor)}
    end = (datetime.combine(record.end_date, datetime.max.time()).replace(microsecond=0)
           if record.end_date else None)
    return Contract.create(
        session, contract_number=record.contract_number, title=record.title[:255],
        start_date=datetime.combine(record.start_date, datetime.min.time()), end_date=end,
        url=record.url, contract_source_id=plan.contract_source_id,
        nsf_program_id=plan.nsf_program_id, **kwargs)
