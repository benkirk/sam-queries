"""Pure comparison of a directory record with a snapshot of SAM's rows (or of the daemon's increment).

Both sides reduce to the same snapshot shape, so one function reports the drift of
SAM against the directory and of the increment against the directory. A ``Discrepancy``
carries ids, dates, flags and usernames; personal names, email addresses and phone
numbers are counted, never valued. Rules: docs/plans/LDAPSYNC_DIRECTORY_AFFILIATIONS.md
section 3.4, section 6 and Appendix C.2.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, FrozenSet, List, Optional, Tuple

from sam.integration.directory.base import (
    CollaborationRecord, EmailRecord, GroupRecord, InstitutionRecord, OrganizationRecord,
    PersonRecord, PhoneRecord, PositionRecord,
)

PERSON_CATEGORIES: Tuple[str, ...] = (
    'missing_in_sam', 'missing_in_directory', 'username', 'unix_uid', 'active',
    'academic_status', 'academic_status_unmapped', 'token_type', 'name', 'email', 'phone')
AFFILIATION_CATEGORIES: Tuple[str, ...] = (
    'planned_end', 'finished_vs_open', 'employer', 'no_sam_row', 'twin', 'history_open',
    'end_day', 'start_day', 'no_directory_start')
ORGANIZATION_CATEGORIES: Tuple[str, ...] = (
    'missing_in_sam', 'missing_in_directory', 'acronym', 'name', 'level', 'level_code',
    'parent', 'active')
INSTITUTION_CATEGORIES: Tuple[str, ...] = (
    'missing_in_sam', 'missing_in_directory', 'acronym', 'name', 'state', 'type',
    'nsf_org_code', 'address')
GROUP_CATEGORIES: Tuple[str, ...] = (
    'missing_in_sam', 'missing_in_directory', 'name', 'active', 'tags',
    'member_missing_in_sam', 'member_missing_in_directory')

SIDES = ('increment', 'sam')
NAME_FIELDS = ('first_name', 'last_name', 'middle_name', 'nickname', 'name_suffix')
MAX_IDS = 10


# --- snapshots -----------------------------------------------------------------

@dataclass(frozen=True)
class AffiliationSnapshot:
    """One ``user_organization`` or ``user_institution`` row (or the daemon's copy of one)."""
    row_id: Optional[int]
    employer_id: Optional[int]
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    directory_id: str = ''


@dataclass(frozen=True)
class PersonSnapshot:
    upid: Optional[int]
    username: str = ''
    unix_uid: Optional[int] = None
    active: bool = False
    first_name: str = ''
    last_name: str = ''
    middle_name: str = ''
    nickname: str = ''
    name_suffix: str = ''
    academic_status_code: Optional[str] = None
    token_type: str = ''
    emails: Tuple[EmailRecord, ...] = ()
    phones: Tuple[PhoneRecord, ...] = ()
    positions: Tuple[AffiliationSnapshot, ...] = ()
    collaborations: Tuple[AffiliationSnapshot, ...] = ()
    user_id: Optional[int] = None


@dataclass(frozen=True)
class OrganizationSnapshot:
    organization_id: int
    acronym: str = ''
    name: str = ''
    level: str = ''
    level_code: str = ''
    parent_org_id: Optional[int] = None
    active: bool = False


@dataclass(frozen=True)
class InstitutionSnapshot:
    institution_id: int
    acronym: str = ''
    name: str = ''
    country: str = ''
    state_code: str = ''
    org_type: str = ''
    nsf_org_code: str = ''
    city: str = ''
    street: str = ''
    postal_code: str = ''


@dataclass(frozen=True)
class GroupSnapshot:
    gid: int
    name: str = ''
    active: bool = False
    tags: FrozenSet[str] = frozenset()
    member_upids: FrozenSet[int] = frozenset()


# --- the result ---------------------------------------------------------------

@dataclass(frozen=True)
class Discrepancy:
    kind: str
    category: str
    field: str = ''
    sam_id: Optional[int] = None
    directory_id: str = ''
    sam_value: str = ''
    directory_value: str = ''


@dataclass(frozen=True)
class DriftReport:
    kind: str
    key: str
    side: str
    found_in_directory: bool
    discrepancies: Tuple[Discrepancy, ...] = ()

    def counts(self) -> Dict[str, int]:
        return dict(Counter(d.category for d in self.discrepancies))

    def ids(self) -> List[str]:
        """Row ids first (``uo:12``), else directory keys (``position:P17``); each once."""
        out: List[str] = []
        for d in self.discrepancies:
            if d.sam_id is not None:
                item = f'{_ID_PREFIX.get(d.kind, d.kind)}:{d.sam_id}'
            elif d.directory_id:
                item = f'{d.kind}:{d.directory_id}'
            else:
                continue
            if item not in out:
                out.append(item)
        return out

    def fragment(self) -> str:
        """``drift=3[employer:1,planned_end:2] ids=uo:12,uo:15``; ``absent`` when the directory lacks the record."""
        if not self.found_in_directory:
            return 'absent'
        counts = ','.join(f'{k}:{v}' for k, v in sorted(self.counts().items()))
        text = f'drift={len(self.discrepancies)}' + (f'[{counts}]' if counts else '')
        ids = self.ids()
        if ids:
            shown = ','.join(ids[:MAX_IDS]) + ('...' if len(ids) > MAX_IDS else '')
            text += f' ids={shown}'
        return text


_ID_PREFIX = {'person': 'u', 'position': 'uo', 'collaboration': 'ui', 'organization': 'org',
              'institution': 'inst', 'group': 'gid'}


# --- helpers -------------------------------------------------------------------

def _text(value) -> str:
    return '' if value is None else str(value).strip()


def _flag(value) -> str:
    return 'true' if value else 'false'


def _day(value: Optional[datetime]) -> str:
    return value.strftime('%Y-%m-%d') if value else ''


def is_open(end: Optional[datetime], now: datetime) -> bool:
    return end is None or end > now


def _digits(number: str) -> str:
    return re.sub(r'\D', '', number or '')


def _record_open(active: bool, end: Optional[datetime], now: datetime) -> bool:
    return bool(active) and is_open(end, now)


# --- people --------------------------------------------------------------------

def _person_fields(record: PersonRecord, snap: PersonSnapshot) -> List[Discrepancy]:
    out = []

    def differ(category, field, sam_value, directory_value, *, valued=True):
        out.append(Discrepancy('person', category, field, sam_id=snap.user_id,
                               sam_value=_text(sam_value) if valued else '',
                               directory_value=_text(directory_value) if valued else ''))

    if record.username and _text(record.username).lower() != _text(snap.username).lower():
        differ('username', 'username', snap.username, record.username)
    if record.unix_uid is not None and record.unix_uid != snap.unix_uid:
        differ('unix_uid', 'unix_uid', snap.unix_uid, record.unix_uid)
    if bool(record.active) != bool(snap.active):
        differ('active', 'active', _flag(snap.active), _flag(record.active))
    if record.academic_status and record.academic_status_code is None:
        differ('academic_status_unmapped', 'academic_status', snap.academic_status_code,
               record.academic_status)
    elif _text(record.academic_status_code).upper() != _text(snap.academic_status_code).upper():
        differ('academic_status', 'academic_status', snap.academic_status_code,
               record.academic_status_code)
    if _text(record.token_type).lower() != _text(snap.token_type).lower():
        differ('token_type', 'token_type', snap.token_type, record.token_type)
    for name in NAME_FIELDS:
        if _text(getattr(record, name)) != _text(getattr(snap, name)):
            differ('name', name, '', '', valued=False)
    sam_addresses = {e.address.lower() for e in snap.emails if e.address}
    for e in record.emails:
        if e.address.lower() not in sam_addresses:
            differ('email', 'missing', '', '', valued=False)
    primary = next((e.address.lower() for e in record.emails if e.primary), '')
    sam_primary = next((e.address.lower() for e in snap.emails if e.primary), '')
    if primary and sam_primary and primary != sam_primary:
        differ('email', 'primary', '', '', valued=False)
    sam_phones = {(p.label.lower(), _digits(p.number)) for p in snap.phones}
    dir_phones = {(p.label.lower(), _digits(p.number)) for p in record.phones}
    for _ in dir_phones - sam_phones:
        differ('phone', 'missing', '', '', valued=False)
    for _ in sam_phones - dir_phones:
        differ('phone', 'extra', '', '', valued=False)
    return out


def _positions(record: PersonRecord, snap: PersonSnapshot, now: datetime) -> List[Discrepancy]:
    out = []
    by_directory_id: Dict[str, AffiliationSnapshot] = {}
    for row in snap.positions:
        if row.directory_id:
            by_directory_id.setdefault(row.directory_id, row)
    directory_ids = {p.position_id for p in record.positions}

    def note(category, row: Optional[AffiliationSnapshot], p: Optional[PositionRecord],
             sam_value='', directory_value='', field=''):
        out.append(Discrepancy('position', category, field,
                               sam_id=row.row_id if row else None,
                               directory_id=p.position_id if p else '',
                               sam_value=_text(sam_value), directory_value=_text(directory_value)))

    for p in record.positions:
        row = by_directory_id.get(p.position_id)
        if row is None:
            twins = [q for q in record.positions
                     if q.position_id != p.position_id and q.organization_id is not None
                     and q.organization_id == p.organization_id
                     and q.position_id in by_directory_id]
            note('twin' if twins else 'no_sam_row', None, p,
                 directory_value=twins[0].position_id if twins else _day(p.start))
            continue
        if p.organization_id is not None and row.employer_id != p.organization_id:
            note('employer', row, p, row.employer_id, p.organization_id)
        p_open = _record_open(p.active, p.end, now)
        r_open = is_open(row.end, now)
        if p_open != r_open:
            note('finished_vs_open', row, p, 'open' if r_open else 'finished',
                 'open' if p_open else 'finished')
        if p.active and p.end is not None and p.end > now and row.end is None:
            note('planned_end', row, p, '', _day(p.end))
        elif p.end is not None and row.end is not None and p.end.date() != row.end.date():
            note('end_day', row, p, _day(row.end), _day(p.end))
        if p.start is not None and row.start is not None and p.start.date() != row.start.date():
            note('start_day', row, p, _day(row.start), _day(p.start))
    for row in snap.positions:
        if row.directory_id in directory_ids:
            continue
        if is_open(row.end, now):
            note('history_open', row, None, field='no directory id' if not row.directory_id
                 else 'id not in directory')
    return out


def _latest(rows: List[AffiliationSnapshot]) -> AffiliationSnapshot:
    return max(rows, key=lambda r: (r.start is not None, r.start or datetime.min))


def _collaborations(record: PersonRecord, snap: PersonSnapshot, now: datetime) -> List[Discrepancy]:
    out = []
    by_institution: Dict[int, List[AffiliationSnapshot]] = {}
    for row in snap.collaborations:
        if row.employer_id is not None:
            by_institution.setdefault(row.employer_id, []).append(row)
    directory_institutions = {c.institution_id for c in record.collaborations}

    def note(category, row: Optional[AffiliationSnapshot], c: Optional[CollaborationRecord],
             sam_value='', directory_value='', field=''):
        out.append(Discrepancy('collaboration', category, field,
                               sam_id=row.row_id if row else None,
                               directory_id=str(c.institution_id) if c else '',
                               sam_value=_text(sam_value), directory_value=_text(directory_value)))

    for c in record.collaborations:
        rows = by_institution.get(c.institution_id)
        if c.start is None:
            note('no_directory_start', _latest(rows) if rows else None, c)
        if not rows:
            note('no_sam_row', None, c, directory_value=_day(c.start))
            continue
        row = _latest(rows)
        c_open = _record_open(c.active, c.end, now)
        r_open = is_open(row.end, now)
        if c_open != r_open:
            note('finished_vs_open', row, c, 'open' if r_open else 'finished',
                 'open' if c_open else 'finished')
        if c.active and c.end is not None and c.end > now and row.end is None:
            note('planned_end', row, c, '', _day(c.end))
        if c.start is not None and row.start is not None and c.start.date() != row.start.date():
            note('start_day', row, c, _day(row.start), _day(c.start))
    for row in snap.collaborations:
        if row.employer_id not in directory_institutions and is_open(row.end, now):
            note('history_open', row, None, field='institution not in directory')
    return out


def compare_person(record: Optional[PersonRecord], snap: Optional[PersonSnapshot], *,
                   key: str, side: str, now: Optional[datetime] = None) -> DriftReport:
    """The person's own fields, then positions, then collaborations."""
    now = now or datetime.now()
    if record is None:
        found = [Discrepancy('person', 'missing_in_directory', sam_id=snap.user_id)] if snap else []
        return DriftReport('person', key, side, False, tuple(found))
    if snap is None:
        return DriftReport('person', key, side, True,
                           (Discrepancy('person', 'missing_in_sam', directory_id=str(record.upid)),))
    found = _person_fields(record, snap) + _positions(record, snap, now) \
        + _collaborations(record, snap, now)
    return DriftReport('person', key, side, True, tuple(found))


# --- organizations, institutions, groups ------------------------------------------

def _field_diffs(kind: str, pairs, sam_id) -> List[Discrepancy]:
    return [Discrepancy(kind, category, field, sam_id=sam_id, sam_value=_text(a),
                        directory_value=_text(b))
            for category, field, a, b in pairs if _text(a) != _text(b)]


def compare_organization(record: Optional[OrganizationRecord],
                         snap: Optional[OrganizationSnapshot], *, key: str,
                         side: str) -> DriftReport:
    if record is None:
        found = [Discrepancy('organization', 'missing_in_directory',
                             sam_id=snap.organization_id)] if snap else []
        return DriftReport('organization', key, side, False, tuple(found))
    if snap is None:
        return DriftReport('organization', key, side, True, (Discrepancy(
            'organization', 'missing_in_sam', directory_id=str(record.organization_id)),))
    pairs = (('acronym', 'acronym', snap.acronym, record.acronym),
             ('name', 'name', snap.name, record.name),
             ('level', 'level', snap.level, record.level),
             ('level_code', 'level_code', snap.level_code, record.level_code),
             ('parent', 'parent_org_id', snap.parent_org_id, record.parent_org_id),
             ('active', 'active', _flag(snap.active), _flag(record.active)))
    return DriftReport('organization', key, side, True,
                       tuple(_field_diffs('organization', pairs, snap.organization_id)))


def compare_institution(record: Optional[InstitutionRecord],
                        snap: Optional[InstitutionSnapshot], *, key: str,
                        side: str) -> DriftReport:
    if record is None:
        found = [Discrepancy('institution', 'missing_in_directory',
                             sam_id=snap.institution_id)] if snap else []
        return DriftReport('institution', key, side, False, tuple(found))
    if snap is None:
        return DriftReport('institution', key, side, True, (Discrepancy(
            'institution', 'missing_in_sam', directory_id=str(record.institution_id)),))
    state = record.state_code if record.state_code != record.country else ''
    pairs = (('acronym', 'acronym', snap.acronym, record.acronym),
             ('name', 'name', snap.name, record.name),
             ('state', 'state', f'{snap.country}/{snap.state_code}'.strip('/'),
              f'{record.country}/{state}'.strip('/')),
             ('type', 'type', snap.org_type.lower(), record.org_type.lower()),
             ('nsf_org_code', 'nsf_org_code', snap.nsf_org_code, record.nsf_org_code),
             ('address', 'address', '|'.join((snap.street, snap.city, snap.postal_code)),
              '|'.join((record.street, record.city, record.postal_code))))
    return DriftReport('institution', key, side, True,
                       tuple(_field_diffs('institution', pairs, snap.institution_id)))


def compare_group(record: Optional[GroupRecord], snap: Optional[GroupSnapshot], *,
                  key: str, side: str, sam_tags_only: bool = True) -> DriftReport:
    """Members by upid; tags compared on the set SAM keeps when ``sam_tags_only``."""
    if record is None:
        found = [Discrepancy('group', 'missing_in_directory', sam_id=snap.gid)] if snap else []
        return DriftReport('group', key, side, False, tuple(found))
    if snap is None:
        return DriftReport('group', key, side, True,
                           (Discrepancy('group', 'missing_in_sam', directory_id=str(record.gid)),))
    found = _field_diffs('group', (('name', 'name', snap.name, record.name),
                                   ('active', 'active', _flag(snap.active), _flag(record.active))),
                         snap.gid)
    record_tags = set(record.tags)
    if sam_tags_only:
        record_tags &= set(snap.tags) | record_tags
    if set(snap.tags) - record_tags or (record_tags - set(snap.tags) and not sam_tags_only):
        found.append(Discrepancy('group', 'tags', 'tags', sam_id=snap.gid,
                                 sam_value=','.join(sorted(snap.tags)),
                                 directory_value=','.join(sorted(record.tags))))
    for upid in sorted(set(record.member_upids) - set(snap.member_upids)):
        found.append(Discrepancy('group', 'member_missing_in_sam', 'member',
                                 directory_id=str(upid), directory_value=str(upid)))
    for upid in sorted(set(snap.member_upids) - set(record.member_upids)):
        found.append(Discrepancy('group', 'member_missing_in_directory', 'member',
                                 sam_value=str(upid)))
    return DriftReport('group', key, side, True, tuple(found))
