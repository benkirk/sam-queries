"""HTTP-layer tests for the hand-rolled handlers migrated to
`HtmxFormHandler` subclasses (WEBAPP_OO_REFACTOR commit 2.4).

Same scope as the CRUD characterization files: validation-error
re-render, domain-error (FormError/FK) paths, and auth — no happy-path
writes (routes commit through Flask-SQLAlchemy's `db.session`). Every
error-path assertion here exercises the full handler lifecycle:
form_input → load → clean → render_errors with the route's own context.
"""

import pytest
from sqlalchemy import func

from sam.accounting.accounts import Account
from sam.accounting.allocations import Allocation
from sam.projects.projects import Project

pytestmark = pytest.mark.unit

MISSING_ID = 99999999


@pytest.fixture
def snapshot_projcode(session):
    """A committed, active project the route handlers can see."""
    projcode = (
        session.query(Project.projcode)
        .filter(Project.is_active)
        .order_by(Project.project_id)
        .limit(1)
        .scalar()
    )
    assert projcode, 'snapshot has no active projects'
    return projcode


@pytest.fixture
def snapshot_allocation_id(session):
    """A committed, non-deleted allocation on a non-deleted account."""
    alloc_id = (
        session.query(func.min(Allocation.allocation_id))
        .join(Account, Allocation.account_id == Account.account_id)
        .filter(Allocation.deleted == False,   # noqa: E712
                Account.deleted == False)      # noqa: E712
        .scalar()
    )
    assert alloc_id, 'snapshot has no allocations'
    return alloc_id


class TestAddMember:

    def test_missing_username_error_surfaces_in_panel(self, auth_client,
                                                      snapshot_projcode):
        # username is a hidden picker input — its field error must be
        # rerouted into the visible alert panel, not dropped.
        resp = auth_client.post(f'/project-members/{snapshot_projcode}/add',
                                data={})
        assert resp.status_code == 200
        assert 'Username:' in resp.get_data(as_text=True)

    def test_unknown_user_reports_not_found(self, auth_client,
                                            snapshot_projcode):
        resp = auth_client.post(f'/project-members/{snapshot_projcode}/add',
                                data={'username': 'zz_no_such_user_zz'})
        assert resp.status_code == 200
        assert 'not found' in resp.get_data(as_text=True)

    def test_unauthenticated_rejected(self, client, snapshot_projcode):
        resp = client.post(f'/project-members/{snapshot_projcode}/add', data={})
        assert resp.status_code in (302, 401)


class TestShellAndPrimaryGid:

    def test_shell_missing_field_rerenders(self, auth_client):
        resp = auth_client.post('/user/htmx/shell/benkirk', data={})
        assert resp.status_code == 200
        assert 'HX-Trigger' not in resp.headers

    def test_shell_not_in_allowable_set(self, auth_client):
        resp = auth_client.post('/user/htmx/shell/benkirk',
                                data={'shell_name': 'zz_bogus'})
        assert resp.status_code == 200
        assert 'not in the allowable set' in resp.get_data(as_text=True)

    def test_primary_gid_missing_field_rerenders(self, auth_client):
        resp = auth_client.post('/user/htmx/primary-gid/benkirk', data={})
        assert resp.status_code == 200
        assert 'HX-Trigger' not in resp.headers


class TestUserEditAllocation:

    def test_invalid_amount_rerenders(self, auth_client, snapshot_allocation_id):
        resp = auth_client.post(
            f'/user/htmx/edit-allocation/{snapshot_allocation_id}',
            data={'amount': 'not-a-number'})
        assert resp.status_code == 200
        assert 'HX-Trigger' not in resp.headers


class TestAdminAllocationHandlers:

    def test_edit_allocation_no_changes(self, auth_client,
                                        snapshot_allocation_id):
        resp = auth_client.post(
            f'/admin/htmx/edit-allocation/{snapshot_allocation_id}', data={})
        assert resp.status_code == 200
        assert 'No changes provided.' in resp.get_data(as_text=True)

    def test_add_allocation_missing_fields_rerenders(self, auth_client,
                                                     snapshot_projcode):
        resp = auth_client.post(f'/admin/htmx/add-allocation/{snapshot_projcode}',
                                data={})
        assert resp.status_code == 200
        assert 'HX-Trigger' not in resp.headers

    def test_add_allocation_unknown_resource(self, auth_client,
                                             snapshot_projcode):
        resp = auth_client.post(
            f'/admin/htmx/add-allocation/{snapshot_projcode}',
            data={'resource_id': str(MISSING_ID), 'amount': '100',
                  'start_date': '2026-01-01'})
        assert resp.status_code == 200
        assert 'Selected resource does not exist.' in resp.get_data(as_text=True)

    def test_exchange_requires_resource(self, auth_client, snapshot_projcode):
        resp = auth_client.post(
            f'/admin/htmx/exchange-allocation/{snapshot_projcode}', data={})
        assert resp.status_code == 200
        assert 'Resource is required.' in resp.get_data(as_text=True)

    def test_allocate_down_missing_fields_rerenders(self, auth_client,
                                                    snapshot_allocation_id):
        resp = auth_client.post(
            f'/admin/htmx/allocate-down/{snapshot_allocation_id}', data={})
        assert resp.status_code == 200
        assert 'HX-Trigger' not in resp.headers

    def test_renew_missing_fields_rerenders(self, auth_client,
                                            snapshot_projcode):
        resp = auth_client.post(
            f'/admin/htmx/renew-allocations/{snapshot_projcode}', data={})
        assert resp.status_code == 200
        assert 'HX-Trigger' not in resp.headers

    def test_extend_missing_fields_rerenders(self, auth_client,
                                             snapshot_projcode):
        resp = auth_client.post(
            f'/admin/htmx/extend-allocations/{snapshot_projcode}', data={})
        assert resp.status_code == 200
        assert 'HX-Trigger' not in resp.headers


class TestProjectUpdate:

    def test_overlong_title_rerenders_with_field_error(self, auth_client,
                                                       snapshot_projcode):
        resp = auth_client.post(f'/admin/htmx/project-update/{snapshot_projcode}',
                                data={'title': 'x' * 300})
        assert resp.status_code == 200
        assert 'HX-Trigger' not in resp.headers

    def test_unknown_lead_fk_error(self, auth_client, snapshot_projcode):
        resp = auth_client.post(f'/admin/htmx/project-update/{snapshot_projcode}',
                                data={'project_lead_user_id': str(MISSING_ID)})
        assert resp.status_code == 200
        assert 'Selected project lead does not exist.' in resp.get_data(as_text=True)


class TestLinkedElements:

    def test_add_contract_unknown_id(self, auth_client, snapshot_projcode):
        resp = auth_client.post(
            f'/admin/htmx/project/{snapshot_projcode}/contracts/add',
            data={'contract_id': str(MISSING_ID)})
        assert resp.status_code == 200
        assert 'Contract not found.' in resp.get_data(as_text=True)

    def test_add_directory_invalid_root(self, auth_client, snapshot_projcode):
        resp = auth_client.post(
            f'/admin/htmx/project/{snapshot_projcode}/directories/add',
            data={'root_directory_id': str(MISSING_ID),
                  'directory_suffix': 'somewhere'})
        assert resp.status_code == 200
        assert 'Selected disk root is invalid.' in resp.get_data(as_text=True)


class TestAdminDirectoryModals:

    def test_create_invalid_root(self, auth_client, snapshot_projcode,
                                 session):
        project_id = (session.query(Project.project_id)
                      .filter(Project.projcode == snapshot_projcode).scalar())
        resp = auth_client.post(
            '/admin/htmx/admin/project-directories/create',
            data={'root_directory_id': str(MISSING_ID),
                  'directory_suffix': 'x', 'project_id': str(project_id)})
        assert resp.status_code == 200
        assert 'Selected disk root is invalid.' in resp.get_data(as_text=True)

    def test_create_missing_fields_rerenders(self, auth_client):
        resp = auth_client.post('/admin/htmx/admin/project-directories/create',
                                data={})
        assert resp.status_code == 200
        assert 'HX-Trigger' not in resp.headers


class TestAccessGridToggle:

    def test_unknown_fks_report_both_errors(self, auth_client,
                                            snapshot_projcode):
        resp = auth_client.post(
            f'/admin/htmx/access-grid/{snapshot_projcode}/toggle',
            data={'user_id': str(MISSING_ID),
                  'resource_id': str(MISSING_ID)})
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'Selected user does not exist.' in html
        assert 'Selected resource does not exist.' in html

    def test_missing_fields_rerender_grid(self, auth_client, snapshot_projcode):
        resp = auth_client.post(
            f'/admin/htmx/access-grid/{snapshot_projcode}/toggle', data={})
        assert resp.status_code == 200
