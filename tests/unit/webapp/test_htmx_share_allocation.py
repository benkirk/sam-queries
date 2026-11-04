"""HTTP layer of the tree's empty-row Share and Add actions (#902): auth, 404,
render and refusal. The write itself is covered in
tests/unit/manage/test_share_with_subproject.py (route writes would commit)."""
import os

import pytest
from sqlalchemy import text

from sam.accounting.allocations import Allocation


_BOGUS = 99_999_999


def _form_url(alloc_id, projcode):
    return f'/admin/htmx/share-allocation-form/{alloc_id}/{projcode}'


def _post_url(alloc_id, projcode):
    return f'/admin/htmx/share-allocation/{alloc_id}/{projcode}'


@pytest.fixture
def share_pair(session):
    """``(parent projcode, parent allocation_id, child projcode)``: a current parent
    allocation and an active child holding nothing live on its resource."""
    row = session.execute(text("""
        SELECT p.projcode, al.allocation_id, c.projcode
        FROM project p
        JOIN account a ON a.project_id = p.project_id AND a.deleted IS FALSE
        JOIN allocation al ON al.account_id = a.account_id AND al.deleted IS FALSE
            AND al.start_date <= NOW() AND (al.end_date IS NULL OR al.end_date >= NOW())
        JOIN project c ON c.parent_id = p.project_id AND c.active IS TRUE
        WHERE p.active IS TRUE AND NOT EXISTS (
            SELECT 1 FROM account ca
            JOIN allocation cal ON cal.account_id = ca.account_id AND cal.deleted IS FALSE
            WHERE ca.project_id = c.project_id AND ca.resource_id = a.resource_id
              AND ca.deleted IS FALSE)
        ORDER BY al.allocation_id LIMIT 1
    """)).first()
    if row is None:
        pytest.skip('snapshot has no current parent pool with an unallocated active child')
    return tuple(row)


@pytest.fixture
def shared_child(session):
    """An inheriting allocation: its project already holds a row, so Share refuses."""
    a = (session.query(Allocation)
         .filter(Allocation.deleted == False,           # noqa: E712
                 Allocation.parent_allocation_id.isnot(None))
         .order_by(Allocation.allocation_id).first())
    if a is None or a.parent is None:
        pytest.skip('snapshot has no inheriting allocations')
    return a


class TestShareForm:

    def test_unauthenticated_redirects_or_401(self, client):
        if os.getenv('DISABLE_AUTH') == '1':
            pytest.skip('Auth disabled in dev environment')
        assert client.get(_form_url(_BOGUS, 'X')).status_code in (302, 401)

    def test_missing_allocation_404(self, auth_client):
        assert auth_client.get(_form_url(_BOGUS, 'X')).status_code == 404

    def test_unknown_project_404(self, auth_client, share_pair):
        _parent, alloc_id, _child = share_pair
        assert auth_client.get(_form_url(alloc_id, 'NOSUCH9999')).status_code == 404

    def test_eligible_pair_names_the_pool(self, auth_client, share_pair):
        parent, alloc_id, child = share_pair
        resp = auth_client.get(_form_url(alloc_id, child))
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert f'#{alloc_id}' in html and parent in html and child in html
        assert 'id="shareAllocationModalLabel" hx-swap-oob="true"' in html
        assert 'alert-danger' not in html
        assert _post_url(alloc_id, child) in html

    def test_refusal_disables_the_submit(self, auth_client, shared_child):
        child = shared_child.account.project.projcode
        resp = auth_client.get(_form_url(shared_child.parent_allocation_id, child))
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'alert-danger' in html
        assert 'disabled' in html.split('type="submit"', 1)[1].split('>', 1)[0]


class TestSharePost:

    def test_unauthenticated_redirects_or_401(self, client):
        if os.getenv('DISABLE_AUTH') == '1':
            pytest.skip('Auth disabled in dev environment')
        assert client.post(_post_url(_BOGUS, 'X')).status_code in (302, 401)

    def test_missing_allocation_404(self, auth_client):
        assert auth_client.post(_post_url(_BOGUS, 'X')).status_code == 404

    def test_refusal_re_renders_without_writing(self, auth_client, session, shared_child):
        child = shared_child.account.project
        before = len(shared_child.account.live_allocations)
        resp = auth_client.post(_post_url(shared_child.parent_allocation_id, child.projcode))
        assert resp.status_code == 200
        assert 'HX-Trigger' not in resp.headers
        assert 'alert-danger' in resp.get_data(as_text=True)
        session.expire_all()
        assert len(session.get(Allocation, shared_child.allocation_id).account.live_allocations) == before


class TestTreeRowActions:

    def test_empty_child_row_offers_share(self, auth_client, share_pair):
        parent, alloc_id, child = share_pair
        resp = auth_client.get(f'/admin/htmx/project-allocation-tree/{parent}')
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert _form_url(alloc_id, child) in html
        assert 'data-bs-target="#shareAllocationModal"' in html

    def test_add_form_focuses_the_named_resource(self, auth_client, session, share_pair):
        _parent, alloc_id, child = share_pair
        rid = session.get(Allocation, alloc_id).account.resource_id
        resp = auth_client.get(f'/admin/htmx/add-allocation-form/{child}?resource_id={rid}')
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        tag = html.split(f'id="addAllocAmt{rid}"', 1)
        if len(tag) == 1:
            pytest.skip('child already held this resource once; the grid shows it as held')
        assert 'data-autofocus' in tag[1].split('>', 1)[0]
