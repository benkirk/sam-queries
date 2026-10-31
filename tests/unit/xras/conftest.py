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


@pytest.fixture
def committed_guide(engine):
    """Make committed ``allocation_type_guide`` rows on XRAS type ids no seed names (a route
    reads through ``db.session``); each is deleted after the test."""
    import secrets

    from sqlalchemy import delete
    from sqlalchemy.orm import Session

    from sam.accounting.allocation_type_guide import AllocationTypeGuide
    made = []

    def make(**kw):
        xras_type = 10_000_000 + secrets.randbelow(10_000_000) * 10
        kw.setdefault('title', f'Probe {xras_type}')
        with Session(engine) as s:
            row = AllocationTypeGuide.create(s, xras_allocation_type_id=xras_type,
                                             created_by='t', **kw)
            s.commit()
            made.append(row.allocation_type_guide_id)
            return type('Guide', (), {'id': row.allocation_type_guide_id, 'title': row.title,
                                      'xras_type': xras_type})

    yield make
    with engine.begin() as conn:
        conn.execute(delete(AllocationTypeGuide).where(
            AllocationTypeGuide.allocation_type_guide_id.in_(made)))
