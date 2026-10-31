"""Allocations > XRAS > Configuration: the guide rows' forms (XRAS_SUBMISSION_SPRINT3.md A2).

HTTP-layer only (auth, render, validation re-render, the duplicate refusal): route handlers
commit through ``db.session``, so no happy-path write runs here.
"""

import secrets

import pytest

from sam.accounting.allocation_type_guide import AllocationTypeGuide
from sam.schemas.forms import EditAllocationTypeGuideForm
from sam.xras.audiences import AUDIENCE_LABELS

URL = '/allocations/htmx/allocation-guide'


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


class TestTheGate:

    def test_view_xras_alone_cannot_open_or_post_a_form(self, view_only_client, a_guide):
        assert view_only_client.get(f'{URL}-edit-form/{a_guide.id}').status_code == 403
        assert view_only_client.get(f'{URL}-create-form').status_code == 403
        assert view_only_client.post(f'{URL}-edit/{a_guide.id}', data={}).status_code == 403

    def test_there_is_no_delete_route(self, auth_client, a_guide):
        """Retiring a row is the edit form's Active box."""
        assert auth_client.delete(f'{URL}-delete/{a_guide.id}').status_code in (404, 405)

    def test_anonymous_is_refused(self, client, a_guide):
        assert client.get(f'{URL}-edit-form/{a_guide.id}').status_code in (302, 401, 403)


class TestTheForms:

    def test_edit_form_renders(self, auth_client, a_guide):
        html = auth_client.get(f'{URL}-edit-form/{a_guide.id}').get_data(
            as_text=True)
        assert 'name="summary"' in html and f'XRAS type {a_guide.xras_type}' in html
        assert 'name="allocation_type"' in html and '<option value="Everyone"' not in html

    def test_the_sam_type_is_a_select_of_the_active_types(self, auth_client):
        from sam.accounting.allocations import AllocationType
        from webapp.extensions import db
        with auth_client.application.app_context():
            a_name = db.session.query(AllocationType.allocation_type).filter(
                AllocationType.is_active).order_by(AllocationType.allocation_type).first()[0]
        html = auth_client.get(f'{URL}-create-form').get_data(as_text=True)
        select = html[html.index('name="allocation_type"'):]
        select = select[:select.index('</select>')]
        assert f'<option value="{a_name}"' in select and AUDIENCE_LABELS['ncar'] in html

    @pytest.mark.parametrize('data, says', [
        ({'title': ''}, 'title'), ({'title': 'T', 'audience': 'everyone'}, 'audience')])
    def test_a_bad_field_rerenders(self, auth_client, a_guide, data, says):
        resp = auth_client.post(f'{URL}-edit/{a_guide.id}', data=data)
        assert resp.status_code == 200 and says in resp.get_data(as_text=True).lower()

    def test_a_second_row_for_a_type_is_refused(self, auth_client, a_guide):
        resp = auth_client.post(f'{URL}-create', data={
            'xras_allocation_type_id': a_guide.xras_type, 'title': 'Again'})
        assert 'already has a guide row' in resp.get_data(as_text=True)

    def test_create_form_renders(self, auth_client):
        html = auth_client.get(f'{URL}-create-form').get_data(as_text=True)
        assert 'name="xras_allocation_type_id"' in html and 'name="title"' in html


class TestTheSchema:

    def test_empty_text_clears_and_audience_is_checked(self):
        from marshmallow import ValidationError
        data = EditAllocationTypeGuideForm().load({'title': 'T', 'summary': '', 'audience': ''})
        assert data['summary'] is None and data['audience'] is None
        with pytest.raises(ValidationError):
            EditAllocationTypeGuideForm().load({'title': 'T', 'audience': 'everyone'})
