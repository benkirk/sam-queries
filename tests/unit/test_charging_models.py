"""Tests for charging infrastructure ORM models: Factor and Formula.

Ported from tests/unit/test_charging_models.py. Structural reads on
Factor + Formula including the is_active date-range hybrid. Dropped
decorative print() statements.
"""
from datetime import datetime

import pytest

from sam import Factor, Formula, ResourceType


pytestmark = pytest.mark.unit


# ============================================================================
# Factor
# ============================================================================


class TestFactorModel:

    def test_factor_count(self, session):
        assert session.query(Factor).count() >= 0

    def test_factor_query(self, session):
        factor = session.query(Factor).first()
        if not factor:
            pytest.skip("No factors in database")
        assert factor.factor_id is not None
        assert factor.factor_name is not None
        assert factor.value is not None
        assert factor.resource_type_id is not None
        assert factor.start_date is not None

    def test_factor_resource_type_relationship(self, session):
        factor = session.query(Factor).first()
        if not factor:
            pytest.skip("No factors in database")
        assert isinstance(factor.resource_type, ResourceType)

    def test_resource_type_to_factors(self, session):
        factor = session.query(Factor).first()
        if not factor:
            pytest.skip("No factors in database")
        resource_type = factor.resource_type
        assert hasattr(resource_type, 'factors')
        assert len(resource_type.factors) > 0
        assert factor in resource_type.factors

    def test_factor_is_active_property(self, session):
        factor = session.query(Factor).first()
        if not factor:
            pytest.skip("No factors in database")
        assert isinstance(factor.is_active, bool)
        if factor.end_date is None and factor.start_date < datetime.now():
            assert factor.is_active is True

    def test_factor_with_end_date_is_inactive(self, session):
        """Factors with end_date in the past must be inactive."""
        expired_factors = session.query(Factor).filter(
            Factor.end_date.isnot(None),
            Factor.end_date < datetime.now(),
        ).all()
        for factor in expired_factors:
            assert factor.is_active is False

    def test_factor_timestamps(self, session):
        factor = session.query(Factor).first()
        if not factor:
            pytest.skip("No factors in database")
        assert hasattr(factor, 'creation_time')
        assert hasattr(factor, 'modified_time')
        assert factor.creation_time is not None

    def test_factor_is_active_sql_filter(self, session):
        """SQL filter using Factor.is_active partitions the table cleanly."""
        active = session.query(Factor).filter(Factor.is_active).all()
        inactive = session.query(Factor).filter(~Factor.is_active).all()
        total = session.query(Factor).count()
        assert len(active) + len(inactive) == total
        for f in active:
            assert f.is_active is True
        for f in inactive:
            assert f.is_active is False

    def test_factor_is_active_at(self, session):
        factor = session.query(Factor).first()
        if not factor:
            pytest.skip("No factors in database")
        assert factor.is_active_at(datetime(2000, 1, 1)) is False
        assert factor.is_active_at() == factor.is_active


# ============================================================================
# Formula
# ============================================================================


class TestFormulaModel:

    def test_formula_count(self, session):
        assert session.query(Formula).count() >= 0

    def test_formula_query(self, session):
        formula = session.query(Formula).first()
        if not formula:
            pytest.skip("No formulas in database")
        assert formula.formula_id is not None
        assert formula.formula_name is not None
        assert formula.formula_str is not None
        assert formula.resource_type_id is not None
        assert formula.start_date is not None

    def test_formula_resource_type_relationship(self, session):
        formula = session.query(Formula).first()
        if not formula:
            pytest.skip("No formulas in database")
        assert isinstance(formula.resource_type, ResourceType)

    def test_resource_type_to_formulas(self, session):
        formula = session.query(Formula).first()
        if not formula:
            pytest.skip("No formulas in database")
        resource_type = formula.resource_type
        assert hasattr(resource_type, 'formulas')
        assert len(resource_type.formulas) > 0
        assert formula in resource_type.formulas

    def test_formula_variables_property(self, session):
        """Formula.variables must be a list, and each variable must appear
        in the formula_str via the @{name} marker syntax."""
        formula = session.query(Formula).first()
        if not formula:
            pytest.skip("No formulas in database")
        variables = formula.variables
        assert isinstance(variables, list)
        for var in variables:
            assert f"@{{{var}}}" in formula.formula_str

    def test_formula_is_active_property(self, session):
        formula = session.query(Formula).first()
        if not formula:
            pytest.skip("No formulas in database")
        assert isinstance(formula.is_active, bool)
        if formula.end_date is None and formula.start_date < datetime.now():
            assert formula.is_active is True

    def test_formula_timestamps(self, session):
        formula = session.query(Formula).first()
        if not formula:
            pytest.skip("No formulas in database")
        assert hasattr(formula, 'creation_time')
        assert hasattr(formula, 'modified_time')
        assert formula.creation_time is not None

    def test_formula_is_active_sql_filter(self, session):
        active = session.query(Formula).filter(Formula.is_active).all()
        inactive = session.query(Formula).filter(~Formula.is_active).all()
        total = session.query(Formula).count()
        assert len(active) + len(inactive) == total
        for f in active:
            assert f.is_active is True
        for f in inactive:
            assert f.is_active is False

    def test_formula_is_active_at(self, session):
        formula = session.query(Formula).first()
        if not formula:
            pytest.skip("No formulas in database")
        assert formula.is_active_at(datetime(2000, 1, 1)) is False
        assert formula.is_active_at() == formula.is_active
