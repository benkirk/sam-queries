"""Unit tests for sam.queries.queue_access.

Validates the shape and active-filter semantics of get_queue_data(), which
reproduces the legacy Java ``GET /api/protected/admin/ssg/queue`` endpoint.

Snapshot-robust: no hardcoded resource/queue names — tests read whatever the
obfuscated test DB happens to contain.
"""

import pytest

from sam.queries.queue_access import get_queue_data

pytestmark = pytest.mark.unit


@pytest.fixture
def queue_all(session):
    """Full queue tree (no filter)."""
    return get_queue_data(session)


# ============================================================================
# Top-level response shape
# ============================================================================

class TestGetQueueDataStructure:

    def test_returns_dict_with_name_and_resources(self, queue_all):
        assert isinstance(queue_all, dict)
        assert 'name' in queue_all
        assert 'resources' in queue_all

    def test_name_is_queues(self, queue_all):
        assert queue_all['name'] == 'queues'

    def test_resources_is_list(self, queue_all):
        assert isinstance(queue_all['resources'], list)

    def test_at_least_one_resource(self, queue_all):
        # The snapshot always has active resources with queues.
        assert len(queue_all['resources']) >= 1

    def test_resources_sorted_by_name(self, queue_all):
        names = [r['resourceName'] for r in queue_all['resources']]
        assert names == sorted(names)


# ============================================================================
# Nested structure & fields
# ============================================================================

class TestQueueEntries:

    def test_resource_has_resourceName_and_queues(self, queue_all):
        for res in queue_all['resources']:
            assert isinstance(res['resourceName'], str)
            assert isinstance(res['queues'], list)

    def test_queues_sorted_by_name(self, queue_all):
        for res in queue_all['resources']:
            names = [q['queueName'] for q in res['queues']]
            assert names == sorted(names)

    def test_queue_has_required_fields(self, queue_all):
        seen = 0
        for res in queue_all['resources']:
            for q in res['queues']:
                assert set(q.keys()) == {
                    'queueName', 'wallClockHoursLimit',
                    'startDate', 'endDate', 'cosId',
                }
                assert isinstance(q['queueName'], str)
                assert q['wallClockHoursLimit'] is None or isinstance(
                    q['wallClockHoursLimit'], (int, float))
                assert q['startDate'] is None or isinstance(q['startDate'], str)
                assert q['endDate'] is None or isinstance(q['endDate'], str)
                assert q['cosId'] is None or isinstance(q['cosId'], int)
                seen += 1
        assert seen >= 1, 'expected at least one queue in the snapshot'


# ============================================================================
# Resource filter
# ============================================================================

class TestQueueResourceFilter:

    def test_filter_returns_only_that_resource(self, session, queue_all):
        rname = queue_all['resources'][0]['resourceName']
        filtered = get_queue_data(session, resource_name=rname)
        assert [r['resourceName'] for r in filtered['resources']] == [rname]

    def test_filter_matches_unfiltered_subset(self, session, queue_all):
        rname = queue_all['resources'][0]['resourceName']
        filtered = get_queue_data(session, resource_name=rname)
        expected = next(r for r in queue_all['resources'] if r['resourceName'] == rname)
        assert filtered['resources'][0] == expected

    def test_unknown_resource_returns_empty(self, session):
        result = get_queue_data(session, resource_name='NonexistentResource99')
        assert result['name'] == 'queues'
        assert result['resources'] == []
