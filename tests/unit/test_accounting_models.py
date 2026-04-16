"""Tests for accounting ORM models: Contract and AllocationTransactionType.

Ported from tests/unit/test_accounting_models.py. Dropped decorative
print statements. Contract.is_active hybrid property is the main thing
under test — both Python and SQL filter paths.
"""
import pytest

from sam import AllocationTransactionType, Contract


pytestmark = pytest.mark.unit


class TestContractModel:

    def test_contract_query(self, session):
        contract = session.query(Contract).first()
        if not contract:
            pytest.skip("No contracts in database")
        assert contract.contract_number is not None
        assert contract.title is not None
        assert contract.principal_investigator is not None
        assert contract.start_date is not None

    def test_contract_is_active_property(self, session):
        """Contract.is_active Python-side matches is_active_at() helper."""
        contract = session.query(Contract).first()
        if not contract:
            pytest.skip("No contracts in database")
        assert isinstance(contract.is_active, bool)
        assert contract.is_active == contract.is_active_at()

    def test_contract_is_active_sql_filter(self, session):
        """Contract.is_active works in SQL filter context, and active+inactive = total."""
        active = session.query(Contract).filter(Contract.is_active).all()
        inactive = session.query(Contract).filter(~Contract.is_active).all()
        total = session.query(Contract).count()
        assert len(active) + len(inactive) == total
        for c in active:
            assert c.is_active is True


class TestAllocationTransactionType:

    def test_allocation_transaction_type_enum(self):
        """AllocationTransactionType is a StrEnum with both new + legacy values."""
        assert AllocationTransactionType.CREATE == "CREATE"
        assert isinstance(AllocationTransactionType.CREATE, str)
        expected_new = {"CREATE", "EDIT", "TRANSFER", "ADJUSTMENT", "EXPIRE", "DELETE", "DETACH", "LINK", "RENEW"}
        expected_legacy = {"NEW", "EXTENSION", "SUPPLEMENT"}
        expected = expected_new | expected_legacy
        actual = {m.value for m in AllocationTransactionType}
        assert actual == expected
