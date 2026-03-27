"""
Tests for the standardized is_active / is_active_at() interface across all SAM ORM models.

Every model should expose:
  - is_active_at(check_date=None) -> bool   (Python method, date may be ignored for flag-only models)
  - is_active                                (hybrid_property, works in Python AND SQL .filter())
"""

import pytest
from datetime import datetime, timedelta

from sam import (
    # Core
    User, Organization,
    # Projects
    Project, Contract, ContractSource,
    # Accounting
    Account, AccountUser, Allocation, AllocationType,
    # Resources
    Resource, ResourceType, Facility, Panel,
    # Areas / ancillary
    AreaOfInterest, AreaOfInterestGroup,
)
from sam.core.users import EmailAddress, AcademicStatus
from sam.resources.facilities import PanelSession
from sam.resources.machines import Queue
from sam.operational import WallclockExemption
from sam.resources.charging import Factor, Formula


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sql_filter_count(session, Model, use_is_active):
    """Return count from SQL filter using Model.is_active (or ~Model.is_active)."""
    if use_is_active:
        return session.query(Model).filter(Model.is_active).count()
    else:
        return session.query(Model).filter(~Model.is_active).count()


# ===========================================================================
# ActiveFlagMixin models — is_active wraps boolean column; date-insensitive
# ===========================================================================

class TestActiveFlagMixinInterface:
    """Verify ActiveFlagMixin provides is_active hybrid + is_active_at()."""

    @pytest.mark.parametrize("ModelClass", [
        Project, Organization, Facility, Panel,
        AllocationType, ResourceType, ContractSource,
        AreaOfInterest, AreaOfInterestGroup,
    ])
    def test_has_is_active_at_method(self, session, ModelClass):
        """All ActiveFlagMixin models have is_active_at()."""
        obj = session.query(ModelClass).first()
        if obj is None:
            pytest.skip(f"No {ModelClass.__name__} rows in database")
        assert callable(getattr(obj, 'is_active_at', None)), \
            f"{ModelClass.__name__} missing is_active_at()"

    @pytest.mark.parametrize("ModelClass", [
        Project, Organization, Facility, Panel,
        AllocationType, ResourceType, ContractSource,
        AreaOfInterest, AreaOfInterestGroup,
    ])
    def test_is_active_at_date_insensitive(self, session, ModelClass):
        """is_active_at() ignores date for flag-only models."""
        obj = session.query(ModelClass).first()
        if obj is None:
            pytest.skip(f"No {ModelClass.__name__} rows in database")
        past = datetime(2000, 1, 1)
        future = datetime(2099, 12, 31)
        # All three calls must agree — the boolean flag is the only factor
        assert obj.is_active_at(past) == obj.is_active_at() == obj.is_active_at(future)

    @pytest.mark.parametrize("ModelClass", [
        Project, Organization, Facility, Panel,
        AllocationType, ResourceType, ContractSource,
        AreaOfInterest, AreaOfInterestGroup,
    ])
    def test_is_active_hybrid_python_matches_flag(self, session, ModelClass):
        """is_active hybrid equals bool(obj.active) on the Python side."""
        obj = session.query(ModelClass).first()
        if obj is None:
            pytest.skip(f"No {ModelClass.__name__} rows in database")
        assert obj.is_active == bool(obj.active)

    @pytest.mark.parametrize("ModelClass", [
        Project, Organization, Facility, Panel,
        AllocationType, ResourceType, ContractSource,
        AreaOfInterest, AreaOfInterestGroup,
    ])
    def test_is_active_sql_filter_equivalent_to_column_filter(self, session, ModelClass):
        """SQL filter using Model.is_active must return same rows as Model.active == True."""
        via_hybrid = set(
            getattr(obj, list(obj.__class__.__mapper__.primary_key[0].key for _ in [obj])[0])
            for obj in session.query(ModelClass).filter(ModelClass.is_active).all()
        )
        via_column = set(
            getattr(obj, list(obj.__class__.__mapper__.primary_key[0].key for _ in [obj])[0])
            for obj in session.query(ModelClass).filter(ModelClass.active == True).all()
        )
        assert via_hybrid == via_column, \
            f"{ModelClass.__name__}: is_active SQL filter differs from active==True"


# ===========================================================================
# DateRangeMixin models — is_active delegates to is_currently_active
# ===========================================================================

class TestDateRangeMixinInterface:
    """Verify DateRangeMixin provides is_active + backward-compat is_currently_active."""

    def test_account_user_is_active_python(self, session):
        obj = session.query(AccountUser).first()
        if obj is None:
            pytest.skip("No AccountUser rows")
        assert isinstance(obj.is_active, bool)
        assert obj.is_active == obj.is_currently_active

    def test_account_user_is_active_sql(self, session):
        via_hybrid = session.query(AccountUser).filter(AccountUser.is_active).count()
        via_compat = session.query(AccountUser).filter(AccountUser.is_currently_active).count()
        assert via_hybrid == via_compat

    def test_account_user_is_active_at_method(self, session):
        obj = session.query(AccountUser).first()
        if obj is None:
            pytest.skip("No AccountUser rows")
        assert callable(obj.is_active_at)
        # Past date well before any start_date should return False for most rows
        result_now = obj.is_active_at()
        result_now2 = obj.is_active  # hybrid should agree
        assert result_now == result_now2

    def test_factor_is_active_matches_is_currently_active_semantics(self, session):
        """Factor (DateRangeMixin subclass) is_active == is_currently_active."""
        obj = session.query(Factor).first()
        if obj is None:
            pytest.skip("No Factor rows")
        assert obj.is_active == obj.is_active_at()

    def test_formula_is_active_sql_filter(self, session):
        active = session.query(Formula).filter(Formula.is_active).count()
        inactive = session.query(Formula).filter(~Formula.is_active).count()
        total = session.query(Formula).count()
        assert active + inactive == total


# ===========================================================================
# SoftDeleteMixin models — is_active means NOT deleted
# ===========================================================================

class TestSoftDeleteMixinInterface:
    """Verify SoftDeleteMixin provides is_active (not deleted) + is_active_at()."""

    def test_account_is_active_python(self, session):
        obj = session.query(Account).first()
        if obj is None:
            pytest.skip("No Account rows")
        assert obj.is_active == (not bool(obj.deleted))

    def test_account_is_active_at_date_insensitive(self, session):
        obj = session.query(Account).first()
        if obj is None:
            pytest.skip("No Account rows")
        past = datetime(2000, 1, 1)
        future = datetime(2099, 12, 31)
        assert obj.is_active_at(past) == obj.is_active_at() == obj.is_active_at(future)

    def test_account_is_active_sql_filter_equivalent(self, session):
        via_hybrid = session.query(Account).filter(Account.is_active).count()
        via_column = session.query(Account).filter(Account.deleted == False).count()
        assert via_hybrid == via_column


# ===========================================================================
# User — active AND NOT locked
# ===========================================================================

class TestUserIsActive:
    """is_active requires active=True AND locked=False."""

    def test_is_active_method_exists(self, session):
        user = session.query(User).first()
        if user is None:
            pytest.skip("No User rows")
        assert callable(user.is_active_at)
        assert isinstance(user.is_active, bool)

    def test_is_active_agrees_with_is_accessible(self, session):
        for user in session.query(User).limit(20).all():
            assert user.is_active == user.is_accessible, \
                f"User {user.username}: is_active ({user.is_active}) != is_accessible ({user.is_accessible})"

    def test_locked_user_is_not_active(self, session):
        locked = session.query(User).filter(User.locked == True).first()
        if locked is None:
            pytest.skip("No locked users in database")
        assert locked.is_active == False

    def test_inactive_user_is_not_active(self, session):
        inactive = session.query(User).filter(User.active == False).first()
        if inactive is None:
            pytest.skip("No inactive users in database")
        assert inactive.is_active == False

    def test_is_active_sql_filter(self, session):
        via_hybrid = session.query(User).filter(User.is_active).count()
        via_columns = session.query(User).filter(
            User.active == True, User.locked == False
        ).count()
        assert via_hybrid == via_columns

    def test_is_active_at_date_insensitive(self, session):
        user = session.query(User).first()
        if user is None:
            pytest.skip("No User rows")
        # User active status has no temporal dimension
        past = datetime(2000, 1, 1)
        future = datetime(2099, 12, 31)
        assert user.is_active_at(past) == user.is_active_at() == user.is_active_at(future)


# ===========================================================================
# EmailAddress — nullable active column, None means active
# ===========================================================================

class TestEmailAddressIsActive:
    """is_active handles nullable active column (None == active)."""

    def test_is_active_method_exists(self, session):
        obj = session.query(EmailAddress).first()
        if obj is None:
            pytest.skip("No EmailAddress rows")
        assert callable(obj.is_active_at)
        assert isinstance(obj.is_active, bool)

    def test_null_active_treated_as_active(self, session):
        null_active = session.query(EmailAddress).filter(
            EmailAddress.active.is_(None)
        ).first()
        if null_active is None:
            pytest.skip("No EmailAddress with NULL active in database")
        assert null_active.is_active == True
        assert null_active.is_active_at() == True

    def test_explicit_false_is_inactive(self, session):
        inactive = session.query(EmailAddress).filter(EmailAddress.active == False).first()
        if inactive is None:
            pytest.skip("No inactive EmailAddress rows in database")
        assert inactive.is_active == False

    def test_is_active_sql_filter(self, session):
        via_hybrid = session.query(EmailAddress).filter(EmailAddress.is_active).count()
        via_column = session.query(EmailAddress).filter(
            (EmailAddress.active.is_(None)) | (EmailAddress.active == True)
        ).count()
        assert via_hybrid == via_column


# ===========================================================================
# AcademicStatus — combined: active AND NOT deleted
# ===========================================================================

class TestAcademicStatusIsActive:
    """is_active checks both the active flag AND soft-delete flag."""

    def test_is_active_method_exists(self, session):
        obj = session.query(AcademicStatus).first()
        if obj is None:
            pytest.skip("No AcademicStatus rows")
        assert callable(obj.is_active_at)
        assert isinstance(obj.is_active, bool)

    def test_active_and_not_deleted_is_active(self, session):
        obj = session.query(AcademicStatus).filter(
            AcademicStatus.active == True,
            AcademicStatus.deleted == False
        ).first()
        if obj is None:
            pytest.skip("No active non-deleted AcademicStatus rows")
        assert obj.is_active == True

    def test_inactive_is_not_active(self, session):
        obj = session.query(AcademicStatus).filter(AcademicStatus.active == False).first()
        if obj is None:
            pytest.skip("No inactive AcademicStatus rows")
        assert obj.is_active == False

    def test_deleted_is_not_active(self, session):
        obj = session.query(AcademicStatus).filter(AcademicStatus.deleted == True).first()
        if obj is None:
            pytest.skip("No deleted AcademicStatus rows")
        assert obj.is_active == False

    def test_is_active_sql_filter(self, session):
        via_hybrid = session.query(AcademicStatus).filter(AcademicStatus.is_active).count()
        via_columns = session.query(AcademicStatus).filter(
            AcademicStatus.active == True,
            AcademicStatus.deleted == False
        ).count()
        assert via_hybrid == via_columns


# ===========================================================================
# WallclockExemption — date-range, is_active == is_currently_active
# ===========================================================================

class TestWallclockExemptionIsActive:
    """is_active is a backward-compat alias for is_currently_active."""

    def test_is_active_matches_is_currently_active(self, session):
        for obj in session.query(WallclockExemption).limit(20).all():
            assert obj.is_active == obj.is_currently_active

    def test_is_active_sql_vs_is_currently_active_sql(self, session):
        via_is_active = session.query(WallclockExemption).filter(
            WallclockExemption.is_active
        ).count()
        via_compat = session.query(WallclockExemption).filter(
            WallclockExemption.is_currently_active
        ).count()
        assert via_is_active == via_compat

    def test_is_active_at_method(self, session):
        obj = session.query(WallclockExemption).first()
        if obj is None:
            pytest.skip("No WallclockExemption rows")
        assert obj.is_active_at() == obj.is_currently_active


# ===========================================================================
# PanelSession — manual date range, no mixin
# ===========================================================================

class TestPanelSessionIsActive:
    """PanelSession has is_active and is_active_at() added directly."""

    def test_is_active_method_exists(self, session):
        obj = session.query(PanelSession).first()
        if obj is None:
            pytest.skip("No PanelSession rows")
        assert callable(obj.is_active_at)
        assert isinstance(obj.is_active, bool)

    def test_is_active_sql_filter_and_python_agree(self, session):
        rows = session.query(PanelSession).limit(20).all()
        if not rows:
            pytest.skip("No PanelSession rows")
        for obj in rows:
            assert obj.is_active == obj.is_active_at()

    def test_is_active_sql_filter_covers_all(self, session):
        active = session.query(PanelSession).filter(PanelSession.is_active).count()
        inactive = session.query(PanelSession).filter(~PanelSession.is_active).count()
        total = session.query(PanelSession).count()
        assert active + inactive == total


# ===========================================================================
# Queue — nullable start/end dates
# ===========================================================================

class TestQueueIsActive:
    """Queue has is_active_at() with null-safe date logic."""

    def test_is_active_method_exists(self, session):
        obj = session.query(Queue).first()
        if obj is None:
            pytest.skip("No Queue rows")
        assert callable(obj.is_active_at)
        assert isinstance(obj.is_active, bool)

    def test_null_start_and_end_date_is_active(self, session):
        obj = session.query(Queue).filter(
            Queue.start_date.is_(None),
            Queue.end_date.is_(None)
        ).first()
        if obj is None:
            pytest.skip("No Queue with both dates NULL")
        assert obj.is_active == True
        assert obj.is_active_at(datetime(2000, 1, 1)) == True

    def test_ended_queue_is_not_active(self, session):
        past = datetime.now() - timedelta(days=1)
        ended = session.query(Queue).filter(Queue.end_date < past).first()
        if ended is None:
            pytest.skip("No ended Queue rows")
        assert ended.is_active == False

    def test_is_active_sql_covers_all_rows(self, session):
        active = session.query(Queue).filter(Queue.is_active).count()
        inactive = session.query(Queue).filter(~Queue.is_active).count()
        total = session.query(Queue).count()
        assert active + inactive == total

    def test_is_active_python_sql_agree(self, session):
        rows = session.query(Queue).limit(20).all()
        if not rows:
            pytest.skip("No Queue rows")
        for obj in rows:
            assert obj.is_active == obj.is_active_at()


# ===========================================================================
# Resource — commission/decommission based, is_active_at added
# ===========================================================================

class TestResourceIsActive:
    """Resource has is_active hybrid already; is_active_at() now delegates to is_commissioned_at()."""

    def test_is_active_at_method_exists(self, session):
        obj = session.query(Resource).first()
        if obj is None:
            pytest.skip("No Resource rows")
        assert callable(obj.is_active_at)

    def test_is_active_at_matches_is_commissioned_at(self, session):
        for obj in session.query(Resource).limit(20).all():
            check = datetime.now()
            assert obj.is_active_at(check) == obj.is_commissioned_at(check)

    def test_is_active_at_now_matches_is_active(self, session):
        for obj in session.query(Resource).limit(20).all():
            assert obj.is_active_at() == obj.is_active

    def test_historical_date_check(self, session):
        obj = session.query(Resource).first()
        if obj is None:
            pytest.skip("No Resource rows")
        # Resources didn't exist in 1970
        assert obj.is_active_at(datetime(1970, 1, 1)) == False


# ===========================================================================
# Contract — already had is_active + is_active_at(); regression guard
# ===========================================================================

class TestContractIsActiveRegression:
    """Ensure Contract's existing interface still works correctly."""

    def test_is_active_sql_filter_unchanged(self, session):
        active = session.query(Contract).filter(Contract.is_active).count()
        inactive = session.query(Contract).filter(~Contract.is_active).count()
        total = session.query(Contract).count()
        assert active + inactive == total

    def test_is_active_at_method(self, session):
        obj = session.query(Contract).first()
        if obj is None:
            pytest.skip("No Contract rows")
        assert obj.is_active_at() == obj.is_active

    def test_historical_check(self, session):
        obj = session.query(Contract).first()
        if obj is None:
            pytest.skip("No Contract rows")
        # 1970 predates all contracts
        assert obj.is_active_at(datetime(1970, 1, 1)) == False


# ===========================================================================
# Allocation — already had is_active + is_active_at(); regression guard
# ===========================================================================

class TestAllocationIsActiveRegression:
    """Ensure Allocation's existing interface still works correctly."""

    def test_is_active_sql_filter_unchanged(self, session):
        active = session.query(Allocation).filter(Allocation.is_active).count()
        inactive = session.query(Allocation).filter(~Allocation.is_active).count()
        total = session.query(Allocation).count()
        assert active + inactive == total

    def test_deleted_allocation_is_not_active(self, session):
        deleted = session.query(Allocation).filter(Allocation.deleted == True).first()
        if deleted is None:
            pytest.skip("No deleted Allocation rows")
        assert deleted.is_active == False

    def test_is_active_at_method(self, session):
        obj = session.query(Allocation).first()
        if obj is None:
            pytest.skip("No Allocation rows")
        assert obj.is_active_at() == bool(obj.is_active)
