"""Fixtures shared across the XRAS tests."""

import pytest


@pytest.fixture
def mapped_resource(session):
    """A resource carrying an ``xras_resource_repository_key_resource`` row.

    Only 13 such rows exist in production and 11 active resources have none,
    so the unmapped case the tests below exercise is a live failure mode
    rather than a defensive branch.
    """
    from factories import make_xras_key_mapping
    return make_xras_key_mapping(session)
