"""Allocations > XRAS > Configuration: the request limits' forms (XRAS_SUBMISSION_SPRINT3.md A2).

HTTP-layer only (auth, render, validation re-render, the duplicate-pair refusal):
route handlers commit through ``db.session``, so no happy-path write runs here.
"""

import os

import pytest

from sam.accounting.allocation_type_limits import AllocationTypeResourceLimit
from sam.schemas.forms import EditAllocationLimitForm

URL = '/allocations/htmx/allocation-limit'


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


class TestTheGate:

    def test_view_xras_alone_cannot_open_or_post_a_form(self, view_only_client, a_limit):
        lid = a_limit.allocation_type_resource_limit_id
        assert view_only_client.get(f'{URL}-edit-form/{lid}').status_code == 403
        assert view_only_client.get(f'{URL}-create-form').status_code == 403
        assert view_only_client.post(f'{URL}-edit/{lid}', data={}).status_code == 403

    def test_there_is_no_delete_route(self, auth_client, a_limit):
        """Retiring a row is the edit form's Active box."""
        lid = a_limit.allocation_type_resource_limit_id
        assert auth_client.delete(f'{URL}-delete/{lid}').status_code in (404, 405)

    def test_anonymous_is_refused(self, client):
        assert client.get(f'{URL}-create-form').status_code in (302, 401, 403)


class TestTheForms:

    def test_edit_form_renders(self, auth_client, a_limit):
        lid = a_limit.allocation_type_resource_limit_id
        html = auth_client.get(f'{URL}-edit-form/{lid}').get_data(
            as_text=True)
        assert 'name="max_amount"' in html

    def test_bounds_out_of_order_rerender(self, auth_client, a_limit):
        lid = a_limit.allocation_type_resource_limit_id
        resp = auth_client.post(f'{URL}-edit/{lid}',
                                data={'min_amount': '10', 'max_amount': '5', 'active': 'y'})
        assert resp.status_code == 200
        assert 'exceeds' in resp.get_data(as_text=True)

    def test_a_second_row_for_a_pair_is_refused(self, auth_client, a_limit):
        resp = auth_client.post(f'{URL}-create', data={
            'allocation_type_id': a_limit.allocation_type_id,
            'resource_id': a_limit.resource_id, 'max_amount': '1'})
        assert 'already have a limit' in resp.get_data(as_text=True)

    def test_create_form_renders(self, auth_client):
        html = auth_client.get(f'{URL}-create-form').get_data(as_text=True)
        assert 'name="allocation_type_id"' in html and 'name="resource_id"' in html


class TestTheSchema:

    def test_empty_bounds_clear(self):
        data = EditAllocationLimitForm().load({'min_amount': '', 'max_amount': '7'})
        assert data['min_amount'] is None and data['max_amount'] == 7.0

    def test_order_is_checked(self):
        from marshmallow import ValidationError
        with pytest.raises(ValidationError):
            EditAllocationLimitForm().load({'default_amount': '9', 'max_amount': '3'})
