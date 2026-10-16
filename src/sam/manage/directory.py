"""Reconcile SAM to a directory record, one kind at a time, each behind ``DIRECTORY_APPLY_<KIND>``.

Step 2 of docs/plans/DIRECTORY_CLIENT.md (AFFILIATIONS stage 2). The rules consume the
compare categories: a row the directory ties to by id is brought to the record; SAM-only
history, twins, an inactive entry with no end date and anything a lever withholds are
reported, never written. Runs inside the caller's ``management_transaction``; ``dry_run``
decides the same changes and touches nothing.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

from sqlalchemy import func

from sam.core.groups import AdhocGroup
from sam.core.organizations import (Institution, InstitutionType, Organization,
                                    UserInstitution, UserOrganization)
from sam.core.users import AcademicStatus, EmailAddress, Phone, PhoneType, User
from sam.dates import end_of_day
from sam.integration.directory.base import (CollaborationRecord, DirectoryReader,
                                            GroupRecord, InstitutionRecord,
                                            OrganizationRecord, PersonRecord,
                                            PositionRecord)
from sam.integration.directory.compare import (
    Discrepancy, DriftReport, compare_group, compare_institution, compare_organization,
    compare_person,
)
from sam.integration.directory.config import DirectoryConfig
from sam.manage.ldapsync import _apply_active_transition, _state_prov_id
from sam.queries.directory import (snapshot_from_group, snapshot_from_institution,
                                   snapshot_from_organization, snapshot_from_user)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Change:
    kind: str
    category: str
    field: str = ''
    sam_id: Optional[int] = None
    directory_id: str = ''
    value: str = ''


@dataclass(frozen=True)
class ReconcileResult:
    kind: str
    key: str
    report: DriftReport
    applied: Tuple[Change, ...] = ()
    withheld: Tuple[Discrepancy, ...] = ()
    dry_run: bool = False

    def fragment(self) -> str:
        """``applied=2[name:1,planned_end:1] withheld=1[twin:1]``, ``dry-run`` prefixed when so."""
        parts = []
        for label, items in (('applied', self.applied), ('withheld', self.withheld)):
            counts = ','.join(f'{k}:{v}' for k, v in sorted(Counter(i.category for i in items).items()))
            parts.append(f'{label}={len(items)}' + (f'[{counts}]' if counts else ''))
        return ('dry-run ' if self.dry_run else '') + ' '.join(parts)


class _Reconciler:
    """Collects decisions; mutates only when not a dry run."""

    def __init__(self, kind: str, config: DirectoryConfig, dry_run: bool) -> None:
        self.kind = kind
        self.config = config
        self.dry_run = dry_run
        self.applied: List[Change] = []
        self.withheld: List[Discrepancy] = []

    def allowed(self, kind: str) -> bool:
        return self.config.apply_enabled(kind)

    def apply(self, d: Discrepancy, mutate: Callable[[], None], *, value: str = '') -> None:
        if not self.allowed(d.kind):
            self.withheld.append(d)
            return
        self.applied.append(Change(d.kind, d.category, d.field, d.sam_id, d.directory_id,
                                   value or d.directory_value))
        if not self.dry_run:
            mutate()

    def withhold(self, d: Discrepancy) -> None:
        self.withheld.append(d)

    def result(self, key: str, report: DriftReport) -> ReconcileResult:
        return ReconcileResult(self.kind, key, report, tuple(self.applied), tuple(self.withheld),
                               self.dry_run)


# --- people -------------------------------------------------------------------------

def _academic_status_id(session, code: Optional[str]) -> Optional[int]:
    if not code:
        return None
    row = (session.query(AcademicStatus.academic_status_id)
           .filter(func.lower(AcademicStatus.academic_status_code) == code.lower())
           .order_by(AcademicStatus.academic_status_id).first())
    return row[0] if row else None


def _phone_types(session) -> Dict[str, int]:
    return {t.phone_type.lower(): t.ext_phone_type_id for t in session.query(PhoneType)}


def _digits(number: str) -> str:
    return ''.join(ch for ch in number if ch.isdigit())


def _person_field(r: _Reconciler, session, user: User, record: PersonRecord, d: Discrepancy,
                  now: datetime, on_reactivate) -> None:
    if d.category == 'active':
        def flip():
            closed_at = _apply_active_transition(user, record.active, now)
            if closed_at is not None and on_reactivate is not None:
                on_reactivate(user, closed_at)
        r.apply(d, flip)
    elif d.category == 'academic_status':
        status_id = _academic_status_id(session, record.academic_status_code)
        if record.academic_status_code and status_id is None:
            r.withhold(d)
            return
        r.apply(d, lambda: setattr(user, 'academic_status_id', status_id))
    elif d.category == 'token_type':
        r.apply(d, lambda: setattr(user, 'token_type', record.token_type or None))
    elif d.category == 'name':
        value = getattr(record, d.field) or None
        r.apply(d, lambda: setattr(user, d.field, value), value='set' if value else 'cleared')
    elif d.category == 'email':
        _email(r, user, record, d)
    elif d.category == 'phone':
        _phone(r, session, user, record, d)
    else:                        # username, unix_uid, academic_status_unmapped, missing_*
        r.withhold(d)


def _email(r: _Reconciler, user: User, record: PersonRecord, d: Discrepancy) -> None:
    def mutate():
        by_address = {row.email_address.lower(): row for row in user.email_addresses}
        for e in record.emails:
            row = by_address.get(e.address.lower())
            if row is None:
                row = EmailAddress(email_address=e.address, is_primary=e.primary)
                user.email_addresses.append(row)
                by_address[e.address.lower()] = row
        primary = next((e.address.lower() for e in record.emails if e.primary), None)
        if primary is not None:
            for row in user.email_addresses:
                row.is_primary = row.email_address.lower() == primary
    r.apply(d, mutate, value=d.field)


def _phone(r: _Reconciler, session, user: User, record: PersonRecord, d: Discrepancy) -> None:
    types = _phone_types(session)
    wanted = {(p.label.lower(), _digits(p.number)): p for p in record.phones}
    if d.field == 'missing' and any(key[0] not in types for key in wanted):
        r.withhold(d)
        return

    def mutate():
        for row in list(user.phones):
            label = row.phone_type.phone_type.lower() if row.phone_type else ''
            if (label, _digits(row.phone_number)) not in wanted:
                user.phones.remove(row)
        have = {((row.phone_type.phone_type.lower() if row.phone_type else ''),
                 _digits(row.phone_number)) for row in user.phones}
        for key, p in wanted.items():
            if key not in have and key[0] in types:
                user.phones.append(Phone(phone_number=p.number, ext_phone_type_id=types[key[0]]))
    r.apply(d, mutate, value=d.field)


def _known_organization(session, organization_id: Optional[int]) -> bool:
    return organization_id is not None and session.get(Organization, organization_id) is not None


def _position(r: _Reconciler, session, user: User, record: PersonRecord, d: Discrepancy) -> None:
    rows = {row.user_organization_id: row for row in user.organizations}
    p: Optional[PositionRecord] = next((x for x in record.positions
                                        if x.position_id == d.directory_id), None)
    row: Optional[UserOrganization] = rows.get(d.sam_id) if d.sam_id is not None else None
    if d.category == 'planned_end' and row is not None and p is not None:
        r.apply(d, lambda: setattr(row, 'end_date', end_of_day(p.end)))
    elif d.category == 'finished_vs_open' and row is not None and p is not None:
        if d.directory_value == 'finished' and p.end is not None:
            r.apply(d, lambda: setattr(row, 'end_date', end_of_day(p.end)), value=p.end.date().isoformat())
        else:                    # D-C: no end to copy; or a reopen, which stage 2 does not do
            r.withhold(d)
    elif d.category == 'employer' and row is not None and p is not None \
            and _known_organization(session, p.organization_id):
        r.apply(d, lambda: setattr(row, 'organization_id', p.organization_id))
    elif d.category == 'no_sam_row' and p is not None and p.start is not None \
            and _known_organization(session, p.organization_id):
        r.apply(d, lambda: user.organizations.append(UserOrganization(
            user_id=user.user_id, organization_id=p.organization_id, start_date=p.start,
            end_date=end_of_day(p.end), idms_unique_name=p.position_id)),
            value=p.start.date().isoformat())
    else:                        # twin, history_open, end_day, start_day, or nothing to copy
        r.withhold(d)


def _collaboration(r: _Reconciler, session, user: User, record: PersonRecord,
                   d: Discrepancy) -> None:
    rows = {row.user_institution_id: row for row in user.institutions}
    c: Optional[CollaborationRecord] = next(
        (x for x in record.collaborations if str(x.institution_id) == d.directory_id), None)
    row: Optional[UserInstitution] = rows.get(d.sam_id) if d.sam_id is not None else None
    if d.category == 'finished_vs_open' and row is not None and c is not None \
            and d.directory_value == 'finished' and c.end is not None:
        r.apply(d, lambda: setattr(row, 'end_date', c.end), value=c.end.date().isoformat())
    elif d.category == 'no_sam_row' and c is not None and c.start is not None \
            and session.get(Institution, c.institution_id) is not None:
        r.apply(d, lambda: user.institutions.append(UserInstitution(
            user_id=user.user_id, institution_id=c.institution_id, start_date=c.start,
            end_date=c.end)), value=c.start.date().isoformat())
    else:                        # planned_end (PeopleDB's dates), no_directory_start, history_open, start_day
        r.withhold(d)


def reconcile_person(session, user: Optional[User], record: Optional[PersonRecord], *,
                     config: DirectoryConfig, key: str = '', now: Optional[datetime] = None,
                     dry_run: bool = False, on_reactivate=None) -> ReconcileResult:
    """Bring one user's own fields, positions and collaborations to the record, per lever."""
    now = now or datetime.now()
    key = key or (user.username if user is not None else (str(record.upid) if record else ''))
    snap = snapshot_from_user(user) if user is not None else None
    report = compare_person(record, snap, key=key, side='sam', now=now)
    r = _Reconciler('person', config, dry_run)
    if user is None or record is None:
        for d in report.discrepancies:
            r.withhold(d)
        return r.result(key, report)
    for d in report.discrepancies:
        if d.kind == 'person':
            _person_field(r, session, user, record, d, now, on_reactivate)
        elif d.kind == 'position':
            _position(r, session, user, record, d)
        elif d.kind == 'collaboration':
            _collaboration(r, session, user, record, d)
        else:
            r.withhold(d)
    if r.applied and not dry_run:
        session.flush()
    return r.result(key, report)


# --- organizations, institutions, groups ------------------------------------------------

def reconcile_organization(session, org: Optional[Organization],
                           record: Optional[OrganizationRecord], *, config: DirectoryConfig,
                           key: str = '', dry_run: bool = False) -> ReconcileResult:
    """All differing fields at once through ``Organization.upsert_from_sync``; a missing row is created."""
    key = key or str(record.organization_id if record else (org.organization_id if org else ''))
    snap = snapshot_from_organization(org) if org is not None else None
    report = compare_organization(record, snap, key=key, side='sam')
    r = _Reconciler('organization', config, dry_run)
    if record is None:
        for d in report.discrepancies:
            r.withhold(d)
        return r.result(key, report)
    fields = {'name': record.name or None, 'acronym': record.acronym or None,
              'level': record.level or None, 'level_code': record.level_code or None,
              'parent_org_id': record.parent_org_id, 'active': record.active}
    if record.parent_org_id is not None and session.get(Organization, record.parent_org_id) is None:
        fields.pop('parent_org_id')
    done = {'written': False}

    def mutate():
        if not done['written']:
            Organization.upsert_from_sync(session, organization_id=record.organization_id, **fields)
            done['written'] = True
    for d in report.discrepancies:
        if d.category == 'parent' and 'parent_org_id' not in fields:
            r.withhold(d)
        else:
            r.apply(d, mutate)
    return r.result(key, report)


def _institution_type_id(session, type_name: str) -> Optional[int]:
    if not type_name:
        return None
    row = (session.query(InstitutionType.institution_type_id)
           .filter(func.lower(InstitutionType.type) == type_name.strip().lower()).first())
    return row[0] if row else None


def reconcile_institution(session, inst: Optional[Institution],
                          record: Optional[InstitutionRecord], *, config: DirectoryConfig,
                          key: str = '', dry_run: bool = False) -> ReconcileResult:
    key = key or str(record.institution_id if record else (inst.institution_id if inst else ''))
    snap = snapshot_from_institution(inst) if inst is not None else None
    report = compare_institution(record, snap, key=key, side='sam')
    r = _Reconciler('institution', config, dry_run)
    if record is None:
        for d in report.discrepancies:
            r.withhold(d)
        return r.result(key, report)
    type_id = _institution_type_id(session, record.org_type)
    fields = {'name': record.name or None, 'acronym': record.acronym or None,
              'nsf_org_code': record.nsf_org_code or None, 'address': record.street or None,
              'city': record.city or None, 'zip': record.postal_code or None,
              'state_prov_id': _state_prov_id(session, record.country, record.state_code,
                                              record.acronym)}
    if type_id is not None:
        fields['institution_type_id'] = type_id
    done = {'written': False}

    def mutate():
        if not done['written']:
            Institution.upsert_from_sync(session, institution_id=record.institution_id, **fields)
            done['written'] = True
    for d in report.discrepancies:
        if d.category == 'type' and type_id is None:
            r.withhold(d)
        else:
            r.apply(d, mutate)
    return r.result(key, report)


def reconcile_group(session, gid: int, record: Optional[GroupRecord], *, config: DirectoryConfig,
                    key: str = '', dry_run: bool = False) -> ReconcileResult:
    """Groups are reported only: project membership flows SAM to directory, adhoc entries are SAM's."""
    snap = snapshot_from_group(session, gid)
    key = key or (snap.name if snap else str(gid))
    report = compare_group(record, snap, key=key, side='sam')
    r = _Reconciler('group', config, dry_run)
    for d in report.discrepancies:
        r.withhold(d)
    return r.result(key, report)


# --- dispatch ----------------------------------------------------------------------------

def reconcile_record(session, kind: str, key, record, *, config: DirectoryConfig,
                     now: Optional[datetime] = None, dry_run: bool = False,
                     on_reactivate=None) -> Optional[ReconcileResult]:
    """Load SAM's side by ``key`` (upid, organization id, institution id or gid) and reconcile it to ``record``."""
    if key is None:
        return None
    if kind == 'person':
        user = User.get_by_upid(session, key)
        return reconcile_person(session, user, record, config=config, now=now, dry_run=dry_run,
                                on_reactivate=on_reactivate)
    if kind == 'organization':
        return reconcile_organization(session, session.get(Organization, key), record,
                                      config=config, dry_run=dry_run)
    if kind == 'institution':
        return reconcile_institution(session, session.get(Institution, key), record,
                                     config=config, dry_run=dry_run)
    if kind == 'group':
        return reconcile_group(session, key, record, config=config, dry_run=dry_run)
    return None


def reconcile_from_directory(session, kind: str, key, *, reader: DirectoryReader,
                             config: DirectoryConfig, now: Optional[datetime] = None,
                             dry_run: bool = False, on_reactivate=None) -> Optional[ReconcileResult]:
    """Read ``key`` from the directory, then :func:`reconcile_record`. Raises ``DirectoryUnavailable``."""
    reads = {'person': reader.person, 'organization': reader.organization,
             'institution': reader.institution, 'group': reader.group}
    if kind not in reads or key is None:
        return None
    return reconcile_record(session, kind, key, reads[kind](key), config=config, now=now,
                            dry_run=dry_run, on_reactivate=on_reactivate)


def log_result(result: ReconcileResult, key) -> None:
    logger.info('ldapsync: directory apply %s %s key=%s %s ids=%s', result.kind, result.key, key,
                result.fragment(), ','.join(result.report.ids()[:10]) or '-')
