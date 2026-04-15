"""Unit tests for sam.queries.project_access.get_project_group_status().

Ported verbatim from tests/unit/test_project_access_queries.py. All tests
are structural (dict shape, field types, value-set invariants) and were
already snapshot-safe — no hardcoded projcodes or usernames.
"""
import pytest

from sam.queries.project_access import get_project_group_status


pytestmark = pytest.mark.unit


class TestGetProjectGroupStatus:

    def test_returns_dict_keyed_by_branch(self, session):
        result = get_project_group_status(session)
        assert isinstance(result, dict)
        assert len(result) >= 1

    def test_each_branch_is_a_list(self, session):
        result = get_project_group_status(session)
        for branch_name, projects in result.items():
            assert isinstance(projects, list), f'Branch {branch_name!r} value should be a list'

    def test_project_entry_has_required_fields(self, session):
        result = get_project_group_status(session)
        required = {'groupName', 'panel', 'autoRenewing', 'projectActive',
                    'status', 'expiration', 'resourceGroupStatuses'}
        for branch_name, projects in result.items():
            for proj in projects[:5]:
                missing = required - proj.keys()
                assert not missing, (
                    f'Branch {branch_name!r}: project {proj.get("groupName")!r} missing {missing}'
                )

    def test_resource_group_status_has_required_fields(self, session):
        result = get_project_group_status(session)
        for branch_name, projects in result.items():
            for proj in projects[:5]:
                for rgs in proj['resourceGroupStatuses']:
                    assert 'resourceName' in rgs
                    assert 'endDate' in rgs

    def test_group_name_is_lowercase(self, session):
        result = get_project_group_status(session)
        for _branch, projects in result.items():
            for proj in projects:
                assert proj['groupName'] == proj['groupName'].lower()

    def test_auto_renewing_always_false(self, session):
        result = get_project_group_status(session)
        for _branch, projects in result.items():
            for proj in projects:
                assert proj['autoRenewing'] is False

    def test_project_active_is_bool(self, session):
        result = get_project_group_status(session)
        for _branch, projects in result.items():
            for proj in projects:
                assert isinstance(proj['projectActive'], bool)

    def test_status_values(self, session):
        result = get_project_group_status(session)
        valid = {'ACTIVE', 'EXPIRING', 'EXPIRED', 'DEAD'}
        for _branch, projects in result.items():
            for proj in projects:
                assert proj['status'] in valid

    def test_expired_or_dead_has_days_expired(self, session):
        result = get_project_group_status(session)
        for _branch, projects in result.items():
            for proj in projects:
                if proj['status'] in ('EXPIRED', 'DEAD'):
                    assert 'days_expired' in proj
                    assert isinstance(proj['days_expired'], int)
                    assert proj['days_expired'] >= 0

    def test_active_or_expiring_has_days_remaining(self, session):
        result = get_project_group_status(session)
        for _branch, projects in result.items():
            for proj in projects:
                if proj['status'] in ('ACTIVE', 'EXPIRING'):
                    assert 'days_remaining' in proj
                    assert isinstance(proj['days_remaining'], int)
                    assert proj['days_remaining'] >= 0

    def test_future_projects_have_no_days_expired(self, session):
        result = get_project_group_status(session)
        for _branch, projects in result.items():
            for proj in projects:
                if proj['status'] in ('ACTIVE', 'EXPIRING'):
                    assert 'days_expired' not in proj

    def test_expiring_within_warning_period(self, session):
        from sam.queries.project_access import WARNING_PERIOD_DAYS
        result = get_project_group_status(session)
        for _branch, projects in result.items():
            for proj in projects:
                if proj['status'] == 'EXPIRING':
                    assert proj['days_remaining'] <= WARNING_PERIOD_DAYS

    def test_expiration_is_max_of_resource_end_dates(self, session):
        result = get_project_group_status(session)
        for _branch, projects in result.items():
            for proj in projects:
                if not proj['resourceGroupStatuses']:
                    continue
                end_dates = [rgs['endDate'] for rgs in proj['resourceGroupStatuses']]
                assert proj['expiration'] == max(end_dates)

    def test_branch_filter_single_branch(self, session):
        all_branches = get_project_group_status(session)
        target = next(iter(all_branches))
        filtered = get_project_group_status(session, access_branch=target)
        assert list(filtered.keys()) == [target]

    def test_branch_filter_unknown_returns_empty(self, session):
        result = get_project_group_status(session, access_branch='nonexistent-branch')
        assert result == {}

    def test_sorted_by_group_name_within_branch(self, session):
        result = get_project_group_status(session)
        for _branch, projects in result.items():
            names = [p['groupName'] for p in projects]
            assert names == sorted(names)
