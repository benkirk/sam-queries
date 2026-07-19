"""Unit tests for sam.queries.queue_access.get_queue_cleanup_candidates.

Every test builds its own Resource + Queue graph via Layer-2 factories, so
results are exact counts rather than "at least one" assertions and are immune
to snapshot refreshes.

The behaviours that matter:
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
