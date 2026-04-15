"""Tests for Organization / Institution / AOI / Contract / NSFProgram .update() methods.

Ported from tests/unit/test_manage_organizations.py. Same pattern as the
manage_facilities port: replace `_get_X` helpers with `any_X` fixtures,
drop post-rollback re-assertions, keep mid-test rollbacks under the
SAVEPOINT-isolated fixture.
"""
from datetime import datetime, timedelta

import pytest

from sam.projects.contracts import Contract


pytestmark = pytest.mark.unit


# ============================================================================
# Organization.update()
# ============================================================================


class TestUpdateOrganization:

    def test_update_name(self, session, any_organization):
        updated = any_organization.update(name="New Name")
        assert updated.name == "New Name"
        session.rollback()

    def test_update_acronym(self, session, any_organization):
        updated = any_organization.update(acronym="TST_X")
        assert updated.acronym == "TST_X"
        session.rollback()

    def test_update_description(self, session, any_organization):
        updated = any_organization.update(description="Test desc")
        assert updated.description == "Test desc"
        session.rollback()

    def test_clear_description(self, session, any_organization):
        updated = any_organization.update(description="")
        assert updated.description is None
        session.rollback()

    def test_empty_name_raises(self, session, any_organization):
        with pytest.raises(ValueError, match="name is required"):
            any_organization.update(name="")
        session.rollback()

    def test_empty_acronym_raises(self, session, any_organization):
        with pytest.raises(ValueError, match="acronym is required"):
            any_organization.update(acronym="")
        session.rollback()

    def test_toggle_active(self, session, any_organization):
        original = any_organization.active
        updated = any_organization.update(active=not original)
        assert updated.active == (not original)
        session.rollback()

    def test_tree_columns_untouched(self, session, any_organization):
        """update() never modifies tree columns — confirm they stay untouched."""
        orig_left = any_organization.tree_left
        orig_right = any_organization.tree_right
        orig_parent = any_organization.parent_org_id
        any_organization.update(description="safe edit")
        assert any_organization.tree_left == orig_left
        assert any_organization.tree_right == orig_right
        assert any_organization.parent_org_id == orig_parent
        session.rollback()

    def test_no_op_when_no_kwargs(self, session, any_organization):
        original = any_organization.name
        any_organization.update()
        assert any_organization.name == original
        session.rollback()


# ============================================================================
# Institution.update()
# ============================================================================


class TestUpdateInstitution:

    def test_update_name(self, session, any_institution):
        updated = any_institution.update(name="New Inst Name")
        assert updated.name == "New Inst Name"
        session.rollback()

    def test_update_acronym(self, session, any_institution):
        updated = any_institution.update(acronym="NEWACRONYM")
        assert updated.acronym == "NEWACRONYM"
        session.rollback()

    def test_empty_name_raises(self, session, any_institution):
        with pytest.raises(ValueError, match="name is required"):
            any_institution.update(name="")
        session.rollback()

    def test_empty_acronym_raises(self, session, any_institution):
        with pytest.raises(ValueError, match="acronym is required"):
            any_institution.update(acronym="  ")
        session.rollback()

    def test_update_city_clears_to_none(self, session, any_institution):
        updated = any_institution.update(city="")
        assert updated.city is None
        session.rollback()

    def test_no_op_when_no_kwargs(self, session, any_institution):
        original = any_institution.name
        any_institution.update()
        assert any_institution.name == original
        session.rollback()


# ============================================================================
# AreaOfInterestGroup.update()
# ============================================================================


class TestUpdateAOIGroup:

    def test_update_name(self, session, any_aoi_group):
        updated = any_aoi_group.update(name="New Group")
        assert updated.name == "New Group"
        session.rollback()

    def test_empty_name_raises(self, session, any_aoi_group):
        with pytest.raises(ValueError, match="name is required"):
            any_aoi_group.update(name="")
        session.rollback()

    def test_toggle_active(self, session, any_aoi_group):
        original = any_aoi_group.active
        updated = any_aoi_group.update(active=not original)
        assert updated.active == (not original)
        session.rollback()

    def test_no_op_when_no_kwargs(self, session, any_aoi_group):
        original = any_aoi_group.name
        any_aoi_group.update()
        assert any_aoi_group.name == original
        session.rollback()


# ============================================================================
# AreaOfInterest.update()
# ============================================================================


class TestUpdateAreaOfInterest:

    def test_update_name(self, session, any_aoi):
        updated = any_aoi.update(area_of_interest="New AOI")
        assert updated.area_of_interest == "New AOI"
        session.rollback()

    def test_empty_name_raises(self, session, any_aoi):
        with pytest.raises(ValueError, match="area_of_interest name is required"):
            any_aoi.update(area_of_interest="")
        session.rollback()

    def test_invalid_group_raises(self, session, any_aoi):
        with pytest.raises(ValueError, match="not found"):
            any_aoi.update(area_of_interest_group_id=999_999_999)
        session.rollback()

    def test_toggle_active(self, session, any_aoi):
        original = any_aoi.active
        updated = any_aoi.update(active=not original)
        assert updated.active == (not original)
        session.rollback()

    def test_no_op_when_no_kwargs(self, session, any_aoi):
        original = any_aoi.area_of_interest
        any_aoi.update()
        assert any_aoi.area_of_interest == original
        session.rollback()


# ============================================================================
# ContractSource.update()
# ============================================================================


class TestUpdateContractSource:

    def test_update_name(self, session, any_contract_source):
        updated = any_contract_source.update(contract_source="NewSource")
        assert updated.contract_source == "NewSource"
        session.rollback()

    def test_empty_name_raises(self, session, any_contract_source):
        with pytest.raises(ValueError, match="contract_source name is required"):
            any_contract_source.update(contract_source="")
        session.rollback()

    def test_toggle_active(self, session, any_contract_source):
        original = any_contract_source.active
        updated = any_contract_source.update(active=not original)
        assert updated.active == (not original)
        session.rollback()

    def test_no_op_when_no_kwargs(self, session, any_contract_source):
        original = any_contract_source.contract_source
        any_contract_source.update()
        assert any_contract_source.contract_source == original
        session.rollback()


# ============================================================================
# Contract.update()
# ============================================================================


class TestUpdateContract:

    def test_update_title(self, session, any_contract):
        updated = any_contract.update(title="New contract title")
        assert updated.title == "New contract title"
        session.rollback()

    def test_empty_title_raises(self, session, any_contract):
        with pytest.raises(ValueError, match="title is required"):
            any_contract.update(title="")
        session.rollback()

    def test_update_url(self, session, any_contract):
        updated = any_contract.update(url="https://example.com/contract")
        assert updated.url == "https://example.com/contract"
        session.rollback()

    def test_clear_url(self, session, any_contract):
        updated = any_contract.update(url="")
        assert updated.url is None
        session.rollback()

    def test_update_start_date(self, session, any_contract):
        new_start = datetime(2020, 1, 1)
        updated = any_contract.update(start_date=new_start)
        assert updated.start_date == new_start
        session.rollback()

    def test_update_end_date(self, session, any_contract):
        future_end = datetime.now() + timedelta(days=3650)
        updated = any_contract.update(end_date=future_end)
        assert updated.end_date == future_end
        session.rollback()

    def test_end_before_start_raises(self, session):
        """Needs a contract with an existing start_date set."""
        c = session.query(Contract).filter(Contract.start_date.isnot(None)).first()
        if not c:
            pytest.skip("No contracts with a start_date in database")
        bad_end = c.start_date - timedelta(days=1)
        with pytest.raises(ValueError, match="end_date must be after start_date"):
            c.update(end_date=bad_end)
        session.rollback()

    def test_no_op_when_no_kwargs(self, session, any_contract):
        original = any_contract.title
        any_contract.update()
        assert any_contract.title == original
        session.rollback()


# ============================================================================
# NSFProgram.update()
# ============================================================================


class TestUpdateNSFProgram:

    def test_update_name(self, session, any_nsf_program):
        updated = any_nsf_program.update(nsf_program_name="New Program")
        assert updated.nsf_program_name == "New Program"
        session.rollback()

    def test_empty_name_raises(self, session, any_nsf_program):
        with pytest.raises(ValueError, match="nsf_program_name is required"):
            any_nsf_program.update(nsf_program_name="")
        session.rollback()

    def test_toggle_active(self, session, any_nsf_program):
        original = any_nsf_program.active
        updated = any_nsf_program.update(active=not original)
        assert updated.active == (not original)
        session.rollback()

    def test_no_op_when_no_kwargs(self, session, any_nsf_program):
        original = any_nsf_program.nsf_program_name
        any_nsf_program.update()
        assert any_nsf_program.nsf_program_name == original
        session.rollback()
