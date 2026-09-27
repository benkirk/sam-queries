"""The Roles & access page (MANAGE_ROLES) and its Configuration tile.

The gate this module exists for is the negative case: VIEW_SYSTEM_CONFIG gets
the tile and must be 403'd off every route here. Writes are covered at the
model layer; the HTTP tests here never commit a row.
"""

import pytest

from webapp.utils.rbac import Permission

PAGE = '/admin/roles'
GRANTS = '/admin/htmx/roles/grants'
GRANT_FORM = '/admin/htmx/roles/grants/form'
ROLES = '/admin/htmx/roles/roles'
NEW_ROLE = '/admin/htmx/roles/roles/form'
EDIT_ROLE = '/admin/htmx/roles/roles/no-such-role/form'
CHECK = '/admin/htmx/roles/check'


@pytest.fixture
def config_only_client(auth_client, monkeypatch):
    """`benkirk` with VIEW_SYSTEM_CONFIG but without MANAGE_ROLES or SYSTEM_ADMIN."""
    from webapp.utils import rbac
    real = rbac.get_user_permissions

    def _without(user):
        kept = {p for p in real(user) if p not in (Permission.SYSTEM_ADMIN, Permission.MANAGE_ROLES)}
        kept.add(Permission.VIEW_SYSTEM_CONFIG)
        return kept

    monkeypatch.setattr(rbac, 'get_user_permissions', _without)
    return auth_client


class TestThePermissionBoundary:

    @pytest.mark.parametrize('path', [PAGE, GRANTS, GRANT_FORM, ROLES, NEW_ROLE, CHECK])
    def test_view_system_config_alone_is_refused(self, config_only_client, path):
        assert config_only_client.get(path).status_code == 403

    @pytest.mark.parametrize('path', [GRANTS, ROLES, ROLES + '/csg'])
    def test_view_system_config_alone_cannot_post(self, config_only_client, path):
        assert config_only_client.post(path, data={'name': 'x'}).status_code == 403

    def test_view_system_config_alone_cannot_revoke(self, config_only_client):
        assert config_only_client.delete(f'{GRANTS}/1').status_code == 403

    @pytest.mark.parametrize('path', [PAGE, GRANTS, ROLES, CHECK])
    def test_anonymous_is_refused(self, client, path):
        assert client.get(path).status_code in (302, 401, 403)

    def test_the_tile_is_reachable_at_the_lower_tier_without_the_link(self, config_only_client):
        resp = config_only_client.get('/admin/htmx/configuration')
        assert resp.status_code == 200
        assert b'Roles &amp; access' in resp.data
        assert PAGE.encode() not in resp.data

    def test_the_tile_links_for_a_holder(self, auth_client):
        resp = auth_client.get('/admin/htmx/configuration')
        assert resp.status_code == 200
        assert PAGE.encode() in resp.data


class TestThePage:

    def test_it_renders_with_its_tabs(self, auth_client):
        resp = auth_client.get(PAGE)
        assert resp.status_code == 200
        for key in (b'grants-pane', b'roles-pane', b'check-pane'):
            assert key in resp.data

    def test_a_deep_link_selects_the_tab(self, auth_client):
        html = auth_client.get(PAGE + '?tab=roles').get_data(as_text=True)
        assert 'nav-link active" id="roles-tab"' in html

    def test_the_grants_card_renders_with_the_add_form(self, auth_client):
        html = auth_client.get(GRANTS).get_data(as_text=True)
        assert 'Add a grant' in html and 'name="subject_type"' in html
        assert 'grantSubjectUser_search' in html

    @pytest.mark.parametrize('kind, marker', [('group', 'name="subject_name"'),
                                              ('apikey', 'id="grant-subject-key"')])
    def test_the_subject_type_cascade_swaps_the_widget(self, auth_client, kind, marker):
        html = auth_client.get(GRANT_FORM + f'?subject_type={kind}').get_data(as_text=True)
        assert marker in html
        assert 'grantSubjectUser_search' not in html

    def test_the_roles_card_and_the_new_role_editor_render(self, auth_client):
        assert auth_client.get(ROLES).status_code == 200
        html = auth_client.get(NEW_ROLE).get_data(as_text=True)
        assert 'name="permissions"' in html and 'value="view_projects"' in html
        assert 'name="active"' not in html

    def test_editing_an_unknown_role_is_404(self, auth_client):
        assert auth_client.get(EDIT_ROLE).status_code == 404
        assert auth_client.post(ROLES + '/no-such-role', data={'name': 'x'}).status_code == 404

    def test_check_before_and_after_a_search(self, auth_client):
        assert auth_client.get(CHECK).status_code == 200
        html = auth_client.get(CHECK + '?subject=zz-nobody-here').get_data(as_text=True)
        assert 'holds no grant' in html


class TestValidationRerenders:
    """A refused POST comes back as the form with the reason, and writes nothing."""

    def test_no_user_picked(self, auth_client):
        resp = auth_client.post(GRANTS, data={'subject_type': 'user', 'role_name': 'x'})
        assert resp.status_code == 200
        assert b'Pick a user' in resp.data

    def test_role_and_permission_both_given(self, auth_client):
        resp = auth_client.post(GRANTS, data={'subject_type': 'group', 'subject_name': 'zz-no-group',
                                              'role_name': 'x', 'permission': 'view_users'})
        assert b'Pick a POSIX group' in resp.data

    def test_unknown_permission_is_a_field_error(self, auth_client):
        resp = auth_client.post(GRANTS, data={'subject_type': 'apikey', 'subject_name': 'zz',
                                              'permission': 'no_such'})
        assert resp.status_code == 200
        assert b'Must be one of' in resp.data

    def test_unknown_facility(self, auth_client, session):
        from sam.core.groups import AdhocGroup
        group = session.query(AdhocGroup).filter(AdhocGroup.is_active).first()
        if group is None:
            pytest.skip('no active adhoc group in the snapshot')
        resp = auth_client.post(GRANTS, data={'subject_type': 'group', 'subject_name': group.group_name,
                                              'permission': 'view_users',
                                              'facility_name': 'ZZ-NOWHERE'})
        assert b'No facility named' in resp.data

    def test_scoped_api_key_grant_is_refused_by_the_model(self, auth_client):
        resp = auth_client.post(GRANTS, data={'subject_type': 'apikey', 'subject_name': 'collector',
                                              'permission': 'view_users', 'facility_name': 'UNIV'})
        assert resp.status_code == 200
        assert b'never facility-scoped' in resp.data

    def test_bad_role_name(self, auth_client):
        resp = auth_client.post(ROLES, data={'name': 'has space'})
        assert resp.status_code == 200
        assert b'Letters, digits' in resp.data

    def test_unknown_parent(self, auth_client):
        resp = auth_client.post(ROLES, data={'name': 'zz-new-role', 'extends': 'zz-no-parent'})
        assert b'No active role named' in resp.data
