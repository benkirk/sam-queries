"""Unit tests for sam.queries.queue_access.get_queue_cleanup_candidates.

Every test builds its own Resource + Queue graph via Layer-2 factories, so
results are exact counts rather than "at least one" assertions and are immune
to snapshot refreshes.

The behaviors that matter:
  * charged inside the window   → not a candidate
  * charged before the window   → candidate, pre-selected
  * never charged               → candidate, NOT pre-selected (may be routing)
  * name contains '*'           → excluded (pattern/template row)
  * started inside the window   → excluded (grace period)
  * already expired             → excluded (not active)
"""
from datetime import datetime, timedelta

import pytest

from sam.queries.queue_access import get_queue_cleanup_candidates
from factories import make_comp_charge_summary, make_queue, make_resource

pytestmark = pytest.mark.unit


NOW = datetime(2026, 7, 1, 12, 0, 0)
OLD = NOW - timedelta(days=1000)        # safely before any window used here


@pytest.fixture
def resource(session):
    return make_resource(session)


def _candidates(session, resource, **kwargs):
    return get_queue_cleanup_candidates(
        session, resource.resource_id, now=NOW, **kwargs
    )


def _by_name(candidates):
    return {c['queue'].queue_name: c for c in candidates}


class TestUsageWindow:

    def test_queue_charged_inside_window_is_not_a_candidate(self, session, resource):
        q = make_queue(session, resource=resource, queue_name='busy', start_date=OLD)
        make_comp_charge_summary(
            session, queue=q, activity_date=NOW - timedelta(days=5)
        )

        assert _by_name(_candidates(session, resource)) == {}

    def test_queue_charged_before_window_is_a_candidate(self, session, resource):
        q = make_queue(session, resource=resource, queue_name='stale', start_date=OLD)
        make_comp_charge_summary(
            session, queue=q, activity_date=NOW - timedelta(days=200)
        )

        found = _by_name(_candidates(session, resource))
        assert set(found) == {'stale'}
        assert found['stale']['ever_charged'] is True
        assert found['stale']['last_charged'] == (NOW - timedelta(days=200)).date()

    def test_never_charged_queue_is_a_candidate(self, session, resource):
        make_queue(session, resource=resource, queue_name='never', start_date=OLD)

        found = _by_name(_candidates(session, resource))
        assert set(found) == {'never'}
        assert found['never']['ever_charged'] is False
        assert found['never']['last_charged'] is None

    def test_most_recent_charge_wins(self, session, resource):
        """A queue with old *and* recent charges is in use, not a candidate."""
        q = make_queue(session, resource=resource, queue_name='mixed', start_date=OLD)
        make_comp_charge_summary(session, queue=q,
                                 activity_date=NOW - timedelta(days=300))
        make_comp_charge_summary(session, queue=q,
                                 activity_date=NOW - timedelta(days=2))

        assert _by_name(_candidates(session, resource)) == {}

    def test_window_is_configurable(self, session, resource):
        q = make_queue(session, resource=resource, queue_name='q120', start_date=OLD)
        make_comp_charge_summary(session, queue=q,
                                 activity_date=NOW - timedelta(days=120))

        # 90-day window: last charge is outside it → candidate
        assert set(_by_name(_candidates(session, resource, days=90))) == {'q120'}
        # 365-day window: last charge is inside it → not a candidate
        assert _by_name(_candidates(session, resource, days=365)) == {}


class TestPreselection:
    """Only charged-then-quiet queues are pre-checked. Never-charged queues
    are indistinguishable from healthy routing queues, which never accrue
    charges because jobs charge to the execution queue they route into."""

    def test_preselected_only_for_previously_charged(self, session, resource):
        stale = make_queue(session, resource=resource,
                           queue_name='stale', start_date=OLD)
        make_comp_charge_summary(session, queue=stale,
                                 activity_date=NOW - timedelta(days=200))
        make_queue(session, resource=resource, queue_name='routing', start_date=OLD)

        found = _by_name(_candidates(session, resource))
        assert found['stale']['preselected'] is True
        assert found['routing']['preselected'] is False


class TestExclusions:

    def test_pattern_queues_are_excluded(self, session, resource):
        for name in ('M*', 'S*', 'R*'):
            make_queue(session, resource=resource, queue_name=name, start_date=OLD)
        make_queue(session, resource=resource, queue_name='real', start_date=OLD)

        assert set(_by_name(_candidates(session, resource))) == {'real'}

    def test_queue_started_inside_window_is_excluded(self, session, resource):
        """A queue younger than the window can't be judged unused."""
        make_queue(session, resource=resource, queue_name='fresh',
                   start_date=NOW - timedelta(days=3))
        make_queue(session, resource=resource, queue_name='old', start_date=OLD)

        assert set(_by_name(_candidates(session, resource))) == {'old'}

    def test_null_start_date_is_past_the_grace_period(self, session, resource):
        """NULL start_date means 'active from the beginning' (Queue.is_active)."""
        make_queue(session, resource=resource, queue_name='nostart', start_date=None)

        assert set(_by_name(_candidates(session, resource))) == {'nostart'}

    def test_already_expired_queue_is_excluded(self, session, resource):
        make_queue(session, resource=resource, queue_name='gone',
                   start_date=OLD, end_date=OLD + timedelta(days=1))

        assert _by_name(_candidates(session, resource)) == {}

    def test_other_resources_are_not_included(self, session, resource):
        other = make_resource(session)
        make_queue(session, resource=other, queue_name='elsewhere', start_date=OLD)
        make_queue(session, resource=resource, queue_name='mine', start_date=OLD)

        assert set(_by_name(_candidates(session, resource))) == {'mine'}


class TestOrdering:

    def test_sorted_by_queue_name(self, session, resource):
        for name in ('zeta', 'alpha', 'mid'):
            make_queue(session, resource=resource, queue_name=name, start_date=OLD)

        names = [c['queue'].queue_name for c in _candidates(session, resource)]
        assert names == sorted(names)


# ---------------------------------------------------------------------------
# system_status cross-check helpers
#
# These run on the per-worker SQLite system_status bind (status_session),
# not the SAM MySQL snapshot — they back the PBS cross-check the admin
# cleanup routes layer on top of get_queue_cleanup_candidates.
# ---------------------------------------------------------------------------


def _tick(status_session, system, queue, ts):
    """Insert one QueueStatus snapshot row (string setters resolve lookups)."""
    from system_status import QueueStatus

    row = QueueStatus(timestamp=ts, system_name=system, queue_name=queue)
    status_session.add(row)
    status_session.flush()
    return row


class TestGetQueueLastSeen:

    def test_returns_max_tick_per_queue(self, status_session):
        from system_status.queries import get_queue_last_seen

        _tick(status_session, 'derecho', 'main', NOW - timedelta(days=30))
        _tick(status_session, 'derecho', 'main', NOW - timedelta(days=2))
        _tick(status_session, 'derecho', 'develop', NOW - timedelta(days=150))

        seen = get_queue_last_seen(status_session, 'derecho')
        assert seen == {
            'main': NOW - timedelta(days=2),
            'develop': NOW - timedelta(days=150),
        }

    def test_is_system_scoped(self, status_session):
        """'cpu' exists on both derecho and casper — never cross the streams."""
        from system_status.queries import get_queue_last_seen

        _tick(status_session, 'derecho', 'cpu', NOW - timedelta(days=1))
        _tick(status_session, 'casper', 'cpu', NOW - timedelta(days=400))

        assert get_queue_last_seen(status_session, 'derecho') == {
            'cpu': NOW - timedelta(days=1)}
        assert get_queue_last_seen(status_session, 'casper') == {
            'cpu': NOW - timedelta(days=400)}

    def test_unknown_system_returns_empty(self, status_session):
        from system_status.queries import get_queue_last_seen

        _tick(status_session, 'derecho', 'main', NOW)
        assert get_queue_last_seen(status_session, 'cheyenne') == {}


class TestQueueDefinitions:

    def test_update_then_get_roundtrip(self, status_session):
        from system_status.queries import get_queue_definitions
        from system_status.queries.lookups import update_queue_definitions

        applied = update_queue_definitions(status_session, 'casper', [
            {'queue_name': 'casper', 'queue_type': 'Route'},
            {'queue_name': 'htc', 'queue_type': 'Execution'},
        ], NOW)

        assert applied == 2
        defs = get_queue_definitions(status_session, 'casper')
        assert defs['casper'] == {'queue_type': 'Route', 'last_defined_at': NOW}
        assert defs['htc'] == {'queue_type': 'Execution', 'last_defined_at': NOW}

    def test_last_defined_at_never_moves_backwards(self, status_session):
        """Replayed / out-of-order collector posts must not rewind the stamp."""
        from system_status.queries import get_queue_definitions
        from system_status.queries.lookups import update_queue_definitions

        update_queue_definitions(
            status_session, 'casper',
            [{'queue_name': 'casper', 'queue_type': 'Route'}], NOW)
        update_queue_definitions(
            status_session, 'casper',
            [{'queue_name': 'casper', 'queue_type': 'Route'}],
            NOW - timedelta(hours=1))

        defs = get_queue_definitions(status_session, 'casper')
        assert defs['casper']['last_defined_at'] == NOW

    def test_blank_names_are_skipped(self, status_session):
        from system_status.queries.lookups import update_queue_definitions

        applied = update_queue_definitions(status_session, 'casper', [
            {'queue_name': ''},
            {'queue_name': '   '},
            {'queue_type': 'Route'},           # no name at all
        ], NOW)
        assert applied == 0

    def test_queues_without_roster_sighting_are_omitted(self, status_session):
        """Pre-roster QueueDef rows (last_defined_at NULL) must not appear —
        absence of data is not evidence the queue is defined."""
        from system_status.queries import get_queue_definitions

        _tick(status_session, 'casper', 'vis', NOW)   # lookup row, no roster stamp
        assert get_queue_definitions(status_session, 'casper') == {}

    def test_unknown_system_returns_empty(self, status_session):
        from system_status.queries import get_queue_definitions
        from system_status.queries.lookups import update_queue_definitions

        update_queue_definitions(
            status_session, 'casper',
            [{'queue_name': 'casper', 'queue_type': 'Route'}], NOW)
        assert get_queue_definitions(status_session, 'derecho') == {}
