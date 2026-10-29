"""Admin -> Configuration: the request-limits card and the XRAS tile.

HTTP-layer only (auth, render, validation re-render, the duplicate-pair refusal):
route handlers commit through ``db.session``, so no happy-path write runs here.
"""

import os

import pytest

from sam.accounting.allocation_type_limits import AllocationTypeResourceLimit
from sam.schemas.forms import EditAllocationLimitForm
from webapp.utils.rbac import Permission

CARD = '/admin/htmx/allocation-limits'


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
def a_limit(engine):
    """A committed row on a pair the seed never names (routes read through ``db.session``)."""
    from sqlalchemy import delete
    from sqlalchemy.orm import Session
    from sam.accounting.allocations import AllocationType
    from sam.resources.resources import Resource

    # The row is committed and xdist workers share the DB: one resource per worker, and
    # never Derecho, which the model tests' savepoint rows use on the same type name.
    worker = int(os.environ.get('PYTEST_XDIST_WORKER', 'gw0')[2:] or 0)
    with Session(engine) as s:
        type_id = s.query(AllocationType.allocation_type_id).filter(
            AllocationType.allocation_type == 'Education').order_by(
            AllocationType.allocation_type_id.desc()).limit(1).scalar()
        resources = (s.query(Resource).filter(Resource.resource_name != 'Derecho')
                     .order_by(Resource.resource_id).all())
        if type_id is None or len(resources) <= worker:
            pytest.skip('no Education type, or too few resources for this worker')
        resource = resources[worker]
        row = AllocationTypeResourceLimit.create(
            s, allocation_type_id=type_id, resource_id=resource.resource_id,
            created_by='test', default_amount=10, max_amount=20)
        s.commit()
        lid = row.allocation_type_resource_limit_id
        found = (lid, type_id, resource.resource_id, resource.resource_name)
    yield type('Limit', (), dict(zip(
        ('allocation_type_resource_limit_id', 'allocation_type_id', 'resource_id',
         'resource_name'), found)))
    with engine.begin() as conn:
        conn.execute(delete(AllocationTypeResourceLimit).where(
            AllocationTypeResourceLimit.allocation_type_resource_limit_id == lid))


class TestTheCard:

    def test_renders_the_seeded_rows(self, auth_client, a_limit):
        html = auth_client.get(CARD + '?active_only=1').get_data(as_text=True)
        assert 'Education' in html
        assert 'editAllocationLimitModal' in html
        assert a_limit.resource_name in html

    def test_the_page_hosts_the_card_and_its_shells(self, auth_client):
        html = auth_client.get('/admin/configuration').get_data(as_text=True)
        assert 'allocationLimitsSection' in html
        assert 'id="editAllocationLimitModal"' in html

    def test_view_system_config_reads_but_cannot_write(self, config_only_client, a_limit):
        html = config_only_client.get(CARD).get_data(as_text=True)
        assert a_limit.resource_name in html
        assert 'editAllocationLimitModal' not in html
        lid = a_limit.allocation_type_resource_limit_id
        assert config_only_client.post(f'/admin/htmx/allocation-limit-edit/{lid}',
                                       data={}).status_code == 403
        assert config_only_client.delete(
            f'/admin/htmx/allocation-limit-delete/{lid}').status_code == 403

    def test_anonymous_is_refused(self, client):
        assert client.get(CARD).status_code in (302, 401, 403)


class TestTheForms:

    def test_edit_form_renders(self, auth_client, a_limit):
        lid = a_limit.allocation_type_resource_limit_id
        html = auth_client.get(f'/admin/htmx/allocation-limit-edit-form/{lid}').get_data(
            as_text=True)
        assert 'name="max_amount"' in html

    def test_bounds_out_of_order_rerender(self, auth_client, a_limit):
        lid = a_limit.allocation_type_resource_limit_id
        resp = auth_client.post(f'/admin/htmx/allocation-limit-edit/{lid}',
                                data={'min_amount': '10', 'max_amount': '5', 'active': 'y'})
        assert resp.status_code == 200
        assert 'exceeds' in resp.get_data(as_text=True)

    def test_a_second_row_for_a_pair_is_refused(self, auth_client, a_limit):
        resp = auth_client.post('/admin/htmx/allocation-limit-create', data={
            'allocation_type_id': a_limit.allocation_type_id,
            'resource_id': a_limit.resource_id, 'max_amount': '1'})
        assert 'already have a limit' in resp.get_data(as_text=True)

    def test_create_form_renders(self, auth_client):
        html = auth_client.get('/admin/htmx/allocation-limit-create-form').get_data(as_text=True)
        assert 'name="allocation_type_id"' in html and 'name="resource_id"' in html


class TestTheSchema:

    def test_empty_bounds_clear(self):
        data = EditAllocationLimitForm().load({'min_amount': '', 'max_amount': '7'})
        assert data['min_amount'] is None and data['max_amount'] == 7.0

    def test_order_is_checked(self):
        from marshmallow import ValidationError
        with pytest.raises(ValidationError):
            EditAllocationLimitForm().load({'default_amount': '9', 'max_amount': '3'})


class TestTheXrasTile:

    def test_the_submit_lever_is_shown_off_in_tests(self, auth_client):
        html = auth_client.get('/admin/htmx/configuration').get_data(as_text=True)
        assert 'XRAS_SUBMIT_ENABLED' in html and 'Users may file' in html
