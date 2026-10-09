"""Write side of the LDAP sync API: upserts pushed by ``sam-ldap-syncd``.

SAM never originates identities; this module mirrors them. Every rule, and each
deliberate deviation from legacy Java SAM, is in ``docs/plans/LDAP_SYNC_API.md``.
"""

import logging

from sqlalchemy import func

from sam.core.groups import GidAllocation
from sam.core.organizations import Institution, InstitutionType, Organization
from sam.geography import Country, StateProv

logger = logging.getLogger(__name__)


class SyncValidationError(ValueError):
    """A payload SAM rejects; the API answers 400 with legacy's message text."""

    def __init__(self, *messages: str):
        super().__init__('; '.join(messages))
        self.messages = list(messages)


# ---------------------------------------------------------------------------
# shared checks
# ---------------------------------------------------------------------------

def _blank(value) -> bool:
    return value is None or not str(value).strip()


def _check_widths(kind: str, data: dict, widths: dict) -> None:
    """Over-long values as one 400 (legacy let the database reject them with a 500)."""
    errors = [f'{kind} {name} is longer than {width} characters.'
              for name, width in widths.items()
              if data.get(name) is not None and len(str(data[name])) > width]
    if errors:
        raise SyncValidationError(*errors)


def _require(data: dict, *names_and_messages) -> None:
    errors = [msg for name, msg in names_and_messages if _blank(data.get(name))]
    if errors:
        raise SyncValidationError(*errors)


# ---------------------------------------------------------------------------
# institution
# ---------------------------------------------------------------------------

INSTITUTION_WIDTHS = {'name': 128, 'acronym': 40, 'nsf_org_code': 200, 'address': 255,
                      'city': 30, 'zip': 15}


def _institution_type_id(session, type_name):
    if _blank(type_name):
        return None
    row = (session.query(InstitutionType)
           .filter(func.lower(InstitutionType.type) == type_name.strip().lower()).first())
    if row is None:
        raise SyncValidationError(f'Could not find institution type {type_name}.')
    return row.institution_type_id


def _state_prov_id(session, country, state, acronym):
    """By country + state code, then by name; a state equal to the country code is no state."""
    if _blank(country) or _blank(state) or state.strip().upper() == country.strip().upper():
        return None
    base = (session.query(StateProv.ext_state_prov_id).join(Country)
            .filter(func.upper(Country.code) == country.strip().upper())
            .order_by(StateProv.ext_state_prov_id))
    for column in (StateProv.code, StateProv.name):
        found = base.filter(func.upper(column) == state.strip().upper()).first()
        if found is not None:
            return found[0]
    logger.warning('ldapsync: no state_prov for %s, %s (institution=%s)', country, state, acronym)
    return None


def sync_institution(session, data: dict) -> int:
    """Upsert one institution by its IdM id; returns the id."""
    _require(data, ('institution_id', 'Institution id must be specified.'),
             ('acronym', 'Institution acronym must be specified.'))
    _check_widths('Institution', data, INSTITUTION_WIDTHS)
    Institution.upsert_from_sync(
        session, institution_id=data['institution_id'],
        name=data['name'], acronym=data['acronym'], nsf_org_code=data['nsf_org_code'],
        address=data['address'], city=data['city'], zip=data['zip'],
        deleted=data['deleted'],
        institution_type_id=_institution_type_id(session, data['institution_type']),
        state_prov_id=_state_prov_id(session, data['country'], data['state'], data['acronym']),
    )
    return data['institution_id']


# ---------------------------------------------------------------------------
# organization
# ---------------------------------------------------------------------------

ORGANIZATION_WIDTHS = {'name': 100, 'acronym': 15, 'description': 255, 'level': 80,
                       'level_code': 10, 'idms_unique_name': 64}


def _parent_org_id(session, acronym, own_id):
    """The active organization with this acronym (case-insensitive), else None."""
    if _blank(acronym):
        return None
    parent = (session.query(Organization)
              .filter(Organization.is_active,
                      func.lower(Organization.acronym) == acronym.strip().lower())
              .order_by(Organization.organization_id).first())
    if parent is None or parent.organization_id == own_id:
        return None
    return parent.organization_id


def sync_organization(session, data: dict) -> int:
    """Upsert one organization by its IdM id; returns the id."""
    _require(data, ('organization_id', 'Organization id must be specified.'),
             ('name', 'Organization name must be specified.'),
             ('acronym', 'Organization acronym must be specified.'))
    if data.get('active') is None:
        raise SyncValidationError('Organization active must be specified.')
    _check_widths('Organization', data, ORGANIZATION_WIDTHS)
    oid = data['organization_id']
    fields = {name: data[name] for name in ('name', 'acronym', 'active', 'tree_left',
                                            'tree_right', 'level', 'level_code',
                                            'idms_unique_name', 'deleted')}
    if 'description' in data:
        fields['description'] = data['description']
    fields['parent_org_id'] = _parent_org_id(session, data['parent_org_acronym'], oid)
    Organization.upsert_from_sync(session, organization_id=oid, **fields)
    return oid


# ---------------------------------------------------------------------------
# gidAllocation
# ---------------------------------------------------------------------------

def sync_gid_allocation(session, data: dict) -> int:
    """Register a GID block; the identical block is a no-op. Returns startGid."""
    start, end = data.get('start_gid'), data.get('end_gid')
    if start is None or end is None:
        raise SyncValidationError('Gid range must give startGid and endGid.')
    try:
        GidAllocation.create_block(session, start, end)
    except ValueError as exc:
        raise SyncValidationError(str(exc)) from exc
    return start
