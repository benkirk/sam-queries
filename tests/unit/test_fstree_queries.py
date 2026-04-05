"""
Unit tests for fstree_access query functions.

Tests get_fstree_data() directly against the database,
verifying structure, field types, and key behaviours.
"""

import re
import pytest
from sam.queries.fstree_access import get_fstree_data


class TestGetFstreeDataStructure:
    """Tests for top-level response structure."""

    def test_returns_dict_with_name_and_facilities(self, session):
        result = get_fstree_data(session)
        assert isinstance(result, dict)
        assert 'name' in result
        assert 'facilities' in result

    def test_name_is_fairShareTree(self, session):
        result = get_fstree_data(session)
        assert result['name'] == 'fairShareTree'

    def test_facilities_is_list(self, session):
        result = get_fstree_data(session)
        assert isinstance(result['facilities'], list)

    def test_at_least_one_facility(self, session):
        result = get_fstree_data(session)
        assert len(result['facilities']) >= 1, 'Expected at least one facility'


class TestFacilityStructure:
    """Tests for facility-level fields."""

    def test_facility_has_required_fields(self, session):
        result = get_fstree_data(session)
        required = {'name', 'description', 'fairSharePercentage', 'allocationTypes'}
        for fac in result['facilities']:
            missing = required - fac.keys()
            assert not missing, f'Facility {fac.get("name")!r} missing: {missing}'

    def test_facility_fairSharePercentage_is_float(self, session):
        result = get_fstree_data(session)
        for fac in result['facilities']:
            assert isinstance(fac['fairSharePercentage'], float), \
                f'Facility {fac["name"]!r} fairSharePercentage should be float'

    def test_allocation_types_is_list(self, session):
        result = get_fstree_data(session)
        for fac in result['facilities']:
            assert isinstance(fac['allocationTypes'], list), \
                f'Facility {fac["name"]!r} allocationTypes should be list'


class TestAllocationTypeStructure:
    """Tests for allocationTypes-level fields."""

    def test_alloc_type_has_required_fields(self, session):
        result = get_fstree_data(session)
        required = {'name', 'description', 'fairSharePercentage', 'projects'}
        for fac in result['facilities']:
            for at in fac['allocationTypes'][:3]:  # spot-check first 3
                missing = required - at.keys()
                assert not missing, \
                    f'AllocationType in {fac["name"]!r} missing: {missing}'

    def test_alloc_type_name_convention(self, session):
        """AllocationType name must match {facilityCode}_{typeNoSpecialChars}."""
        result = get_fstree_data(session)
        for fac in result['facilities']:
            for at in fac['allocationTypes']:
                # Name should be non-empty and contain an underscore after a single char
                name = at['name']
                assert re.match(r'^[A-Za-z]_', name), \
                    f'AllocationType name {name!r} does not match {{code}}_{{type}} convention'
                # No special chars after the underscore
                suffix = name.split('_', 1)[1] if '_' in name else name
                assert re.match(r'^\w*$', suffix), \
                    f'AllocationType name suffix {suffix!r} contains special chars'

    def test_alloc_type_fairSharePercentage_is_float(self, session):
        result = get_fstree_data(session)
        for fac in result['facilities']:
            for at in fac['allocationTypes'][:3]:
                assert isinstance(at['fairSharePercentage'], float), \
                    f'AllocationType {at["name"]!r} fairSharePercentage should be float'

    def test_projects_is_list(self, session):
        result = get_fstree_data(session)
        for fac in result['facilities']:
            for at in fac['allocationTypes'][:3]:
                assert isinstance(at['projects'], list), \
                    f'AllocationType {at["name"]!r} projects should be list'


class TestProjectStructure:
    """Tests for project-level fields."""

    def test_project_has_required_fields(self, session):
        result = get_fstree_data(session)
        required = {'projectCode', 'active', 'resources'}
        for fac in result['facilities']:
            for at in fac['allocationTypes'][:2]:
                for proj in at['projects'][:5]:  # spot-check
                    missing = required - proj.keys()
                    assert not missing, \
                        f'Project {proj.get("projectCode")!r} missing: {missing}'

    def test_project_active_is_bool(self, session):
        result = get_fstree_data(session)
        for fac in result['facilities']:
            for at in fac['allocationTypes'][:2]:
                for proj in at['projects'][:5]:
                    assert isinstance(proj['active'], bool), \
                        f'Project {proj["projectCode"]!r} active should be bool'

    def test_resources_is_list(self, session):
        result = get_fstree_data(session)
        for fac in result['facilities']:
            for at in fac['allocationTypes'][:2]:
                for proj in at['projects'][:5]:
                    assert isinstance(proj['resources'], list), \
                        f'Project {proj["projectCode"]!r} resources should be list'

    def test_projects_sorted_by_code(self, session):
        result = get_fstree_data(session)
        for fac in result['facilities']:
            for at in fac['allocationTypes'][:2]:
                codes = [p['projectCode'] for p in at['projects']]
                assert codes == sorted(codes), \
                    f'Projects in {at["name"]!r} are not sorted by projectCode'


class TestResourceStructure:
    """Tests for resource-level fields within projects."""

    def _get_first_resources(self, session, n=20):
        """Collect up to n resource entries across the tree."""
        result = get_fstree_data(session)
        items = []
        for fac in result['facilities']:
            for at in fac['allocationTypes']:
                for proj in at['projects']:
                    for res in proj['resources']:
                        items.append(res)
                        if len(items) >= n:
                            return items
        return items

    def test_resource_has_required_fields(self, session):
        required = {'name', 'accountStatus', 'cutoffThreshold',
                    'adjustedUsage', 'balance', 'allocationAmount', 'users'}
        for res in self._get_first_resources(session):
            missing = required - res.keys()
            assert not missing, f'Resource {res.get("name")!r} missing: {missing}'

    def test_account_status_is_valid(self, session):
        valid = {'Normal', 'Overspent'}
        for res in self._get_first_resources(session):
            assert res['accountStatus'] in valid, \
                f'Resource {res["name"]!r} accountStatus {res["accountStatus"]!r} is not valid'

    def test_cutoff_threshold_is_int(self, session):
        for res in self._get_first_resources(session):
            assert isinstance(res['cutoffThreshold'], int), \
                f'Resource {res["name"]!r} cutoffThreshold should be int'

    def test_adjusted_usage_is_int(self, session):
        for res in self._get_first_resources(session):
            if res['adjustedUsage'] is not None:
                assert isinstance(res['adjustedUsage'], int), \
                    f'Resource {res["name"]!r} adjustedUsage should be int'

    def test_users_is_list(self, session):
        for res in self._get_first_resources(session):
            assert isinstance(res['users'], list), \
                f'Resource {res["name"]!r} users should be list'

    def test_user_has_username_and_uid(self, session):
        for res in self._get_first_resources(session):
            for user in res['users'][:3]:
                assert 'username' in user, f'User entry missing username in {res["name"]!r}'
                assert 'uid' in user, f'User entry missing uid in {res["name"]!r}'
                assert isinstance(user['uid'], int), \
                    f'uid should be int in {res["name"]!r}'

    def test_balance_equals_allocated_minus_usage(self, session):
        """balance = allocationAmount - adjustedUsage when allocationAmount is not None."""
        for res in self._get_first_resources(session):
            if res['allocationAmount'] is not None and res['balance'] is not None:
                expected = res['allocationAmount'] - res['adjustedUsage']
                assert res['balance'] == expected, \
                    f'Resource {res["name"]!r}: balance {res["balance"]} != ' \
                    f'{res["allocationAmount"]} - {res["adjustedUsage"]}'


class TestResourceFilter:
    """Tests for resource_name filter."""

    def test_filter_by_resource_name(self, session):
        result = get_fstree_data(session, resource_name='Derecho')
        # Every resource entry should be Derecho
        for fac in result['facilities']:
            for at in fac['allocationTypes']:
                for proj in at['projects']:
                    for res in proj['resources']:
                        assert res['name'] == 'Derecho', \
                            f'Expected only Derecho resources, got {res["name"]!r}'

    def test_filter_by_unknown_resource_returns_empty(self, session):
        result = get_fstree_data(session, resource_name='NonexistentResource99')
        assert result['name'] == 'fairShareTree'
        assert result['facilities'] == [], \
            'Unknown resource should produce empty facilities list'

    def test_no_filter_returns_multiple_resources(self, session):
        result = get_fstree_data(session)
        all_resource_names = set()
        for fac in result['facilities']:
            for at in fac['allocationTypes']:
                for proj in at['projects']:
                    for res in proj['resources']:
                        all_resource_names.add(res['name'])
        assert len(all_resource_names) >= 2, \
            'Unfiltered result should include multiple resources'
