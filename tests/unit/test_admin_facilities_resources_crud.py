"""HTTP-layer characterization tests for the admin Facilities + Resources
CRUD routes.

Same scope and rationale as tests/unit/test_admin_orgs_crud.py: auth,
permission, validation-error re-render, not-found behavior, render smoke —
no happy-path writes (route handlers commit through Flask-SQLAlchemy's
`db.session`). Written BEFORE the CrudSpec registrar migration
(WEBAPP_OO_REFACTOR commit 2.3) to pin current behavior.

`edit_required=False` cases (panel, allocation-type, resource-type) have
edit schemas with no required fields, so an empty POST would *succeed* and
write to the shared snapshot — those skip the invalid-POST probe.

Queue create + cleanup flows are covered by test_htmx_queue_admin.py.
"""

import os

import pytest
from sqlalchemy import func, inspect as sa_inspect

from sam.accounting.allocations import AllocationType
from sam.resources.facilities import Facility, Panel, PanelSession
from sam.resources.machines import Machine, Queue
from sam.resources.resources import Resource, ResourceType

pytestmark = pytest.mark.unit

MISSING_ID = 99999999

#: (slug, model, display name, has_delete_route, edit_has_required_fields)
CASES = [
    ('facility', Facility, 'Facility', True, True),
    ('panel', Panel, 'Panel', True, False),
    ('allocation-type', AllocationType, 'Allocation type', True, False),
    ('resource', Resource, 'Resource', True, True),       # delete is bespoke (decommission)
    ('resource-type', ResourceType, 'Resource type', True, False),
    ('machine', Machine, 'Machine', True, True),          # delete is bespoke (decommission)
    ('queue', Queue, 'Queue', True, True),                # edit/delete bespoke (cache invalidation)
]

SLUGS = [c[0] for c in CASES]


def _snapshot_id(session, model):
    pk_col = sa_inspect(model).primary_key[0]
    value = session.query(func.min(pk_col)).scalar()
    assert value is not None, f'snapshot has no {model.__name__} rows'
    return value


@pytest.mark.parametrize('slug,model,name,has_delete,edit_required',
                         CASES, ids=SLUGS)
class TestFacilitiesResourcesCrud:

    def test_edit_form_renders(self, auth_client, session, slug, model, name,
                               has_delete, edit_required):
        entity_id = _snapshot_id(session, model)
        resp = auth_client.get(f'/admin/htmx/{slug}-edit-form/{entity_id}')
        assert resp.status_code == 200
        assert 'form' in resp.get_data(as_text=True)

    def test_edit_form_missing_id_warns_at_200(self, auth_client, slug, model,
                                               name, has_delete, edit_required):
        resp = auth_client.get(f'/admin/htmx/{slug}-edit-form/{MISSING_ID}')
        assert resp.status_code == 200
        assert f'{name} not found' in resp.get_data(as_text=True)

    def test_edit_post_missing_id_404s(self, auth_client, slug, model, name,
                                       has_delete, edit_required):
        resp = auth_client.post(f'/admin/htmx/{slug}-edit/{MISSING_ID}', data={})
        assert resp.status_code == 404
        assert f'{name} not found' in resp.get_data(as_text=True)

    def test_edit_post_invalid_rerenders(self, auth_client, session, slug,
                                         model, name, has_delete, edit_required):
        if not edit_required:
            pytest.skip('edit schema has no required fields — empty POST would write')
        entity_id = _snapshot_id(session, model)
        resp = auth_client.post(f'/admin/htmx/{slug}-edit/{entity_id}', data={})
        assert resp.status_code == 200
        assert 'HX-Trigger' not in resp.headers

    def test_create_form_renders(self, auth_client, slug, model, name,
                                 has_delete, edit_required):
        resp = auth_client.get(f'/admin/htmx/{slug}-create-form')
        assert resp.status_code == 200

    def test_create_post_invalid_rerenders(self, auth_client, slug, model,
                                           name, has_delete, edit_required):
        resp = auth_client.post(f'/admin/htmx/{slug}-create', data={})
        assert resp.status_code == 200
        assert 'HX-Trigger' not in resp.headers

    def test_delete_missing_id_404s(self, auth_client, slug, model, name,
                                    has_delete, edit_required):
        if not has_delete:
            pytest.skip('entity has no delete route')
        resp = auth_client.delete(f'/admin/htmx/{slug}-delete/{MISSING_ID}')
        assert resp.status_code == 404

    def test_non_admin_forbidden(self, non_admin_client, slug, model, name,
                                 has_delete, edit_required):
        assert non_admin_client.get(
            f'/admin/htmx/{slug}-edit-form/1').status_code == 403
        assert non_admin_client.post(
            f'/admin/htmx/{slug}-edit/1', data={}).status_code == 403
        assert non_admin_client.post(
            f'/admin/htmx/{slug}-create', data={}).status_code == 403
        if has_delete:
            assert non_admin_client.delete(
                f'/admin/htmx/{slug}-delete/1').status_code == 403

    def test_unauthenticated_rejected(self, client, slug, model, name,
                                      has_delete, edit_required):
        if os.getenv('DISABLE_AUTH') == '1':
            pytest.skip('Auth disabled in dev environment')
        resp = client.post(f'/admin/htmx/{slug}-edit/1', data={})
        assert resp.status_code in (302, 401)


class TestPanelSessionEdit:
    """Panel-session has an edit pair only (no create/delete) and stays a
    bespoke handler — its cross-field check needs the loaded ORM object."""

    def test_edit_form_renders(self, auth_client, session):
        entity_id = _snapshot_id(session, PanelSession)
        resp = auth_client.get(f'/admin/htmx/panel-session-edit-form/{entity_id}')
        assert resp.status_code == 200

    def test_edit_form_missing_id_warns_at_200(self, auth_client):
        resp = auth_client.get(f'/admin/htmx/panel-session-edit-form/{MISSING_ID}')
        assert resp.status_code == 200
        assert 'Panel session not found' in resp.get_data(as_text=True)

    def test_edit_post_missing_id_404s(self, auth_client):
        resp = auth_client.post(f'/admin/htmx/panel-session-edit/{MISSING_ID}',
                                data={})
        assert resp.status_code == 404

    def test_edit_post_invalid_rerenders(self, auth_client, session):
        entity_id = _snapshot_id(session, PanelSession)
        resp = auth_client.post(f'/admin/htmx/panel-session-edit/{entity_id}',
                                data={})   # start_date is required
        assert resp.status_code == 200
        assert 'HX-Trigger' not in resp.headers

    def test_non_admin_forbidden(self, non_admin_client):
        assert non_admin_client.post(
            '/admin/htmx/panel-session-edit/1', data={}).status_code == 403
