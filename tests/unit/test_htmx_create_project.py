"""HTTP-layer + helper tests for the Create Project HTMX form.

Scope mirrors tests/unit/test_htmx_create_adjustment.py: we exercise
auth, the rendering shape of the form, and the pure-Python pool-badge
helper. We do NOT round-trip a successful project create through the
HTTP layer — that would require SAVEPOINT bridging between the test
session and Flask-SQLAlchemy's db.session, which the existing pattern
deliberately avoids. The successful-create / GID-rollback behavior is
covered by the model-level tests in test_gid_allocation.py plus the
SQLAlchemy transaction contract.
"""
import os

import pytest

from sam import GidPoolSummary
from webapp.dashboards.admin.projects_routes import _gid_pool_badge


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# _gid_pool_badge — threshold logic
# ---------------------------------------------------------------------------


def _summary(available):
    """GidPoolSummary with the given available count, total/blocks unused."""
    return GidPoolSummary(
        available=available, total=1000,
        block_count=1, exhausted_block_count=0,
    )


class TestGidPoolBadge:

    def test_exhausted_disables_submit(self):
        """All blocks exhausted (block_count > 0, available == 0)."""
        b = _gid_pool_badge(_summary(0))
        assert b['disable_submit'] is True
        assert 'exhaust' in b['label'].lower()
        assert b['css_class'] == 'bg-danger'

    def test_no_blocks_defined_disables_submit_with_distinct_label(self):
        """Empty table (block_count == 0): operator sees a different
        message because the remediation is different — seed a block
        via IDMS, not extend an existing exhausted block."""
        empty = GidPoolSummary(available=0, total=0,
                               block_count=0, exhausted_block_count=0)
        b = _gid_pool_badge(empty)
        assert b['disable_submit'] is True
        assert b['css_class'] == 'bg-danger'
        # Wording distinguishes the two zero-states.
        assert 'no gid blocks' in b['label'].lower()
        assert 'exhaust' not in b['label'].lower()

    @pytest.mark.parametrize('n', [1, 5, 9])
    def test_red_below_danger_threshold(self, n):
        b = _gid_pool_badge(_summary(n))
        assert b['css_class'] == 'bg-danger'
        assert b['disable_submit'] is False
        assert str(n) in b['label']

    @pytest.mark.parametrize('n', [10, 50, 99])
    def test_yellow_in_warn_range(self, n):
        b = _gid_pool_badge(_summary(n))
        assert 'bg-warning' in b['css_class']
        assert b['disable_submit'] is False
        assert str(n) in b['label']

    @pytest.mark.parametrize('n', [100, 1000, 1_000_000])
    def test_green_above_warn_threshold(self, n):
        b = _gid_pool_badge(_summary(n))
        assert b['css_class'] == 'bg-success'
        assert b['disable_submit'] is False

    def test_singular_label_when_exactly_one(self):
        b = _gid_pool_badge(_summary(1))
        assert 'GID available' in b['label']
        assert 'GIDs' not in b['label']


# ---------------------------------------------------------------------------
# GET /htmx/project-create-form — auth + render shape
# ---------------------------------------------------------------------------


class TestCreateProjectFormEndpoint:

    def test_unauthenticated_redirects_or_401(self, client):
        if os.getenv('DISABLE_AUTH') == '1':
            pytest.skip("Auth disabled in dev environment")
        resp = client.get('/admin/htmx/project-create-form')
        assert resp.status_code in (302, 401)

    def test_non_admin_denied(self, non_admin_client):
        resp = non_admin_client.get('/admin/htmx/project-create-form')
        assert resp.status_code == 403

    def test_admin_renders_form(self, auth_client):
        resp = auth_client.get('/admin/htmx/project-create-form')
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        # The form posts to the create endpoint.
        assert 'hx-post' in html
        assert '/admin/htmx/project-create' in html

    def test_admin_render_omits_unix_gid_input(self, auth_client):
        """The Unix GID input is gone — operators no longer hand-pick a GID."""
        resp = auth_client.get('/admin/htmx/project-create-form')
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'name="unix_gid"' not in html
        assert 'id="createProjectUnixGid"' not in html

    def test_admin_render_shows_gid_pool_badge(self, auth_client):
        """The pool-status badge replaces the input."""
        resp = auth_client.get('/admin/htmx/project-create-form')
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'createProjectGidPoolBadge' in html
        # One of the three known badge classes must appear (color depends
        # on snapshot pool state, which we don't pin down here).
        assert any(cls in html for cls in (
            'bg-success', 'bg-warning', 'bg-danger',
        ))
