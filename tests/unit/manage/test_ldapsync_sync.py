"""Institution, organization and GID-block upserts from ``sam-ldap-syncd`` (``sam.manage.ldapsync``)."""

import os

import pytest

from factories import make_gid_allocation, make_institution, make_organization, next_int
from sam.core.groups import GidAllocation
from sam.core.organizations import Institution, Organization
from sam.manage.ldapsync import (
    SyncValidationError,
    sync_gid_allocation,
    sync_institution,
    sync_organization,
)
from sam.schemas.forms.ldapsync import (
    GidAllocationSyncInput,
    InstitutionSyncInput,
    OrganizationSyncInput,
)

_WORKER = int(os.environ.get('PYTEST_XDIST_WORKER', 'gw0').removeprefix('gw') or '0')


def _new_id():
    """A worker-namespaced id no snapshot or factory row uses (the PKs are IdM-assigned)."""
    return 30_000_000 + _WORKER * 100_000 + next_int('ldapsync_id')


def _institution(**overrides):
    payload = {'institutionId': _new_id(), 'name': 'EXAMPLE STATE UNIVERSITY',
               'acronym': 'ESU', 'institutionType': 'University', 'country': 'US',
               'state': 'CO', 'city': 'Boulder', 'zip': '80301', 'deleted': False}
    payload.update(overrides)
    return InstitutionSyncInput().load(payload)


class TestInstitution:

    def test_insert_takes_the_assigned_id_and_resolves_lookups(self, session):
        data = _institution()
        assert sync_institution(session, data) == data['institution_id']
        inst = session.get(Institution, data['institution_id'])
        assert inst.acronym == 'ESU'
        assert inst.institution_type.type == 'University'
        assert inst.state_prov.code == 'CO' and inst.state_prov.country.code == 'US'

    def test_update_overwrites_every_synced_column(self, session):
        inst = make_institution(session)
        sync_institution(session, _institution(institutionId=inst.institution_id,
                                               acronym='NEW', city=None, deleted=True))
        assert (inst.acronym, inst.city, inst.deleted) == ('NEW', None, True)

    def test_state_equal_to_country_is_no_state(self, session):
        """Legacy logged (and mailed) an ERROR for each of these; it is simply no state."""
        data = _institution(state='US')
        sync_institution(session, data)
        assert session.get(Institution, data['institution_id']).state_prov_id is None

    def test_state_by_name_when_code_misses(self, session):
        data = _institution(state='Colorado')
        sync_institution(session, data)
        assert session.get(Institution, data['institution_id']).state_prov.code == 'CO'

    def test_unknown_type_is_a_400_not_a_500(self, session):
        with pytest.raises(SyncValidationError, match='Could not find institution type Moon Base.'):
            sync_institution(session, _institution(institutionType='Moon Base'))

    def test_over_long_acronym_is_rejected_before_the_database(self, session):
        with pytest.raises(SyncValidationError, match='acronym is longer than 40'):
            sync_institution(session, _institution(acronym='X' * 41))

    def test_id_is_required(self, session):
        with pytest.raises(SyncValidationError, match='Institution id must be specified.'):
            sync_institution(session, _institution(institutionId=None))


def _organization(**overrides):
    payload = {'organizationId': _new_id(), 'name': 'Test Lab', 'acronym': 'TLAB',
               'active': True, 'deleted': False, 'level': 'Lab', 'levelCode': 'L'}
    payload.update(overrides)
    return OrganizationSyncInput().load(payload)


class TestOrganization:

    def test_insert_and_parent_by_active_acronym(self, session):
        parent = make_organization(session)
        data = _organization(parentOrgAcronym=parent.acronym.lower())
        sync_organization(session, data)
        org = session.get(Organization, data['organization_id'])
        assert org.parent_org_id == parent.organization_id
        assert (org.level, org.level_code) == ('Lab', 'L')

    def test_unknown_parent_is_no_parent(self, session):
        data = _organization(parentOrgAcronym='NOSUCHORG')
        sync_organization(session, data)
        assert session.get(Organization, data['organization_id']).parent_org_id is None

    def test_absent_description_is_left_alone(self, session):
        """The daemon strips description; legacy then erased SAM's text on every PUT."""
        org = make_organization(session)
        org.description = 'kept'
        session.flush()
        sync_organization(session, _organization(organizationId=org.organization_id,
                                                 acronym=org.acronym, name=org.name))
        assert org.description == 'kept'

    def test_null_active_is_a_400(self, session):
        with pytest.raises(SyncValidationError, match='active must be specified'):
            sync_organization(session, _organization(active=None))


class TestGidAllocation:

    def test_identical_block_is_a_noop(self, session):
        block = make_gid_allocation(session)
        data = GidAllocationSyncInput().load({'startGid': block.start_gid,
                                              'endGid': block.end_gid})
        before = session.query(GidAllocation).count()
        assert sync_gid_allocation(session, data) == block.start_gid
        assert session.query(GidAllocation).count() == before

    @pytest.mark.parametrize('offset', [(-5, 5), (5, 10), (-5, 2000)])
    def test_overlap_including_containment_is_rejected(self, session, offset):
        """Legacy missed a new range that strictly contains an existing one."""
        block = make_gid_allocation(session)
        lo, hi = offset
        data = GidAllocationSyncInput().load({'startGid': block.start_gid + lo,
                                              'endGid': block.start_gid + hi})
        with pytest.raises(SyncValidationError, match='overlaps existing allocation.'):
            sync_gid_allocation(session, data)

    def test_new_block_starts_pristine(self, session):
        block = make_gid_allocation(session)
        start = block.end_gid + 10
        sync_gid_allocation(session, GidAllocationSyncInput().load(
            {'startGid': start, 'endGid': start + 99}))
        new = session.query(GidAllocation).filter(GidAllocation.start_gid == start).one()
        assert new.next_gid == start
