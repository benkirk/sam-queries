"""
Tests for accounting ORM models: Contract and AllocationTransactionType.
"""

import pytest

from sam import Contract, AllocationTransactionType


class TestContractModel:
    """Test Contract model - funding contracts with is_active hybrid property."""

    def test_contract_query(self, session):
        """Test basic contract query and relationships."""
        contract = session.query(Contract).first()
        if not contract:
            pytest.skip("No contracts in database")
        assert contract.contract_number is not None
        assert contract.title is not None
        assert contract.principal_investigator is not None
        assert contract.start_date is not None
        print(f"✅ Contract: {contract.contract_number} - {contract.title[:50]}")

    def test_contract_is_active_property(self, session):
        """Test Contract.is_active hybrid property."""
        contract = session.query(Contract).first()
        if not contract:
            pytest.skip("No contracts in database")
        assert isinstance(contract.is_active, bool)
        assert contract.is_active == contract.is_active_at()
        print(f"✅ Contract.is_active={contract.is_active}, is_active_at()={contract.is_active_at()}")

    def test_contract_is_active_sql_filter(self, session):
        """Test Contract.is_active works in SQL queries."""
        active = session.query(Contract).filter(Contract.is_active).all()
        inactive = session.query(Contract).filter(~Contract.is_active).all()
        total = session.query(Contract).count()
        assert len(active) + len(inactive) == total
        for c in active:
            assert c.is_active == True
        print(f"✅ Contract SQL filter: {len(active)} active, {len(inactive)} inactive out of {total}")


class TestAllocationTransactionType:
    """Test AllocationTransactionType StrEnum."""

    def test_allocation_transaction_type_enum(self):
        """Test AllocationTransactionType is a StrEnum with expected values."""
        assert AllocationTransactionType.CREATE == "CREATE"
        assert isinstance(AllocationTransactionType.CREATE, str)
        # Python-side types (new operations)
        expected_new = {"CREATE", "EDIT", "TRANSFER", "ADJUSTMENT", "EXPIRE", "DELETE", "DETACH", "LINK"}
        # Legacy Java-side types present in existing DB data
        expected_legacy = {"NEW", "EXTENSION", "SUPPLEMENT"}
        expected = expected_new | expected_legacy
        actual = {m.value for m in AllocationTransactionType}
        assert actual == expected
        print(f"✅ AllocationTransactionType has {len(expected)} members: {sorted(expected)}")
