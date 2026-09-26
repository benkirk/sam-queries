"""D4: a tree-ancestor lead reaches the Manage Project page of a sub-project,
as they already reach its invitation routes -- and every tab on it."""

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
                             Permission.VIEW_PROJECTS, Permission.VIEW_PROJECT_MEMBERS,
                             Permission.EDIT_PROJECT_MEMBERS)}
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


class TestEveryTabFollowsThePage:
    """The page admitted the ancestor lead while its lazy-loaded tabs still
    answered 403, which the browser shows as a spinner that never settles."""

    def test_the_allocations_tab(self, lead_only_client, led_child):
        _, child = led_child
        resp = lead_only_client.get(f'/admin/htmx/project-allocation-tree/{child}')
        assert resp.status_code == 200

    def test_the_details_tab_linked_elements(self, lead_only_client, led_child):
        _, child = led_child
        resp = lead_only_client.get(f'/admin/htmx/project/{child}/linked-elements')
        assert resp.status_code == 200

    def test_the_members_tab_with_its_add_button(self, lead_only_client, led_child):
        _, child = led_child
        resp = lead_only_client.get(f'/project-members/{child}')
        assert resp.status_code == 200
        assert 'htmx_add_member_form' in resp.get_data(as_text=True) or \
               f'/project-members/{child}/add-form' in resp.get_data(as_text=True)
        assert lead_only_client.get(f'/project-members/{child}/add-form').status_code == 200

    def test_a_stranger_still_gets_403(self, lead_only_client, session):
        """The walk grants ancestors, not everyone: a project benkirk neither
        leads, administers, belongs to nor governs from above stays closed."""
        from sam.core.users import User
        from sam.projects.projects import Project
        me = User.get_by_username(session, 'benkirk')
        mine = {p.project_id for p in me.all_projects}
        stranger = None
        for p in session.query(Project).filter(Project.is_active).order_by(Project.project_id):
            if p.project_id in mine:
                continue
            node, governed = p, False
            while node is not None:
                if node.project_lead_user_id == me.user_id or node.project_admin_user_id == me.user_id:
                    governed = True
                    break
                node = node.parent
            if not governed:
                stranger = p.projcode
                break
        assert stranger is not None
        assert lead_only_client.get(f'/project-members/{stranger}').status_code == 403
        assert lead_only_client.get(f'/admin/htmx/project-allocation-tree/{stranger}').status_code == 403
