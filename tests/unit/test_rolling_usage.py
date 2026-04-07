"""
Tests for sam.queries.rolling_usage — get_project_rolling_usage().

Covers:
  - Basic return structure and field types
  - Leaf project (direct account charges)
  - Non-leaf project with children (MPTT subtree rollup) — NMMM0003
  - resource_name filter
  - Unknown project returns {}
  - fstree still works after SQL helpers were moved to rolling_usage
"""

import pytest
from sam.queries.rolling_usage import get_project_rolling_usage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LEAF_PROJCODE    = 'SCSG0001'   # CSG systems project — expected leaf, has HPC allocation
_SUBTREE_PROJCODE = 'NMMM0003'  # 29 children — uses MPTT subtree rollup


def _first_hpc_project(session) -> str:
    """Return the projcode of the first project found with an active HPC allocation."""
    from sam import Project, Account, Resource, ResourceType
    from sam.accounting.allocations import Allocation
    from sqlalchemy.orm import joinedload, selectinload

    row = (
        session.query(Project.projcode)
        .join(Project.accounts)
        .join(Account.resource)
        .join(Resource.resource_type)
        .join(Account.allocations)
        .filter(ResourceType.resource_type == 'HPC')
        .filter(Allocation.is_active)
        .filter(~Account.deleted)
        .first()
    )
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Structure tests (any project with an active HPC/DAV allocation)
# ---------------------------------------------------------------------------

class TestRollingUsageStructure:
    """Basic return structure and field types."""

    def test_returns_dict(self, session):
        projcode = _first_hpc_project(session)
        if not projcode:
            pytest.skip('No project with active HPC allocation found in test DB')
        result = get_project_rolling_usage(session, projcode)
        assert isinstance(result, dict)

    def test_resource_keys_are_strings(self, session):
        projcode = _first_hpc_project(session)
        if not projcode:
            pytest.skip('No project with active HPC allocation found in test DB')
        result = get_project_rolling_usage(session, projcode)
        for k in result:
            assert isinstance(k, str), f'Resource key {k!r} should be str'

    def test_each_entry_has_required_fields(self, session):
        projcode = _first_hpc_project(session)
        if not projcode:
            pytest.skip('No project with active HPC allocation found in test DB')
        result = get_project_rolling_usage(session, projcode)
        required = {'allocated', 'start_date', 'end_date', 'windows'}
        for rname, data in result.items():
            missing = required - data.keys()
            assert not missing, f'{rname}: missing fields {missing}'

    def test_windows_dict_has_30_and_90(self, session):
        projcode = _first_hpc_project(session)
        if not projcode:
            pytest.skip('No project with active HPC allocation found in test DB')
        result = get_project_rolling_usage(session, projcode)
        for rname, data in result.items():
            wins = data['windows']
            assert 30 in wins, f'{rname}: missing window 30'
            assert 90 in wins, f'{rname}: missing window 90'

    def test_window_entry_has_required_fields(self, session):
        projcode = _first_hpc_project(session)
        if not projcode:
            pytest.skip('No project with active HPC allocation found in test DB')
        result = get_project_rolling_usage(session, projcode)
        required = {'charges', 'prorated_alloc', 'pct_of_prorated'}
        for rname, data in result.items():
            for wdays, winfo in data['windows'].items():
                missing = required - winfo.keys()
                assert not missing, f'{rname}/window {wdays}: missing {missing}'

    def test_pct_of_prorated_is_non_negative(self, session):
        projcode = _first_hpc_project(session)
        if not projcode:
            pytest.skip('No project with active HPC allocation found in test DB')
        result = get_project_rolling_usage(session, projcode)
        for rname, data in result.items():
            for wdays, winfo in data['windows'].items():
                assert winfo['pct_of_prorated'] >= 0.0, \
                    f'{rname}/window {wdays}: pct_of_prorated should be >= 0'

    def test_custom_single_window(self, session):
        projcode = _first_hpc_project(session)
        if not projcode:
            pytest.skip('No project with active HPC allocation found in test DB')
        result = get_project_rolling_usage(session, projcode, windows=[7])
        for rname, data in result.items():
            assert 7 in data['windows'], f'{rname}: expected window 7'
            assert 30 not in data['windows'], f'{rname}: unexpected window 30'


# ---------------------------------------------------------------------------
# Leaf project
# ---------------------------------------------------------------------------

class TestLeafProject:
    """Tests for a known leaf project (SCSG0001)."""

    def test_scsg0001_returns_data(self, session):
        result = get_project_rolling_usage(session, _LEAF_PROJCODE)
        if not result:
            pytest.skip(f'{_LEAF_PROJCODE} has no active HPC/DAV allocation in test DB')
        assert isinstance(result, dict)
        assert len(result) >= 1

    def test_scsg0001_window_charges_are_floats(self, session):
        result = get_project_rolling_usage(session, _LEAF_PROJCODE)
        if not result:
            pytest.skip(f'{_LEAF_PROJCODE} has no active HPC/DAV allocation in test DB')
        for rname, data in result.items():
            for wdays, winfo in data['windows'].items():
                assert isinstance(winfo['charges'], float), \
                    f'{rname}/window {wdays}: charges should be float'
                assert isinstance(winfo['prorated_alloc'], float), \
                    f'{rname}/window {wdays}: prorated_alloc should be float'


# ---------------------------------------------------------------------------
# Non-leaf (subtree) project
# ---------------------------------------------------------------------------

class TestSubtreeProject:
    """Tests for NMMM0003 — a parent project with 29 children requiring MPTT rollup."""

    def test_nmmm0003_returns_data(self, session):
        result = get_project_rolling_usage(session, _SUBTREE_PROJCODE)
        if not result:
            pytest.skip(f'{_SUBTREE_PROJCODE} has no active HPC/DAV allocation in test DB')
        assert isinstance(result, dict)
        assert len(result) >= 1

    def test_nmmm0003_has_30_and_90_windows(self, session):
        result = get_project_rolling_usage(session, _SUBTREE_PROJCODE)
        if not result:
            pytest.skip(f'{_SUBTREE_PROJCODE} has no active HPC/DAV allocation in test DB')
        for rname, data in result.items():
            assert 30 in data['windows'], f'{rname}: missing 30d window'
            assert 90 in data['windows'], f'{rname}: missing 90d window'

    def test_nmmm0003_charges_non_negative(self, session):
        result = get_project_rolling_usage(session, _SUBTREE_PROJCODE)
        if not result:
            pytest.skip(f'{_SUBTREE_PROJCODE} has no active HPC/DAV allocation in test DB')
        for rname, data in result.items():
            for wdays, winfo in data['windows'].items():
                assert winfo['charges'] >= 0.0, \
                    f'{rname}/window {wdays}: charges should be >= 0'

    def test_90d_charges_gte_30d_charges(self, session):
        """90d window covers 3× the time period so charges should be >= 30d charges."""
        result = get_project_rolling_usage(session, _SUBTREE_PROJCODE)
        if not result:
            pytest.skip(f'{_SUBTREE_PROJCODE} has no active HPC/DAV allocation in test DB')
        for rname, data in result.items():
            w30 = data['windows'][30]['charges']
            w90 = data['windows'][90]['charges']
            assert w90 >= w30, \
                f'{rname}: 90d charges ({w90}) should be >= 30d charges ({w30})'


# ---------------------------------------------------------------------------
# resource_name filter
# ---------------------------------------------------------------------------

class TestResourceFilter:

    def test_filter_returns_only_requested_resource(self, session):
        # Find an active HPC resource name
        from sam import Resource, ResourceType
        row = (
            session.query(Resource.resource_name)
            .join(Resource.resource_type)
            .filter(ResourceType.resource_type == 'HPC')
            .filter(Resource.is_active)
            .first()
        )
        if not row:
            pytest.skip('No active HPC resource in test DB')
        target = row[0]

        projcode = _first_hpc_project(session)
        if not projcode:
            pytest.skip('No eligible project found')

        result = get_project_rolling_usage(session, projcode, resource_name=target)
        for rname in result:
            assert rname == target, f'Expected only {target!r}, got {rname!r}'

    def test_filter_nonexistent_resource_returns_empty(self, session):
        projcode = _first_hpc_project(session)
        if not projcode:
            pytest.skip('No eligible project found')
        result = get_project_rolling_usage(session, projcode, resource_name='XXXX_FAKE_XXXX')
        assert result == {}


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_unknown_projcode_returns_empty(self, session):
        result = get_project_rolling_usage(session, 'XXXX9999')
        assert result == {}

    def test_empty_windows_list_returns_empty_windows(self, session):
        projcode = _first_hpc_project(session)
        if not projcode:
            pytest.skip('No eligible project found')
        result = get_project_rolling_usage(session, projcode, windows=[])
        # Should return resource entries but windows dicts will be empty
        for rname, data in result.items():
            assert data['windows'] == {}, f'{rname}: expected empty windows dict'


# ---------------------------------------------------------------------------
# Fstree regression — verify helpers still work after being moved
# ---------------------------------------------------------------------------

class TestFstreeRegressionAfterRefactor:
    """Confirm fstree_access imports the helpers correctly and still functions."""

    def test_fstree_data_loads(self, session):
        from sam.queries.fstree_access import get_fstree_data
        result = get_fstree_data(session)
        assert isinstance(result, dict)
        assert 'facilities' in result

    def test_fstree_derecho_has_resources(self, session):
        from sam.queries.fstree_access import get_fstree_data
        result = get_fstree_data(session, 'Derecho')
        found_resources = False
        for fac in result['facilities']:
            for at in fac['allocationTypes']:
                for proj in at['projects']:
                    if proj['resources']:
                        found_resources = True
                        break
        assert found_resources, 'Expected at least one project with resources on Derecho'

    def test_window_helpers_importable_directly(self):
        from sam.queries.rolling_usage import (
            _query_window_charges,
            _query_window_subtree_charges,
        )
        assert callable(_query_window_charges)
        assert callable(_query_window_subtree_charges)
