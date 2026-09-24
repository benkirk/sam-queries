"""HTTP-layer tests for the hand-rolled handlers migrated to
`HtmxFormHandler` subclasses (WEBAPP_OO_REFACTOR commit 2.4).

Same scope as the CRUD characterization files: validation-error
re-render, domain-error (FormError/FK) paths, and auth — no happy-path
writes (routes commit through Flask-SQLAlchemy's `db.session`). Every
error-path assertion here exercises the full handler lifecycle:
form_input -> load -> clean -> render_errors with the route's own context.
"""

import pytest
from sqlalchemy import func

from sam.accounting.accounts import Account
from sam.accounting.allocations import Allocation
from sam.projects.projects import Project


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


class TestRenewTruncateControl:
    """The live-recomputed fragment: always answers with its swap target."""

    URL = '/admin/htmx/renew-truncate-control/{}'

    def test_renders_swap_target(self, auth_client, snapshot_projcode):
        resp = auth_client.get(
            self.URL.format(snapshot_projcode),
            query_string={'source_active_at': '2026-09-15',
                          'new_start_date': '2026-10-01',
                          'new_end_date': '2027-09-30',
                          'resource_ids': ['1']})
        assert resp.status_code == 200
        assert 'id="renewTruncateControl"' in resp.get_data(as_text=True)

    def test_half_typed_date_hides_control(self, auth_client, snapshot_projcode):
        resp = auth_client.get(
            self.URL.format(snapshot_projcode),
            query_string={'new_start_date': '2026-1', 'new_end_date': '',
                          'resource_ids': ['1']})
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert 'id="renewTruncateControl"' in body
        assert 'name="replace_existing"' not in body

    def test_unknown_project_404(self, auth_client):
        resp = auth_client.get(self.URL.format('ZZZZ9999'))
        assert resp.status_code == 404

    def test_unauthenticated_rejected(self, client, snapshot_projcode):
        resp = client.get(self.URL.format(snapshot_projcode))
        assert resp.status_code in (302, 401)


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


class TestExemptionHandlers:

    def test_admin_create_empty_post_rerenders(self, auth_client):
        # Regression: the error re-render context must not depend on
        # attributes only set in clean() (schema errors fire first).
        resp = auth_client.post('/admin/htmx/admin/exemption/create', data={})
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'User is required.' in html
        assert 'Queue is required.' in html

    def test_user_scoped_create_empty_post_rerenders(self, auth_client):
        resp = auth_client.post('/admin/htmx/exemption/benkirk', data={})
        assert resp.status_code == 200
        assert 'Queue is required.' in resp.get_data(as_text=True)

    def test_edit_missing_id_404s(self, auth_client):
        resp = auth_client.post(f'/admin/htmx/exemption-edit/{MISSING_ID}',
                                data={})
        assert resp.status_code == 404


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


@pytest.fixture
def renewable(session):
    """``(projcode, resource_id, allocation_id, end_date)`` of a root project's
    standalone allocation active today, whose lead has an email on file."""
    from datetime import datetime
    now = datetime.now()
    rows = (session.query(Project, Account.resource_id, Allocation)
            .join(Account, Account.project_id == Project.project_id)
            .join(Allocation, Allocation.account_id == Account.account_id)
            .filter(Project.is_active, Project.parent_id.is_(None),
                    Allocation.deleted.is_(False), Allocation.parent_allocation_id.is_(None),
                    Allocation.start_date <= now, Allocation.end_date >= now)
            .order_by(Project.project_id).limit(50).all())
    for project, resource_id, alloc in rows:
        if project.lead is not None and project.lead.primary_email:
            return project.projcode, resource_id, alloc.allocation_id, alloc.end_date
    pytest.skip('no renewable root allocation in this snapshot')


def _allocation_state(app, allocation_id):
    from webapp.extensions import db
    with app.app_context():
        alloc = db.session.get(Allocation, allocation_id)
        db.session.refresh(alloc)
        return (alloc.end_date, db.session.query(func.count(Allocation.allocation_id)).scalar())


class TestRenewalPreviews:
    """Renew / Extend: the notice from a read-only plan of the write."""

    def _post(self, auth_client, verb, projcode, **data):
        return auth_client.post(f'/admin/htmx/{verb}-allocations-preview/{projcode}',
                                data=data)

    @pytest.mark.parametrize('verb', ['renew', 'extend'])
    def test_the_form_carries_the_button_and_pane(self, auth_client, verb, renewable):
        html = auth_client.get(
            f'/admin/htmx/{verb}-allocations-form/{renewable[0]}').get_data(as_text=True)
        assert f'/admin/htmx/{verb}-allocations-preview/{renewable[0]}' in html
        assert f'id="{verb}PreviewPane"' in html

    @pytest.mark.parametrize('verb', ['renew', 'extend'])
    def test_an_incomplete_form_asks_for_more(self, auth_client, verb, snapshot_projcode):
        resp = self._post(auth_client, verb, snapshot_projcode)
        assert resp.status_code == 200
        assert 'Set the dates' in resp.get_data(as_text=True)

    def test_renew_previews_the_notice_and_writes_nothing(self, app, auth_client,
                                                          renewable):
        from datetime import date
        projcode, rid, alloc_id, _ = renewable
        before = _allocation_state(app, alloc_id)
        resp = self._post(auth_client, 'renew', projcode,
                          source_active_at=date.today().isoformat(),
                          new_start_date='2099-01-01', new_end_date='2099-12-31',
                          resource_ids=str(rid), operator_comment='zz-renew-note')
        html = resp.get_data(as_text=True)
        assert resp.status_code == 200
        assert f'Your NSF NCAR project {projcode} has been renewed' in html
        assert 'zz-renew-note' in html and 'The box is unticked' in html
        assert _allocation_state(app, alloc_id) == before

    def test_extend_previews_the_notice_and_writes_nothing(self, app, auth_client,
                                                           renewable):
        from datetime import date
        projcode, rid, alloc_id, _ = renewable
        before = _allocation_state(app, alloc_id)
        resp = self._post(auth_client, 'extend', projcode,
                          source_active_at=date.today().isoformat(),
                          new_end_date='2099-12-31', resource_ids=str(rid),
                          notify_leads='1')
        html = resp.get_data(as_text=True)
        assert f'Your NSF NCAR project {projcode} has been extended' in html
        assert 'The box is unticked' not in html
        assert _allocation_state(app, alloc_id) == before

    def test_extend_shows_the_refusal_the_save_would_raise(self, auth_client, renewable):
        from datetime import date
        projcode, rid, _, end = renewable
        resp = self._post(auth_client, 'extend', projcode,
                          source_active_at=date.today().isoformat(),
                          new_end_date=end.date().isoformat(), resource_ids=str(rid))
        assert 'must be later than the current latest end date' in resp.get_data(as_text=True)

    def test_renew_shows_the_refusal_the_save_would_raise(self, auth_client, renewable):
        projcode, rid, _, _ = renewable
        resp = self._post(auth_client, 'renew', projcode, source_active_at='1990-01-01',
                          new_start_date='2099-01-01', new_end_date='2099-12-31',
                          resource_ids=str(rid))
        assert 'No active allocation anywhere in the tree at 1990-01-01' in \
            resp.get_data(as_text=True)

    @pytest.mark.parametrize('verb', ['renew', 'extend'])
    def test_it_is_guarded_like_the_save(self, non_admin_client, verb, snapshot_projcode):
        resp = self._post(non_admin_client, verb, snapshot_projcode)
        assert resp.status_code in (302, 403)
