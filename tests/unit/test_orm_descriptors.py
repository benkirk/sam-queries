"""ORM descriptor coverage tests.

Automatically discovers every SQLAlchemy model registered with Base (sam
models) and StatusBase (system_status models), and verifies that each one
defines both __str__ and __repr__ — not just the inherited no-op versions
from object.

Ported from tests/unit/test_orm_descriptors.py with no behavioral changes.
This file does NOT hit the database — it's pure class introspection — but
it still runs under the new_tests/ harness so its coverage counts against
the unified test suite.
"""
import pytest

import sam  # noqa: F401 — side-effect import registers all sam ORM models
import system_status  # noqa: F401 — side-effect import registers system_status models
from sam.base import Base
from system_status.base import StatusBase


pytestmark = pytest.mark.unit


def _all_orm_classes():
    """Return every ORM class from all known registries, deduplicated, sorted by name."""
    classes = {mapper.class_ for mapper in Base.registry.mappers}
    classes |= {mapper.class_ for mapper in StatusBase.registry.mappers}
    return sorted(classes, key=lambda cls: cls.__name__)


def _has_repr(cls) -> bool:
    """True if the class (or any non-object ancestor) defines __repr__."""
    return cls.__repr__ is not object.__repr__


def _has_str(cls) -> bool:
    """True if the class (or any non-object ancestor) defines __str__."""
    return cls.__str__ is not object.__str__


@pytest.mark.parametrize('cls', _all_orm_classes(), ids=lambda c: c.__name__)
def test_orm_has_repr(cls):
    """Every ORM model must define a __repr__ (not just object.__repr__)."""
    assert _has_repr(cls), (
        f"{cls.__name__} is missing __repr__. "
        f"Add one following the '<ClassName(id=..., ...)>' pattern."
    )


@pytest.mark.parametrize('cls', _all_orm_classes(), ids=lambda c: c.__name__)
def test_orm_has_str(cls):
    """Every ORM model must define a __str__ (not just object.__str__)."""
    assert _has_str(cls), (
        f"{cls.__name__} is missing __str__. "
        f"Add one following the human-readable 'key (description)' pattern."
    )
