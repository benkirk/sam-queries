"""
Tests for charging infrastructure ORM models: Factor and Formula.
"""

import pytest
from datetime import datetime

from sam import Factor, Formula, ResourceType


class TestFactorModel:
    """Test Factor model - charging factors for resource types."""

    def test_factor_count(self, session):
        """Test that we can query factors."""
        factor_count = session.query(Factor).count()
        assert factor_count >= 0, "Should be able to count factors"
        print(f"✅ Found {factor_count} factors")

    def test_factor_query(self, session):
        """Test querying and accessing factor properties."""
        factor = session.query(Factor).first()
        if not factor:
            pytest.skip("No factors in database")

        assert factor.factor_id is not None
        assert factor.factor_name is not None
        assert factor.value is not None
        assert factor.resource_type_id is not None
        assert factor.start_date is not None
        print(f"✅ Factor: {factor.factor_name} = {factor.value}")

    def test_factor_resource_type_relationship(self, session):
        """Test Factor -> ResourceType relationship."""
        factor = session.query(Factor).first()
        if not factor:
            pytest.skip("No factors in database")

        assert factor.resource_type is not None
        assert isinstance(factor.resource_type, ResourceType)
        print(f"✅ Factor {factor.factor_name} → ResourceType {factor.resource_type.resource_type}")

    def test_resource_type_to_factors(self, session):
        """Test reverse relationship: ResourceType -> [Factors]."""
        factor = session.query(Factor).first()
        if not factor:
            pytest.skip("No factors in database")

        resource_type = factor.resource_type
        assert hasattr(resource_type, 'factors')
        assert len(resource_type.factors) > 0
        assert factor in resource_type.factors
        print(f"✅ ResourceType {resource_type.resource_type} has {len(resource_type.factors)} factor(s)")

    def test_factor_is_active_property(self, session):
        """Test factor.is_active property for date-based validity."""
        factor = session.query(Factor).first()
        if not factor:
            pytest.skip("No factors in database")

        assert isinstance(factor.is_active, bool)

        if factor.end_date is None and factor.start_date < datetime.now():
            assert factor.is_active == True
            print(f"✅ Factor {factor.factor_name} is active (no end_date)")
        elif factor.end_date and factor.end_date > datetime.now():
            print(f"✅ Factor {factor.factor_name} active status: {factor.is_active}")

    def test_factor_with_end_date(self, session):
        """Test factors with end_date (expired factors)."""
        expired_factors = session.query(Factor).filter(
            Factor.end_date.isnot(None),
            Factor.end_date < datetime.now()
        ).all()

        print(f"✅ Found {len(expired_factors)} expired factor(s)")
        for factor in expired_factors:
            assert factor.is_active == False
            print(f"   - {factor.factor_name}: expired on {factor.end_date}")

    def test_factor_timestamps(self, session):
        """Test that factors have timestamp fields."""
        factor = session.query(Factor).first()
        if not factor:
            pytest.skip("No factors in database")

        assert hasattr(factor, 'creation_time')
        assert hasattr(factor, 'modified_time')
        assert factor.creation_time is not None
        print(f"✅ Factor has timestamps: created={factor.creation_time}")

    def test_factor_is_active_sql_filter(self, session):
        """Test Factor.is_active hybrid works in SQL queries."""
        active = session.query(Factor).filter(Factor.is_active).all()
        inactive = session.query(Factor).filter(~Factor.is_active).all()
        total = session.query(Factor).count()
        assert len(active) + len(inactive) == total
        for f in active:
            assert f.is_active == True
        for f in inactive:
            assert f.is_active == False
        print(f"✅ Factor SQL filter: {len(active)} active, {len(inactive)} inactive out of {total}")

    def test_factor_is_active_at(self, session):
        """Test Factor.is_active_at() method from DateRangeMixin."""
        factor = session.query(Factor).first()
        if not factor:
            pytest.skip("No factors in database")
        assert factor.is_active_at(datetime(2000, 1, 1)) == False
        assert factor.is_active_at() == factor.is_active
        print(f"✅ Factor.is_active_at() works: past=False, now={factor.is_active}")


class TestFormulaModel:
    """Test Formula model - charging formulas for resource types."""

    def test_formula_count(self, session):
        """Test that we can query formulas."""
        formula_count = session.query(Formula).count()
        assert formula_count >= 0, "Should be able to count formulas"
        print(f"✅ Found {formula_count} formulas")

    def test_formula_query(self, session):
        """Test querying and accessing formula properties."""
        formula = session.query(Formula).first()
        if not formula:
            pytest.skip("No formulas in database")

        assert formula.formula_id is not None
        assert formula.formula_name is not None
        assert formula.formula_str is not None
        assert formula.resource_type_id is not None
        assert formula.start_date is not None
        print(f"✅ Formula: {formula.formula_name}")
        print(f"   {formula.formula_str[:80]}...")

    def test_formula_resource_type_relationship(self, session):
        """Test Formula -> ResourceType relationship."""
        formula = session.query(Formula).first()
        if not formula:
            pytest.skip("No formulas in database")

        assert formula.resource_type is not None
        assert isinstance(formula.resource_type, ResourceType)
        print(f"✅ Formula {formula.formula_name} → ResourceType {formula.resource_type.resource_type}")

    def test_resource_type_to_formulas(self, session):
        """Test reverse relationship: ResourceType -> [Formulas]."""
        formula = session.query(Formula).first()
        if not formula:
            pytest.skip("No formulas in database")

        resource_type = formula.resource_type
        assert hasattr(resource_type, 'formulas')
        assert len(resource_type.formulas) > 0
        assert formula in resource_type.formulas
        print(f"✅ ResourceType {resource_type.resource_type} has {len(resource_type.formulas)} formula(s)")

    def test_formula_variables_property(self, session):
        """Test formula.variables property extracts @{variable} names."""
        formula = session.query(Formula).first()
        if not formula:
            pytest.skip("No formulas in database")

        variables = formula.variables
        assert isinstance(variables, list)

        if variables:
            print(f"✅ Formula {formula.formula_name} uses variables: {', '.join(variables)}")
            for var in variables:
                assert f"@{{{var}}}" in formula.formula_str
        else:
            print(f"✅ Formula {formula.formula_name} has no variables")

    def test_formula_is_active_property(self, session):
        """Test formula.is_active property for date-based validity."""
        formula = session.query(Formula).first()
        if not formula:
            pytest.skip("No formulas in database")

        assert isinstance(formula.is_active, bool)

        if formula.end_date is None and formula.start_date < datetime.now():
            assert formula.is_active == True
            print(f"✅ Formula {formula.formula_name} is active (no end_date)")

    def test_formula_timestamps(self, session):
        """Test that formulas have timestamp fields."""
        formula = session.query(Formula).first()
        if not formula:
            pytest.skip("No formulas in database")

        assert hasattr(formula, 'creation_time')
        assert hasattr(formula, 'modified_time')
        assert formula.creation_time is not None
        print(f"✅ Formula has timestamps: created={formula.creation_time}")

    def test_formula_is_active_sql_filter(self, session):
        """Test Formula.is_active hybrid works in SQL queries."""
        active = session.query(Formula).filter(Formula.is_active).all()
        inactive = session.query(Formula).filter(~Formula.is_active).all()
        total = session.query(Formula).count()
        assert len(active) + len(inactive) == total
        for f in active:
            assert f.is_active == True
        for f in inactive:
            assert f.is_active == False
        print(f"✅ Formula SQL filter: {len(active)} active, {len(inactive)} inactive out of {total}")

    def test_formula_is_active_at(self, session):
        """Test Formula.is_active_at() method from DateRangeMixin."""
        formula = session.query(Formula).first()
        if not formula:
            pytest.skip("No formulas in database")
        assert formula.is_active_at(datetime(2000, 1, 1)) == False
        assert formula.is_active_at() == formula.is_active
        print(f"✅ Formula.is_active_at() works: past=False, now={formula.is_active}")


class TestChargingIntegration:
    """Integration tests spanning Factor, Formula, and ResourceType."""

    def test_resource_type_charging_setup(self, session):
        """Test complete charging setup for a resource type."""
        resource_type = session.query(ResourceType).join(ResourceType.factors).first()
        if not resource_type:
            pytest.skip("No resource types with factors")

        print(f"\n✅ Resource Type: {resource_type.resource_type}")
        print(f"   Factors: {len(resource_type.factors)}")
        for factor in resource_type.factors:
            print(f"     - {factor.factor_name}: {factor.value} (active={factor.is_active})")

        print(f"   Formulas: {len(resource_type.formulas)}")
        for formula in resource_type.formulas:
            print(f"     - {formula.formula_name}: {formula.formula_str[:50]}...")
