"""Tests for sam.queries.rolling_usage.get_project_rolling_usage().

Ported from tests/unit/test_rolling_usage.py. Transformations:

- The legacy `_first_hpc_project` helper is replaced by the
  `active_project` fixture — both pick the first project with an active
  HPC allocation, but the fixture caches the ID at session start.
- The hardcoded leaf-project reference (`SCSG0001`) is replaced by
  `active_project` (any project with active allocations — leaf or not).
- The hardcoded subtree-project reference (`NMMM0003`) is replaced by
  the new `subtree_project` fixture for the generic subtree tests
  (verifies rollup works on ANY project with children).
- The threshold-specific tests (`test_nmmm0003_derecho_has_threshold`
  and friends) keep the hardcoded `NMMM0003/Derecho` pair: threshold
  configuration is data-dependent and the interim approach is to skip
  if the combo isn't present in the snapshot. Phase 5 will replace
  these with factory-built data.
- Some tests are dropped as redundant: the "generic rollup" test on
  `active_project` already exercises the code paths that the specific
  subtree tests were exercising.
"""
import pytest

from sam.queries.rolling_usage import get_project_rolling_usage


pytestmark = pytest.mark.unit


# Tests that still require NMMM0003/Derecho specifically (threshold config).
# Remove when Phase 5 ports these onto factory-built threshold data.
_THRESHOLD_PROJCODE = 'NMMM0003'
_THRESHOLD_RESOURCE = 'Derecho'


# ============================================================================
# Structure (any project with an active allocation)
# ============================================================================


class TestRollingUsageStructure:

    def test_returns_dict(self, session, active_project):
        result = get_project_rolling_usage(session, active_project.projcode)
        assert isinstance(result, dict)

    def test_resource_keys_are_strings(self, session, active_project):
        result = get_project_rolling_usage(session, active_project.projcode)
        for k in result:
            assert isinstance(k, str)

    def test_each_entry_has_required_fields(self, session, active_project):
        result = get_project_rolling_usage(session, active_project.projcode)
        required = {'allocated', 'start_date', 'end_date', 'windows',
                    'is_inheriting', 'root_projcode'}
        for rname, data in result.items():
            assert not (required - data.keys()), f'{rname}: missing {required - data.keys()}'

    def test_windows_dict_has_30_and_90(self, session, active_project):
        result = get_project_rolling_usage(session, active_project.projcode)
        for rname, data in result.items():
            assert 30 in data['windows']
            assert 90 in data['windows']

    def test_window_entry_has_required_fields(self, session, active_project):
        result = get_project_rolling_usage(session, active_project.projcode)
        required = {'charges', 'self_charges', 'prorated_alloc', 'pct_of_prorated',
                    'threshold_pct', 'use_limit', 'pct_of_limit'}
        for rname, data in result.items():
            for wdays, winfo in data['windows'].items():
                missing = required - winfo.keys()
                assert not missing, f'{rname}/window {wdays}: missing {missing}'

    def test_pct_of_prorated_is_non_negative(self, session, active_project):
        result = get_project_rolling_usage(session, active_project.projcode)
        for rname, data in result.items():
            for wdays, winfo in data['windows'].items():
                assert winfo['pct_of_prorated'] >= 0.0

    def test_custom_single_window(self, session, active_project):
        result = get_project_rolling_usage(session, active_project.projcode, windows=[7])
        for _rname, data in result.items():
            assert 7 in data['windows']
            assert 30 not in data['windows']


# ============================================================================
# Leaf / typical project — charge fields are numeric and sensible
# ============================================================================


class TestLeafProject:
    """Any project with allocations should produce well-typed window data."""

    def test_window_charges_are_floats(self, session, active_project):
        result = get_project_rolling_usage(session, active_project.projcode)
        if not result:
            pytest.skip(f'{active_project.projcode} produced empty rolling usage')
        for rname, data in result.items():
            for wdays, winfo in data['windows'].items():
                assert isinstance(winfo['charges'], float)
                assert isinstance(winfo['prorated_alloc'], float)


# ============================================================================
# Subtree (non-leaf) project — MPTT rollup
# ============================================================================


class TestSubtreeProject:
    """Any active project with >=3 children should produce MPTT-rolled data."""

    def test_returns_data(self, session, subtree_project):
        result = get_project_rolling_usage(session, subtree_project.projcode)
        if not result:
            pytest.skip(
                f'{subtree_project.projcode} has no active HPC/DAV allocation'
            )
        assert isinstance(result, dict)
        assert len(result) >= 1

    def test_has_30_and_90_windows(self, session, subtree_project):
        result = get_project_rolling_usage(session, subtree_project.projcode)
        if not result:
            pytest.skip('subtree project has no allocations to rollup')
        for rname, data in result.items():
            assert 30 in data['windows']
            assert 90 in data['windows']

    def test_charges_non_negative(self, session, subtree_project):
        result = get_project_rolling_usage(session, subtree_project.projcode)
        if not result:
            pytest.skip('subtree project has no allocations')
        for _rname, data in result.items():
            for _wdays, winfo in data['windows'].items():
                assert winfo['charges'] >= 0.0

    def test_90d_charges_gte_30d_charges(self, session, subtree_project):
        """A 90-day window covers 3× the period, so charges should be >= the 30-day window."""
        result = get_project_rolling_usage(session, subtree_project.projcode)
        if not result:
            pytest.skip('subtree project has no allocations')
        for rname, data in result.items():
            w30 = data['windows'][30]['charges']
            w90 = data['windows'][90]['charges']
            assert w90 >= w30, f'{rname}: 90d charges ({w90}) should be >= 30d ({w30})'


# ============================================================================
# Threshold limits
#
# These tests keep a hardcoded (project, resource) reference because
# threshold configuration is a specific per-(project, resource) data shape
# that's complex to discover generically. When no threshold data is
# present, the tests skip. Phase 5 (factories) will replace these with
# synthetic threshold fixtures so they stop depending on snapshot data.
# ============================================================================


class TestThresholdLimits:

    def test_no_threshold_means_no_limit_fields(self, session, active_project):
        """When threshold_pct is None, the derived use_limit/pct_of_limit must also be None."""
        result = get_project_rolling_usage(session, active_project.projcode)
        for _rname, data in result.items():
            for _wdays, winfo in data['windows'].items():
                if winfo['threshold_pct'] is None:
                    assert winfo['use_limit'] is None
                    assert winfo['pct_of_limit'] is None

    def test_threshold_values_present_when_configured(self, session):
        """NMMM0003/Derecho has first_threshold=125, second_threshold=105 in typical snapshots."""
        result = get_project_rolling_usage(
            session, _THRESHOLD_PROJCODE, resource_name=_THRESHOLD_RESOURCE
        )
        if not result or _THRESHOLD_RESOURCE not in result:
            pytest.skip(f'{_THRESHOLD_PROJCODE}/{_THRESHOLD_RESOURCE} not in snapshot')
        data = result[_THRESHOLD_RESOURCE]
        w30 = data['windows'][30]
        if w30['threshold_pct'] is None:
            pytest.skip(f'{_THRESHOLD_PROJCODE}/{_THRESHOLD_RESOURCE} has no threshold configured')
        w90 = data['windows'][90]
        assert w30['threshold_pct'] is not None
        assert w90['threshold_pct'] is not None
        assert w30['use_limit'] is not None
        assert w90['use_limit'] is not None
        assert w30['pct_of_limit'] is not None
        assert w90['pct_of_limit'] is not None

    def test_use_limit_equals_prorated_times_threshold(self, session):
        """use_limit == round(prorated_alloc * threshold_pct / 100)."""
        result = get_project_rolling_usage(
            session, _THRESHOLD_PROJCODE, resource_name=_THRESHOLD_RESOURCE
        )
        if not result or _THRESHOLD_RESOURCE not in result:
            pytest.skip(f'{_THRESHOLD_PROJCODE}/{_THRESHOLD_RESOURCE} not in snapshot')
        data = result[_THRESHOLD_RESOURCE]
        any_threshold = False
        for wdays, winfo in data['windows'].items():
            if winfo['threshold_pct'] is None:
                continue
            any_threshold = True
            expected = round(winfo['prorated_alloc'] * winfo['threshold_pct'] / 100.0)
            assert winfo['use_limit'] == expected, (
                f'window {wdays}: use_limit {winfo["use_limit"]} != expected {expected}'
            )
        if not any_threshold:
            pytest.skip(f'{_THRESHOLD_PROJCODE}/{_THRESHOLD_RESOURCE} has no thresholds in snapshot')

    def test_pct_of_limit_consistent_with_charges(self, session):
        """pct_of_limit == charges / use_limit * 100."""
        result = get_project_rolling_usage(
            session, _THRESHOLD_PROJCODE, resource_name=_THRESHOLD_RESOURCE
        )
        if not result or _THRESHOLD_RESOURCE not in result:
            pytest.skip(f'{_THRESHOLD_PROJCODE}/{_THRESHOLD_RESOURCE} not in snapshot')
        data = result[_THRESHOLD_RESOURCE]
        any_threshold = False
        for wdays, winfo in data['windows'].items():
            if winfo['threshold_pct'] is None or not winfo['use_limit']:
                continue
            any_threshold = True
            expected = round(winfo['charges'] / winfo['use_limit'] * 100.0, 1)
            assert abs(winfo['pct_of_limit'] - expected) < 1.0, (
                f'window {wdays}: pct_of_limit {winfo["pct_of_limit"]} vs expected {expected}'
            )
        if not any_threshold:
            pytest.skip('no thresholds in snapshot')

    def test_fstree_threshold_limit_values_match(self, session):
        """rolling_usage threshold values must match the fstree limit fields."""
        from sam.queries.fstree_access import get_fstree_data

        fstree = get_fstree_data(session, _THRESHOLD_RESOURCE)
        fstree_thresholds = None
        for fac in fstree['facilities']:
            for at in fac['allocationTypes']:
                for proj in at['projects']:
                    if proj['projectCode'] == _THRESHOLD_PROJCODE:
                        for res in proj['resources']:
                            if res['name'] == _THRESHOLD_RESOURCE and res.get('thresholds'):
                                fstree_thresholds = res['thresholds']
        if not fstree_thresholds:
            pytest.skip(f'{_THRESHOLD_PROJCODE}/{_THRESHOLD_RESOURCE} has no threshold data in fstree')

        result = get_project_rolling_usage(
            session, _THRESHOLD_PROJCODE, resource_name=_THRESHOLD_RESOURCE
        )
        if not result or _THRESHOLD_RESOURCE not in result:
            pytest.skip(f'{_THRESHOLD_PROJCODE}/{_THRESHOLD_RESOURCE} not in rolling_usage')
        w30 = result[_THRESHOLD_RESOURCE]['windows'][30]
        w90 = result[_THRESHOLD_RESOURCE]['windows'][90]

        if 'period30' in fstree_thresholds:
            ft30 = fstree_thresholds['period30']
            assert w30['threshold_pct'] == ft30['thresholdPct']
            assert w30['use_limit'] == ft30['useLimitCharges']
        if 'period90' in fstree_thresholds:
            ft90 = fstree_thresholds['period90']
            assert w90['threshold_pct'] == ft90['thresholdPct']
            assert w90['use_limit'] == ft90['useLimitCharges']


# ============================================================================
# resource_name filter
# ============================================================================


class TestResourceFilter:

    def test_filter_returns_only_requested_resource(self, session, active_project, hpc_resource):
        result = get_project_rolling_usage(
            session, active_project.projcode, resource_name=hpc_resource.resource_name
        )
        for rname in result:
            assert rname == hpc_resource.resource_name

    def test_filter_nonexistent_resource_returns_empty(self, session, active_project):
        result = get_project_rolling_usage(
            session, active_project.projcode, resource_name='XXXX_FAKE_XXXX'
        )
        assert result == {}


# ============================================================================
# Edge cases
# ============================================================================


class TestEdgeCases:

    def test_unknown_projcode_returns_empty(self, session):
        result = get_project_rolling_usage(session, 'XXXX9999')
        assert result == {}

    def test_empty_windows_list_returns_empty_windows(self, session, active_project):
        result = get_project_rolling_usage(session, active_project.projcode, windows=[])
        for _rname, data in result.items():
            assert data['windows'] == {}


# ============================================================================
# Inherited (shared-pool) allocations
#
# When a project's allocation on a resource is inheriting (parent_allocation_id
# IS NOT NULL), `charges` and the derived percentages must reflect *pool burn*
# across the whole shared-allocation tree — that's the rate that depletes the
# allocation. Each window also carries `self_charges` so the UI can surface
# the per-project slice as "(N yours)".
# ============================================================================


class TestInheritedAllocation:

    def test_resource_dict_flags_inheriting(self, session, inheriting_project):
        project, resource_name = inheriting_project
        result = get_project_rolling_usage(
            session, project.projcode, resource_name=resource_name
        )
        if not result or resource_name not in result:
            pytest.skip(
                f'{project.projcode}/{resource_name}: no rolling data '
                f'(allocation may be excluded by HPC/DAV/active filter)'
            )
        data = result[resource_name]
        assert data['is_inheriting'] is True
        assert isinstance(data.get('root_projcode'), str)
        assert data['root_projcode'], 'root_projcode must be a non-empty projcode'

    def test_windows_carry_self_charges(self, session, inheriting_project):
        project, resource_name = inheriting_project
        result = get_project_rolling_usage(
            session, project.projcode, resource_name=resource_name
        )
        if not result or resource_name not in result:
            pytest.skip(f'{project.projcode}/{resource_name}: no rolling data')
        for wdays, winfo in result[resource_name]['windows'].items():
            assert 'self_charges' in winfo, f'window {wdays}: missing self_charges'
            assert winfo['self_charges'] is not None, (
                f'window {wdays}: self_charges must be set when inheriting'
            )
            assert isinstance(winfo['self_charges'], float)

    def test_pool_charges_gte_self(self, session, inheriting_project):
        """Pool burn must include self-burn — invariant `charges >= self_charges`."""
        project, resource_name = inheriting_project
        result = get_project_rolling_usage(
            session, project.projcode, resource_name=resource_name
        )
        if not result or resource_name not in result:
            pytest.skip(f'{project.projcode}/{resource_name}: no rolling data')
        for wdays, winfo in result[resource_name]['windows'].items():
            assert winfo['charges'] >= winfo['self_charges'], (
                f'window {wdays}: pool charges ({winfo["charges"]}) '
                f'must be >= self_charges ({winfo["self_charges"]})'
            )

    def test_threshold_math_holds_on_pool(self, session, inheriting_project):
        """`pct_of_limit == charges / use_limit * 100` — same formula as the
        non-inheriting path, applied to pool numbers. Skips if no threshold
        configured on the inheriting project's account.
        """
        project, resource_name = inheriting_project
        result = get_project_rolling_usage(
            session, project.projcode, resource_name=resource_name
        )
        if not result or resource_name not in result:
            pytest.skip(f'{project.projcode}/{resource_name}: no rolling data')
        any_threshold = False
        for wdays, winfo in result[resource_name]['windows'].items():
            if winfo['threshold_pct'] is None or not winfo['use_limit']:
                continue
            any_threshold = True
            expected = round(winfo['charges'] / winfo['use_limit'] * 100.0, 1)
            assert abs(winfo['pct_of_limit'] - expected) < 1.0
        if not any_threshold:
            pytest.skip(
                f'{project.projcode}/{resource_name}: no threshold configured'
            )

    def test_non_inheriting_has_null_self_charges(self, session, active_project):
        """Regression guard: non-inheriting allocations don't leak the new
        `self_charges` value (must be None to keep the UI annotation gated).
        """
        result = get_project_rolling_usage(session, active_project.projcode)
        for rname, data in result.items():
            if data.get('is_inheriting'):
                continue  # inheriting case is covered by tests above
            assert data.get('is_inheriting') is False
            assert data.get('root_projcode') is None
            for wdays, winfo in data['windows'].items():
                assert winfo.get('self_charges') is None, (
                    f'{rname}/window {wdays}: non-inheriting must have '
                    f'self_charges=None, got {winfo.get("self_charges")}'
                )


# ============================================================================
# fstree regression — helpers moved into rolling_usage still importable/callable
# ============================================================================


class TestFstreeRegressionAfterRefactor:

    def test_fstree_data_loads(self, session):
        from sam.queries.fstree_access import get_fstree_data
        result = get_fstree_data(session)
        assert isinstance(result, dict)
        assert 'facilities' in result

    def test_fstree_has_resources_on_hpc(self, session, hpc_resource):
        from sam.queries.fstree_access import get_fstree_data
        result = get_fstree_data(session, hpc_resource.resource_name)
        found_resources = False
        for fac in result['facilities']:
            for at in fac['allocationTypes']:
                for proj in at['projects']:
                    if proj['resources']:
                        found_resources = True
                        break
        assert found_resources, (
            f'Expected at least one project with resources on {hpc_resource.resource_name}'
        )

    def test_window_helpers_importable_directly(self):
        from sam.queries.rolling_usage import (
            _query_window_charges,
            _query_window_subtree_charges,
        )
        assert callable(_query_window_charges)
        assert callable(_query_window_subtree_charges)
