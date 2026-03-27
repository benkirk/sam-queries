"""
Tests for Organization, Institution, AreaOfInterestGroup, AreaOfInterest,
ContractSource, Contract, and NSFProgram ORM update() methods.
"""

import pytest
from datetime import datetime, timedelta

from sam.core.organizations import Organization, Institution
from sam.projects.areas import AreaOfInterest, AreaOfInterestGroup
from sam.projects.contracts import Contract, ContractSource, NSFProgram


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
# Organization.update()
# ---------------------------------------------------------------------------

class TestUpdateOrganization:

    def test_update_name(self, session):
        """update() sets a new name."""
        o = _get_org(session)
        original = o.name

        updated = o.update(name="New Name")

        assert updated.name == "New Name"
        session.rollback()
        assert o.name == original

    def test_update_acronym(self, session):
        """update() sets a new acronym."""
        o = _get_org(session)
        original = o.acronym

        updated = o.update(acronym="TST_X")

        assert updated.acronym == "TST_X"
        session.rollback()
        assert o.acronym == original

    def test_update_description(self, session):
        """update() sets a new description."""
        o = _get_org(session)

        updated = o.update(description="Test desc")

        assert updated.description == "Test desc"
        session.rollback()

    def test_clear_description(self, session):
        """Passing empty string clears description to None."""
        o = _get_org(session)

        updated = o.update(description="")

        assert updated.description is None
        session.rollback()

    def test_empty_name_raises(self, session):
        """update() raises ValueError for empty name."""
        o = _get_org(session)

        with pytest.raises(ValueError, match="name is required"):
            o.update(name="")
        session.rollback()

    def test_empty_acronym_raises(self, session):
        """update() raises ValueError for empty acronym."""
        o = _get_org(session)

        with pytest.raises(ValueError, match="acronym is required"):
            o.update(acronym="")
        session.rollback()

    def test_toggle_active(self, session):
        """update() can toggle the active flag."""
        o = _get_org(session)
        original = o.active

        updated = o.update(active=not original)

        assert updated.active == (not original)
        session.rollback()

    def test_tree_columns_untouched(self, session):
        """update() never modifies tree columns."""
        o = _get_org(session)
        orig_left = o.tree_left
        orig_right = o.tree_right
        orig_parent = o.parent_org_id

        o.update(description="safe edit")

        assert o.tree_left == orig_left
        assert o.tree_right == orig_right
        assert o.parent_org_id == orig_parent
        session.rollback()

    def test_no_op_when_no_kwargs(self, session):
        """Calling update() with no kwargs is a no-op."""
        o = _get_org(session)
        original = o.name

        o.update()

        assert o.name == original
        session.rollback()


# ---------------------------------------------------------------------------
# Institution.update()
# ---------------------------------------------------------------------------

class TestUpdateInstitution:

    def test_update_name(self, session):
        """update() sets a new name."""
        i = _get_institution(session)
        original = i.name

        updated = i.update(name="New Inst Name")

        assert updated.name == "New Inst Name"
        session.rollback()
        assert i.name == original

    def test_update_acronym(self, session):
        """update() sets a new acronym."""
        i = _get_institution(session)

        updated = i.update(acronym="NEWACRONYM")

        assert updated.acronym == "NEWACRONYM"
        session.rollback()

    def test_empty_name_raises(self, session):
        """update() raises ValueError for empty name."""
        i = _get_institution(session)

        with pytest.raises(ValueError, match="name is required"):
            i.update(name="")
        session.rollback()

    def test_empty_acronym_raises(self, session):
        """update() raises ValueError for empty acronym."""
        i = _get_institution(session)

        with pytest.raises(ValueError, match="acronym is required"):
            i.update(acronym="  ")
        session.rollback()

    def test_update_city_clears_to_none(self, session):
        """Passing empty string for city clears it to None."""
        i = _get_institution(session)

        updated = i.update(city="")

        assert updated.city is None
        session.rollback()

    def test_no_op_when_no_kwargs(self, session):
        """Calling update() with no kwargs is a no-op."""
        i = _get_institution(session)
        original = i.name

        i.update()

        assert i.name == original
        session.rollback()


# ---------------------------------------------------------------------------
# AreaOfInterestGroup.update()
# ---------------------------------------------------------------------------

class TestUpdateAOIGroup:

    def test_update_name(self, session):
        """update() sets a new name."""
        g = _get_aoi_group(session)
        original = g.name

        updated = g.update(name="New Group")

        assert updated.name == "New Group"
        session.rollback()
        assert g.name == original

    def test_empty_name_raises(self, session):
        """update() raises ValueError for empty name."""
        g = _get_aoi_group(session)

        with pytest.raises(ValueError, match="name is required"):
            g.update(name="")
        session.rollback()

    def test_toggle_active(self, session):
        """update() can toggle active."""
        g = _get_aoi_group(session)
        original = g.active

        updated = g.update(active=not original)

        assert updated.active == (not original)
        session.rollback()

    def test_no_op_when_no_kwargs(self, session):
        """Calling update() with no kwargs is a no-op."""
        g = _get_aoi_group(session)
        original = g.name

        g.update()

        assert g.name == original
        session.rollback()


# ---------------------------------------------------------------------------
# AreaOfInterest.update()
# ---------------------------------------------------------------------------

class TestUpdateAreaOfInterest:

    def test_update_name(self, session):
        """update() sets a new name."""
        a = _get_aoi(session)
        original = a.area_of_interest

        updated = a.update(area_of_interest="New AOI")

        assert updated.area_of_interest == "New AOI"
        session.rollback()
        assert a.area_of_interest == original

    def test_empty_name_raises(self, session):
        """update() raises ValueError for empty name."""
        a = _get_aoi(session)

        with pytest.raises(ValueError, match="area_of_interest name is required"):
            a.update(area_of_interest="")
        session.rollback()

    def test_invalid_group_raises(self, session):
        """update() raises ValueError for nonexistent group."""
        a = _get_aoi(session)

        with pytest.raises(ValueError, match="not found"):
            a.update(area_of_interest_group_id=999999999)
        session.rollback()

    def test_toggle_active(self, session):
        """update() can toggle active."""
        a = _get_aoi(session)
        original = a.active

        updated = a.update(active=not original)

        assert updated.active == (not original)
        session.rollback()

    def test_no_op_when_no_kwargs(self, session):
        """Calling update() with no kwargs is a no-op."""
        a = _get_aoi(session)
        original = a.area_of_interest

        a.update()

        assert a.area_of_interest == original
        session.rollback()


# ---------------------------------------------------------------------------
# ContractSource.update()
# ---------------------------------------------------------------------------

class TestUpdateContractSource:

    def test_update_name(self, session):
        """update() sets a new name."""
        cs = _get_contract_source(session)
        original = cs.contract_source

        updated = cs.update(contract_source="NewSource")

        assert updated.contract_source == "NewSource"
        session.rollback()
        assert cs.contract_source == original

    def test_empty_name_raises(self, session):
        """update() raises ValueError for empty name."""
        cs = _get_contract_source(session)

        with pytest.raises(ValueError, match="contract_source name is required"):
            cs.update(contract_source="")
        session.rollback()

    def test_toggle_active(self, session):
        """update() can toggle active."""
        cs = _get_contract_source(session)
        original = cs.active

        updated = cs.update(active=not original)

        assert updated.active == (not original)
        session.rollback()

    def test_no_op_when_no_kwargs(self, session):
        """Calling update() with no kwargs is a no-op."""
        cs = _get_contract_source(session)
        original = cs.contract_source

        cs.update()

        assert cs.contract_source == original
        session.rollback()


# ---------------------------------------------------------------------------
# Contract.update()
# ---------------------------------------------------------------------------

class TestUpdateContract:

    def test_update_title(self, session):
        """update() sets a new title."""
        c = _get_contract(session)
        original = c.title

        updated = c.update(title="New contract title")

        assert updated.title == "New contract title"
        session.rollback()
        assert c.title == original

    def test_empty_title_raises(self, session):
        """update() raises ValueError for empty title."""
        c = _get_contract(session)

        with pytest.raises(ValueError, match="title is required"):
            c.update(title="")
        session.rollback()

    def test_update_url(self, session):
        """update() sets a new URL."""
        c = _get_contract(session)

        updated = c.update(url="https://example.com/contract")

        assert updated.url == "https://example.com/contract"
        session.rollback()

    def test_clear_url(self, session):
        """Passing empty string for url clears it to None."""
        c = _get_contract(session)

        updated = c.update(url="")

        assert updated.url is None
        session.rollback()

    def test_update_start_date(self, session):
        """update() sets a new start_date."""
        c = _get_contract(session)
        new_start = datetime(2020, 1, 1)

        updated = c.update(start_date=new_start)

        assert updated.start_date == new_start
        session.rollback()

    def test_update_end_date(self, session):
        """update() sets a valid end_date after start_date."""
        c = _get_contract(session)
        future_end = datetime.now() + timedelta(days=3650)

        updated = c.update(end_date=future_end)

        assert updated.end_date == future_end
        session.rollback()

    def test_end_before_start_raises(self, session):
        """update() raises ValueError when end_date <= start_date."""
        c = session.query(Contract).filter(Contract.start_date.isnot(None)).first()
        if not c:
            pytest.skip("No contracts with a start_date in database")

        bad_end = c.start_date - timedelta(days=1)

        with pytest.raises(ValueError, match="end_date must be after start_date"):
            c.update(end_date=bad_end)
        session.rollback()

    def test_no_op_when_no_kwargs(self, session):
        """Calling update() with no kwargs is a no-op."""
        c = _get_contract(session)
        original = c.title

        c.update()

        assert c.title == original
        session.rollback()


# ---------------------------------------------------------------------------
# NSFProgram.update()
# ---------------------------------------------------------------------------

class TestUpdateNSFProgram:

    def test_update_name(self, session):
        """update() sets a new name."""
        p = _get_nsf_program(session)
        original = p.nsf_program_name

        updated = p.update(nsf_program_name="New Program")

        assert updated.nsf_program_name == "New Program"
        session.rollback()
        assert p.nsf_program_name == original

    def test_empty_name_raises(self, session):
        """update() raises ValueError for empty name."""
        p = _get_nsf_program(session)

        with pytest.raises(ValueError, match="nsf_program_name is required"):
            p.update(nsf_program_name="")
        session.rollback()

    def test_toggle_active(self, session):
        """update() can toggle active."""
        p = _get_nsf_program(session)
        original = p.active

        updated = p.update(active=not original)

        assert updated.active == (not original)
        session.rollback()

    def test_no_op_when_no_kwargs(self, session):
        """Calling update() with no kwargs is a no-op."""
        p = _get_nsf_program(session)
        original = p.nsf_program_name

        p.update()

        assert p.nsf_program_name == original
        session.rollback()
