"""Admin -> Users & Groups -> Group masks, the adhoc card's controls and the pinned form:
auth, render smoke and the refusal paths. The write path is covered at the manage layer
(tests/unit/manage/test_group_masks.py). Route reads go through db.session, which sees
only committed rows: the snapshot's ``csg`` (gid 1564) pins the form, and one really
committed mask row (deleted afterwards) feeds the table."""
import os
from datetime import datetime

import pytest

PAGE = '/admin/users/group-masks'
TABLE = '/admin/htmx/users/group-masks'
FORM = '/admin/htmx/group-mask-form'
CREATE = '/admin/htmx/group-mask-create'
CSG_GID = 1564


@pytest.fixture
def committed_mask(app):
    """One open adhoc mask on csg, really committed (no entry rows are touched)."""
    from sam.core.groups import UnixGroupMask
    from webapp.extensions import db
    with app.app_context():
        row = UnixGroupMask.create(db.session, unix_gid=CSG_GID, kind='adhoc', username='zz-mask-test',
                                   op='add', requested_by='test', note='RC-00000 smoke')
        db.session.commit()
        mask_id = row.mask_id
    yield mask_id
    with app.app_context():
        db.session.query(UnixGroupMask).filter(UnixGroupMask.mask_id == mask_id).delete()
        db.session.commit()


class TestAuth:
    @pytest.mark.parametrize('path', [PAGE, TABLE, FORM])
    def test_unauthenticated(self, client, path):
        if os.getenv('DISABLE_AUTH') == '1':
            pytest.skip('Auth disabled in dev environment')
        assert client.get(path).status_code in (302, 401)

    @pytest.mark.parametrize('path', [PAGE, TABLE, FORM])
    def test_non_admin_denied(self, non_admin_client, path):
        assert non_admin_client.get(path).status_code == 403

    def test_non_admin_cannot_open_or_withdraw(self, non_admin_client):
        assert non_admin_client.post(CREATE, data={'op': 'add'}).status_code == 403
        assert non_admin_client.post('/admin/htmx/group-mask-withdraw/1').status_code == 403


class TestPages:
    def test_the_tab_renders(self, auth_client):
        html = auth_client.get(PAGE).get_data(as_text=True)
        assert 'Group masks' in html and 'groupMaskModal' in html
        assert 'reloadGroupMasks from:body' in html

    def test_the_table_lists_a_mask_with_its_state(self, auth_client, committed_mask):
        html = auth_client.get(TABLE, query_string={'active_only': '1', 'q': 'zz-mask'}).get_data(as_text=True)
        assert 'csg' in html and 'Pending' in html and 'RC-00000 smoke' in html
        assert 'no upid' in html, 'a username no user holds can never be confirmed'
        assert 'no ticket' in html, 'nothing was filed for it'
        assert 'Withdraw' in html and 'New mask' in html
        html = auth_client.get(TABLE, query_string={'active_only': '1', 'q': 'zz-mask', 'flag': 'no upid'}).get_data(as_text=True)
        assert 'zz-mask-test' in html
        html = auth_client.get(TABLE, query_string={'active_only': '1', 'q': 'zz-mask', 'flag': 'ticket closed'}).get_data(as_text=True)
        assert 'zz-mask-test' not in html
        html = auth_client.get(TABLE, query_string={'active_only': '1', 'kind': 'project', 'q': 'zz-mask'}).get_data(as_text=True)
        assert 'zz-mask-test' not in html

    def test_the_pending_list_for_one_gid(self, auth_client, committed_mask):
        html = auth_client.get('/admin/htmx/group-masks/pending', query_string={'gid': CSG_GID}).get_data(as_text=True)
        assert 'zz-mask-test' in html and 'Withdraw' in html
        html = auth_client.get('/admin/htmx/group-masks/pending', query_string={'gid': 1}).get_data(as_text=True)
        assert 'No pending changes' in html
        assert auth_client.get('/admin/htmx/group-masks/pending').status_code == 200

    def test_the_group_card_carries_the_controls(self, auth_client):
        html = auth_client.get('/admin/group/csg').get_data(as_text=True)
        assert 'Add member' in html and 'Remove member' in html
        assert 'Pending changes' in html and 'group-masks/pending?gid=1564' in html


class TestForm:
    def test_unpinned_form_offers_one_picker_and_a_members_box(self, auth_client):
        html = auth_client.get(FORM).get_data(as_text=True)
        assert 'New group mask' in html
        assert 'search/mask-targets' in html and 'search/mask-members' in html
        assert 'search/groups-fk' not in html and 'projects-for-mask' not in html
        assert 'id="groupMaskMembers"' in html and 'data-fk-append="#groupMaskMembers"' in html
        assert 'name="op"' in html

    def test_pinned_form_fixes_the_group_and_op(self, auth_client):
        html = auth_client.get(FORM, query_string={'gid': CSG_GID, 'op': 'remove'}).get_data(as_text=True)
        assert 'Remove members from csg' in html and f'csg (gid {CSG_GID}, adhoc)' in html
        assert f'value="{CSG_GID}"' in html and 'value="remove"' in html
        assert 'search/mask-targets' not in html and 'data-fk-append="#groupMaskMembers"' in html

    def test_unknown_gid_falls_back_to_unpinned(self, auth_client):
        html = auth_client.get(FORM, query_string={'gid': 424242424}).get_data(as_text=True)
        assert 'New group mask' in html

    def test_a_pinned_refusal_re_renders_the_form(self, auth_client):
        resp = auth_client.post(CREATE, data={'unix_gid': CSG_GID, 'pinned': '1',
                                              'username': 'no-such-user-xyz', 'op': 'add'})
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'No user named' in html and f'value="{CSG_GID}"' in html

    def test_every_bad_line_renders_under_the_box_and_the_chip_survives(self, auth_client):
        resp = auth_client.post(CREATE, data={'unix_gid': CSG_GID, 'op': 'add',
                                              'usernames': 'no-such-user-xyz\nnot a name\nno-such-user-xyz'})
        html = resp.get_data(as_text=True)
        assert resp.status_code == 200
        assert 'No user named' in html and 'no-such-user-xyz' in html      # the quotes are escaped
        assert 'line 2: expected one username' in html and 'line 3: duplicate of line 1' in html
        assert 'form-control is-invalid' in html.split('id="groupMaskMembers"')[0].rsplit('<textarea', 1)[1]
        assert f'value="{CSG_GID}"' in html and f'csg (gid {CSG_GID}, adhoc)' in html, 'the chip survives'
        assert 'no-such-user-xyz\nnot a name' in html, 'the box keeps the operator\'s lines'

    def test_past_the_cap_no_name_is_checked(self, auth_client):
        from sam.manage.groups import MASK_BATCH_MAX
        names = '\n'.join(f'no-such-user-{i}' for i in range(MASK_BATCH_MAX + 1))
        html = auth_client.post(CREATE, data={'unix_gid': CSG_GID, 'op': 'add',
                                              'usernames': names}).get_data(as_text=True)
        assert f'At most {MASK_BATCH_MAX} members' in html and 'No user named' not in html

    def test_nothing_picked_is_refused(self, auth_client):
        html = auth_client.post(CREATE, data={'op': 'add', 'usernames': 'benkirk'}).get_data(as_text=True)
        assert 'Pick a group or a project' in html

    def test_withdraw_of_a_missing_mask_is_404(self, auth_client):
        assert auth_client.post('/admin/htmx/group-mask-withdraw/999999999').status_code == 404

    def test_edit_form_renders(self, auth_client, committed_mask):
        html = auth_client.get(f'/admin/htmx/group-mask-edit-form/{committed_mask}').get_data(as_text=True)
        assert 'add zz-mask-test' in html and 'name="expires_at"' in html


class TestPickers:
    def test_target_picker_mixes_adhoc_groups_and_projects(self, auth_client):
        html = auth_client.get('/admin/htmx/search/mask-targets', query_string={'q': 'csg'}).get_data(as_text=True)
        assert f'data-fk-id="{CSG_GID}"' in html and f'data-fk-label="csg (gid {CSG_GID}, adhoc)"' in html
        html = auth_client.get('/admin/htmx/search/mask-targets', query_string={'q': 'SCSG0001'}).get_data(as_text=True)
        assert ', project)"' in html and '>project<' in html
        html = auth_client.get('/admin/htmx/search/mask-targets', query_string={'q': str(CSG_GID)}).get_data(as_text=True)
        assert f'data-fk-id="{CSG_GID}"' in html

    def test_member_picker_labels_bare_usernames(self, auth_client):
        html = auth_client.get('/admin/htmx/search/mask-members', query_string={'q': 'benkirk'}).get_data(as_text=True)
        assert 'data-fk-label="benkirk"' in html
