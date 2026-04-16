"""Unit tests for sam.queries.fstree_access.

Ported from tests/unit/test_fstree_queries.py. Transformations:

- Hardcoded `'Derecho'` throughout is replaced by the `hpc_resource`
  fixture. Tests that iterate over the whole tree (no filter) are
  unchanged.
- `test_ncgd0006_has_higher_usage_than_account_only` is generalized:
  any project in the tree with children should produce a non-negative
  adjusted usage. The legacy version named NCGD0006 specifically but
  only asserted `>= 0`, so the name was more specific than the assertion.
- Structural spot-checks (`[:3]`, `[:5]`, `[:20]`) are kept — they're
  already snapshot-safe since they only read the first N entries.
- `fstree_all` / `fstree_hpc` / `project_fs_all` / `user_fs_all` are
  module-scoped fixtures that cache the result of the expensive
  get_fstree_data / get_project_fsdata / get_user_fsdata calls ONCE
  per module. Each call walks the entire sam database (~5-15s with
  no LRU cache under TestingConfig) so re-running per test made this
  file 10× slower than the legacy suite. Caching the read-only dict
  drops the cost back to one call per module.
"""
import re

import pytest
from sqlalchemy.orm import sessionmaker

from sam.queries.fstree_access import get_fstree_data, get_project_fsdata, get_user_fsdata


pytestmark = pytest.mark.unit


# ---- Module-scoped expensive-query caches ---------------------------------


def _throwaway_session(engine):
    """Read-only session for fixture setup, not bound to the test transaction."""
    return sessionmaker(bind=engine, autoflush=False, future=True)()


@pytest.fixture(scope='module')
def fstree_all(engine):
    """Full fstree (no filter). Queried once per module."""
    with _throwaway_session(engine) as s:
        return get_fstree_data(s)


@pytest.fixture(scope='module')
def fstree_hpc(engine, _hpc_resource_id):
    """(fstree_dict, resource_name) filtered to an active HPC resource."""
    from sam import Resource
    with _throwaway_session(engine) as s:
        resource_name = s.get(Resource, _hpc_resource_id).resource_name
        return get_fstree_data(s, resource_name=resource_name), resource_name


@pytest.fixture(scope='module')
def project_fs_all(engine):
    with _throwaway_session(engine) as s:
        return get_project_fsdata(s)


@pytest.fixture(scope='module')
def project_fs_hpc(engine, _hpc_resource_id):
    from sam import Resource
    with _throwaway_session(engine) as s:
        resource_name = s.get(Resource, _hpc_resource_id).resource_name
        return get_project_fsdata(s, resource_name=resource_name), resource_name


@pytest.fixture(scope='module')
def user_fs_all(engine):
    with _throwaway_session(engine) as s:
        return get_user_fsdata(s)


@pytest.fixture(scope='module')
def user_fs_hpc(engine, _hpc_resource_id):
    from sam import Resource
    with _throwaway_session(engine) as s:
        resource_name = s.get(Resource, _hpc_resource_id).resource_name
        return get_user_fsdata(s, resource_name=resource_name), resource_name


# ============================================================================
# Top-level response shape
# ============================================================================


class TestGetFstreeDataStructure:

    def test_returns_dict_with_name_and_facilities(self, fstree_all):
        assert isinstance(fstree_all, dict)
        assert 'name' in fstree_all
        assert 'facilities' in fstree_all

    def test_name_is_fairShareTree(self, fstree_all):
        assert fstree_all['name'] == 'fairShareTree'

    def test_facilities_is_list(self, fstree_all):
        assert isinstance(fstree_all['facilities'], list)

    def test_at_least_one_facility(self, fstree_all):
        assert len(fstree_all['facilities']) >= 1


# ============================================================================
# Facility level
# ============================================================================


class TestFacilityStructure:

    def test_facility_has_required_fields(self, fstree_all):
        required = {'name', 'description', 'fairSharePercentage', 'allocationTypes'}
        for fac in fstree_all['facilities']:
            assert not (required - fac.keys()), (
                f'Facility {fac.get("name")!r} missing: {required - fac.keys()}'
            )

    def test_facility_fairSharePercentage_is_float(self, fstree_all):
        for fac in fstree_all['facilities']:
            assert isinstance(fac['fairSharePercentage'], float)

    def test_allocation_types_is_list(self, fstree_all):
        for fac in fstree_all['facilities']:
            assert isinstance(fac['allocationTypes'], list)


# ============================================================================
# AllocationType level
# ============================================================================


class TestAllocationTypeStructure:

    def test_alloc_type_has_required_fields(self, fstree_all):
        required = {'name', 'description', 'fairSharePercentage', 'projects'}
        for fac in fstree_all['facilities']:
            for at in fac['allocationTypes'][:3]:
                assert not (required - at.keys())

    def test_alloc_type_name_convention(self, fstree_all):
        """AllocationType name should follow the `<code>_<type>` convention."""
        for fac in fstree_all['facilities']:
            for at in fac['allocationTypes']:
                name = at['name']
                assert re.match(r'^[A-Za-z]_', name), f'bad name: {name!r}'
                suffix = name.split('_', 1)[1] if '_' in name else name
                assert re.match(r'^\w*$', suffix), f'bad suffix: {suffix!r}'

    def test_alloc_type_fairSharePercentage_is_float(self, fstree_all):
        for fac in fstree_all['facilities']:
            for at in fac['allocationTypes'][:3]:
                assert isinstance(at['fairSharePercentage'], float)

    def test_projects_is_list(self, fstree_all):
        for fac in fstree_all['facilities']:
            for at in fac['allocationTypes'][:3]:
                assert isinstance(at['projects'], list)


# ============================================================================
# Project level
# ============================================================================


class TestProjectStructure:

    def test_project_has_required_fields(self, fstree_all):
        required = {'projectCode', 'active', 'resources'}
        for fac in fstree_all['facilities']:
            for at in fac['allocationTypes'][:2]:
                for proj in at['projects'][:5]:
                    assert not (required - proj.keys())

    def test_project_active_is_bool(self, fstree_all):
        for fac in fstree_all['facilities']:
            for at in fac['allocationTypes'][:2]:
                for proj in at['projects'][:5]:
                    assert isinstance(proj['active'], bool)

    def test_resources_is_list(self, fstree_all):
        for fac in fstree_all['facilities']:
            for at in fac['allocationTypes'][:2]:
                for proj in at['projects'][:5]:
                    assert isinstance(proj['resources'], list)

    def test_projects_sorted_by_code(self, fstree_all):
        for fac in fstree_all['facilities']:
            for at in fac['allocationTypes'][:2]:
                codes = [p['projectCode'] for p in at['projects']]
                assert codes == sorted(codes)


# ============================================================================
# Resource level
# ============================================================================


class TestResourceStructure:

    @staticmethod
    def _first_resources(fstree, n=20):
        items = []
        for fac in fstree['facilities']:
            for at in fac['allocationTypes']:
                for proj in at['projects']:
                    for res in proj['resources']:
                        items.append(res)
                        if len(items) >= n:
                            return items
        return items

    def test_resource_has_required_fields(self, fstree_all):
        required = {'name', 'accountStatus', 'cutoffThreshold',
                    'adjustedUsage', 'balance', 'allocationAmount', 'users'}
        for res in self._first_resources(fstree_all):
            assert not (required - res.keys()), f'{res.get("name")}: {required - res.keys()}'

    def test_account_status_is_valid(self, fstree_all):
        valid = {
            'Normal',
            'Overspent',
            'Exceed One Threshold',
            'Exceed Two Thresholds',
            'Expired',
            'No Account',
        }
        for res in self._first_resources(fstree_all):
            assert res['accountStatus'] in valid

    def test_cutoff_threshold_is_int(self, fstree_all):
        for res in self._first_resources(fstree_all):
            assert isinstance(res['cutoffThreshold'], int)

    def test_adjusted_usage_is_int(self, fstree_all):
        for res in self._first_resources(fstree_all):
            if res['adjustedUsage'] is not None:
                assert isinstance(res['adjustedUsage'], int)

    def test_users_is_list(self, fstree_all):
        for res in self._first_resources(fstree_all):
            assert isinstance(res['users'], list)

    def test_user_has_username_and_uid(self, fstree_all):
        for res in self._first_resources(fstree_all):
            for user in res['users'][:3]:
                assert 'username' in user
                assert 'uid' in user
                assert isinstance(user['uid'], int)

    def test_balance_equals_allocated_minus_usage(self, fstree_all):
        for res in self._first_resources(fstree_all):
            if res['allocationAmount'] is not None and res['balance'] is not None:
                expected = res['allocationAmount'] - res['adjustedUsage']
                assert res['balance'] == expected


# ============================================================================
# Resource filter
# ============================================================================


class TestResourceFilter:

    def test_filter_by_resource_name(self, fstree_hpc):
        result, resource_name = fstree_hpc
        for fac in result['facilities']:
            for at in fac['allocationTypes']:
                for proj in at['projects']:
                    for res in proj['resources']:
                        assert res['name'] == resource_name

    def test_filter_by_unknown_resource_returns_empty(self, session):
        # This test is the one case where we need a fresh query (custom filter).
        result = get_fstree_data(session, resource_name='NonexistentResource99')
        assert result['name'] == 'fairShareTree'
        assert result['facilities'] == []

    def test_no_filter_returns_multiple_resources(self, fstree_all):
        seen = set()
        for fac in fstree_all['facilities']:
            for at in fac['allocationTypes']:
                for proj in at['projects']:
                    for res in proj['resources']:
                        seen.add(res['name'])
        assert len(seen) >= 2


# ============================================================================
# Subtree charge rollup (MPTT)
# ============================================================================


class TestSubtreeChargeRollup:
    """Sanity checks for MPTT subtree aggregation."""

    def test_subtree_usage_is_non_negative(self, fstree_hpc, subtree_project):
        """Any project with children should produce non-negative adjusted usage."""
        result, _ = fstree_hpc
        for fac in result['facilities']:
            for at in fac['allocationTypes']:
                for proj in at['projects']:
                    if proj['projectCode'] == subtree_project.projcode:
                        for res in proj['resources']:
                            assert res['adjustedUsage'] >= 0

    def test_adjusted_usage_is_int(self, fstree_hpc):
        """adjustedUsage must be int (can be negative when credits exceed charges)."""
        result, _ = fstree_hpc
        for fac in result['facilities']:
            for at in fac['allocationTypes']:
                for proj in at['projects'][:10]:
                    for res in proj['resources']:
                        assert isinstance(res['adjustedUsage'], int)

    def test_subtree_usage_consistent_with_balance(self, fstree_hpc):
        """balance == allocationAmount - adjustedUsage for all entries with allocations."""
        result, _ = fstree_hpc
        for fac in result['facilities']:
            for at in fac['allocationTypes']:
                for proj in at['projects'][:20]:
                    for res in proj['resources']:
                        if res['allocationAmount'] is not None and res['balance'] is not None:
                            expected = res['allocationAmount'] - res['adjustedUsage']
                            assert res['balance'] == expected


# ============================================================================
# Thresholds
# ============================================================================


class TestThresholdData:

    @staticmethod
    def _all_resources(fstree):
        items = []
        for fac in fstree['facilities']:
            for at in fac['allocationTypes']:
                for proj in at['projects']:
                    for res in proj['resources']:
                        items.append((proj['projectCode'], res))
        return items

    def test_thresholds_key_present_on_all_resources(self, fstree_hpc):
        result, _ = fstree_hpc
        for _projcode, res in self._all_resources(result):
            assert 'thresholds' in res

    def test_thresholds_is_none_or_dict(self, fstree_hpc):
        result, _ = fstree_hpc
        for _projcode, res in self._all_resources(result):
            assert res['thresholds'] is None or isinstance(res['thresholds'], dict)

    def test_threshold_period_has_required_fields(self, fstree_hpc):
        result, _ = fstree_hpc
        required = {'days', 'thresholdPct', 'windowCharges', 'useLimitCharges', 'pctUsed'}
        for _projcode, res in self._all_resources(result):
            if res['thresholds']:
                for _period_key, period in res['thresholds'].items():
                    assert not (required - period.keys())

    def test_threshold_pct_used_is_float(self, fstree_hpc):
        result, _ = fstree_hpc
        for _projcode, res in self._all_resources(result):
            if res['thresholds']:
                for period in res['thresholds'].values():
                    assert isinstance(period['pctUsed'], float)

    def test_window_charges_are_ints(self, fstree_hpc):
        result, _ = fstree_hpc
        for _projcode, res in self._all_resources(result):
            if res['thresholds']:
                for period in res['thresholds'].values():
                    assert isinstance(period['windowCharges'], int)
                    assert isinstance(period['useLimitCharges'], int)

    def test_lifecycle_rows_have_null_thresholds(self, fstree_hpc):
        """Expired and No Account rows must always have thresholds=null."""
        result, _ = fstree_hpc
        for fac in result['facilities']:
            for at in fac['allocationTypes']:
                for proj in at['projects']:
                    for res in proj['resources']:
                        if res['accountStatus'] in ('Expired', 'No Account'):
                            assert res['thresholds'] is None


# ============================================================================
# Parent → child status propagation
# ============================================================================


class TestParentStatusPropagation:

    def test_status_values_are_all_valid(self, fstree_hpc):
        valid = {'Normal', 'Overspent', 'Exceed One Threshold', 'Exceed Two Thresholds',
                 'Expired', 'No Account'}
        result, _ = fstree_hpc
        for fac in result['facilities']:
            for at in fac['allocationTypes']:
                for proj in at['projects']:
                    for res in proj['resources']:
                        assert res['accountStatus'] in valid


# ============================================================================
# get_project_fsdata
# ============================================================================


class TestGetProjectFsdata:

    def test_returns_dict_with_expected_keys(self, project_fs_all):
        assert isinstance(project_fs_all, dict)
        assert project_fs_all['name'] == 'projectFairShareData'
        assert 'projects' in project_fs_all
        assert isinstance(project_fs_all['projects'], dict)

    def test_projects_keyed_by_projcode(self, project_fs_all):
        for projcode, _proj in list(project_fs_all['projects'].items())[:10]:
            assert isinstance(projcode, str)
            assert len(projcode) > 0

    def test_project_has_required_fields(self, project_fs_all):
        required = {'active', 'facility', 'allocationType', 'allocationTypeDescription', 'resources'}
        for _projcode, proj in list(project_fs_all['projects'].items())[:20]:
            assert not (required - proj.keys())

    def test_project_active_is_bool(self, project_fs_all):
        for _projcode, proj in list(project_fs_all['projects'].items())[:20]:
            assert isinstance(proj['active'], bool)

    def test_resources_is_list(self, project_fs_all):
        for _projcode, proj in list(project_fs_all['projects'].items())[:20]:
            assert isinstance(proj['resources'], list)

    def test_resources_sorted_by_name(self, project_fs_all):
        for _projcode, proj in list(project_fs_all['projects'].items())[:20]:
            names = [r['name'] for r in proj['resources']]
            assert names == sorted(names)

    def test_resource_has_required_fields(self, project_fs_all):
        required = {'name', 'accountStatus', 'adjustedUsage', 'balance',
                    'allocationAmount', 'cutoffThreshold', 'users', 'thresholds'}
        for _projcode, proj in list(project_fs_all['projects'].items())[:10]:
            for res in proj['resources']:
                assert not (required - res.keys())

    def test_users_list_present_on_resources(self, project_fs_all):
        for _projcode, proj in list(project_fs_all['projects'].items())[:10]:
            for res in proj['resources']:
                assert isinstance(res['users'], list)

    def test_resource_filter_works(self, project_fs_hpc):
        result, resource_name = project_fs_hpc
        for _projcode, proj in result['projects'].items():
            for res in proj['resources']:
                assert res['name'] == resource_name

    def test_same_projects_as_fstree(self, fstree_all, project_fs_all):
        """Project set must match get_fstree_data exactly."""
        fstree_codes = {
            proj['projectCode']
            for fac in fstree_all['facilities']
            for at in fac['allocationTypes']
            for proj in at['projects']
        }
        assert set(project_fs_all['projects'].keys()) == fstree_codes

    def test_alloc_type_name_format(self, project_fs_all):
        for _projcode, proj in list(project_fs_all['projects'].items())[:20]:
            at_name = proj['allocationType']
            assert re.match(r'^[A-Za-z]_', at_name)


# ============================================================================
# get_user_fsdata
# ============================================================================


class TestGetUserFsdata:

    def test_returns_dict_with_expected_keys(self, user_fs_all):
        assert isinstance(user_fs_all, dict)
        assert user_fs_all['name'] == 'userFairShareData'
        assert 'users' in user_fs_all
        assert isinstance(user_fs_all['users'], dict)

    def test_users_keyed_by_username(self, user_fs_all):
        for username in list(user_fs_all['users'].keys())[:10]:
            assert isinstance(username, str)
            assert len(username) > 0

    def test_user_has_uid_and_projects(self, user_fs_all):
        for _username, user in list(user_fs_all['users'].items())[:20]:
            assert 'uid' in user
            assert 'projects' in user
            assert isinstance(user['projects'], dict)

    def test_project_entry_has_required_fields(self, user_fs_all):
        required = {'active', 'facility', 'allocationType', 'allocationTypeDescription', 'resources'}
        for _username, user in list(user_fs_all['users'].items())[:10]:
            for _projcode, proj in list(user['projects'].items())[:5]:
                assert not (required - proj.keys())

    def test_resource_entry_has_no_users_key(self, user_fs_all):
        """In the user view, resources should not carry a redundant 'users' list."""
        for _username, user in list(user_fs_all['users'].items())[:10]:
            for _projcode, proj in user['projects'].items():
                for res in proj['resources']:
                    assert 'users' not in res

    def test_resource_entry_has_required_fields(self, user_fs_all):
        required = {'name', 'accountStatus', 'adjustedUsage', 'balance',
                    'allocationAmount', 'cutoffThreshold', 'thresholds'}
        for _username, user in list(user_fs_all['users'].items())[:10]:
            for _projcode, proj in list(user['projects'].items())[:3]:
                for res in proj['resources']:
                    assert not (required - res.keys())

    def test_resources_sorted_by_name(self, user_fs_all):
        for _username, user in list(user_fs_all['users'].items())[:20]:
            for _projcode, proj in user['projects'].items():
                names = [r['name'] for r in proj['resources']]
                assert names == sorted(names)

    def test_resource_filter_works(self, user_fs_hpc):
        result, resource_name = user_fs_hpc
        for _username, user in result['users'].items():
            for _projcode, proj in user['projects'].items():
                for res in proj['resources']:
                    assert res['name'] == resource_name

    def test_users_appear_only_on_their_resources(self, fstree_hpc, user_fs_hpc):
        """Every (user, projcode, resource) in the user view must be in the fstree roster."""
        fstree, _ = fstree_hpc
        memberships = set()
        for fac in fstree['facilities']:
            for at in fac['allocationTypes']:
                for proj in at['projects']:
                    for res in proj['resources']:
                        for u in res['users']:
                            memberships.add(
                                (u['username'], proj['projectCode'], res['name'])
                            )

        ud, _ = user_fs_hpc
        for username, user in ud['users'].items():
            for projcode, proj in user['projects'].items():
                for res in proj['resources']:
                    key = (username, projcode, res['name'])
                    assert key in memberships, f'{key} in user view but not in fstree roster'

    def test_at_least_one_user_present(self, user_fs_all):
        assert len(user_fs_all['users']) >= 1
