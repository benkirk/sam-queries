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


@pytest.fixture
def view_only_client(auth_client, monkeypatch):
    """`benkirk` minus MANAGE_XRAS: no snapshot user holds VIEW_XRAS alone, so it is simulated.

    Every gate in the request path (``require_permission``, the ``has_permission`` template
    global) reads through ``get_user_permissions``.
    """
    from webapp.utils import rbac
    from webapp.utils.rbac import Permission
    real = rbac.get_user_permissions

    def limited(user, *args, **kwargs):
        return {p for p in real(user, *args, **kwargs) if p != Permission.MANAGE_XRAS}

    monkeypatch.setattr(rbac, 'get_user_permissions', limited)
    return auth_client
