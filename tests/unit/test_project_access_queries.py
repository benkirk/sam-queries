"""
Unit tests for project_access query functions.

Tests get_project_group_status() directly against the database,
verifying structure, field types, and key behaviours.
"""

import pytest
from sam.queries.project_access import get_project_group_status


class TestGetProjectGroupStatus:
    """Tests for get_project_group_status()."""

    def test_returns_dict_keyed_by_branch(self, session):
        result = get_project_group_status(session)
        assert isinstance(result, dict)
        assert len(result) >= 1, 'Expected at least one access branch'

    def test_each_branch_is_a_list(self, session):
        result = get_project_group_status(session)
        for branch_name, projects in result.items():
            assert isinstance(projects, list), \
                f'Branch {branch_name!r} value should be a list'

    def test_project_entry_has_required_fields(self, session):
        result = get_project_group_status(session)
        required = {'groupName', 'panel', 'autoRenewing', 'projectActive',
                    'status', 'expiration', 'resourceGroupStatuses'}
        for branch_name, projects in result.items():
            for proj in projects[:5]:  # spot-check first 5 per branch
                missing = required - proj.keys()
                assert not missing, \
                    f'Branch {branch_name!r}: project {proj.get("groupName")!r} missing {missing}'

    def test_resource_group_status_has_required_fields(self, session):
        result = get_project_group_status(session)
        for branch_name, projects in result.items():
            for proj in projects[:5]:
                for rgs in proj['resourceGroupStatuses']:
                    assert 'resourceName' in rgs, \
                        f'resourceGroupStatuses entry missing resourceName in {branch_name!r}'
                    assert 'endDate' in rgs, \
                        f'resourceGroupStatuses entry missing endDate in {branch_name!r}'

    def test_group_name_is_lowercase(self, session):
        result = get_project_group_status(session)
        for branch_name, projects in result.items():
            for proj in projects:
                assert proj['groupName'] == proj['groupName'].lower(), \
                    f'groupName not lowercase: {proj["groupName"]!r}'

    def test_auto_renewing_always_false(self, session):
        result = get_project_group_status(session)
        for branch_name, projects in result.items():
            for proj in projects:
                assert proj['autoRenewing'] is False, \
                    f'autoRenewing should always be False; got {proj["autoRenewing"]!r} in {proj["groupName"]!r}'

    def test_project_active_is_bool(self, session):
        result = get_project_group_status(session)
        for branch_name, projects in result.items():
            for proj in projects:
                assert isinstance(proj['projectActive'], bool), \
                    f'projectActive should be bool for {proj["groupName"]!r}'

    def test_status_values(self, session):
        result = get_project_group_status(session)
        valid_statuses = {'ACTIVE', 'EXPIRING', 'EXPIRED', 'DEAD'}
        for branch_name, projects in result.items():
            for proj in projects:
                assert proj['status'] in valid_statuses, \
                    f'Unexpected status {proj["status"]!r} for {proj["groupName"]!r}'

    def test_expired_or_dead_has_days_expired(self, session):
        result = get_project_group_status(session)
        for branch_name, projects in result.items():
            for proj in projects:
                if proj['status'] in ('EXPIRED', 'DEAD'):
                    assert 'days_expired' in proj, \
                        f'{proj["status"]} project {proj["groupName"]!r} missing days_expired'
                    assert isinstance(proj['days_expired'], int)
                    assert proj['days_expired'] >= 0

    def test_active_or_expiring_has_days_remaining(self, session):
        result = get_project_group_status(session)
        for branch_name, projects in result.items():
            for proj in projects:
                if proj['status'] in ('ACTIVE', 'EXPIRING'):
                    assert 'days_remaining' in proj, \
                        f'{proj["status"]} project {proj["groupName"]!r} missing days_remaining'
                    assert isinstance(proj['days_remaining'], int)
                    assert proj['days_remaining'] >= 0

    def test_future_projects_have_no_days_expired(self, session):
        result = get_project_group_status(session)
        for branch_name, projects in result.items():
            for proj in projects:
                if proj['status'] in ('ACTIVE', 'EXPIRING'):
                    assert 'days_expired' not in proj, \
                        f'{proj["status"]} project {proj["groupName"]!r} should not have days_expired'

    def test_expiring_within_warning_period(self, session):
        from sam.queries.project_access import WARNING_PERIOD_DAYS
        result = get_project_group_status(session)
        for branch_name, projects in result.items():
            for proj in projects:
                if proj['status'] == 'EXPIRING':
                    assert proj['days_remaining'] <= WARNING_PERIOD_DAYS, \
                        f'EXPIRING project {proj["groupName"]!r} days_remaining={proj["days_remaining"]} > {WARNING_PERIOD_DAYS}'

    def test_expiration_is_max_of_resource_end_dates(self, session):
        """expiration must equal the latest endDate across all resource entries."""
        result = get_project_group_status(session)
        for branch_name, projects in result.items():
            for proj in projects:
                if not proj['resourceGroupStatuses']:
                    continue
                end_dates = [rgs['endDate'] for rgs in proj['resourceGroupStatuses']]
                assert proj['expiration'] == max(end_dates), (
                    f'{proj["groupName"]!r} in {branch_name!r}: expiration '
                    f'{proj["expiration"]!r} != max end_date {max(end_dates)!r}'
                )

    def test_branch_filter_single_branch(self, session):
        all_branches = get_project_group_status(session)
        target = next(iter(all_branches))  # pick any branch

        filtered = get_project_group_status(session, access_branch=target)
        assert list(filtered.keys()) == [target], \
            f'Expected only {target!r} but got {list(filtered.keys())}'

    def test_branch_filter_unknown_returns_empty(self, session):
        result = get_project_group_status(session, access_branch='nonexistent-branch')
        assert result == {}, 'Expected empty dict for unknown branch'

    def test_sorted_by_group_name_within_branch(self, session):
        result = get_project_group_status(session)
        for branch_name, projects in result.items():
            names = [p['groupName'] for p in projects]
            assert names == sorted(names), \
                f'Branch {branch_name!r}: projects not sorted by groupName'
