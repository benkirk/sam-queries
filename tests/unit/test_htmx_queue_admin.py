"""HTTP-layer tests for the admin queue create + cleanup routes.

Scope (mirrors tests/unit/test_htmx_exemption_admin.py): auth, permission,
validation, 404 and render smoke. Happy-path DB writes are deliberately not
exercised here — route handlers go through Flask-SQLAlchemy's `db.session`,
which only sees committed snapshot rows, so factory-built graphs are invisible
to them and a real write would leak out of the per-test SAVEPOINT. Those paths
are covered at the model layer in test_manage_resources.py (Queue.create) and
at the query layer in test_queue_cleanup_queries.py.

Endpoints tested:
    GET  /admin/htmx/queue-create-form
    POST /admin/htmx/queue-create
    GET  /admin/htmx/queue-cleanup-form/<resource_id>
    POST /admin/htmx/queue-cleanup-preview/<resource_id>
    POST /admin/htmx/queue-cleanup/<resource_id>
"""
import os

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def snapshot_resource_id(session):
    """A committed resource id the route handlers can actually see."""
    from sam.resources.machines import Queue

    row = (
        session.query(Queue.resource_id)
        .group_by(Queue.resource_id)
        .order_by(Queue.resource_id)
        .first()
    )
    assert row is not None, "snapshot has no queues"
    return row[0]


# ---------------------------------------------------------------------------
# Create Queue
# ---------------------------------------------------------------------------


class TestQueueCreateForm:

    def test_unauthenticated_redirects_or_401(self, client):
        if os.getenv('DISABLE_AUTH') == '1':
            pytest.skip("Auth disabled in dev environment")
        resp = client.get('/admin/htmx/queue-create-form')
        assert resp.status_code in (302, 401)

    def test_non_admin_denied(self, non_admin_client):
        resp = non_admin_client.get('/admin/htmx/queue-create-form')
        assert resp.status_code == 403

    def test_admin_renders_form(self, auth_client):
        resp = auth_client.get('/admin/htmx/queue-create-form')
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'queue_name' in html
        assert 'wall_clock_hours_limit' in html
        # Resource picker is populated from active resources
        assert 'resource_id' in html


class TestQueueCreateEndpoint:

    def test_unauthenticated_redirects_or_401(self, client):
        if os.getenv('DISABLE_AUTH') == '1':
            pytest.skip("Auth disabled in dev environment")
        resp = client.post('/admin/htmx/queue-create', data={})
        assert resp.status_code in (302, 401)

    def test_non_admin_denied(self, non_admin_client):
        resp = non_admin_client.post('/admin/htmx/queue-create', data={})
        assert resp.status_code == 403

    def test_missing_required_fields_rerenders_with_errors(self, auth_client):
        resp = auth_client.post('/admin/htmx/queue-create', data={})
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        # Form comes back rather than 500ing, with the field still present
        assert 'queue_name' in html

    def test_unknown_resource_id_reports_error(self, auth_client):
        resp = auth_client.post('/admin/htmx/queue-create', data={
            'queue_name': 'test-nonexistent-resource',
            'resource_id': '99999999',
        })
        assert resp.status_code == 200
        assert 'does not exist' in resp.get_data(as_text=True)

    def test_negative_wallclock_rejected(self, auth_client, snapshot_resource_id):
        resp = auth_client.post('/admin/htmx/queue-create', data={
            'queue_name': 'test-bad-wc',
            'resource_id': str(snapshot_resource_id),
            'wall_clock_hours_limit': '-5',
        })
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'queue_name' in html          # re-rendered form, not a redirect
        assert 'test-bad-wc' in html         # user input preserved


# ---------------------------------------------------------------------------
# Cleanup — step 1 (window form)
# ---------------------------------------------------------------------------


class TestQueueCleanupForm:

    def test_unauthenticated_redirects_or_401(self, client, snapshot_resource_id):
        if os.getenv('DISABLE_AUTH') == '1':
            pytest.skip("Auth disabled in dev environment")
        resp = client.get(f'/admin/htmx/queue-cleanup-form/{snapshot_resource_id}')
        assert resp.status_code in (302, 401)

    def test_non_admin_denied(self, non_admin_client, snapshot_resource_id):
        resp = non_admin_client.get(
            f'/admin/htmx/queue-cleanup-form/{snapshot_resource_id}')
        assert resp.status_code == 403

    def test_admin_renders_form(self, auth_client, snapshot_resource_id):
        resp = auth_client.get(
            f'/admin/htmx/queue-cleanup-form/{snapshot_resource_id}')
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'name="days"' in html
        assert 'value="90"' in html          # default window
        assert 'routing' in html.lower()     # the routing-queue caveat

    def test_nonexistent_resource_returns_404(self, auth_client):
        resp = auth_client.get('/admin/htmx/queue-cleanup-form/99999999')
        assert resp.status_code == 404
        assert b'Resource not found' in resp.data


# ---------------------------------------------------------------------------
# Cleanup — step 2 (preview)
# ---------------------------------------------------------------------------


class TestQueueCleanupPreview:

    def test_non_admin_denied(self, non_admin_client, snapshot_resource_id):
        resp = non_admin_client.post(
            f'/admin/htmx/queue-cleanup-preview/{snapshot_resource_id}',
            data={'days': '90'})
        assert resp.status_code == 403

    def test_admin_renders_preview(self, auth_client, snapshot_resource_id):
        resp = auth_client.post(
            f'/admin/htmx/queue-cleanup-preview/{snapshot_resource_id}',
            data={'days': '90'})
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        # Either a candidate table or the empty-state message, but always the
        # commit form wired to the right resource.
        assert f'/admin/htmx/queue-cleanup/{snapshot_resource_id}' in html
        assert 'name="days"' in html

    def test_zero_days_rejected(self, auth_client, snapshot_resource_id):
        resp = auth_client.post(
            f'/admin/htmx/queue-cleanup-preview/{snapshot_resource_id}',
            data={'days': '0'})
        assert resp.status_code == 200
        # Bounced back to step 1, which posts to the preview endpoint
        assert 'queue-cleanup-preview' in resp.get_data(as_text=True)

    def test_nonexistent_resource_returns_404(self, auth_client):
        resp = auth_client.post('/admin/htmx/queue-cleanup-preview/99999999',
                                data={'days': '90'})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Cleanup — step 3 (commit)
# ---------------------------------------------------------------------------


class TestQueueCleanupCommit:

    def test_unauthenticated_redirects_or_401(self, client, snapshot_resource_id):
        if os.getenv('DISABLE_AUTH') == '1':
            pytest.skip("Auth disabled in dev environment")
        resp = client.post(f'/admin/htmx/queue-cleanup/{snapshot_resource_id}',
                           data={'days': '90'})
        assert resp.status_code in (302, 401)

    def test_non_admin_denied(self, non_admin_client, snapshot_resource_id):
        resp = non_admin_client.post(
            f'/admin/htmx/queue-cleanup/{snapshot_resource_id}',
            data={'days': '90'})
        assert resp.status_code == 403

    def test_empty_selection_changes_nothing(self, auth_client, session,
                                             snapshot_resource_id):
        """No checkboxes ticked → re-rendered preview, no queues expired."""
        from sam.resources.machines import Queue

        before = session.query(Queue).filter(
            Queue.resource_id == snapshot_resource_id,
            Queue.end_date.is_(None),
        ).count()

        resp = auth_client.post(
            f'/admin/htmx/queue-cleanup/{snapshot_resource_id}',
            data={'days': '90'})

        assert resp.status_code == 200
        assert 'nothing to do' in resp.get_data(as_text=True)

        after = session.query(Queue).filter(
            Queue.resource_id == snapshot_resource_id,
            Queue.end_date.is_(None),
        ).count()
        assert after == before

    def test_non_candidate_ids_are_dropped(self, auth_client, session,
                                           snapshot_resource_id):
        """A submitted id that isn't a current candidate must not be expired —
        the commit step intersects the selection against a fresh query rather
        than trusting the POST body."""
        from sam.resources.machines import Queue

        before = session.query(Queue).filter(
            Queue.resource_id == snapshot_resource_id,
            Queue.end_date.is_(None),
        ).count()

        resp = auth_client.post(
            f'/admin/htmx/queue-cleanup/{snapshot_resource_id}',
            data={'days': '90', 'queue_ids': '99999999'})

        assert resp.status_code == 200
        assert 'nothing to do' in resp.get_data(as_text=True)

        after = session.query(Queue).filter(
            Queue.resource_id == snapshot_resource_id,
            Queue.end_date.is_(None),
        ).count()
        assert after == before

    def test_nonexistent_resource_returns_404(self, auth_client):
        resp = auth_client.post('/admin/htmx/queue-cleanup/99999999',
                                data={'days': '90'})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Resources card — the new buttons render
# ---------------------------------------------------------------------------


class TestResourcesCardQueueButtons:

    def test_admin_sees_create_and_cleanup_buttons(self, auth_client):
        resp = auth_client.get('/admin/htmx/resources')
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'Create Queue' in html
        assert 'Cleanup' in html
        assert 'queue-cleanup-form' in html

    def test_cleanup_button_row_is_not_a_collapse_trigger(self, auth_client):
        """Regression guard: the Cleanup button must not sit inside a <tr>
        that is itself a Bootstrap collapse toggle.

        Bootstrap registers its data-api handlers on `document` in the CAPTURE
        phase, so they run before any listener on the button — nothing the
        button does (stopPropagation included) can stop the row from expanding.
        The queue group row therefore puts the toggle on its <td>s instead.
        Putting it back on the <tr> silently reintroduces the bug.
        """
        import re

        html = auth_client.get('/admin/htmx/resources').get_data(as_text=True)

        # Isolate the queue group rows by their collapse target id
        for m in re.finditer(r'<tr\b[^>]*>', html):
            tag = m.group(0)
            if 'data-bs-toggle="collapse"' not in tag:
                continue
            # A collapse-trigger <tr> is fine as long as it holds no buttons.
            row_end = html.find('</tr>', m.end())
            row = html[m.end():row_end]
            assert 'queue-cleanup-form' not in row, (
                'Cleanup button is inside a collapse-trigger <tr>; the row '
                'will expand on click. Move the toggle to the <td>s.'
            )
