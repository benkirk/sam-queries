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
        """No checkboxes ticked -> re-rendered preview, no queues expired."""
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
# Cleanup — PBS cross-check (_annotate_pbs_activity + preview rendering)
# ---------------------------------------------------------------------------


class _StubQueue:
    """Just enough of a Queue for _annotate_pbs_activity (name access only)."""

    def __init__(self, name):
        self.queue_name = name


def _candidate(name, preselected):
    return {'queue': _StubQueue(name), 'last_charged': None,
            'ever_charged': preselected, 'preselected': preselected}


def _seed_tick(status_session, system, queue, ts):
    from system_status import QueueStatus

    status_session.add(QueueStatus(timestamp=ts, system_name=system,
                                   queue_name=queue))
    status_session.commit()


class TestAnnotatePbsActivity:
    """Direct tests of the scrub/annotate step. status_session guarantees a
    clean SQLite status DB and provides the app context the helper's
    db.session usage needs."""

    def test_recent_snapshot_unpreselects_and_flags(self, status_session):
        from datetime import timedelta
        from system_status.timeutil import utcnow_naive
        from webapp.dashboards.admin.resources_routes import _annotate_pbs_activity

        class R:
            resource_name = 'Derecho'

        _seed_tick(status_session, 'derecho', 'develop',
                   utcnow_naive() - timedelta(days=3))

        cands = [_candidate('develop', preselected=True),
                 _candidate('preempt', preselected=True)]
        available = _annotate_pbs_activity(cands, R, days=90)

        assert available is True
        develop, preempt = cands
        assert develop['active_in_pbs'] is True
        assert develop['preselected'] is False          # rescued
        assert preempt['active_in_pbs'] is False
        assert preempt['preselected'] is True           # untouched
        assert preempt['last_seen_pbs'] is None

    def test_stale_snapshot_annotates_but_does_not_rescue(self, status_session):
        from datetime import timedelta
        from system_status.timeutil import utcnow_naive
        from webapp.dashboards.admin.resources_routes import _annotate_pbs_activity

        class R:
            resource_name = 'Casper'

        stale = utcnow_naive() - timedelta(days=200)
        _seed_tick(status_session, 'casper', 'rda', stale)

        cands = [_candidate('rda', preselected=True)]
        _annotate_pbs_activity(cands, R, days=90)

        assert cands[0]['active_in_pbs'] is False
        assert cands[0]['preselected'] is True          # still recommended
        assert cands[0]['last_seen_pbs'] == stale       # negative evidence shown

    def test_roster_definition_rescues_routing_queue(self, status_session):
        """The casper case: never holds jobs, but the qstat -Q roster still
        defines it — must be un-preselected and typed as a routing queue."""
        from datetime import timedelta
        from system_status.timeutil import utcnow_naive
        from system_status.queries.lookups import update_queue_definitions
        from webapp.dashboards.admin.resources_routes import _annotate_pbs_activity

        class R:
            resource_name = 'Casper'

        update_queue_definitions(
            status_session, 'casper',
            [{'queue_name': 'casper', 'queue_type': 'Route'}],
            utcnow_naive() - timedelta(days=1))
        status_session.commit()

        cands = [_candidate('casper', preselected=True)]
        available = _annotate_pbs_activity(cands, R, days=90)

        assert available is True
        assert cands[0]['defined_in_pbs'] is True
        assert cands[0]['pbs_queue_type'] == 'Route'
        assert cands[0]['preselected'] is False

    def test_gpu_resource_maps_to_base_system(self, status_session):
        from datetime import timedelta
        from system_status.timeutil import utcnow_naive
        from webapp.dashboards.admin.resources_routes import _annotate_pbs_activity

        class R:
            resource_name = 'Derecho GPU'

        _seed_tick(status_session, 'derecho', 'hybrid',
                   utcnow_naive() - timedelta(days=3))

        cands = [_candidate('hybrid', preselected=False)]
        _annotate_pbs_activity(cands, R, days=90)
        assert cands[0]['active_in_pbs'] is True

    def test_other_system_activity_does_not_rescue(self, status_session):
        """'cpu' on derecho must not vouch for 'cpu' on a Casper resource."""
        from datetime import timedelta
        from system_status.timeutil import utcnow_naive
        from webapp.dashboards.admin.resources_routes import _annotate_pbs_activity

        class R:
            resource_name = 'Casper'

        _seed_tick(status_session, 'derecho', 'cpu',
                   utcnow_naive() - timedelta(days=1))

        cands = [_candidate('cpu', preselected=True)]
        _annotate_pbs_activity(cands, R, days=90)
        assert cands[0]['active_in_pbs'] is False
        assert cands[0]['preselected'] is True

    def test_no_status_data_changes_nothing(self, status_session):
        """Old resources without snapshot coverage behave exactly as before."""
        from webapp.dashboards.admin.resources_routes import _annotate_pbs_activity

        class R:
            resource_name = 'Cheyenne'

        cands = [_candidate('regular', preselected=True),
                 _candidate('share', preselected=False)]
        available = _annotate_pbs_activity(cands, R, days=90)

        assert available is False
        assert cands[0]['preselected'] is True
        assert cands[1]['preselected'] is False


class TestQueueCleanupPreviewPbsColumn:

    def test_no_status_data_hides_column_and_notes_skip(
            self, auth_client, status_session, snapshot_resource_id):
        resp = auth_client.post(
            f'/admin/htmx/queue-cleanup-preview/{snapshot_resource_id}',
            data={'days': '90'})
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'Last seen in PBS' not in html
        if 'cleanupQueue' in html:               # only shown with candidates
            assert 'cross-check was skipped' in html

    def test_seeded_status_data_badges_candidate(
            self, auth_client, session, status_session):
        """End-to-end: a committed snapshot candidate seen in PBS renders the
        badge, the PBS column, and an unchecked checkbox."""
        from datetime import timedelta

        from sam.queries.queue_access import get_queue_cleanup_candidates
        from sam.resources.machines import Queue
        from sam.resources.resources import Resource
        from system_status.timeutil import utcnow_naive

        # Find any committed resource with a cleanup candidate (route + this
        # query both see only committed snapshot rows).
        target = None
        resource_ids = [r[0] for r in session.query(Queue.resource_id)
                        .group_by(Queue.resource_id).all()]
        for rid in resource_ids:
            cands = get_queue_cleanup_candidates(session, rid, days=90)
            if cands:
                target = (session.get(Resource, rid), cands[0])
                break
        if target is None:
            pytest.skip('snapshot has no cleanup candidates')

        resource, cand = target
        system = resource.resource_name.split()[0].lower()
        _seed_tick(status_session, system, cand['queue'].queue_name,
                   utcnow_naive() - timedelta(days=1))

        resp = auth_client.post(
            f'/admin/htmx/queue-cleanup-preview/{resource.resource_id}',
            data={'days': '90'})
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)

        assert 'Last seen in PBS' in html
        assert 'active in PBS' in html
        # The badged queue's checkbox must not be pre-checked ('checked'
        # renders after the id attribute in the template, so inspect the
        # remainder of the <input> tag).
        qid = cand['queue'].queue_id
        tag_rest = html.split(f'id="cleanupQueue{qid}"', 1)[1].split('>', 1)[0]
        assert 'checked' not in tag_rest


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

    # The collapse-trigger-row guard is deliberately NOT here. Rendering only
    # /admin/htmx/resources cannot see the same bug in the contracts card
    # (issue #356), so it scans the whole template tree statically instead —
    # see tests/unit/test_collapse_trigger_rows.py.
