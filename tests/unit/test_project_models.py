"""Tests for project-related ORM models: ProjectCode, FosAoi, ResponsibleParty.

Ported from tests/unit/test_project_models.py. Most tests are pure reads.
Two tests (`test_responsible_party_create`, `test_responsible_party_timestamps`)
do construct a ResponsibleParty row and flush it — those writes stay under
the SAVEPOINT'd fixture so they roll back at test exit. Dropped decorative
print() statements.
"""
from datetime import datetime

import pytest

from sam import (
    Account,
    AreaOfInterest,
    Facility,
    FosAoi,
    MnemonicCode,
    Project,
    ProjectCode,
    ResponsibleParty,
    User,
)
from factories import make_project


pytestmark = pytest.mark.unit


# ============================================================================
# ProjectCode — composite-key model
# ============================================================================


class TestProjectCodeModel:

    def test_count(self, session):
        assert session.query(ProjectCode).count() >= 0

    def test_query_fields(self, session):
        proj_code = session.query(ProjectCode).first()
        if not proj_code:
            pytest.skip("No project codes in database")
        assert proj_code.facility_id is not None
        assert proj_code.mnemonic_code_id is not None
        assert proj_code.digits is not None
        assert isinstance(proj_code.digits, int)

    def test_composite_key(self, session):
        """Composite primary key lookup by (facility_id, mnemonic_code_id)."""
        proj_code = session.query(ProjectCode).first()
        if not proj_code:
            pytest.skip("No project codes in database")
        same = session.query(ProjectCode).filter(
            ProjectCode.facility_id == proj_code.facility_id,
            ProjectCode.mnemonic_code_id == proj_code.mnemonic_code_id,
        ).one()
        assert same.facility_id == proj_code.facility_id
        assert same.mnemonic_code_id == proj_code.mnemonic_code_id

    def test_facility_relationship(self, session):
        proj_code = session.query(ProjectCode).first()
        if not proj_code:
            pytest.skip("No project codes in database")
        assert isinstance(proj_code.facility, Facility)

    def test_mnemonic_relationship(self, session):
        proj_code = session.query(ProjectCode).first()
        if not proj_code:
            pytest.skip("No project codes in database")
        assert isinstance(proj_code.mnemonic_code, MnemonicCode)

    def test_facility_back_populates_project_codes(self, session):
        proj_code = session.query(ProjectCode).first()
        if not proj_code:
            pytest.skip("No project codes in database")
        facility = proj_code.facility
        assert hasattr(facility, 'project_codes')
        assert len(facility.project_codes) > 0
        assert proj_code in facility.project_codes

    def test_mnemonic_back_populates_project_codes(self, session):
        proj_code = session.query(ProjectCode).first()
        if not proj_code:
            pytest.skip("No project codes in database")
        mnemonic = proj_code.mnemonic_code
        assert hasattr(mnemonic, 'project_codes')
        assert len(mnemonic.project_codes) > 0
        assert proj_code in mnemonic.project_codes

    def test_digits_range(self, session):
        proj_codes = session.query(ProjectCode).all()
        if not proj_codes:
            pytest.skip("No project codes in database")
        for pc in proj_codes:
            assert pc.digits > 0
            assert pc.digits <= 1000


# ============================================================================
# FosAoi — Field of Science ↔ Area of Interest mapping
# ============================================================================


class TestFosAoiModel:

    def test_count(self, session):
        assert session.query(FosAoi).count() >= 0

    def test_query_fields(self, session):
        fos = session.query(FosAoi).first()
        if not fos:
            pytest.skip("No FOS-AOI mappings in database")
        assert fos.fos_aoi_id is not None
        assert fos.fos_id is not None
        assert fos.area_of_interest_id is not None

    def test_aoi_relationship(self, session):
        fos = session.query(FosAoi).first()
        if not fos:
            pytest.skip("No FOS-AOI mappings in database")
        assert isinstance(fos.area_of_interest, AreaOfInterest)

    def test_aoi_back_populates_fos_mappings(self, session):
        fos = session.query(FosAoi).first()
        if not fos:
            pytest.skip("No FOS-AOI mappings in database")
        aoi = fos.area_of_interest
        assert hasattr(aoi, 'fos_mappings')
        assert len(aoi.fos_mappings) > 0
        assert fos in aoi.fos_mappings

    def test_fos_id_unique(self, session):
        fos_list = session.query(FosAoi).all()
        if not fos_list:
            pytest.skip("No FOS-AOI mappings in database")
        fos_ids = [f.fos_id for f in fos_list]
        assert len(fos_ids) == len(set(fos_ids))

    def test_timestamps(self, session):
        fos = session.query(FosAoi).first()
        if not fos:
            pytest.skip("No FOS-AOI mappings in database")
        assert hasattr(fos, 'creation_time')
        assert hasattr(fos, 'modified_time')
        assert fos.creation_time is not None


# ============================================================================
# ResponsibleParty — account-user responsibility tracking
#
# Table is typically empty in the snapshot, so the create/flush tests
# double as smoke tests for ResponsibleParty insertion. Writes happen
# inside the SAVEPOINT'd fixture session, so they roll back at teardown.
# ============================================================================


class TestResponsiblePartyModel:

    def test_count(self, session):
        assert session.query(ResponsibleParty).count() >= 0

    def test_create_and_traverse(self, session):
        """Insert a responsible party and verify both relationship directions."""
        account = session.query(Account).first()
        user = session.query(User).first()
        if not account or not user:
            pytest.skip("Need account and user in database")

        rp = ResponsibleParty(
            account_id=account.account_id,
            user_id=user.user_id,
            responsible_party_type='PI',
        )
        session.add(rp)
        session.flush()

        assert rp.responsible_party_id is not None
        assert rp.account_id == account.account_id
        assert rp.user_id == user.user_id
        assert rp.responsible_party_type == 'PI'
        assert rp.account == account
        assert rp.user == user
        session.rollback()

    def test_rp_account_relationship(self, session):
        rp = session.query(ResponsibleParty).first()
        if not rp:
            pytest.skip("No responsible parties in database")
        assert isinstance(rp.account, Account)

    def test_rp_user_relationship(self, session):
        rp = session.query(ResponsibleParty).first()
        if not rp:
            pytest.skip("No responsible parties in database")
        assert isinstance(rp.user, User)

    def test_account_back_populates_responsible_parties(self, session):
        account = session.query(Account).first()
        if not account:
            pytest.skip("No accounts in database")
        assert hasattr(account, 'responsible_parties')
        assert isinstance(account.responsible_parties, list)

    def test_user_back_populates_responsible_accounts(self, session):
        user = session.query(User).first()
        if not user:
            pytest.skip("No users in database")
        assert hasattr(user, 'responsible_accounts')
        assert isinstance(user.responsible_accounts, list)

    def test_timestamps_populated_on_flush(self, session):
        account = session.query(Account).first()
        user = session.query(User).first()
        if not account or not user:
            pytest.skip("Need account and user in database")

        rp = ResponsibleParty(
            account_id=account.account_id,
            user_id=user.user_id,
            responsible_party_type='admin',
        )
        session.add(rp)
        session.flush()
        assert rp.creation_time is not None
        assert hasattr(rp, 'modified_time')
        session.rollback()


# ============================================================================
# Project.facility_name — derives via allocation_type → panel → facility
# ============================================================================


class TestProjectFacilityName:
    """The facility-scoped RBAC layer keys off ``Project.facility_name``.
    Verify the chain resolution and the ``None`` fallback for orphan
    projects (no ``allocation_type``)."""

    def test_full_chain_returns_facility_name(self, session):
        # Pick any snapshot project that has the full chain.
        project = (
            session.query(Project)
            .filter(Project.allocation_type_id.isnot(None))
            .first()
        )
        if project is None:
            pytest.skip("No projects with allocation_type in snapshot")
        if project.allocation_type is None or project.allocation_type.panel is None:
            pytest.skip("Snapshot project has a truncated chain")

        expected = project.allocation_type.panel.facility.facility_name
        assert project.facility_name == expected
        assert isinstance(project.facility_name, str)

    def test_orphan_project_returns_none(self, session):
        # make_project leaves allocation_type_id=None by default — that
        # is the exact orphan shape the RBAC layer needs to cover.
        project = make_project(session)
        assert project.allocation_type_id is None
        assert project.facility_name is None


# ============================================================================
# Project.reactivate — the deactivation stamp
# ============================================================================


class TestProjectReactivate:
    """``inactivate_time`` is stamped on deactivate (``sam-admin project``) and
    was never cleared by anything. ``reactivate()`` is the one path that clears
    it, kept deliberately separate from ``update(active=True)``."""

    def test_reactivate_sets_active_and_clears_the_stamp(self, session):
        project = make_project(session, active=False)
        project.inactivate_time = datetime(2026, 1, 15, 9, 30)
        session.flush()

        result = project.reactivate()

        assert result is project              # chainable
        assert project.is_active
        assert project.inactivate_time is None

    def test_reactivate_is_idempotent_on_an_active_project(self, session):
        project = make_project(session, active=True)
        project.reactivate()
        assert project.is_active
        assert project.inactivate_time is None

    def test_update_active_true_leaves_the_stamp_alone(self, session):
        """The guard rail for the road not taken.

        Making ``update()`` symmetric would clear ``inactivate_time`` on every
        save of an already-active project, because the admin Details tab loads
        with ``partial=True`` and only sends ``active`` when it is *checked*.
        This test fails the moment someone widens ``update()``.
        """
        project = make_project(session, active=False)
        stamp = datetime(2026, 1, 15, 9, 30)
        project.inactivate_time = stamp
        session.flush()

        project.update(active=True)

        assert project.is_active
        assert project.inactivate_time == stamp


class TestProjectDeactivate:
    """``deactivate()`` is the other half of ``reactivate()`` — the stamping
    half, which used to live open-coded in ``sam-admin project --deactivate``
    while the admin button dropped the stamp entirely."""

    def test_deactivate_clears_active_and_stamps(self, session):
        project = make_project(session, active=True)

        result = project.deactivate()

        assert result is project              # chainable
        assert not project.is_active
        assert project.inactivate_time is not None

    def test_an_explicit_when_is_used_verbatim(self, session):
        """How a batch shares one stamp, and how a task stamps its occurrence
        rather than whenever the pod happened to wake up."""
        project = make_project(session, active=True)
        stamp = datetime(2026, 9, 3, 4, 30)

        project.deactivate(when=stamp)

        assert project.inactivate_time == stamp

    def test_round_trips_with_reactivate(self, session):
        project = make_project(session, active=True)

        project.deactivate(when=datetime(2026, 9, 3, 4, 30))
        project.reactivate()

        assert project.is_active
        assert project.inactivate_time is None

    def test_update_active_false_still_leaves_the_stamp_alone(self, session):
        """The mirror of ``test_update_active_true_leaves_the_stamp_alone``.

        ``update()`` must stay dumb about the stamp column in BOTH directions —
        stamping from ``update(active=False)`` would fire on any admin save that
        happens to include an unchecked box.
        """
        project = make_project(session, active=True)

        project.update(active=False)

        assert not project.is_active
        assert project.inactivate_time is None

    def test_flush_false_defers_the_write(self, session):
        """The knob `deactivate_projects` uses to flush a batch once."""
        project = make_project(session, active=True)
        session.flush()

        project.deactivate(when=datetime(2026, 9, 3, 4, 30), flush=False)

        assert project in session.dirty       # staged, not yet flushed
        session.flush()
        assert not session.dirty
