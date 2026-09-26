"""D4: a tree-ancestor lead reaches the Manage Project page of a sub-project,
as they already reach its invitation routes."""

import pytest

from webapp.utils.rbac import Permission


@pytest.fixture
def lead_only_client(auth_client, monkeypatch):
    """benkirk with every project-wide permission stripped: a plain lead."""
    from webapp.utils import rbac
    real = rbac.get_user_permissions

    def _without(user):
        return {p for p in real(user)
                if p not in (Permission.EDIT_PROJECTS, Permission.SYSTEM_ADMIN,
                             Permission.VIEW_PROJECTS)}
    monkeypatch.setattr(rbac, 'get_user_permissions', _without)
    return auth_client


@pytest.fixture
def led_child(session):
    """``(parent projcode, child projcode)``: an active sub-project under a
    project benkirk leads, from the snapshot. Skips when the snapshot has none."""
    from sam.core.users import User
    from sam.projects.projects import Project
    me = User.get_by_username(session, 'benkirk')
    led = [p.project_id for p in session.query(Project)
           .filter(Project.project_lead_user_id == me.user_id, Project.is_active)]
    child = (session.query(Project)
             .filter(Project.parent_id.in_(led), Project.is_active,
                     Project.project_lead_user_id != me.user_id)
             .order_by(Project.project_id).first()) if led else None
    if child is None:
        pytest.skip('no sub-project led by someone else under a project benkirk leads')
    return child.parent.projcode, child.projcode


def test_an_ancestor_lead_reaches_the_child_edit_page(lead_only_client, led_child):
    parent, child = led_child
    assert lead_only_client.get(f'/admin/project/{parent}/edit').status_code == 200
    assert lead_only_client.get(f'/admin/project/{child}/edit').status_code == 200
