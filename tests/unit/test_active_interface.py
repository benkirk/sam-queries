"""Tests for the universal is_active / is_active_at() interface across models.

Ported from tests/unit/test_active_interface.py. Every ORM model should
expose both a Python `is_active_at(check_date=None)` method and a
`Model.is_active` hybrid property that works in SQL filters. Parametrized
across every model class that uses each mixin.

No transformations beyond dropping the unused `_sql_filter_count` helper
and cleaning up a hard-to-read one-liner in the ActiveFlagMixin SQL
equivalence test.
"""
from datetime import datetime, timedelta

import pytest

from sam import (
    Account,
    AccountUser,
    Allocation,
    AllocationType,
    AreaOfInterest,
    AreaOfInterestGroup,
    Contract,
    ContractSource,
    Facility,
    Organization,
    Panel,
    Project,
    Resource,
    ResourceType,
    User,
)
from sam.core.users import AcademicStatus, EmailAddress
from sam.operational import WallclockExemption
from sam.resources.charging import Factor, Formula
from sam.resources.facilities import PanelSession
from sam.resources.machines import Queue


pytestmark = pytest.mark.unit


# ============================================================================
# ActiveFlagMixin — is_active wraps a boolean column; date-insensitive
# ============================================================================


_ACTIVE_FLAG_MODELS = [
    Project, Organization, Facility, Panel,
    AllocationType, ResourceType, ContractSource,
    AreaOfInterest, AreaOfInterestGroup,
]


class TestActiveFlagMixinInterface:

    @pytest.mark.parametrize("ModelClass", _ACTIVE_FLAG_MODELS)
    def test_has_is_active_at_method(self, session, ModelClass):
        obj = session.query(ModelClass).first()
        if obj is None:
            pytest.skip(f"No {ModelClass.__name__} rows in database")
        assert callable(getattr(obj, 'is_active_at', None))

    @pytest.mark.parametrize("ModelClass", _ACTIVE_FLAG_MODELS)
    def test_is_active_at_date_insensitive(self, session, ModelClass):
        """Date-insensitive: past, present, future all agree."""
        obj = session.query(ModelClass).first()
        if obj is None:
            pytest.skip(f"No {ModelClass.__name__} rows in database")
        past = datetime(2000, 1, 1)
        future = datetime(2099, 12, 31)
        assert obj.is_active_at(past) == obj.is_active_at() == obj.is_active_at(future)

    @pytest.mark.parametrize("ModelClass", _ACTIVE_FLAG_MODELS)
    def test_is_active_hybrid_python_matches_flag(self, session, ModelClass):
        obj = session.query(ModelClass).first()
        if obj is None:
            pytest.skip(f"No {ModelClass.__name__} rows in database")
        assert obj.is_active == bool(obj.active)

    @pytest.mark.parametrize("ModelClass", _ACTIVE_FLAG_MODELS)
    def test_is_active_sql_filter_equivalent_to_column_filter(self, session, ModelClass):
        """SQL filter via Model.is_active returns same IDs as Model.active == True."""
        pk = list(ModelClass.__mapper__.primary_key)[0].key
        via_hybrid = {
            getattr(obj, pk) for obj in session.query(ModelClass).filter(ModelClass.is_active).all()
        }
        via_column = {
            getattr(obj, pk) for obj in session.query(ModelClass).filter(ModelClass.active == True).all()  # noqa: E712
        }
        assert via_hybrid == via_column


# ============================================================================
# DateRangeMixin — is_active delegates to is_currently_active
# ============================================================================


class TestDateRangeMixinInterface:

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
        assert obj.is_active_at() == obj.is_active

    def test_factor_is_active_matches_is_active_at(self, session):
        obj = session.query(Factor).first()
        if obj is None:
            pytest.skip("No Factor rows")
        assert obj.is_active == obj.is_active_at()

    def test_formula_is_active_sql_filter_partitions(self, session):
        active = session.query(Formula).filter(Formula.is_active).count()
        inactive = session.query(Formula).filter(~Formula.is_active).count()
        total = session.query(Formula).count()
        assert active + inactive == total


# ============================================================================
# SoftDeleteMixin — is_active means NOT deleted
# ============================================================================


class TestSoftDeleteMixinInterface:

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
        via_column = session.query(Account).filter(Account.deleted == False).count()  # noqa: E712
        assert via_hybrid == via_column


# ============================================================================
# User — is_active ⇔ active AND NOT locked
# ============================================================================


class TestUserIsActive:

    def test_is_active_method_exists(self, session):
        user = session.query(User).first()
        if user is None:
            pytest.skip("No User rows")
        assert callable(user.is_active_at)
        assert isinstance(user.is_active, bool)

    def test_is_active_agrees_with_is_accessible(self, session):
        for user in session.query(User).limit(20).all():
            assert user.is_active == user.is_accessible, (
                f"User {user.username}: is_active={user.is_active} != is_accessible={user.is_accessible}"
            )

    def test_locked_user_is_not_active(self, session):
        locked = session.query(User).filter(User.locked == True).first()  # noqa: E712
        if locked is None:
            pytest.skip("No locked users in database")
        assert locked.is_active is False

    def test_inactive_user_is_not_active(self, session):
        inactive = session.query(User).filter(User.active == False).first()  # noqa: E712
        if inactive is None:
            pytest.skip("No inactive users in database")
        assert inactive.is_active is False

    def test_is_active_sql_filter(self, session):
        via_hybrid = session.query(User).filter(User.is_active).count()
        via_columns = session.query(User).filter(
            User.active == True, User.locked == False  # noqa: E712
        ).count()
        assert via_hybrid == via_columns

    def test_is_active_at_date_insensitive(self, session):
        user = session.query(User).first()
        if user is None:
            pytest.skip("No User rows")
        past = datetime(2000, 1, 1)
        future = datetime(2099, 12, 31)
        assert user.is_active_at(past) == user.is_active_at() == user.is_active_at(future)


# ============================================================================
# EmailAddress — nullable active column, None == active
# ============================================================================


class TestEmailAddressIsActive:

    def test_is_active_method_exists(self, session):
        obj = session.query(EmailAddress).first()
        if obj is None:
            pytest.skip("No EmailAddress rows")
        assert callable(obj.is_active_at)
        assert isinstance(obj.is_active, bool)

    def test_null_active_treated_as_active(self, session):
        null_active = session.query(EmailAddress).filter(EmailAddress.active.is_(None)).first()
        if null_active is None:
            pytest.skip("No EmailAddress rows with NULL active column")
        assert null_active.is_active is True
        assert null_active.is_active_at() is True

    def test_explicit_false_is_inactive(self, session):
        inactive = session.query(EmailAddress).filter(EmailAddress.active == False).first()  # noqa: E712
        if inactive is None:
            pytest.skip("No EmailAddress rows with active=False")
        assert inactive.is_active is False

    def test_is_active_sql_filter(self, session):
        via_hybrid = session.query(EmailAddress).filter(EmailAddress.is_active).count()
        via_column = session.query(EmailAddress).filter(
            (EmailAddress.active.is_(None)) | (EmailAddress.active == True)  # noqa: E712
        ).count()
        assert via_hybrid == via_column


# ============================================================================
# AcademicStatus — active AND NOT deleted
# ============================================================================


class TestAcademicStatusIsActive:

    def test_is_active_method_exists(self, session):
        obj = session.query(AcademicStatus).first()
        if obj is None:
            pytest.skip("No AcademicStatus rows")
        assert callable(obj.is_active_at)
        assert isinstance(obj.is_active, bool)

    def test_active_and_not_deleted_is_active(self, session):
        obj = session.query(AcademicStatus).filter(
            AcademicStatus.active == True,  # noqa: E712
            AcademicStatus.deleted == False,  # noqa: E712
        ).first()
        if obj is None:
            pytest.skip("No active non-deleted AcademicStatus rows")
        assert obj.is_active is True

    def test_inactive_is_not_active(self, session):
        obj = session.query(AcademicStatus).filter(AcademicStatus.active == False).first()  # noqa: E712
        if obj is None:
            pytest.skip("No inactive AcademicStatus rows")
        assert obj.is_active is False

    def test_deleted_is_not_active(self, session):
        obj = session.query(AcademicStatus).filter(AcademicStatus.deleted == True).first()  # noqa: E712
        if obj is None:
            pytest.skip("No deleted AcademicStatus rows")
        assert obj.is_active is False

    def test_is_active_sql_filter(self, session):
        via_hybrid = session.query(AcademicStatus).filter(AcademicStatus.is_active).count()
        via_columns = session.query(AcademicStatus).filter(
            AcademicStatus.active == True,  # noqa: E712
            AcademicStatus.deleted == False,  # noqa: E712
        ).count()
        assert via_hybrid == via_columns


# ============================================================================
# WallclockExemption — date-range; is_active == is_currently_active
# ============================================================================


class TestWallclockExemptionIsActive:

    def test_is_active_matches_is_currently_active(self, session):
        for obj in session.query(WallclockExemption).limit(20).all():
            assert obj.is_active == obj.is_currently_active

    def test_is_active_sql_vs_is_currently_active_sql(self, session):
        via_is_active = session.query(WallclockExemption).filter(WallclockExemption.is_active).count()
        via_compat = session.query(WallclockExemption).filter(WallclockExemption.is_currently_active).count()
        assert via_is_active == via_compat

    def test_is_active_at_method(self, session):
        obj = session.query(WallclockExemption).first()
        if obj is None:
            pytest.skip("No WallclockExemption rows")
        assert obj.is_active_at() == obj.is_currently_active


# ============================================================================
# PanelSession — manual date range, no mixin
# ============================================================================


class TestPanelSessionIsActive:

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


# ============================================================================
# Queue — nullable start/end dates
# ============================================================================


class TestQueueIsActive:

    def test_is_active_method_exists(self, session):
        obj = session.query(Queue).first()
        if obj is None:
            pytest.skip("No Queue rows")
        assert callable(obj.is_active_at)
        assert isinstance(obj.is_active, bool)

    def test_null_start_and_end_date_is_active(self, session):
        obj = session.query(Queue).filter(
            Queue.start_date.is_(None),
            Queue.end_date.is_(None),
        ).first()
        if obj is None:
            pytest.skip("No Queue rows with both dates NULL")
        assert obj.is_active is True
        assert obj.is_active_at(datetime(2000, 1, 1)) is True

    def test_ended_queue_is_not_active(self, session):
        past = datetime.now() - timedelta(days=1)
        ended = session.query(Queue).filter(Queue.end_date < past).first()
        if ended is None:
            pytest.skip("No ended Queue rows")
        assert ended.is_active is False

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


# ============================================================================
# Resource — commission/decommission based
# ============================================================================


class TestResourceIsActive:

    def test_is_active_at_method_exists(self, session):
        obj = session.query(Resource).first()
        if obj is None:
            pytest.skip("No Resource rows")
        assert callable(obj.is_active_at)

    def test_is_active_at_matches_is_commissioned_at(self, session):
        check = datetime.now()
        for obj in session.query(Resource).limit(20).all():
            assert obj.is_active_at(check) == obj.is_commissioned_at(check)

    def test_is_active_at_now_matches_is_active(self, session):
        for obj in session.query(Resource).limit(20).all():
            assert obj.is_active_at() == obj.is_active

    def test_historical_date_check(self, session):
        obj = session.query(Resource).first()
        if obj is None:
            pytest.skip("No Resource rows")
        assert obj.is_active_at(datetime(1970, 1, 1)) is False


# ============================================================================
# Contract regression guard
# ============================================================================


class TestContractIsActiveRegression:

    def test_is_active_sql_filter_partitions(self, session):
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
        assert obj.is_active_at(datetime(1970, 1, 1)) is False


# ============================================================================
# Allocation regression guard
# ============================================================================


class TestAllocationIsActiveRegression:

    def test_is_active_sql_filter_partitions(self, session):
        active = session.query(Allocation).filter(Allocation.is_active).count()
        inactive = session.query(Allocation).filter(~Allocation.is_active).count()
        total = session.query(Allocation).count()
        assert active + inactive == total

    def test_deleted_allocation_is_not_active(self, session):
        deleted = session.query(Allocation).filter(Allocation.deleted == True).first()  # noqa: E712
        if deleted is None:
            pytest.skip("No deleted Allocation rows")
        assert deleted.is_active is False

    def test_is_active_at_method(self, session):
        obj = session.query(Allocation).first()
        if obj is None:
            pytest.skip("No Allocation rows")
        assert obj.is_active_at() == bool(obj.is_active)
