"""The directory sweep: every record of each kind compared with SAM's, in bulk, counts only.

Read-only. One ``reader.session()`` for the run; SAM's side is one query per table (the
sync's own group lists, users with a upid eager-loaded, organizations, institutions), never
one per record. Consumed by ``sam-admin directory report`` and the ``directory_report`` task.
A ``limit`` narrows SAM's side to the keys the sample holds (the queries filter on them),
so a smoke is cheap and counts no absences.
"""

from __future__ import annotations

import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional, Tuple

from sqlalchemy.orm import selectinload

from sam.core.organizations import Institution, Organization
from sam.core.users import Phone, User
from sam.integration.directory.compare import (compare_group, compare_institution,
                                                compare_organization, compare_person)
from sam.queries.directory import (group_snapshot_from_row, snapshot_from_institution,
                                   snapshot_from_organization, snapshot_from_user)
from sam.queries.ldapsync import groups, project_groups

SWEPT_KINDS: Tuple[str, ...] = ('person', 'organization', 'institution', 'group')
#: Keys kept per category; the task's ``detail`` is capped at 60 kB by the runner.
MAX_IDS = 50


@dataclass(frozen=True)
class KindReport:
    kind: str
    directory: int                      # records read
    sam: int                            # SAM rows compared (a known-but-inactive group is not one)
    compared: int                       # keys found on both sides
    counts: Dict[str, int]              # category -> discrepancies
    ids: Dict[str, Tuple[str, ...]]     # category -> the first MAX_IDS keys carrying it
    elapsed_seconds: float

    def as_dict(self) -> dict:
        return {'directory': self.directory, 'sam': self.sam, 'compared': self.compared,
                'counts': dict(self.counts), 'ids': {c: list(v) for c, v in self.ids.items()},
                'elapsed_seconds': self.elapsed_seconds}

    def line(self) -> str:
        drift = sum(self.counts.values())
        absent = self.counts.get('missing_in_directory', 0)
        return (f'{self.kind}: {self.directory:,} read, {self.sam:,} in SAM, {drift:,} drift'
                + (f' ({absent:,} missing in directory)' if absent else ''))


@dataclass(frozen=True)
class SweepReport:
    generated: datetime
    kinds: Dict[str, KindReport]
    limit: int = 0

    def as_dict(self) -> dict:
        return {'generated': self.generated.isoformat(), 'limit': self.limit,
                'kinds': {k: r.as_dict() for k, r in self.kinds.items()}}

    def headline(self) -> str:
        return '; '.join(r.line() for r in self.kinds.values())


# --- SAM's side, one query per table -------------------------------------------------

def _people(session, keys=None) -> dict:
    q = session.query(User).filter(User.upid.isnot(None))
    if keys is not None:
        q = q.filter(User.upid.in_(keys))
    q = q.options(
        selectinload(User.phones).selectinload(Phone.phone_type), selectinload(User.organizations),
        selectinload(User.institutions), selectinload(User.academic_status))
    return {u.upid: snapshot_from_user(u) for u in q}


def _organizations(session, keys=None) -> dict:
    q = session.query(Organization)
    if keys is not None:
        q = q.filter(Organization.organization_id.in_(keys))
    return {o.organization_id: snapshot_from_organization(o) for o in q}


def _institutions(session, keys=None) -> dict:
    q = session.query(Institution).options(selectinload(Institution.state_prov),
                                           selectinload(Institution.institution_type))
    if keys is not None:
        q = q.filter(Institution.institution_id.in_(keys))
    return {i.institution_id: snapshot_from_institution(i) for i in q}


def _groups(session, keys=None) -> dict:
    """SAM's groups from the sync's own lists: adhoc first (no managers to compare), then
    projects. An inactive one maps to ``None``: known to SAM, so not ``missing_in_sam``, but
    not compared, because the directory strips a retired group's members while the sync keeps
    its lead and admin, and 4,400 retired projects would drown the live drift."""
    out = {}
    for row in groups(session):
        if row['posix_gid'] is not None:
            out[row['posix_gid']] = group_snapshot_from_row(row) if row['active'] else None
    for row in project_groups(session):
        if row['posix_gid'] is not None:
            out.setdefault(row['posix_gid'], group_snapshot_from_row(
                row, managers=frozenset(row['managers'])) if row['active'] else None)
    return out if keys is None else {k: v for k, v in out.items() if k in keys}


_SAM_SIDE = {'person': _people, 'organization': _organizations, 'institution': _institutions,
             'group': _groups}
_RECORD_KEY = {'person': lambda r: r.upid, 'organization': lambda r: r.organization_id,
               'institution': lambda r: r.institution_id, 'group': lambda r: r.gid}


def _label(kind: str, obj) -> str:
    """The key a row is reported under: username, acronym (else the id), group name."""
    if kind == 'person':
        return obj.username
    if kind == 'group':
        return obj.name
    return obj.acronym or str(_RECORD_KEY[kind](obj))


def _compare(kind: str, record, snap, key: str, now: datetime):
    if kind == 'person':
        return compare_person(record, snap, key=key, side='sam', now=now)
    if kind == 'organization':
        return compare_organization(record, snap, key=key, side='sam')
    if kind == 'institution':
        return compare_institution(record, snap, key=key, side='sam')
    return compare_group(record, snap, key=key, side='sam')


def sweep(session, reader, *, kinds: Tuple[str, ...] = SWEPT_KINDS, limit: int = 0,
          now: Optional[datetime] = None) -> SweepReport:
    """Read every record of each kind, compare with SAM's, count by category."""
    now = now or datetime.now()
    out = {}
    with reader.session():
        for kind in kinds:
            started = time.monotonic()
            records = reader.records(kind, limit=limit)
            by_key = {_RECORD_KEY[kind](r): r for r in records}
            sam = _SAM_SIDE[kind](session, keys=set(by_key) if limit else None)
            counts, ids, compared = Counter(), defaultdict(list), 0
            for key, snap in sam.items():
                if snap is None:        # known to SAM, not compared (an inactive group)
                    continue
                record = by_key.get(key)
                compared += record is not None
                report = _compare(kind, record, snap, _label(kind, snap), now)
                for category, n in report.counts().items():
                    counts[category] += n
                    if len(ids[category]) < MAX_IDS:
                        ids[category].append(report.key)
            for key, record in by_key.items():
                if key not in sam:
                    counts['missing_in_sam'] += 1
                    if len(ids['missing_in_sam']) < MAX_IDS:
                        ids['missing_in_sam'].append(_label(kind, record))
            out[kind] = KindReport(kind, len(records), sum(s is not None for s in sam.values()),
                                   compared, dict(counts),
                                   {c: tuple(v) for c, v in ids.items()},
                                   round(time.monotonic() - started, 1))
    return SweepReport(generated=now, kinds=out, limit=limit)
