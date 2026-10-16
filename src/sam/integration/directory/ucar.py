"""The UCAR directory (``ldap.ucar.edu``): the one place that knows its schema.

Anonymous reads of ``ou=allPeople`` (people with their position and collaboration
children, all keyed by ``x-ucar-upid``), ``ou=allOrganizations``, ``ou=externalOrgs``
and ``ou=allGroups``. Field map: docs/plans/LDAPSYNC_DIRECTORY_AFFILIATIONS.md Appendix C.
``ldap3`` is imported inside the connection, so the parsers run without it.
"""

from __future__ import annotations

import re
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import replace
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

from sam.dates import from_generalized_time
from sam.integration.directory.base import (
    CollaborationRecord, DirectoryReader, DirectoryUnavailable, EmailRecord, GroupRecord,
    InstitutionRecord, OrganizationRecord, PersonRecord, PhoneRecord, PositionRecord,
)
from sam.integration.directory.config import DirectoryConfig

Attrs = Mapping[str, Sequence[str]]

PEOPLE = 'ou=allPeople'
ORGANIZATIONS = 'ou=allOrganizations'
INSTITUTIONS = 'ou=externalOrgs'
GROUPS = 'ou=allGroups'

PERSON_CLASS = 'x-ucar-uniquePerson'
POSITION_CLASS = 'x-ucar-position'
COLLABORATION_CLASS = 'x-ucar-collaboration'
ORGANIZATION_CLASS = 'x-ucar-extendedOrganization'
INSTITUTION_CLASS = 'x-ucar-externalOrganization'
GROUP_CLASS = 'x-ucar-uniqueGroup'

OPERATIONAL = ('modifiersName', 'modifyTimestamp')
PERSON_ATTRS = ('objectClass', 'x-ucar-upid', 'uid', 'uidNumber', 'givenName', 'sn',
                'x-ucar-preferredName', 'x-ucar-middleName', 'x-ucar-nameSuffix', 'mail',
                'x-ucar-forwardEmail', 'x-ucar-typedPhone', 'x-ucar-academicStatus',
                'x-ucar-authType', 'x-ucar-active', 'x-ucar-source', 'x-ucar-githubUser',
                'x-ucar-supervisor', 'physicalDeliveryOfficeName', 'title', 'o', 'ou',
                'x-ucar-adminFor',
                'x-ucar-positionId', 'x-ucar-organizationId', 'x-ucar-startDate',
                'x-ucar-endDate', 'x-ucar-position-type', 'x-ucar-primary',
                'x-ucar-externalOrgId', 'x-ucar-adminOrg') + OPERATIONAL
ORGANIZATION_ATTRS = ('x-ucar-organizationId', 'ou', 'o', 'x-ucar-orgLevel',
                      'x-ucar-orgLevelCode', 'x-ucar-parent', 'x-ucar-active',
                      'x-ucar-source') + OPERATIONAL
INSTITUTION_ATTRS = ('x-ucar-externalOrgId', 'x-ucar-shortOrgName', 'o', 'c', 'st',
                     'x-ucar-stateProvCode', 'l', 'street', 'postalCode', 'x-ucar-nsfOrgCode',
                     'x-ucar-externalOrgType', 'x-ucar-source') + OPERATIONAL
GROUP_ATTRS = ('gidNumber', 'x-ucar-groupName', 'cn', 'x-ucar-tag', 'uniqueMember',
               'x-ucar-active', 'x-ucar-adminFor', 'x-ucar-source') + OPERATIONAL

#: Workday people carry the academic status as a label; PeopleDB people carry SAM's
#: two-letter code. Lower-cased label -> code, from ``academic_status.description``.
ACADEMIC_STATUS_LABELS: Dict[str, str] = {
    'center non-research staff': 'CN',
    'center research staff': 'CR',
    'faculty': 'F',
    'government user': 'G',
    'graduate student': 'GS',
    'high school student': 'HS',
    'high school teacher': 'HT',
    'industrial user': 'I',
    'unaffiliated user': 'N',
    'nonprofit user': 'NP',
    'other user': 'O',
    'post doc': 'PD',
    'undergraduate student': 'UG',
    'unknown': 'UK',
    'university non-research staff': 'UN',
    'university research staff (excluding postdoctorates)': 'UR',
    'university research staff': 'UR',
}


def academic_status_code(value: str) -> Optional[str]:
    """SAM's code for a directory value: a 1-2 letter value is a code already; a label is mapped."""
    text = (value or '').strip()
    if not text:
        return None
    if len(text) <= 2:
        return text.upper()
    return ACADEMIC_STATUS_LABELS.get(text.lower())


# --- pure parsers over ldap3's ``attributes`` shape (every value a list) ----------

def _values(attrs: Attrs, name: str) -> List[str]:
    value = attrs.get(name)
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v is not None and str(v) != '']
    return [str(value)] if str(value) != '' else []


def _first(attrs: Attrs, name: str) -> str:
    values = _values(attrs, name)
    return values[0].strip() if values else ''


def _flag(attrs: Attrs, name: str) -> bool:
    return _first(attrs, name).upper() == 'TRUE'


def _int(attrs: Attrs, name: str) -> Optional[int]:
    text = _first(attrs, name)
    try:
        return int(text)
    except ValueError:
        return None


def _time(attrs: Attrs, name: str):
    return from_generalized_time(_first(attrs, name))


def _dn_id(dn: str, key: str) -> Optional[int]:
    """The integer value of ``key=`` in a DN's leading component; None when absent."""
    match = re.match(rf'^\s*{re.escape(key)}=(\d+)\s*,', dn + ',', re.IGNORECASE)
    return int(match.group(1)) if match else None


def _parent_org_id(dn: str) -> Optional[int]:
    """An organization parent; a parent under ``ou=externalOrgs`` is not one SAM holds."""
    if not dn or INSTITUTIONS.lower() in dn.lower():
        return None
    return _dn_id(dn, 'x-ucar-organizationId')


def _phone(value: str) -> PhoneRecord:
    label, sep, number = value.partition(':')
    if not sep:
        return PhoneRecord('', value.strip())
    return PhoneRecord(label.strip(), number.strip())


def _classes(attrs: Attrs) -> set:
    return {c.lower() for c in _values(attrs, 'objectClass')}


def parse_position(attrs: Attrs) -> PositionRecord:
    return PositionRecord(
        position_id=_first(attrs, 'x-ucar-positionId'),
        organization_id=_int(attrs, 'x-ucar-organizationId'),
        start=_time(attrs, 'x-ucar-startDate'),
        end=_time(attrs, 'x-ucar-endDate'),
        active=_flag(attrs, 'x-ucar-active'),
        source=_first(attrs, 'x-ucar-source'),
        modifier=_first(attrs, 'modifiersName'),
    )


def parse_collaboration(attrs: Attrs) -> Optional[CollaborationRecord]:
    institution_id = _int(attrs, 'x-ucar-externalOrgId')
    if institution_id is None:
        return None
    return CollaborationRecord(
        institution_id=institution_id,
        start=_time(attrs, 'x-ucar-startDate'),
        end=_time(attrs, 'x-ucar-endDate'),
        active=_flag(attrs, 'x-ucar-active'),
        admin_org=_first(attrs, 'x-ucar-adminOrg'),
        source=_first(attrs, 'x-ucar-source'),
        modifier=_first(attrs, 'modifiersName'),
    )


def parse_person(attrs: Attrs, positions: Sequence[Attrs] = (),
                 collaborations: Sequence[Attrs] = ()) -> Optional[PersonRecord]:
    upid = _int(attrs, 'x-ucar-upid')
    if upid is None:
        return None
    emails = []
    if _first(attrs, 'mail'):
        emails.append(EmailRecord(_first(attrs, 'mail'), primary=True))
    forward = _first(attrs, 'x-ucar-forwardEmail')
    if forward and forward.lower() != _first(attrs, 'mail').lower():
        emails.append(EmailRecord(forward, primary=False))
    status = _first(attrs, 'x-ucar-academicStatus')
    extra = {key: _first(attrs, name) for key, name in (
        ('supervisor', 'x-ucar-supervisor'), ('office', 'physicalDeliveryOfficeName'),
        ('github_user', 'x-ucar-githubUser'), ('title', 'title'), ('org', 'o'),
        ('org_unit', 'ou'))}
    extra['admin_for'] = ','.join(str(g) for g in (
        _dn_id(dn, 'gidNumber') for dn in _values(attrs, 'x-ucar-adminFor')) if g is not None)
    return PersonRecord(
        upid=upid,
        username=_first(attrs, 'uid'),
        unix_uid=_int(attrs, 'uidNumber'),
        active=_flag(attrs, 'x-ucar-active'),
        first_name=_first(attrs, 'givenName'),
        last_name=_first(attrs, 'sn'),
        middle_name=_first(attrs, 'x-ucar-middleName'),
        nickname=_first(attrs, 'x-ucar-preferredName'),
        name_suffix=_first(attrs, 'x-ucar-nameSuffix'),
        emails=tuple(emails),
        phones=tuple(_phone(v) for v in _values(attrs, 'x-ucar-typedPhone')),
        academic_status=status,
        academic_status_code=academic_status_code(status),
        token_type=_first(attrs, 'x-ucar-authType'),
        source=_first(attrs, 'x-ucar-source'),
        positions=tuple(parse_position(p) for p in positions),
        collaborations=tuple(c for c in (parse_collaboration(x) for x in collaborations)
                             if c is not None),
        modified=_time(attrs, 'modifyTimestamp'),
        extra={k: v for k, v in extra.items() if v},
    )


def parse_organization(attrs: Attrs) -> Optional[OrganizationRecord]:
    organization_id = _int(attrs, 'x-ucar-organizationId')
    if organization_id is None:
        return None
    return OrganizationRecord(
        organization_id=organization_id,
        acronym=_first(attrs, 'ou'),
        name=_first(attrs, 'o'),
        level=_first(attrs, 'x-ucar-orgLevel'),
        level_code=_first(attrs, 'x-ucar-orgLevelCode'),
        parent_org_id=_parent_org_id(_first(attrs, 'x-ucar-parent')),
        active=_flag(attrs, 'x-ucar-active'),
        source=_first(attrs, 'x-ucar-source'),
    )


def parse_institution(attrs: Attrs) -> Optional[InstitutionRecord]:
    institution_id = _int(attrs, 'x-ucar-externalOrgId')
    if institution_id is None:
        return None
    return InstitutionRecord(
        institution_id=institution_id,
        acronym=_first(attrs, 'x-ucar-shortOrgName'),
        name=_first(attrs, 'o'),
        country=_first(attrs, 'c'),
        state=_first(attrs, 'st'),
        state_code=_first(attrs, 'x-ucar-stateProvCode'),
        city=_first(attrs, 'l'),
        street=_first(attrs, 'street'),
        postal_code=_first(attrs, 'postalCode'),
        nsf_org_code=_first(attrs, 'x-ucar-nsfOrgCode'),
        org_type=_first(attrs, 'x-ucar-externalOrgType'),
        source=_first(attrs, 'x-ucar-source'),
    )


def parse_group(attrs: Attrs) -> Optional[GroupRecord]:
    gid = _int(attrs, 'gidNumber')
    if gid is None:
        return None
    members = (_dn_id(dn, 'x-ucar-upid') for dn in _values(attrs, 'uniqueMember'))
    managers = (_dn_id(dn, 'gidNumber') for dn in _values(attrs, 'x-ucar-adminFor'))
    return GroupRecord(
        gid=gid,
        name=_first(attrs, 'x-ucar-groupName') or _first(attrs, 'cn'),
        tags=tuple(sorted(_values(attrs, 'x-ucar-tag'))),
        member_upids=tuple(sorted(m for m in members if m is not None)),
        active=_flag(attrs, 'x-ucar-active'),
        manager_gids=tuple(sorted(g for g in managers if g is not None)),
        source=_first(attrs, 'x-ucar-source'),
    )


def split_person_entries(entries: Sequence[Tuple[str, Attrs]]):
    """``(person, positions, collaborations)`` from one person's subtree, by object class."""
    person, positions, collaborations = None, [], []
    for _dn, attrs in entries:
        classes = _classes(attrs)
        if PERSON_CLASS.lower() in classes:
            person = attrs
        elif POSITION_CLASS.lower() in classes:
            positions.append(attrs)
        elif COLLABORATION_CLASS.lower() in classes:
            collaborations.append(attrs)
    return person, positions, collaborations


def _escape(value: Any) -> str:
    from ldap3.utils.conv import escape_filter_chars
    return escape_filter_chars(str(value))


class UCARDirectory(DirectoryReader):
    """Anonymous reads of the UCAR directory; one connection per call unless inside ``session()``."""

    name = 'ucar'

    def __init__(self, config: DirectoryConfig) -> None:
        self.config = config
        self._shared = None

    @classmethod
    def from_environment(cls) -> 'UCARDirectory':
        return cls(DirectoryConfig.from_environment())

    @property
    def configured(self) -> bool:
        return bool(self.config.url)

    def summary(self) -> Dict[str, Any]:
        return {'provider': self.name, **self.config.summary()}

    # --- connection ----------------------------------------------------------

    def _open(self):
        import ldap3
        timeout = max(1, int(round(self.config.timeout)))      # ldap3 packs it as an int
        server = ldap3.Server(self.config.url, connect_timeout=timeout, get_info=ldap3.NONE)
        return ldap3.Connection(server, auto_bind=ldap3.AUTO_BIND_NO_TLS, read_only=True,
                                receive_timeout=timeout, raise_exceptions=True)

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        self.guard_read()
        if self._shared is not None:
            yield self._shared
            return
        try:
            conn = self._open()
        except Exception as exc:
            raise DirectoryUnavailable(f'{type(exc).__name__}: {exc}') from exc
        try:
            yield conn
        except DirectoryUnavailable:
            raise
        except Exception as exc:
            raise DirectoryUnavailable(f'{type(exc).__name__}: {exc}') from exc
        finally:
            try:
                conn.unbind()
            except Exception:
                pass

    @contextmanager
    def session(self) -> Iterator['UCARDirectory']:
        with self._connection() as conn:
            self._shared = conn
            try:
                yield self
            finally:
                self._shared = None

    def _search(self, conn, subtree: str, flt: str, attrs: Sequence[str], *,
                scope: str = 'subtree', limit: int = 0) -> List[Tuple[str, Attrs]]:
        import ldap3
        base = f'{subtree},{self.config.base_dn}' if subtree else self.config.base_dn
        scopes = {'subtree': ldap3.SUBTREE, 'level': ldap3.LEVEL, 'base': ldap3.BASE}
        conn.search(base, flt, search_scope=scopes[scope], attributes=list(attrs),
                    size_limit=limit)
        return [(e['dn'], e['attributes']) for e in conn.response
                if e.get('type') == 'searchResEntry']

    # --- reads ----------------------------------------------------------------

    def check(self) -> Tuple[bool, str]:
        try:
            with self._connection() as conn:
                self._search(conn, '', '(objectClass=*)', ('o',), scope='base')
            return True, ''
        except DirectoryUnavailable as exc:
            return False, str(exc)

    def person(self, upid: int) -> Optional[PersonRecord]:
        with self._connection() as conn:
            return self._person_in(conn, upid)

    def _person_in(self, conn, upid: int) -> Optional[PersonRecord]:
        """One subtree search: the person and its position and collaboration children."""
        entries = self._search(conn, PEOPLE, f'(x-ucar-upid={_escape(int(upid))})', PERSON_ATTRS)
        person, positions, collaborations = split_person_entries(entries)
        return parse_person(person, positions, collaborations) if person is not None else None

    def organization(self, organization_id: int) -> Optional[OrganizationRecord]:
        with self._connection() as conn:
            entries = self._search(
                conn, ORGANIZATIONS,
                f'(&(objectClass={ORGANIZATION_CLASS})(x-ucar-organizationId={_escape(int(organization_id))}))',
                ORGANIZATION_ATTRS, scope='level', limit=1)
        return parse_organization(entries[0][1]) if entries else None

    def institution(self, institution_id: int) -> Optional[InstitutionRecord]:
        with self._connection() as conn:
            entries = self._search(
                conn, INSTITUTIONS,
                f'(&(objectClass={INSTITUTION_CLASS})(x-ucar-externalOrgId={_escape(int(institution_id))}))',
                INSTITUTION_ATTRS, scope='level', limit=1)
        return parse_institution(entries[0][1]) if entries else None

    def group(self, gid: int) -> Optional[GroupRecord]:
        with self._connection() as conn:
            entries = self._search(
                conn, GROUPS, f'(&(objectClass={GROUP_CLASS})(gidNumber={_escape(int(gid))}))',
                GROUP_ATTRS, scope='level', limit=1)
            record = parse_group(entries[0][1]) if entries else None
            if record is None:
                return None
            return replace(record, managers=self._managers(conn, record.gid))

    def _managers(self, conn, gid: int) -> Tuple[int, ...]:
        """upids of the people whose ``x-ucar-adminFor`` names the group (the person-side attribute)."""
        dn = f'gidNumber={gid},{GROUPS},{self.config.base_dn}'
        entries = self._search(conn, PEOPLE, f'(x-ucar-adminFor={_escape(dn)})',
                               ('x-ucar-upid',), scope='level')
        return tuple(sorted(u for u in (_int(a, 'x-ucar-upid') for _, a in entries) if u is not None))

    # --- the sweep ------------------------------------------------------------

    def records(self, kind: str, *, limit: int = 0) -> List[Any]:
        """Every record of ``kind`` through paged searches on one connection (the nightly sweep)."""
        plans = {
            'organization': (ORGANIZATIONS, ORGANIZATION_CLASS, ORGANIZATION_ATTRS, parse_organization),
            'institution': (INSTITUTIONS, INSTITUTION_CLASS, INSTITUTION_ATTRS, parse_institution),
            'group': (GROUPS, GROUP_CLASS, GROUP_ATTRS, parse_group),
        }
        if kind != 'person' and kind not in plans:
            raise ValueError(f'cannot sweep the directory for {kind!r}')
        with self._connection() as conn:
            if kind == 'person':
                return self._people(conn, limit)
            subtree, cls, attrs, parse = plans[kind]
            entries = self._paged(conn, subtree, f'(objectClass={cls})', attrs, limit=limit)
            records = [r for r in (parse(a) for _dn, a in entries) if r is not None]
            if kind == 'group':
                managers = self._all_managers(conn)
                records = [replace(r, managers=managers.get(r.gid, ())) for r in records]
            return records

    def _paged(self, conn, subtree: str, flt: str, attrs: Sequence[str], *,
               scope: str = 'level', limit: int = 0) -> List[Tuple[str, Attrs]]:
        """ldap3's simple paged results, 1,000 per page; ``limit`` stops after that many entries."""
        import ldap3
        scopes = {'subtree': ldap3.SUBTREE, 'level': ldap3.LEVEL}
        out = []
        for e in conn.extend.standard.paged_search(
                f'{subtree},{self.config.base_dn}', flt, search_scope=scopes[scope],
                attributes=list(attrs), paged_size=1000, generator=True):
            if e.get('type') != 'searchResEntry':
                continue
            out.append((e['dn'], e['attributes']))
            if limit and len(out) >= limit:
                break
        return out

    def _people(self, conn, limit: int) -> List[PersonRecord]:
        """Persons at one level. A limited read fetches each person's children by its own
        search; a full one pages the position and collaboration classes once and joins by upid."""
        persons = self._paged(conn, PEOPLE, f'(objectClass={PERSON_CLASS})', PERSON_ATTRS, limit=limit)
        if limit:
            upids = (_int(a, 'x-ucar-upid') for _dn, a in persons)
            records = (self._person_in(conn, u) for u in upids if u is not None)
            return [r for r in records if r is not None]
        children = {POSITION_CLASS: defaultdict(list), COLLABORATION_CLASS: defaultdict(list)}
        for cls, by_upid in children.items():
            for _dn, a in self._paged(conn, PEOPLE, f'(objectClass={cls})', PERSON_ATTRS, scope='subtree'):
                upid = _int(a, 'x-ucar-upid')
                if upid is not None:
                    by_upid[upid].append(a)
        out = []
        for _dn, a in persons:
            upid = _int(a, 'x-ucar-upid')
            record = parse_person(a, children[POSITION_CLASS].get(upid, ()),
                                  children[COLLABORATION_CLASS].get(upid, ()))
            if record is not None:
                out.append(record)
        return out

    def _all_managers(self, conn) -> Dict[int, Tuple[int, ...]]:
        """gid -> upids of the people whose ``x-ucar-adminFor`` names it: one search for the sweep."""
        managers = defaultdict(set)
        for _dn, a in self._paged(conn, PEOPLE, '(x-ucar-adminFor=*)', ('x-ucar-upid', 'x-ucar-adminFor')):
            upid = _int(a, 'x-ucar-upid')
            if upid is None:
                continue
            for dn in _values(a, 'x-ucar-adminFor'):
                gid = _dn_id(dn, 'gidNumber')
                if gid is not None:
                    managers[gid].add(upid)
        return {gid: tuple(sorted(upids)) for gid, upids in managers.items()}

    def search(self, kind: str, query: str, *, limit: int = 20) -> List[Any]:
        q = _escape(query.strip())
        if not q:
            return []
        plans = {
            'person': (PEOPLE, f'(&(objectClass={PERSON_CLASS})(|(uid={q}*)(cn=*{q}*)))',
                       PERSON_ATTRS, parse_person),
            'organization': (ORGANIZATIONS,
                             f'(&(objectClass={ORGANIZATION_CLASS})(|(ou={q}*)(o=*{q}*)))',
                             ORGANIZATION_ATTRS, parse_organization),
            'institution': (INSTITUTIONS,
                            f'(&(objectClass={INSTITUTION_CLASS})(|(x-ucar-shortOrgName={q}*)(o=*{q}*)))',
                            INSTITUTION_ATTRS, parse_institution),
            'group': (GROUPS, f'(&(objectClass={GROUP_CLASS})(|(x-ucar-groupName={q}*)(cn={q}*)))',
                      GROUP_ATTRS, parse_group),
        }
        if kind not in plans:
            raise ValueError(f'cannot search the directory for {kind!r}')
        subtree, flt, attrs, parse = plans[kind]
        with self._connection() as conn:
            entries = self._search(conn, subtree, flt, attrs, scope='level', limit=limit)
        records = (parse(attrs) for _dn, attrs in entries)
        return [r for r in records if r is not None]
