"""Admin -> Configuration: the allocation type guide card (XRAS_SUBMISSION_SPRINT2.md § 4).

HTTP-layer only (auth, render, validation re-render, the duplicate refusal): route handlers
commit through ``db.session``, so no happy-path write runs here.
"""

import secrets

import pytest

from sam.accounting.allocation_type_guide import AllocationTypeGuide
from sam.schemas.forms import EditAllocationTypeGuideForm
from webapp.utils.rbac import Permission

CARD = '/admin/htmx/allocation-guides'


@pytest.fixture
def config_only_client(auth_client, monkeypatch):
    """`benkirk` with VIEW_SYSTEM_CONFIG but without SYSTEM_ADMIN."""
    from webapp.utils import rbac
    real = rbac.get_user_permissions

    def _without(user):
        kept = {p for p in real(user) if p is not Permission.SYSTEM_ADMIN}
        kept.add(Permission.VIEW_SYSTEM_CONFIG)
        return kept

    monkeypatch.setattr(rbac, 'get_user_permissions', _without)
    return auth_client


@pytest.fixture
def a_guide(engine):
    """A committed row on an XRAS type id no seed names (routes read through ``db.session``)."""
    from sqlalchemy import delete
    from sqlalchemy.orm import Session

    xras_type = 10_000_000 + secrets.randbelow(10_000_000) * 10
    with Session(engine) as s:
        row = AllocationTypeGuide.create(s, xras_allocation_type_id=xras_type,
                                         title=f'Probe {xras_type}', created_by='t',
                                         funding='Any funding source')
        s.commit()
        gid, title = row.allocation_type_guide_id, row.title
    yield type('Guide', (), {'id': gid, 'title': title, 'xras_type': xras_type})
    with engine.begin() as conn:
        conn.execute(delete(AllocationTypeGuide).where(
            AllocationTypeGuide.allocation_type_guide_id == gid))


class TestTheCard:

    def test_it_renders_its_rows_for_an_admin(self, auth_client, a_guide):
        html = auth_client.get(f'{CARD}?active_only=1').get_data(as_text=True)
        assert a_guide.title in html and 'editAllocationGuideModal' in html
        assert "(XRAS's description)" in html, 'an empty summary says what stands in'

    def test_the_configuration_page_hosts_it(self, auth_client):
        html = auth_client.get('/admin/configuration').get_data(as_text=True)
        assert 'allocationGuidesSection' in html
        assert 'id="editAllocationGuideModal"' in html

    def test_view_system_config_reads_but_cannot_write(self, config_only_client, a_guide):
        html = config_only_client.get(CARD).get_data(as_text=True)
        assert a_guide.title in html and 'editAllocationGuideModal' not in html
        assert config_only_client.post(f'/admin/htmx/allocation-guide-edit/{a_guide.id}',
                                       data={}).status_code == 403
        assert config_only_client.delete(
            f'/admin/htmx/allocation-guide-delete/{a_guide.id}').status_code == 403

    def test_anonymous_is_refused(self, client):
        assert client.get(CARD).status_code in (302, 401, 403)


class TestTheForms:

    def test_edit_form_renders(self, auth_client, a_guide):
        html = auth_client.get(f'/admin/htmx/allocation-guide-edit-form/{a_guide.id}').get_data(
            as_text=True)
        assert 'name="summary"' in html and f'XRAS type {a_guide.xras_type}' in html

    @pytest.mark.parametrize('data, says', [
        ({'title': ''}, 'title'), ({'title': 'T', 'audience': 'everyone'}, 'audience')])
    def test_a_bad_field_rerenders(self, auth_client, a_guide, data, says):
        resp = auth_client.post(f'/admin/htmx/allocation-guide-edit/{a_guide.id}', data=data)
        assert resp.status_code == 200 and says in resp.get_data(as_text=True).lower()

    def test_a_second_row_for_a_type_is_refused(self, auth_client, a_guide):
        resp = auth_client.post('/admin/htmx/allocation-guide-create', data={
            'xras_allocation_type_id': a_guide.xras_type, 'title': 'Again'})
        assert 'already has a guide row' in resp.get_data(as_text=True)

    def test_create_form_renders(self, auth_client):
        html = auth_client.get('/admin/htmx/allocation-guide-create-form').get_data(as_text=True)
        assert 'name="xras_allocation_type_id"' in html and 'name="title"' in html


class TestTheSchema:

    def test_empty_text_clears_and_audience_is_checked(self):
        from marshmallow import ValidationError
        data = EditAllocationTypeGuideForm().load({'title': 'T', 'summary': '', 'audience': ''})
        assert data['summary'] is None and data['audience'] is None
        with pytest.raises(ValidationError):
            EditAllocationTypeGuideForm().load({'title': 'T', 'audience': 'everyone'})
