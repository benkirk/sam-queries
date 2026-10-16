"""Directory test doubles: a dict-backed reader and record builders.

``FakeDirectory`` records every call; set ``raise_with`` to make the next read raise.
``entry(**attrs)`` builds ldap3's ``attributes`` shape (every value a list) for the parsers.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sam.integration.directory.base import (CollaborationRecord, DirectoryReader,
                                            EmailRecord, GroupRecord, InstitutionRecord,
                                            OrganizationRecord, PersonRecord, PhoneRecord,
                                            PositionRecord)


class FakeDirectory(DirectoryReader):
    name = 'fake'

    def __init__(self, *, configured: bool = True) -> None:
        self._configured = configured
        self.people: Dict[int, PersonRecord] = {}
        self.organizations: Dict[int, OrganizationRecord] = {}
        self.institutions: Dict[int, InstitutionRecord] = {}
        self.groups: Dict[int, GroupRecord] = {}
        self.calls: List[Tuple[str, Any]] = []
        self.raise_with: Optional[Exception] = None

    @classmethod
    def from_environment(cls) -> 'FakeDirectory':
        return cls()

    @property
    def configured(self) -> bool:
        return self._configured

    def summary(self) -> Dict[str, Any]:
        return {'provider': self.name, 'configured': self._configured}

    def add(self, record) -> Any:
        """Seed one record of any kind."""
        store = {PersonRecord: (self.people, 'upid'),
                 OrganizationRecord: (self.organizations, 'organization_id'),
                 InstitutionRecord: (self.institutions, 'institution_id'),
                 GroupRecord: (self.groups, 'gid')}[type(record)]
        store[0][getattr(record, store[1])] = record
        return record

    def _read(self, op: str, key, store: dict):
        self.guard_read()
        self.calls.append((op, key))
        if self.raise_with is not None:
            raise self.raise_with
        return store.get(key)

    def person(self, upid: int) -> Optional[PersonRecord]:
        return self._read('person', upid, self.people)

    def organization(self, organization_id: int) -> Optional[OrganizationRecord]:
        return self._read('organization', organization_id, self.organizations)

    def institution(self, institution_id: int) -> Optional[InstitutionRecord]:
        return self._read('institution', institution_id, self.institutions)

    def group(self, gid: int) -> Optional[GroupRecord]:
        return self._read('group', gid, self.groups)

    def records(self, kind: str, *, limit: int = 0) -> List[Any]:
        self.guard_read()
        self.calls.append(('records', kind))
        if self.raise_with is not None:
            raise self.raise_with
        store = {'person': self.people, 'organization': self.organizations,
                 'institution': self.institutions, 'group': self.groups}[kind]
        values = list(store.values())
        return values[:limit] if limit else values

    def search(self, kind: str, query: str, *, limit: int = 20) -> List[Any]:
        self.guard_read()
        self.calls.append(('search', (kind, query)))
        store = {'person': self.people, 'organization': self.organizations,
                 'institution': self.institutions, 'group': self.groups}[kind]
        q = query.lower()
        return [r for r in store.values() if q in str(r).lower()][:limit]


def make_person_record(upid: int = 35399, **overrides) -> PersonRecord:
    values = dict(upid=upid, username=f'user{upid}', unix_uid=10000 + upid, active=True,
                  first_name='Jane', last_name='Doe', nickname='', middle_name='',
                  name_suffix='', emails=(EmailRecord(f'user{upid}@example.invalid', True),),
                  phones=(PhoneRecord('Ucar Office', '303-555-0100'),),
                  academic_status='', academic_status_code=None, token_type='DUO', source='FI',
                  positions=(), collaborations=(), modified=datetime(2026, 8, 4, 2, 58, 19))
    values.update(overrides)
    return PersonRecord(**values)


def make_position_record(position_id: str = 'P001', **overrides) -> PositionRecord:
    values = dict(position_id=position_id, organization_id=375,
                  start=datetime(2022, 1, 18), end=None, active=True, source='FI')
    values.update(overrides)
    return PositionRecord(**values)


def make_collaboration_record(institution_id: int = 1, **overrides) -> CollaborationRecord:
    values = dict(institution_id=institution_id, start=datetime(2020, 7, 1), end=None,
                  active=True, admin_org='CISL', source='PDB')
    values.update(overrides)
    return CollaborationRecord(**values)


def make_group_record(gid: int = 68283, name: str = 'SCSG0001', **overrides) -> GroupRecord:
    values = dict(gid=gid, name=name, tags=('exclude-from-google', 'hpc'), member_upids=(35399,),
                  active=True, manager_gids=(), source='PDB', managers=())
    values.update(overrides)
    return GroupRecord(**values)


def entry(**attrs) -> Dict[str, List[str]]:
    """ldap3's ``attributes`` dict: a list per attribute, hyphenated names via ``attrs={...}``."""
    raw = attrs.pop('attrs', {})
    out = {}
    for name, value in {**attrs, **raw}.items():
        out[name] = [str(v) for v in value] if isinstance(value, (list, tuple)) else [str(value)]
    return out
