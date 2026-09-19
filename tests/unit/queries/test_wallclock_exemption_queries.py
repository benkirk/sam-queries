"""Unit tests for sam.queries.wallclock_exemption_access.

Validates the shape and active-filter semantics of
get_wallclock_exemption_data(), which reproduces the legacy Java
``GET /api/protected/admin/ssg/wallClockExemption`` endpoint.

Snapshot-robust: no hardcoded resource/queue/user names.
"""

import pytest

from sam.queries.wallclock_exemption_access import get_wallclock_exemption_data

pytestmark = pytest.mark.unit


@pytest.fixture
def wce_all(session):
    """Full exemption tree (no filter)."""
    return get_wallclock_exemption_data(session)


# ============================================================================
# Top-level response shape
# ============================================================================

class TestGetWceDataStructure:

    def test_returns_dict_with_name_and_resources(self, wce_all):
        assert isinstance(wce_all, dict)
        assert 'name' in wce_all
        assert 'resources' in wce_all

    def test_name_is_exemptions(self, wce_all):
        assert wce_all['name'] == 'exemptions'

    def test_resources_is_list(self, wce_all):
        assert isinstance(wce_all['resources'], list)

    def test_resources_sorted_by_name(self, wce_all):
        names = [r['resourceName'] for r in wce_all['resources']]
        assert names == sorted(names)


# ============================================================================
# Nested structure & fields
# ============================================================================

class TestWceEntries:

    def test_resource_has_resourceName_and_queues(self, wce_all):
        for res in wce_all['resources']:
            assert isinstance(res['resourceName'], str)
            assert isinstance(res['queues'], list)

    def test_queues_sorted_by_name(self, wce_all):
        for res in wce_all['resources']:
            names = [q['queueName'] for q in res['queues']]
            assert names == sorted(names)

    def test_queue_has_queueName_and_limits(self, wce_all):
        for res in wce_all['resources']:
            for q in res['queues']:
                assert isinstance(q['queueName'], str)
                assert isinstance(q['limits'], list)

    def test_limits_have_required_fields_and_sorted(self, wce_all):
        for res in wce_all['resources']:
            for q in res['queues']:
                usernames = [lim['username'] for lim in q['limits']]
                assert usernames == sorted(usernames)
                for lim in q['limits']:
                    assert set(lim.keys()) == {'username', 'wallClockLimit'}
                    assert isinstance(lim['username'], str)
                    assert isinstance(lim['wallClockLimit'], (int, float))


# ============================================================================
# Resource filter
# ============================================================================

class TestWceResourceFilter:

    def test_filter_returns_only_that_resource(self, session, wce_all):
        if not wce_all['resources']:
            pytest.skip('snapshot has no active wallclock exemptions')
        rname = wce_all['resources'][0]['resourceName']
        filtered = get_wallclock_exemption_data(session, resource_name=rname)
        assert [r['resourceName'] for r in filtered['resources']] == [rname]

    def test_filter_matches_unfiltered_subset(self, session, wce_all):
        if not wce_all['resources']:
            pytest.skip('snapshot has no active wallclock exemptions')
        rname = wce_all['resources'][0]['resourceName']
        filtered = get_wallclock_exemption_data(session, resource_name=rname)
        expected = next(r for r in wce_all['resources'] if r['resourceName'] == rname)
        assert filtered['resources'][0] == expected

    def test_unknown_resource_returns_empty(self, session):
        result = get_wallclock_exemption_data(session, resource_name='NonexistentResource99')
        assert result['name'] == 'exemptions'
        assert result['resources'] == []
