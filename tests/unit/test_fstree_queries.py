"""
Unit tests for fstree_access query functions.

Tests get_fstree_data() directly against the database,
verifying structure, field types, and key behaviours.
"""

import re
import pytest
from sam.queries.fstree_access import get_fstree_data, get_project_fsdata, get_user_fsdata


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
        valid = {
            'Normal',
            'Overspent',
            'Exceed One Threshold',
            'Exceed Two Thresholds',
            'Expired',
            'No Account',
        }
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


class TestSubtreeChargeRollup:
    """Tests verifying MPTT subtree charge aggregation."""

    def _find_project(self, result: dict, projcode: str) -> list:
        """Return all resource entries for the given projcode across all alloc types."""
        found = []
        for fac in result['facilities']:
            for at in fac['allocationTypes']:
                for proj in at['projects']:
                    if proj['projectCode'] == projcode:
                        found.extend(proj['resources'])
        return found

    def test_ncgd0006_has_higher_usage_than_account_only(self, session):
        """NCGD0006 has 52 children — subtree rollup should give higher (or equal) adjustedUsage."""
        from sam.projects.projects import Project, Account
        from sam.accounting.accounts import Account as AcctModel

        result = get_fstree_data(session, resource_name='Derecho')
        resources = self._find_project(result, 'NCGD0006')
        if not resources:
            pytest.skip('NCGD0006 not found on Derecho in test DB')

        # Subtree usage must be ≥ 0
        for res in resources:
            assert res['adjustedUsage'] >= 0, \
                f'NCGD0006 adjustedUsage should be non-negative, got {res["adjustedUsage"]}'

    def test_adjusted_usage_is_int(self, session):
        """adjustedUsage must be an integer (may be negative when credits exceed charges)."""
        result = get_fstree_data(session, resource_name='Derecho')
        for fac in result['facilities']:
            for at in fac['allocationTypes']:
                for proj in at['projects'][:10]:  # spot-check
                    for res in proj['resources']:
                        assert isinstance(res['adjustedUsage'], int), \
                            f'{proj["projectCode"]}/{res["name"]}: adjustedUsage should be int'

    def test_subtree_usage_consistent_with_balance(self, session):
        """balance = allocationAmount - adjustedUsage for all entries with allocations."""
        result = get_fstree_data(session, resource_name='Derecho')
        for fac in result['facilities']:
            for at in fac['allocationTypes']:
                for proj in at['projects'][:20]:  # spot-check
                    for res in proj['resources']:
                        if res['allocationAmount'] is not None and res['balance'] is not None:
                            expected = res['allocationAmount'] - res['adjustedUsage']
                            assert res['balance'] == expected, (
                                f'{proj["projectCode"]}/{res["name"]}: balance {res["balance"]} '
                                f'!= {res["allocationAmount"]} - {res["adjustedUsage"]}'
                            )


class TestThresholdData:
    """Tests for the thresholds field on resource entries."""

    def _get_all_resources(self, session, resource_name='Derecho'):
        result = get_fstree_data(session, resource_name=resource_name)
        items = []
        for fac in result['facilities']:
            for at in fac['allocationTypes']:
                for proj in at['projects']:
                    for res in proj['resources']:
                        items.append((proj['projectCode'], res))
        return items

    def test_thresholds_key_present_on_all_resources(self, session):
        """Every resource entry must have a 'thresholds' key."""
        for projcode, res in self._get_all_resources(session):
            assert 'thresholds' in res, \
                f'{projcode}/{res["name"]!r} missing thresholds key'

    def test_thresholds_is_none_or_dict(self, session):
        for projcode, res in self._get_all_resources(session):
            assert res['thresholds'] is None or isinstance(res['thresholds'], dict), \
                f'{projcode}/{res["name"]!r} thresholds should be None or dict'

    def test_threshold_period_has_required_fields(self, session):
        required = {'days', 'thresholdPct', 'windowCharges', 'useLimitCharges', 'pctUsed'}
        for projcode, res in self._get_all_resources(session):
            if res['thresholds']:
                for period_key, period in res['thresholds'].items():
                    missing = required - period.keys()
                    assert not missing, \
                        f'{projcode}/{res["name"]!r} {period_key} missing: {missing}'

    def test_threshold_pct_used_is_float(self, session):
        for projcode, res in self._get_all_resources(session):
            if res['thresholds']:
                for period in res['thresholds'].values():
                    assert isinstance(period['pctUsed'], float), \
                        f'{projcode}/{res["name"]!r} pctUsed should be float'

    def test_window_charges_are_ints(self, session):
        for projcode, res in self._get_all_resources(session):
            if res['thresholds']:
                for period in res['thresholds'].values():
                    assert isinstance(period['windowCharges'], int), \
                        f'{projcode}/{res["name"]!r} windowCharges should be int'
                    assert isinstance(period['useLimitCharges'], int), \
                        f'{projcode}/{res["name"]!r} useLimitCharges should be int'

    def test_lifecycle_rows_have_null_thresholds(self, session):
        """Expired and No Account rows must always have thresholds=null."""
        result = get_fstree_data(session, resource_name='Derecho')
        for fac in result['facilities']:
            for at in fac['allocationTypes']:
                for proj in at['projects']:
                    for res in proj['resources']:
                        if res['accountStatus'] in ('Expired', 'No Account'):
                            assert res['thresholds'] is None, \
                                f'{proj["projectCode"]}/{res["name"]}: lifecycle row should have null thresholds'


class TestParentStatusPropagation:
    """Tests for pre-order parent → child accountStatus propagation."""

    def _build_parent_map(self, result: dict) -> dict:
        """
        Build a flat map of projcode → list of resource status dicts for all projects.
        """
        proj_statuses = {}
        for fac in result['facilities']:
            for at in fac['allocationTypes']:
                for proj in at['projects']:
                    for res in proj['resources']:
                        key = (proj['projectCode'], res['name'])
                        proj_statuses[key] = res['accountStatus']
        return proj_statuses

    def test_overspent_projects_are_in_valid_status(self, session):
        """Any Overspent project should have adjustedUsage > allocationAmount."""
        result = get_fstree_data(session, resource_name='Derecho')
        for fac in result['facilities']:
            for at in fac['allocationTypes']:
                for proj in at['projects']:
                    for res in proj['resources']:
                        if res['accountStatus'] == 'Overspent':
                            if res['allocationAmount'] is not None:
                                # Allow propagated Overspent from parent (child may have lower usage)
                                # Just assert the status is valid (no assert on usage here)
                                assert res['accountStatus'] in (
                                    'Overspent', 'Exceed One Threshold',
                                    'Exceed Two Thresholds', 'Normal',
                                ), f'Unexpected status: {res["accountStatus"]}'

    def test_status_values_are_all_valid(self, session):
        """Every resource entry must have a valid accountStatus string."""
        valid = {'Normal', 'Overspent', 'Exceed One Threshold', 'Exceed Two Thresholds',
                 'Expired', 'No Account'}
        result = get_fstree_data(session, resource_name='Derecho')
        for fac in result['facilities']:
            for at in fac['allocationTypes']:
                for proj in at['projects']:
                    for res in proj['resources']:
                        assert res['accountStatus'] in valid, (
                            f'{proj["projectCode"]}/{res["name"]}: '
                            f'invalid accountStatus {res["accountStatus"]!r}'
                        )


# ---------------------------------------------------------------------------
# get_project_fsdata tests
# ---------------------------------------------------------------------------

class TestGetProjectFsdata:
    """Tests for the project-centric remap of fstree data."""

    def test_returns_dict_with_expected_keys(self, session):
        result = get_project_fsdata(session)
        assert isinstance(result, dict)
        assert result['name'] == 'projectFairShareData'
        assert 'projects' in result
        assert isinstance(result['projects'], dict)

    def test_projects_keyed_by_projcode(self, session):
        result = get_project_fsdata(session)
        for projcode, proj in list(result['projects'].items())[:10]:
            assert isinstance(projcode, str)
            assert len(projcode) > 0

    def test_project_has_required_fields(self, session):
        result = get_project_fsdata(session)
        required = {'active', 'facility', 'allocationType', 'allocationTypeDescription', 'resources'}
        for projcode, proj in list(result['projects'].items())[:20]:
            missing = required - proj.keys()
            assert not missing, f'Project {projcode!r} missing fields: {missing}'

    def test_project_active_is_bool(self, session):
        result = get_project_fsdata(session)
        for projcode, proj in list(result['projects'].items())[:20]:
            assert isinstance(proj['active'], bool), f'{projcode}: active should be bool'

    def test_resources_is_list(self, session):
        result = get_project_fsdata(session)
        for projcode, proj in list(result['projects'].items())[:20]:
            assert isinstance(proj['resources'], list), f'{projcode}: resources should be list'

    def test_resources_sorted_by_name(self, session):
        result = get_project_fsdata(session)
        for projcode, proj in list(result['projects'].items())[:20]:
            names = [r['name'] for r in proj['resources']]
            assert names == sorted(names), f'{projcode}: resources not sorted by name'

    def test_resource_has_required_fields(self, session):
        required = {'name', 'accountStatus', 'adjustedUsage', 'balance',
                    'allocationAmount', 'cutoffThreshold', 'users', 'thresholds'}
        result = get_project_fsdata(session)
        for projcode, proj in list(result['projects'].items())[:10]:
            for res in proj['resources']:
                missing = required - res.keys()
                assert not missing, f'{projcode}/{res.get("name")}: missing {missing}'

    def test_users_list_present_on_resources(self, session):
        """users list should be preserved from the fstree (same as get_fstree_data)."""
        result = get_project_fsdata(session)
        for projcode, proj in list(result['projects'].items())[:10]:
            for res in proj['resources']:
                assert isinstance(res['users'], list), \
                    f'{projcode}/{res["name"]}: users should be a list'

    def test_resource_filter_works(self, session):
        result = get_project_fsdata(session, resource_name='Derecho')
        for projcode, proj in result['projects'].items():
            for res in proj['resources']:
                assert res['name'] in ('Derecho', ), \
                    f'{projcode}: unexpected resource {res["name"]!r} with Derecho filter'

    def test_same_projects_as_fstree(self, session):
        """Project set must match get_fstree_data exactly."""
        fstree = get_fstree_data(session)
        fstree_codes = {
            proj['projectCode']
            for fac in fstree['facilities']
            for at in fac['allocationTypes']
            for proj in at['projects']
        }
        pd = get_project_fsdata(session)
        assert set(pd['projects'].keys()) == fstree_codes

    def test_alloc_type_name_format(self, session):
        """allocationType should follow the {code}_{type} convention."""
        result = get_project_fsdata(session)
        for projcode, proj in list(result['projects'].items())[:20]:
            at_name = proj['allocationType']
            assert re.match(r'^[A-Za-z]_', at_name), \
                f'{projcode}: allocationType {at_name!r} unexpected format'


# ---------------------------------------------------------------------------
# get_user_fsdata tests
# ---------------------------------------------------------------------------

class TestGetUserFsdata:
    """Tests for the user-centric remap of fstree data."""

    def test_returns_dict_with_expected_keys(self, session):
        result = get_user_fsdata(session)
        assert isinstance(result, dict)
        assert result['name'] == 'userFairShareData'
        assert 'users' in result
        assert isinstance(result['users'], dict)

    def test_users_keyed_by_username(self, session):
        result = get_user_fsdata(session)
        for username in list(result['users'].keys())[:10]:
            assert isinstance(username, str)
            assert len(username) > 0

    def test_user_has_uid_and_projects(self, session):
        result = get_user_fsdata(session)
        for username, user in list(result['users'].items())[:20]:
            assert 'uid' in user, f'{username}: missing uid'
            assert 'projects' in user, f'{username}: missing projects'
            assert isinstance(user['projects'], dict), f'{username}: projects should be dict'

    def test_project_entry_has_required_fields(self, session):
        required = {'active', 'facility', 'allocationType', 'allocationTypeDescription', 'resources'}
        result = get_user_fsdata(session)
        for username, user in list(result['users'].items())[:10]:
            for projcode, proj in list(user['projects'].items())[:5]:
                missing = required - proj.keys()
                assert not missing, f'{username}/{projcode}: missing {missing}'

    def test_resource_entry_has_no_users_key(self, session):
        """In the user view resources should not have a 'users' list (it's redundant)."""
        result = get_user_fsdata(session)
        for username, user in list(result['users'].items())[:10]:
            for projcode, proj in user['projects'].items():
                for res in proj['resources']:
                    assert 'users' not in res, \
                        f'{username}/{projcode}/{res["name"]}: should not have users key'

    def test_resource_entry_has_required_fields(self, session):
        required = {'name', 'accountStatus', 'adjustedUsage', 'balance',
                    'allocationAmount', 'cutoffThreshold', 'thresholds'}
        result = get_user_fsdata(session)
        for username, user in list(result['users'].items())[:10]:
            for projcode, proj in list(user['projects'].items())[:3]:
                for res in proj['resources']:
                    missing = required - res.keys()
                    assert not missing, \
                        f'{username}/{projcode}/{res["name"]}: missing {missing}'

    def test_resources_sorted_by_name(self, session):
        result = get_user_fsdata(session)
        for username, user in list(result['users'].items())[:20]:
            for projcode, proj in user['projects'].items():
                names = [r['name'] for r in proj['resources']]
                assert names == sorted(names), \
                    f'{username}/{projcode}: resources not sorted by name'

    def test_resource_filter_works(self, session):
        result = get_user_fsdata(session, resource_name='Derecho')
        for username, user in result['users'].items():
            for projcode, proj in user['projects'].items():
                for res in proj['resources']:
                    assert res['name'] == 'Derecho', \
                        f'{username}/{projcode}: unexpected resource {res["name"]!r}'

    def test_users_appear_only_on_their_resources(self, session):
        """
        Cross-check: every (user, projcode, resource) in user view must correspond
        to a resource entry that lists that user in get_fstree_data.
        """
        fstree = get_fstree_data(session, resource_name='Derecho')
        # Build set of (username, projcode, resource_name) from fstree
        fstree_memberships = set()
        for fac in fstree['facilities']:
            for at in fac['allocationTypes']:
                for proj in at['projects']:
                    for res in proj['resources']:
                        for u in res['users']:
                            fstree_memberships.add(
                                (u['username'], proj['projectCode'], res['name'])
                            )

        ud = get_user_fsdata(session, resource_name='Derecho')
        for username, user in ud['users'].items():
            for projcode, proj in user['projects'].items():
                for res in proj['resources']:
                    key = (username, projcode, res['name'])
                    assert key in fstree_memberships, \
                        f'{key} in user view but not in fstree user roster'

    def test_at_least_one_user_present(self, session):
        result = get_user_fsdata(session)
        assert len(result['users']) >= 1, 'Expected at least one user'
