"""
Tests for organization management functions:
  update_organization, update_institution, update_area_of_interest_group,
  update_area_of_interest, update_contract_source, update_contract,
  update_nsf_program
"""

import pytest
from datetime import datetime, timedelta

from sam.core.organizations import Organization, Institution
from sam.projects.areas import AreaOfInterest, AreaOfInterestGroup
from sam.projects.contracts import Contract, ContractSource, NSFProgram
from sam.manage.organizations import (
    update_organization,
    update_institution,
    update_area_of_interest_group,
    update_area_of_interest,
    update_contract_source,
    update_contract,
    update_nsf_program,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_org(session) -> Organization:
    o = session.query(Organization).first()
    if not o:
        pytest.skip("No organizations in database")
    return o


def _get_institution(session) -> Institution:
    i = session.query(Institution).first()
    if not i:
        pytest.skip("No institutions in database")
    return i


def _get_aoi_group(session) -> AreaOfInterestGroup:
    g = session.query(AreaOfInterestGroup).first()
    if not g:
        pytest.skip("No AOI groups in database")
    return g


def _get_aoi(session) -> AreaOfInterest:
    a = session.query(AreaOfInterest).first()
    if not a:
        pytest.skip("No areas of interest in database")
    return a


def _get_contract_source(session) -> ContractSource:
    cs = session.query(ContractSource).first()
    if not cs:
        pytest.skip("No contract sources in database")
    return cs


def _get_contract(session) -> Contract:
    c = session.query(Contract).first()
    if not c:
        pytest.skip("No contracts in database")
    return c


def _get_nsf_program(session) -> NSFProgram:
    p = session.query(NSFProgram).first()
    if not p:
        pytest.skip("No NSF programs in database")
    return p


# ---------------------------------------------------------------------------
# update_organization
# ---------------------------------------------------------------------------

class TestUpdateOrganization:

    def test_update_name(self, session):
        """update_organization sets a new name."""
        o = _get_org(session)
        original = o.name

        updated = update_organization(session, o.organization_id, name="New Name")

        assert updated.name == "New Name"
        session.rollback()
        assert o.name == original

    def test_update_acronym(self, session):
        """update_organization sets a new acronym."""
        o = _get_org(session)
        original = o.acronym

        # Use a unique-ish value to avoid constraint conflicts
        updated = update_organization(session, o.organization_id, acronym="TST_X")

        assert updated.acronym == "TST_X"
        session.rollback()
        assert o.acronym == original

    def test_update_description(self, session):
        """update_organization sets a new description."""
        o = _get_org(session)

        updated = update_organization(session, o.organization_id, description="Test desc")

        assert updated.description == "Test desc"
        session.rollback()

    def test_clear_description(self, session):
        """Passing empty string clears description to None."""
        o = _get_org(session)

        updated = update_organization(session, o.organization_id, description="")

        assert updated.description is None
        session.rollback()

    def test_empty_name_raises(self, session):
        """update_organization raises ValueError for empty name."""
        o = _get_org(session)

        with pytest.raises(ValueError, match="name is required"):
            update_organization(session, o.organization_id, name="")
        session.rollback()

    def test_empty_acronym_raises(self, session):
        """update_organization raises ValueError for empty acronym."""
        o = _get_org(session)

        with pytest.raises(ValueError, match="acronym is required"):
            update_organization(session, o.organization_id, acronym="")
        session.rollback()

    def test_toggle_active(self, session):
        """update_organization can toggle the active flag."""
        o = _get_org(session)
        original = o.active

        updated = update_organization(session, o.organization_id, active=not original)

        assert updated.active == (not original)
        session.rollback()

    def test_tree_columns_untouched(self, session):
        """update_organization never modifies tree columns."""
        o = _get_org(session)
        orig_left = o.tree_left
        orig_right = o.tree_right
        orig_parent = o.parent_org_id

        update_organization(session, o.organization_id, description="safe edit")

        assert o.tree_left == orig_left
        assert o.tree_right == orig_right
        assert o.parent_org_id == orig_parent
        session.rollback()

    def test_no_op_when_no_kwargs(self, session):
        """Calling update_organization with no kwargs is a no-op."""
        o = _get_org(session)
        original = o.name

        update_organization(session, o.organization_id)

        assert o.name == original
        session.rollback()

    def test_not_found_raises(self, session):
        """update_organization raises ValueError for a nonexistent ID."""
        with pytest.raises(ValueError, match="not found"):
            update_organization(session, org_id=999999999, name="x")


# ---------------------------------------------------------------------------
# update_institution
# ---------------------------------------------------------------------------

class TestUpdateInstitution:

    def test_update_name(self, session):
        """update_institution sets a new name."""
        i = _get_institution(session)
        original = i.name

        updated = update_institution(session, i.institution_id, name="New Inst Name")

        assert updated.name == "New Inst Name"
        session.rollback()
        assert i.name == original

    def test_update_acronym(self, session):
        """update_institution sets a new acronym."""
        i = _get_institution(session)

        updated = update_institution(session, i.institution_id, acronym="NEWACRONYM")

        assert updated.acronym == "NEWACRONYM"
        session.rollback()

    def test_empty_name_raises(self, session):
        """update_institution raises ValueError for empty name."""
        i = _get_institution(session)

        with pytest.raises(ValueError, match="name is required"):
            update_institution(session, i.institution_id, name="")
        session.rollback()

    def test_empty_acronym_raises(self, session):
        """update_institution raises ValueError for empty acronym."""
        i = _get_institution(session)

        with pytest.raises(ValueError, match="acronym is required"):
            update_institution(session, i.institution_id, acronym="  ")
        session.rollback()

    def test_update_city_clears_to_none(self, session):
        """Passing empty string for city clears it to None."""
        i = _get_institution(session)

        updated = update_institution(session, i.institution_id, city="")

        assert updated.city is None
        session.rollback()

    def test_no_op_when_no_kwargs(self, session):
        """Calling update_institution with no kwargs is a no-op."""
        i = _get_institution(session)
        original = i.name

        update_institution(session, i.institution_id)

        assert i.name == original
        session.rollback()

    def test_not_found_raises(self, session):
        """update_institution raises ValueError for a nonexistent ID."""
        with pytest.raises(ValueError, match="not found"):
            update_institution(session, inst_id=999999999, name="x")


# ---------------------------------------------------------------------------
# update_area_of_interest_group
# ---------------------------------------------------------------------------

class TestUpdateAOIGroup:

    def test_update_name(self, session):
        """update_area_of_interest_group sets a new name."""
        g = _get_aoi_group(session)
        original = g.name

        updated = update_area_of_interest_group(session, g.area_of_interest_group_id, name="New Group")

        assert updated.name == "New Group"
        session.rollback()
        assert g.name == original

    def test_empty_name_raises(self, session):
        """update_area_of_interest_group raises ValueError for empty name."""
        g = _get_aoi_group(session)

        with pytest.raises(ValueError, match="name is required"):
            update_area_of_interest_group(session, g.area_of_interest_group_id, name="")
        session.rollback()

    def test_toggle_active(self, session):
        """update_area_of_interest_group can toggle active."""
        g = _get_aoi_group(session)
        original = g.active

        updated = update_area_of_interest_group(
            session, g.area_of_interest_group_id, active=not original
        )

        assert updated.active == (not original)
        session.rollback()

    def test_no_op_when_no_kwargs(self, session):
        """Calling update_area_of_interest_group with no kwargs is a no-op."""
        g = _get_aoi_group(session)
        original = g.name

        update_area_of_interest_group(session, g.area_of_interest_group_id)

        assert g.name == original
        session.rollback()

    def test_not_found_raises(self, session):
        """update_area_of_interest_group raises ValueError for a nonexistent ID."""
        with pytest.raises(ValueError, match="not found"):
            update_area_of_interest_group(session, group_id=999999999, name="x")


# ---------------------------------------------------------------------------
# update_area_of_interest
# ---------------------------------------------------------------------------

class TestUpdateAreaOfInterest:

    def test_update_name(self, session):
        """update_area_of_interest sets a new name."""
        a = _get_aoi(session)
        original = a.area_of_interest

        updated = update_area_of_interest(session, a.area_of_interest_id, area_of_interest="New AOI")

        assert updated.area_of_interest == "New AOI"
        session.rollback()
        assert a.area_of_interest == original

    def test_empty_name_raises(self, session):
        """update_area_of_interest raises ValueError for empty name."""
        a = _get_aoi(session)

        with pytest.raises(ValueError, match="area_of_interest name is required"):
            update_area_of_interest(session, a.area_of_interest_id, area_of_interest="")
        session.rollback()

    def test_invalid_group_raises(self, session):
        """update_area_of_interest raises ValueError for nonexistent group."""
        a = _get_aoi(session)

        with pytest.raises(ValueError, match="not found"):
            update_area_of_interest(
                session, a.area_of_interest_id, area_of_interest_group_id=999999999
            )
        session.rollback()

    def test_toggle_active(self, session):
        """update_area_of_interest can toggle active."""
        a = _get_aoi(session)
        original = a.active

        updated = update_area_of_interest(session, a.area_of_interest_id, active=not original)

        assert updated.active == (not original)
        session.rollback()

    def test_no_op_when_no_kwargs(self, session):
        """Calling update_area_of_interest with no kwargs is a no-op."""
        a = _get_aoi(session)
        original = a.area_of_interest

        update_area_of_interest(session, a.area_of_interest_id)

        assert a.area_of_interest == original
        session.rollback()

    def test_not_found_raises(self, session):
        """update_area_of_interest raises ValueError for a nonexistent ID."""
        with pytest.raises(ValueError, match="not found"):
            update_area_of_interest(session, aoi_id=999999999, area_of_interest="x")


# ---------------------------------------------------------------------------
# update_contract_source
# ---------------------------------------------------------------------------

class TestUpdateContractSource:

    def test_update_name(self, session):
        """update_contract_source sets a new name."""
        cs = _get_contract_source(session)
        original = cs.contract_source

        updated = update_contract_source(session, cs.contract_source_id, contract_source="NewSource")

        assert updated.contract_source == "NewSource"
        session.rollback()
        assert cs.contract_source == original

    def test_empty_name_raises(self, session):
        """update_contract_source raises ValueError for empty name."""
        cs = _get_contract_source(session)

        with pytest.raises(ValueError, match="contract_source name is required"):
            update_contract_source(session, cs.contract_source_id, contract_source="")
        session.rollback()

    def test_toggle_active(self, session):
        """update_contract_source can toggle active."""
        cs = _get_contract_source(session)
        original = cs.active

        updated = update_contract_source(session, cs.contract_source_id, active=not original)

        assert updated.active == (not original)
        session.rollback()

    def test_no_op_when_no_kwargs(self, session):
        """Calling update_contract_source with no kwargs is a no-op."""
        cs = _get_contract_source(session)
        original = cs.contract_source

        update_contract_source(session, cs.contract_source_id)

        assert cs.contract_source == original
        session.rollback()

    def test_not_found_raises(self, session):
        """update_contract_source raises ValueError for a nonexistent ID."""
        with pytest.raises(ValueError, match="not found"):
            update_contract_source(session, source_id=999999999, contract_source="x")


# ---------------------------------------------------------------------------
# update_contract
# ---------------------------------------------------------------------------

class TestUpdateContract:

    def test_update_title(self, session):
        """update_contract sets a new title."""
        c = _get_contract(session)
        original = c.title

        updated = update_contract(session, c.contract_id, title="New contract title")

        assert updated.title == "New contract title"
        session.rollback()
        assert c.title == original

    def test_empty_title_raises(self, session):
        """update_contract raises ValueError for empty title."""
        c = _get_contract(session)

        with pytest.raises(ValueError, match="title is required"):
            update_contract(session, c.contract_id, title="")
        session.rollback()

    def test_update_url(self, session):
        """update_contract sets a new URL."""
        c = _get_contract(session)

        updated = update_contract(session, c.contract_id, url="https://example.com/contract")

        assert updated.url == "https://example.com/contract"
        session.rollback()

    def test_clear_url(self, session):
        """Passing empty string for url clears it to None."""
        c = _get_contract(session)

        updated = update_contract(session, c.contract_id, url="")

        assert updated.url is None
        session.rollback()

    def test_update_start_date(self, session):
        """update_contract sets a new start_date."""
        c = _get_contract(session)
        new_start = datetime(2020, 1, 1)

        updated = update_contract(session, c.contract_id, start_date=new_start)

        assert updated.start_date == new_start
        session.rollback()

    def test_update_end_date(self, session):
        """update_contract sets a valid end_date after start_date."""
        c = _get_contract(session)
        future_end = datetime.now() + timedelta(days=3650)

        updated = update_contract(session, c.contract_id, end_date=future_end)

        assert updated.end_date == future_end
        session.rollback()

    def test_end_before_start_raises(self, session):
        """update_contract raises ValueError when end_date <= start_date."""
        c = session.query(Contract).filter(Contract.start_date.isnot(None)).first()
        if not c:
            pytest.skip("No contracts with a start_date in database")

        bad_end = c.start_date - timedelta(days=1)

        with pytest.raises(ValueError, match="end_date must be after start_date"):
            update_contract(session, c.contract_id, end_date=bad_end)
        session.rollback()

    def test_no_op_when_no_kwargs(self, session):
        """Calling update_contract with no kwargs is a no-op."""
        c = _get_contract(session)
        original = c.title

        update_contract(session, c.contract_id)

        assert c.title == original
        session.rollback()

    def test_not_found_raises(self, session):
        """update_contract raises ValueError for a nonexistent ID."""
        with pytest.raises(ValueError, match="not found"):
            update_contract(session, contract_id=999999999, title="x")


# ---------------------------------------------------------------------------
# update_nsf_program
# ---------------------------------------------------------------------------

class TestUpdateNSFProgram:

    def test_update_name(self, session):
        """update_nsf_program sets a new name."""
        p = _get_nsf_program(session)
        original = p.nsf_program_name

        updated = update_nsf_program(session, p.nsf_program_id, nsf_program_name="New Program")

        assert updated.nsf_program_name == "New Program"
        session.rollback()
        assert p.nsf_program_name == original

    def test_empty_name_raises(self, session):
        """update_nsf_program raises ValueError for empty name."""
        p = _get_nsf_program(session)

        with pytest.raises(ValueError, match="nsf_program_name is required"):
            update_nsf_program(session, p.nsf_program_id, nsf_program_name="")
        session.rollback()

    def test_toggle_active(self, session):
        """update_nsf_program can toggle active."""
        p = _get_nsf_program(session)
        original = p.active

        updated = update_nsf_program(session, p.nsf_program_id, active=not original)

        assert updated.active == (not original)
        session.rollback()

    def test_no_op_when_no_kwargs(self, session):
        """Calling update_nsf_program with no kwargs is a no-op."""
        p = _get_nsf_program(session)
        original = p.nsf_program_name

        update_nsf_program(session, p.nsf_program_id)

        assert p.nsf_program_name == original
        session.rollback()

    def test_not_found_raises(self, session):
        """update_nsf_program raises ValueError for a nonexistent ID."""
        with pytest.raises(ValueError, match="not found"):
            update_nsf_program(session, nsf_program_id=999999999, nsf_program_name="x")
