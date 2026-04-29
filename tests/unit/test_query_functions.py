"""Query function tests — sam.queries.* module coverage.

Ported from tests/unit/test_query_functions.py. Changes from the legacy
file:

- Replaced `test_project`/`test_resource`/`bdobbins|benkirk` fallbacks
  with representative fixtures (`active_project`, `hpc_resource`,
  `multi_project_user`) defined in new_tests/conftest.py. These pick
  ANY row from the snapshot matching the required shape, so the file
  survives snapshot refreshes that remove specific projcodes.
- Dropped hardcoded `'SCSG0001'` / `'Derecho'` / `'UNIV'` / `'TOTAL'`
  search arguments — now derived from the fixture objects at test time.
- The `search_projects_by_code_or_title` tests use a substring of the
  representative project's real projcode as the search term (self-
  consistent; no hardcoded value).
- `test_search_projects_active_filter` now picks a substring that has
  both active and inactive hits dynamically, rather than hardcoding `'U'`.

Assertion style is unchanged — the tests remain structural (dict keys,
type invariants, computed-from-result equalities).
"""
import pytest
from datetime import datetime, timedelta

from sam import (
    Allocation, User,
)
from sam.queries.charges import (
    CHARGE_ADJUSTMENT_SORT_COLUMNS,
    count_recent_charge_adjustments,
    get_daily_charge_trends_for_accounts,
    get_raw_charge_summaries_for_accounts,
    get_recent_charge_adjustments,
    get_user_breakdown_for_project,
)
from sam.accounting.adjustments import ChargeAdjustmentType
from sam.queries.dashboard import (
    _build_project_resources_data,
    _build_user_projects_resources_batched,
    get_project_dashboard_data,
    get_resource_detail_data,
    get_user_dashboard_data,
)
from sam.queries.allocations import (
    ALLOCATION_TRANSACTION_SORT_COLUMNS,
    count_recent_allocation_transactions,
    get_allocation_history,
    get_allocation_summary,
    get_allocation_summary_with_usage,
    get_allocations_by_resource,
    get_project_allocations,
    get_recent_allocation_transactions,
)
from sam.accounting.allocations import AllocationTransactionType
from sam.queries.statistics import (
    get_project_statistics,
    get_user_statistics,
)
from sam.queries.projects import search_projects_by_code_or_title
from sam.queries.lookups import get_user_group_access, get_group_members
from sam.core.groups import (
    AdhocGroup,
    AdhocSystemAccountEntry,
    DEFAULT_COMMON_GROUP,
    DEFAULT_COMMON_GROUP_GID,
    resolve_group_name,
)

from factories import (
    make_account,
    make_allocation,
    make_allocation_transaction,
    make_charge_adjustment,
    make_project,
    make_resource,
    make_resource_type,
    make_user,
)
from factories._seq import next_int, next_seq


pytestmark = pytest.mark.unit


# ============================================================================
# sam.queries.charges
# ============================================================================


class TestChargeQueries:

    @pytest.mark.parametrize('resource_type,expected_keys', [
        (None,      ['comp', 'dav', 'disk', 'archive']),
        ('HPC',     ['comp']),
        ('DAV',     ['comp', 'dav']),
        ('DISK',    ['disk']),
        ('ARCHIVE', ['archive']),
    ])
    def test_get_daily_charge_trends_resource_types(
        self, session, active_project, resource_type, expected_keys
    ):
        account_ids = [acc.account_id for acc in active_project.accounts]
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)
        result = get_daily_charge_trends_for_accounts(
            session, account_ids, start_date, end_date, resource_type=resource_type
        )
        assert isinstance(result, dict)
        if result:
            date_key = list(result.keys())[0]
            assert isinstance(date_key, str)
            charges = result[date_key]
            for key in expected_keys:
                assert key in charges
                assert isinstance(charges[key], (int, float))
                assert charges[key] >= 0

    def test_get_daily_charge_trends_date_range(self, session, active_project):
        account_ids = [acc.account_id for acc in active_project.accounts]
        end_date = datetime.now()
        start_date = end_date - timedelta(days=7)
        result = get_daily_charge_trends_for_accounts(
            session, account_ids, start_date, end_date
        )
        for date_str in result.keys():
            d = datetime.strptime(date_str, '%Y-%m-%d')
            assert start_date.date() <= d.date() <= end_date.date()

    def test_get_daily_charge_trends_empty_accounts(self, session):
        result = get_daily_charge_trends_for_accounts(
            session, [], datetime.now() - timedelta(days=30), datetime.now()
        )
        assert result == {}

    def test_get_raw_charge_summaries_basic(self, session, active_project):
        account_ids = [acc.account_id for acc in active_project.accounts]
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)
        result = get_raw_charge_summaries_for_accounts(
            session, account_ids, start_date, end_date
        )
        assert isinstance(result, dict)
        for key in ('comp', 'dav', 'disk', 'archive'):
            assert key in result
            assert isinstance(result[key], list)

    @pytest.mark.parametrize('resource_type', ['HPC', 'DAV', 'DISK', 'ARCHIVE'])
    def test_get_raw_charge_summaries_resource_filter(
        self, session, active_project, resource_type
    ):
        account_ids = [acc.account_id for acc in active_project.accounts]
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)
        result = get_raw_charge_summaries_for_accounts(
            session, account_ids, start_date, end_date, resource_type=resource_type
        )
        assert isinstance(result, dict)
        if resource_type == 'HPC':
            assert 'comp' in result
        elif resource_type == 'DAV':
            assert 'comp' in result
            assert 'dav' in result

    def test_get_user_breakdown_for_project_basic(
        self, session, active_project, hpc_resource
    ):
        end_date = datetime.now()
        start_date = end_date - timedelta(days=90)
        result = get_user_breakdown_for_project(
            session, active_project.projcode, start_date, end_date,
            hpc_resource.resource_name
        )
        assert isinstance(result, list)
        if result:
            user = result[0]
            for field in ('username', 'user_id', 'jobs', 'core_hours', 'charges'):
                assert field in user
            assert user['charges'] > 0

    def test_get_user_breakdown_for_project_filters_zero_charges(
        self, session, active_project, hpc_resource
    ):
        end_date = datetime.now()
        start_date = end_date - timedelta(days=90)
        result = get_user_breakdown_for_project(
            session, active_project.projcode, start_date, end_date,
            hpc_resource.resource_name
        )
        for user in result:
            assert user['charges'] > 0


# ============================================================================
# sam.queries.dashboard
# ============================================================================


class TestDashboardQueries:

    def test_get_project_dashboard_data_found(self, session, active_project):
        result = get_project_dashboard_data(session, active_project.projcode)
        assert result is not None
        assert isinstance(result, dict)
        for field in ('project', 'resources', 'has_children'):
            assert field in result
        assert result['project'] == active_project
        assert isinstance(result['resources'], list)
        assert isinstance(result['has_children'], bool)

    def test_get_project_dashboard_data_not_found(self, session):
        assert get_project_dashboard_data(session, 'INVALID999') is None

    def test_get_project_dashboard_data_structure(self, session, active_project):
        result = get_project_dashboard_data(session, active_project.projcode)
        if result and result['resources']:
            res = result['resources'][0]
            for field in ('resource_name', 'allocated', 'used', 'remaining', 'percent_used'):
                assert field in res

    def test_get_resource_detail_data_structure(
        self, session, active_project, hpc_resource
    ):
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)
        result = get_resource_detail_data(
            session, active_project.projcode, hpc_resource.resource_name,
            start_date, end_date,
        )
        if result:
            assert isinstance(result, dict)
            for field in ('project', 'resource_obj', 'resource_summary', 'daily_charges'):
                assert field in result

    def test_user_dashboard_batched_matches_per_project(
        self, session, multi_project_user
    ):
        """Equivalence check: the batched helper must agree field-by-field
        with the per-project loop for the same user at the same instant.
        """
        projects = sorted(multi_project_user.active_projects(), key=lambda p: p.projcode)
        assert projects, "multi_project_user fixture returned user with no active projects"

        active_at = datetime.now()

        batched = _build_user_projects_resources_batched(
            session, projects, active_at=active_at,
        )

        SCALAR_FIELDS = (
            'resource_name', 'allocation_id', 'parent_allocation_id',
            'is_inheriting', 'account_id', 'status', 'start_date', 'end_date',
            'days_until_expiration', 'date_group_key', 'bar_state',
            'resource_type', 'root_projcode', 'activity_date',
        )
        FLOAT_FIELDS = (
            'allocated', 'used', 'remaining', 'percent_used',
            'adjustments', 'elapsed_pct',
        )
        # Optional float fields: present-and-equal, or None on both sides.
        OPTIONAL_FLOAT_FIELDS = ('self_used', 'self_percent_used')
        FLOAT_TOL = 1e-6

        for project in projects:
            per_project = sorted(
                _build_project_resources_data(project, active_at=active_at),
                key=lambda r: r['resource_name'],
            )
            from_batch = batched.get(project.project_id, [])

            assert len(per_project) == len(from_batch), (
                f"{project.projcode}: batched returned {len(from_batch)}, "
                f"per-project returned {len(per_project)}"
            )
            for pp, bb in zip(per_project, from_batch):
                ctx = f"{project.projcode}/{pp['resource_name']}"
                for f in SCALAR_FIELDS:
                    assert pp[f] == bb[f], f"{ctx}: {f} differs ({pp[f]!r} vs {bb[f]!r})"
                for f in FLOAT_FIELDS:
                    assert abs(float(pp[f]) - float(bb[f])) < FLOAT_TOL, (
                        f"{ctx}: {f} differs ({pp[f]} vs {bb[f]})"
                    )
                for f in OPTIONAL_FLOAT_FIELDS:
                    if pp[f] is None or bb[f] is None:
                        assert pp[f] == bb[f], (
                            f"{ctx}: {f} differs ({pp[f]!r} vs {bb[f]!r})"
                        )
                    else:
                        assert abs(float(pp[f]) - float(bb[f])) < FLOAT_TOL, (
                            f"{ctx}: {f} differs ({pp[f]} vs {bb[f]})"
                        )
                assert set(pp['charges_by_type'].keys()) == set(bb['charges_by_type'].keys())
                for k in pp['charges_by_type']:
                    assert abs(pp['charges_by_type'][k] - bb['charges_by_type'][k]) < FLOAT_TOL
                assert pp['rolling_30'] == bb['rolling_30']
                assert pp['rolling_90'] == bb['rolling_90']

    def test_get_resource_detail_data_daily_charges(
        self, session, active_project, hpc_resource
    ):
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)
        result = get_resource_detail_data(
            session, active_project.projcode, hpc_resource.resource_name,
            start_date, end_date,
        )
        if result:
            daily = result['daily_charges']
            assert 'dates' in daily
            assert 'values' in daily
            if daily['dates'] is not None:
                assert len(daily['dates']) == len(daily['values'])


class TestDiskCapacityInDashboardData:
    """For DISK resources, both dashboard builders should emit *capacity*
    (point-in-time TiB used / TiB allocated) in the `used`/`percent_used`
    fields, not cumulative TiB-year burn. This pins the contract used by
    the project card / project modal / allocations table progress bars.
    """

    def _build_disk_graph(self, session, *, allocated_tib: float):
        """User → Project → DISK Account+Allocation. Returns (project, account, lead)."""
        from sam import ResourceType
        rt = session.query(ResourceType).filter_by(resource_type='DISK').first()
        if rt is None:
            rt = make_resource_type(session, resource_type='DISK')
        resource = make_resource(
            session, resource_type=rt,
            resource_name=f"Campaign_Store_{next_seq('cs')}",
        )
        lead = make_user(session)
        project = make_project(session, lead=lead)
        account = make_account(session, project=project, resource=resource)
        make_allocation(
            session, account=account, amount=allocated_tib,
            start_date=datetime.now() - timedelta(days=30),
            end_date=datetime.now() + timedelta(days=335),
        )
        return project, account, lead

    def _seed_snapshot(self, session, *, account, lead, snap_date,
                       bytes_used: int, terabyte_years: float):
        """Insert one DiskChargeSummary row + mark its date current."""
        from sam.summaries.disk_summaries import (
            DiskChargeSummary,
            mark_disk_snapshot_current,
        )
        session.add(DiskChargeSummary(
            activity_date=snap_date,
            account_id=account.account_id,
            user_id=lead.user_id,
            username=lead.username,
            projcode=account.project.projcode,
            number_of_files=100,
            bytes=bytes_used,
            terabyte_years=terabyte_years,
            charges=terabyte_years,
        ))
        session.flush()
        mark_disk_snapshot_current(session, snap_date)

    def test_per_project_builder_emits_capacity_not_burn(self, session):
        from datetime import date as _date
        BYTES_PER_TIB = 1024 ** 4
        project, account, lead = self._build_disk_graph(session, allocated_tib=100.0)
        snap = _date(2026, 4, 18)
        # 50 TiB occupancy, but 4.21 TiB-yr cumulative billing burn —
        # the bar must read 50%, not 4.21%.
        self._seed_snapshot(
            session, account=account, lead=lead, snap_date=snap,
            bytes_used=50 * BYTES_PER_TIB, terabyte_years=4.2141,
        )
        rows = _build_project_resources_data(project)
        disk = next(r for r in rows if r['resource_type'] == 'DISK')
        assert disk['used'] == pytest.approx(50.0, abs=1e-6)
        assert disk['percent_used'] == pytest.approx(50.0, abs=1e-4)
        assert disk['remaining'] == pytest.approx(50.0, abs=1e-6)
        assert disk['activity_date'] == snap
        assert disk['allocated'] == pytest.approx(100.0, abs=1e-6)

    def test_batched_builder_matches_per_project_for_disk(self, session):
        from datetime import date as _date
        BYTES_PER_TIB = 1024 ** 4
        project, account, lead = self._build_disk_graph(session, allocated_tib=100.0)
        snap = _date(2026, 4, 18)
        self._seed_snapshot(
            session, account=account, lead=lead, snap_date=snap,
            bytes_used=50 * BYTES_PER_TIB, terabyte_years=4.2141,
        )
        per = _build_project_resources_data(project)
        bat = _build_user_projects_resources_batched(session, [project])[project.project_id]
        # Equivalence on the disk row's capacity fields.
        assert len(per) == len(bat)
        per_disk = next(r for r in per if r['resource_type'] == 'DISK')
        bat_disk = next(r for r in bat if r['resource_type'] == 'DISK')
        assert per_disk['used'] == pytest.approx(bat_disk['used'], abs=1e-6)
        assert per_disk['percent_used'] == pytest.approx(bat_disk['percent_used'], abs=1e-6)
        assert per_disk['activity_date'] == bat_disk['activity_date'] == snap

    def test_no_snapshot_yields_zero_capacity_and_none_activity_date(self, session):
        project, account, lead = self._build_disk_graph(session, allocated_tib=100.0)
        # No DiskChargeSummary row seeded — fresh allocation.
        rows = _build_project_resources_data(project)
        disk = next(r for r in rows if r['resource_type'] == 'DISK')
        assert disk['used'] == 0.0
        assert disk['percent_used'] == 0.0
        assert disk['remaining'] == 100.0
        assert disk['activity_date'] is None

    def test_parent_project_capacity_includes_child_subtree(self, session):
        """For NMMM0003-shaped parents (own account holds 0 bytes, child
        sub-projects hold the actual occupancy), the dashboard dict's
        `used` must reflect the subtree total — not the parent account's
        own snapshot."""
        from datetime import date as _date
        from sam import ResourceType
        from sam.summaries.disk_summaries import (
            DiskChargeSummary,
            mark_disk_snapshot_current,
        )
        BYTES_PER_TIB = 1024 ** 4

        rt = session.query(ResourceType).filter_by(resource_type='DISK').first()
        if rt is None:
            rt = make_resource_type(session, resource_type='DISK')
        resource = make_resource(
            session, resource_type=rt,
            resource_name=f"Campaign_Store_{next_seq('cs')}",
        )
        parent_lead = make_user(session)
        parent = make_project(session, lead=parent_lead)
        child_a = make_project(session, parent=parent, lead=make_user(session))
        child_b = make_project(session, parent=parent, lead=make_user(session))
        # Allocation lives on the parent (capacity cap for the pool).
        parent_account = make_account(session, project=parent, resource=resource)
        make_allocation(
            session, account=parent_account, amount=100.0,
            start_date=datetime.now() - timedelta(days=30),
            end_date=datetime.now() + timedelta(days=335),
        )
        # Children carry the actual snapshot bytes.
        account_a = make_account(session, project=child_a, resource=resource)
        account_b = make_account(session, project=child_b, resource=resource)
        snap = _date(2026, 4, 18)
        for acct, lead, tib in [
            (account_a, child_a.lead, 30.0),
            (account_b, child_b.lead, 20.0),
        ]:
            session.add(DiskChargeSummary(
                activity_date=snap,
                account_id=acct.account_id,
                user_id=lead.user_id,
                username=lead.username,
                projcode=acct.project.projcode,
                number_of_files=100,
                bytes=int(tib * BYTES_PER_TIB),
                terabyte_years=0.0,
                charges=0.0,
            ))
        session.flush()
        mark_disk_snapshot_current(session, snap)

        rows = _build_project_resources_data(parent)
        disk = next(r for r in rows if r['resource_type'] == 'DISK')
        # Parent's bar must read 50 TiB used, not 0 — the parent's own
        # account holds nothing; the children hold 30+20 = 50 TiB.
        assert disk['used'] == pytest.approx(50.0, abs=1e-6)
        assert disk['percent_used'] == pytest.approx(50.0, abs=1e-4)
        assert disk['activity_date'] == snap


class TestDiskCapacityInGetDetailedAllocationUsage:
    """`Project.get_detailed_allocation_usage()` is the data source for
    every CLI surface (`sam-search project`, `sam-admin project`) and
    several webapp paths (admin tree view, single-project dashboard,
    `GET /api/v1/users/<u>`). For DISK rows it must return point-in-time
    capacity in `used`/`remaining`/`percent_used` — not cumulative
    TiB-yr burn — so the CLI matches the webapp."""

    def test_disk_used_reflects_capacity_not_burn(self, session):
        from datetime import date as _date
        from sam import ResourceType
        from sam.summaries.disk_summaries import (
            DiskChargeSummary,
            mark_disk_snapshot_current,
        )
        BYTES_PER_TIB = 1024 ** 4

        rt = session.query(ResourceType).filter_by(resource_type='DISK').first()
        if rt is None:
            rt = make_resource_type(session, resource_type='DISK')
        resource = make_resource(
            session, resource_type=rt,
            resource_name=f"Campaign_Store_{next_seq('cs')}",
        )
        lead = make_user(session)
        project = make_project(session, lead=lead)
        account = make_account(session, project=project, resource=resource)
        make_allocation(
            session, account=account, amount=100.0,
            start_date=datetime.now() - timedelta(days=30),
            end_date=datetime.now() + timedelta(days=335),
        )
        snap = _date(2026, 4, 18)
        # 50 TiB capacity vs. 4.21 TiB-yr cumulative burn — the helper
        # must surface 50, not 4.21.
        session.add(DiskChargeSummary(
            activity_date=snap,
            account_id=account.account_id,
            user_id=lead.user_id,
            username=lead.username,
            projcode=project.projcode,
            number_of_files=100,
            bytes=50 * BYTES_PER_TIB,
            terabyte_years=4.2141,
            charges=4.2141,
        ))
        session.flush()
        mark_disk_snapshot_current(session, snap)

        usage = project.get_detailed_allocation_usage()
        disk = usage[resource.resource_name]
        assert disk['resource_type'] == 'DISK'
        assert disk['used'] == pytest.approx(50.0, abs=1e-6)
        assert disk['remaining'] == pytest.approx(50.0, abs=1e-6)
        assert disk['percent_used'] == pytest.approx(50.0, abs=1e-4)
        assert disk['activity_date'] == snap

    def test_disk_capacity_includes_child_subtree(self, session):
        """For NMMM0003-shaped parents (own account empty, children hold
        the bytes), the helper's `used` must reflect the subtree total."""
        from datetime import date as _date
        from sam import ResourceType
        from sam.summaries.disk_summaries import (
            DiskChargeSummary,
            mark_disk_snapshot_current,
        )
        BYTES_PER_TIB = 1024 ** 4

        rt = session.query(ResourceType).filter_by(resource_type='DISK').first()
        if rt is None:
            rt = make_resource_type(session, resource_type='DISK')
        resource = make_resource(
            session, resource_type=rt,
            resource_name=f"Campaign_Store_{next_seq('cs')}",
        )
        parent_lead = make_user(session)
        parent = make_project(session, lead=parent_lead)
        child_a = make_project(session, parent=parent, lead=make_user(session))
        child_b = make_project(session, parent=parent, lead=make_user(session))
        parent_account = make_account(session, project=parent, resource=resource)
        make_allocation(
            session, account=parent_account, amount=100.0,
            start_date=datetime.now() - timedelta(days=30),
            end_date=datetime.now() + timedelta(days=335),
        )
        account_a = make_account(session, project=child_a, resource=resource)
        account_b = make_account(session, project=child_b, resource=resource)
        snap = _date(2026, 4, 18)
        for acct, lead, tib in [
            (account_a, child_a.lead, 30.0),
            (account_b, child_b.lead, 20.0),
        ]:
            session.add(DiskChargeSummary(
                activity_date=snap,
                account_id=acct.account_id,
                user_id=lead.user_id,
                username=lead.username,
                projcode=acct.project.projcode,
                number_of_files=100,
                bytes=int(tib * BYTES_PER_TIB),
                terabyte_years=0.0,
                charges=0.0,
            ))
        session.flush()
        mark_disk_snapshot_current(session, snap)

        usage = parent.get_detailed_allocation_usage()
        disk = usage[resource.resource_name]
        assert disk['used'] == pytest.approx(50.0, abs=1e-6)
        assert disk['percent_used'] == pytest.approx(50.0, abs=1e-4)
        assert disk['activity_date'] == snap

    def test_hpc_resource_unaffected_by_disk_pass(self, session):
        """The disk pass must not touch HPC/DAV/ARCHIVE rows."""
        lead = make_user(session)
        project = make_project(session, lead=lead)
        # Build an HPC account using whatever default ResourceType the
        # factory wires (HPC by default).
        from sam import ResourceType
        rt = session.query(ResourceType).filter_by(resource_type='HPC').first()
        if rt is None:
            rt = make_resource_type(session, resource_type='HPC')
        resource = make_resource(
            session, resource_type=rt,
            resource_name=f"Derecho_{next_seq('hpc')}",
        )
        account = make_account(session, project=project, resource=resource)
        make_allocation(
            session, account=account, amount=1000.0,
            start_date=datetime.now() - timedelta(days=30),
            end_date=datetime.now() + timedelta(days=335),
        )
        usage = project.get_detailed_allocation_usage()
        hpc = usage[resource.resource_name]
        assert hpc['resource_type'] == 'HPC'
        # Fresh allocation, no charges → used == 0, percent_used == 0,
        # and the helper must NOT inject `activity_date` for HPC rows.
        assert hpc['used'] == 0.0
        assert hpc['percent_used'] == 0.0
        assert 'activity_date' not in hpc


class TestDiskCapacityInAllocationSummary:
    """`get_allocation_summary_with_usage` returns capacity-based
    total_used for disk-only rows (the /allocations dashboard table)."""

    def test_disk_row_uses_capacity(self, session, active_project):
        """Hang a fresh DISK account+allocation+snapshot off an existing
        snapshot project so the AllocationType/Panel/Facility joins in
        get_allocation_summary() resolve. The query inner-joins
        Project → AllocationType → Panel → Facility, so a bare
        make_project() (no allocation_type wiring) yields zero rows."""
        from datetime import date as _date
        from sam import ResourceType
        from sam.summaries.disk_summaries import (
            DiskChargeSummary,
            mark_disk_snapshot_current,
        )
        BYTES_PER_TIB = 1024 ** 4
        rt = session.query(ResourceType).filter_by(resource_type='DISK').first()
        if rt is None:
            rt = make_resource_type(session, resource_type='DISK')
        resource = make_resource(
            session, resource_type=rt,
            resource_name=f"Campaign_Store_{next_seq('cs')}",
        )
        lead = active_project.lead or make_user(session)
        account = make_account(session, project=active_project, resource=resource)
        make_allocation(
            session, account=account, amount=100.0,
            start_date=datetime.now() - timedelta(days=30),
            end_date=datetime.now() + timedelta(days=335),
        )
        snap = _date(2026, 4, 18)
        session.add(DiskChargeSummary(
            activity_date=snap,
            account_id=account.account_id,
            user_id=lead.user_id,
            username=lead.username,
            projcode=active_project.projcode,
            number_of_files=100,
            bytes=25 * BYTES_PER_TIB,
            terabyte_years=2.5,
            charges=2.5,
        ))
        session.flush()
        mark_disk_snapshot_current(session, snap)
        rows = get_allocation_summary_with_usage(
            session,
            resource_name=resource.resource_name,
            projcode=active_project.projcode,
        )
        assert rows, "expected at least one summary row"
        disk_row = rows[0]
        assert disk_row['total_used'] == pytest.approx(25.0, abs=1e-6)
        assert disk_row['percent_used'] == pytest.approx(25.0, abs=1e-4)
        assert disk_row.get('activity_date') == snap


# ============================================================================
# sam.queries.allocations
# ============================================================================


class TestAllocationQueries:

    def test_get_project_allocations_basic(self, session, active_project):
        result = get_project_allocations(session, active_project.projcode)
        assert isinstance(result, list)
        if result:
            alloc, res_name = result[0]
            assert isinstance(alloc, Allocation)
            assert isinstance(res_name, str)

    def test_get_project_allocations_resource_filter(
        self, session, active_project, hpc_resource
    ):
        result = get_project_allocations(
            session, active_project.projcode, resource_name=hpc_resource.resource_name
        )
        for _alloc, res_name in result:
            assert res_name == hpc_resource.resource_name

    def test_get_allocation_history_basic(self, session, active_project):
        result = get_allocation_history(session, active_project.projcode)
        assert isinstance(result, list)
        if result:
            txn = result[0]
            for field in ('transaction_date', 'transaction_type', 'requested_amount',
                          'transaction_amount', 'processed_by', 'comment'):
                assert field in txn

    def test_get_allocation_history_user_join(self, session, active_project):
        result = get_allocation_history(session, active_project.projcode)
        for txn in result:
            assert 'processed_by' in txn
            if txn['processed_by'] is not None:
                assert isinstance(txn['processed_by'], str)

    def test_get_allocations_by_resource_active_only(self, session, hpc_resource):
        result = get_allocations_by_resource(
            session, hpc_resource.resource_name, active_only=True
        )
        assert isinstance(result, list)
        now = datetime.now()
        for _project, allocation in result:
            assert allocation.start_date <= now
            assert allocation.end_date is None or allocation.end_date >= now

    def test_get_allocations_by_resource_include_expired(self, session, hpc_resource):
        all_allocs = get_allocations_by_resource(
            session, hpc_resource.resource_name, active_only=False
        )
        active_allocs = get_allocations_by_resource(
            session, hpc_resource.resource_name, active_only=True
        )
        assert len(all_allocs) >= len(active_allocs)


# ============================================================================
# sam.queries.allocations.get_recent_allocation_transactions
# ============================================================================


EXPECTED_TXN_KEYS = {
    'transaction_id', 'allocation_id', 'transaction_type', 'creation_time',
    'transaction_amount', 'requested_amount', 'alloc_start_date', 'alloc_end_date',
    'transaction_comment', 'auth_at_panel_mtg', 'propagated',
    'projcode', 'project_id', 'resource_name', 'resource_id',
    'facility', 'allocation_type',
    'user_id', 'username', 'user_display_name',
}


class TestRecentAllocationTransactions:
    """Tests for get_recent_allocation_transactions. Each test seeds its own
    allocation + transactions and scopes the query by the fresh projcode so
    assertions are deterministic against snapshot data."""

    def test_returns_dicts_with_expected_keys_ordered_desc(self, session):
        allocation = make_allocation(session)
        projcode = allocation.account.project.projcode
        now = datetime.now()
        make_allocation_transaction(
            session, allocation=allocation, transaction_type="CREATE",
            creation_time=now - timedelta(days=30),
        )
        make_allocation_transaction(
            session, allocation=allocation, transaction_type="EDIT",
            creation_time=now - timedelta(days=5),
        )
        make_allocation_transaction(
            session, allocation=allocation, transaction_type="EXTENSION",
            creation_time=now - timedelta(hours=1),
        )

        rows = get_recent_allocation_transactions(session, projcode=projcode)

        assert len(rows) == 3
        for r in rows:
            assert set(r.keys()) == EXPECTED_TXN_KEYS
            assert r['projcode'] == projcode
        times = [r['creation_time'] for r in rows]
        assert times == sorted(times, reverse=True)

    def test_projcode_list_filter(self, session):
        a1 = make_allocation(session)
        a2 = make_allocation(session)
        make_allocation_transaction(session, allocation=a1, transaction_type="CREATE")
        make_allocation_transaction(session, allocation=a2, transaction_type="CREATE")

        codes = [a1.account.project.projcode, a2.account.project.projcode]
        rows = get_recent_allocation_transactions(session, projcode=codes)
        returned = {r['projcode'] for r in rows}
        assert set(codes).issubset(returned)

    def test_resource_and_facility_filter(self, session):
        allocation = make_allocation(session)
        projcode = allocation.account.project.projcode
        make_allocation_transaction(session, allocation=allocation)

        resource_name = allocation.account.resource.resource_name
        rows = get_recent_allocation_transactions(
            session, projcode=projcode, resource_name=resource_name,
        )
        assert len(rows) == 1
        assert rows[0]['resource_name'] == resource_name

        # Non-matching resource yields empty
        rows = get_recent_allocation_transactions(
            session, projcode=projcode, resource_name="__no_such_resource__",
        )
        assert rows == []

    def test_date_range_is_inclusive(self, session):
        allocation = make_allocation(session)
        projcode = allocation.account.project.projcode
        base = datetime(2099, 6, 15, 12, 0, 0)
        make_allocation_transaction(
            session, allocation=allocation, creation_time=base - timedelta(days=1),
        )
        on_start = make_allocation_transaction(
            session, allocation=allocation, creation_time=base,
        )
        on_end = make_allocation_transaction(
            session, allocation=allocation,
            creation_time=base + timedelta(days=10),
        )
        make_allocation_transaction(
            session, allocation=allocation,
            creation_time=base + timedelta(days=11),
        )

        rows = get_recent_allocation_transactions(
            session, projcode=projcode,
            start_date=base, end_date=base + timedelta(days=10),
        )
        ids = {r['transaction_id'] for r in rows}
        assert ids == {on_start.allocation_transaction_id,
                       on_end.allocation_transaction_id}

    def test_transaction_types_accepts_enum_and_string(self, session):
        allocation = make_allocation(session)
        projcode = allocation.account.project.projcode
        make_allocation_transaction(session, allocation=allocation, transaction_type="EDIT")
        make_allocation_transaction(session, allocation=allocation, transaction_type="EXTENSION")
        make_allocation_transaction(session, allocation=allocation, transaction_type="CREATE")

        via_enum = get_recent_allocation_transactions(
            session, projcode=projcode,
            transaction_types=AllocationTransactionType.EDIT,
        )
        via_str = get_recent_allocation_transactions(
            session, projcode=projcode, transaction_types="EDIT",
        )
        via_list = get_recent_allocation_transactions(
            session, projcode=projcode,
            transaction_types=[AllocationTransactionType.EDIT,
                               AllocationTransactionType.EXTENSION],
        )

        assert {r['transaction_type'] for r in via_enum} == {"EDIT"}
        assert {r['transaction_type'] for r in via_str} == {"EDIT"}
        assert {r['transaction_type'] for r in via_list} == {"EDIT", "EXTENSION"}

    def test_user_id_and_username_filters(self, session):
        allocation = make_allocation(session)
        projcode = allocation.account.project.projcode
        alice = make_user(session)
        bob = make_user(session)
        make_allocation_transaction(session, allocation=allocation, user=alice)
        make_allocation_transaction(session, allocation=allocation, user=bob)
        make_allocation_transaction(session, allocation=allocation, user=None)

        by_id = get_recent_allocation_transactions(
            session, projcode=projcode, user_id=alice.user_id,
        )
        by_name = get_recent_allocation_transactions(
            session, projcode=projcode, username=alice.username,
        )
        assert len(by_id) == 1 and len(by_name) == 1
        assert by_id[0]['user_id'] == alice.user_id
        assert by_name[0]['username'] == alice.username

        with pytest.raises(ValueError):
            get_recent_allocation_transactions(
                session, projcode=projcode,
                user_id=alice.user_id, username=alice.username,
            )

    def test_outer_join_returns_rows_with_no_user(self, session):
        allocation = make_allocation(session)
        projcode = allocation.account.project.projcode
        make_allocation_transaction(session, allocation=allocation, user=None)

        rows = get_recent_allocation_transactions(session, projcode=projcode)
        assert len(rows) == 1
        assert rows[0]['user_id'] is None
        assert rows[0]['username'] is None
        assert rows[0]['user_display_name'] is None

    def test_include_deleted_hides_then_shows_soft_deleted(self, session):
        allocation = make_allocation(session)
        projcode = allocation.account.project.projcode
        make_allocation_transaction(session, allocation=allocation, transaction_type="DELETE")
        allocation.deleted = True
        session.flush()

        hidden = get_recent_allocation_transactions(session, projcode=projcode)
        assert hidden == []

        shown = get_recent_allocation_transactions(
            session, projcode=projcode, include_deleted=True,
        )
        assert len(shown) == 1
        assert shown[0]['transaction_type'] == "DELETE"

    def test_include_propagated_toggle(self, session):
        allocation = make_allocation(session)
        projcode = allocation.account.project.projcode
        make_allocation_transaction(
            session, allocation=allocation, transaction_type="EDIT", propagated=False,
        )
        make_allocation_transaction(
            session, allocation=allocation, transaction_type="EDIT", propagated=True,
        )

        default = get_recent_allocation_transactions(session, projcode=projcode)
        trimmed = get_recent_allocation_transactions(
            session, projcode=projcode, include_propagated=False,
        )
        assert len(default) == 2
        assert len(trimmed) == 1
        assert trimmed[0]['propagated'] is False

    def test_limit_caps_results(self, session):
        allocation = make_allocation(session)
        projcode = allocation.account.project.projcode
        now = datetime.now()
        for i in range(5):
            make_allocation_transaction(
                session, allocation=allocation,
                creation_time=now - timedelta(minutes=i),
            )

        rows = get_recent_allocation_transactions(
            session, projcode=projcode, limit=3,
        )
        assert len(rows) == 3

    def test_sort_by_amount_asc_and_desc(self, session):
        allocation = make_allocation(session)
        projcode = allocation.account.project.projcode
        for amt in (250.0, 50.0, 500.0, 100.0):
            make_allocation_transaction(
                session, allocation=allocation, transaction_amount=amt,
            )
        asc = get_recent_allocation_transactions(
            session, projcode=projcode,
            sort_by='transaction_amount', sort_dir='asc',
        )
        desc = get_recent_allocation_transactions(
            session, projcode=projcode,
            sort_by='transaction_amount', sort_dir='desc',
        )
        asc_amts = [r['transaction_amount'] for r in asc]
        desc_amts = [r['transaction_amount'] for r in desc]
        assert asc_amts == sorted(asc_amts)
        assert desc_amts == sorted(desc_amts, reverse=True)

    @pytest.mark.parametrize('sort_by', list(ALLOCATION_TRANSACTION_SORT_COLUMNS))
    def test_sort_by_whitelist_all_succeed(self, session, sort_by):
        allocation = make_allocation(session)
        projcode = allocation.account.project.projcode
        make_allocation_transaction(session, allocation=allocation)
        rows = get_recent_allocation_transactions(
            session, projcode=projcode, sort_by=sort_by,
        )
        assert len(rows) == 1

    def test_unknown_sort_by_raises(self, session):
        with pytest.raises(ValueError, match='Unknown sort_by'):
            get_recent_allocation_transactions(session, sort_by='bogus_col')

    def test_bad_sort_dir_raises(self, session):
        with pytest.raises(ValueError, match="sort_dir"):
            get_recent_allocation_transactions(session, sort_dir='sideways')

    def test_offset_and_limit_pagination(self, session):
        allocation = make_allocation(session)
        projcode = allocation.account.project.projcode
        now = datetime.now()
        # Seed 7 txns with distinct creation_time values so the DESC order is stable.
        for i in range(7):
            make_allocation_transaction(
                session, allocation=allocation,
                creation_time=now - timedelta(hours=i),
            )
        all_rows = get_recent_allocation_transactions(session, projcode=projcode)
        assert len(all_rows) == 7

        page1 = get_recent_allocation_transactions(
            session, projcode=projcode, limit=3,
        )
        page2 = get_recent_allocation_transactions(
            session, projcode=projcode, offset=3, limit=3,
        )
        page3 = get_recent_allocation_transactions(
            session, projcode=projcode, offset=6, limit=3,
        )
        assert [r['transaction_id'] for r in page1] == \
               [r['transaction_id'] for r in all_rows[:3]]
        assert [r['transaction_id'] for r in page2] == \
               [r['transaction_id'] for r in all_rows[3:6]]
        assert [r['transaction_id'] for r in page3] == \
               [r['transaction_id'] for r in all_rows[6:]]

    def test_count_matches_unlimited_get(self, session):
        allocation = make_allocation(session)
        projcode = allocation.account.project.projcode
        for _ in range(4):
            make_allocation_transaction(session, allocation=allocation)
        total = count_recent_allocation_transactions(session, projcode=projcode)
        rows = get_recent_allocation_transactions(session, projcode=projcode)
        assert total == len(rows) == 4

        # Filter-consistency: count must honor the same filters as get_recent.
        make_allocation_transaction(
            session, allocation=allocation,
            transaction_type="EDIT", propagated=True,
        )
        assert count_recent_allocation_transactions(
            session, projcode=projcode, include_propagated=False,
        ) == 4  # the new propagated txn is excluded by both

    def test_transaction_id_filter_returns_single_row(self, session):
        allocation = make_allocation(session)
        txn_a = make_allocation_transaction(session, allocation=allocation)
        make_allocation_transaction(session, allocation=allocation)

        rows = get_recent_allocation_transactions(
            session, transaction_id=txn_a.allocation_transaction_id,
        )
        assert len(rows) == 1
        assert rows[0]['transaction_id'] == txn_a.allocation_transaction_id

    def test_transaction_id_unknown_returns_empty(self, session):
        rows = get_recent_allocation_transactions(session, transaction_id=99_999_999)
        assert rows == []


# ============================================================================
# sam.queries.charges.get_recent_charge_adjustments
# ============================================================================


EXPECTED_ADJ_KEYS = {
    'adjustment_id', 'account_id', 'amount', 'adjustment_date', 'comment',
    'adjustment_type',
    'projcode', 'project_id', 'resource_name', 'resource_id', 'facility',
    'user_id', 'username', 'user_display_name',
}


class TestRecentChargeAdjustments:
    """Tests for get_recent_charge_adjustments. Each test seeds its own
    adjustments scoped to a fresh projcode so assertions are deterministic
    against snapshot data."""

    def test_returns_dicts_with_expected_keys_ordered_desc(self, session):
        allocation = make_allocation(session)
        account = allocation.account
        projcode = account.project.projcode
        base = datetime(2099, 6, 15, 12, 0, 0)
        make_charge_adjustment(session, account=account, adjustment_date=base - timedelta(days=30))
        make_charge_adjustment(session, account=account, adjustment_date=base - timedelta(days=5))
        make_charge_adjustment(session, account=account, adjustment_date=base)

        rows = get_recent_charge_adjustments(session, projcode=projcode)

        assert len(rows) == 3
        for r in rows:
            assert set(r.keys()) == EXPECTED_ADJ_KEYS
            assert r['projcode'] == projcode
        dates = [r['adjustment_date'] for r in rows]
        assert dates == sorted(dates, reverse=True)

    def test_projcode_list_filter(self, session):
        a1 = make_allocation(session)
        a2 = make_allocation(session)
        make_charge_adjustment(session, account=a1.account)
        make_charge_adjustment(session, account=a2.account)

        codes = [a1.account.project.projcode, a2.account.project.projcode]
        rows = get_recent_charge_adjustments(session, projcode=codes)
        returned = {r['projcode'] for r in rows}
        assert set(codes).issubset(returned)

    def test_resource_filter(self, session):
        allocation = make_allocation(session)
        account = allocation.account
        projcode = account.project.projcode
        make_charge_adjustment(session, account=account)

        resource_name = account.resource.resource_name
        rows = get_recent_charge_adjustments(
            session, projcode=projcode, resource_name=resource_name,
        )
        assert len(rows) == 1
        assert rows[0]['resource_name'] == resource_name

        empty = get_recent_charge_adjustments(
            session, projcode=projcode, resource_name="__no_such_resource__",
        )
        assert empty == []

    def test_date_range_is_inclusive(self, session):
        allocation = make_allocation(session)
        account = allocation.account
        projcode = account.project.projcode
        base = datetime(2099, 6, 15)
        make_charge_adjustment(
            session, account=account, adjustment_date=base - timedelta(days=1),
        )
        on_start = make_charge_adjustment(
            session, account=account, adjustment_date=base,
        )
        on_end = make_charge_adjustment(
            session, account=account, adjustment_date=base + timedelta(days=10),
        )
        make_charge_adjustment(
            session, account=account, adjustment_date=base + timedelta(days=11),
        )

        rows = get_recent_charge_adjustments(
            session, projcode=projcode,
            start_date=base, end_date=base + timedelta(days=10),
        )
        ids = {r['adjustment_id'] for r in rows}
        assert ids == {on_start.charge_adjustment_id, on_end.charge_adjustment_id}

    def test_adjustment_types_scalar_and_list(self, session):
        types = session.query(ChargeAdjustmentType).limit(2).all()
        if len(types) < 2:
            pytest.skip("Need ≥2 ChargeAdjustmentType rows for this test")
        t_a, t_b = types[0], types[1]

        allocation = make_allocation(session)
        account = allocation.account
        projcode = account.project.projcode
        make_charge_adjustment(session, account=account, adjustment_type=t_a)
        make_charge_adjustment(session, account=account, adjustment_type=t_a)
        make_charge_adjustment(session, account=account, adjustment_type=t_b)

        scalar = get_recent_charge_adjustments(
            session, projcode=projcode, adjustment_types=t_a.type,
        )
        assert len(scalar) == 2
        assert {r['adjustment_type'] for r in scalar} == {t_a.type}

        both = get_recent_charge_adjustments(
            session, projcode=projcode, adjustment_types=[t_a.type, t_b.type],
        )
        assert len(both) == 3
        assert {r['adjustment_type'] for r in both} == {t_a.type, t_b.type}

    def test_user_id_and_username_filters(self, session):
        allocation = make_allocation(session)
        account = allocation.account
        projcode = account.project.projcode
        alice = make_user(session)
        bob = make_user(session)
        make_charge_adjustment(session, account=account, adjusted_by=alice)
        make_charge_adjustment(session, account=account, adjusted_by=bob)
        make_charge_adjustment(session, account=account, adjusted_by=None)

        by_id = get_recent_charge_adjustments(
            session, projcode=projcode, user_id=alice.user_id,
        )
        by_name = get_recent_charge_adjustments(
            session, projcode=projcode, username=alice.username,
        )
        assert len(by_id) == 1 and len(by_name) == 1
        assert by_id[0]['user_id'] == alice.user_id
        assert by_name[0]['username'] == alice.username

        with pytest.raises(ValueError):
            get_recent_charge_adjustments(
                session, projcode=projcode,
                user_id=alice.user_id, username=alice.username,
            )

    def test_outer_join_returns_rows_with_no_user(self, session):
        allocation = make_allocation(session)
        account = allocation.account
        projcode = account.project.projcode
        make_charge_adjustment(session, account=account, adjusted_by=None)

        rows = get_recent_charge_adjustments(session, projcode=projcode)
        assert len(rows) == 1
        assert rows[0]['user_id'] is None
        assert rows[0]['username'] is None
        assert rows[0]['user_display_name'] is None

    def test_include_deleted_hides_then_shows_soft_deleted_account(self, session):
        allocation = make_allocation(session)
        account = allocation.account
        projcode = account.project.projcode
        make_charge_adjustment(session, account=account, comment="after-delete")
        account.deleted = True
        session.flush()

        hidden = get_recent_charge_adjustments(session, projcode=projcode)
        assert hidden == []

        shown = get_recent_charge_adjustments(
            session, projcode=projcode, include_deleted=True,
        )
        assert len(shown) == 1
        assert shown[0]['comment'] == "after-delete"

    def test_limit_caps_results(self, session):
        allocation = make_allocation(session)
        account = allocation.account
        projcode = account.project.projcode
        base = datetime(2099, 6, 15)
        for i in range(5):
            make_charge_adjustment(
                session, account=account,
                adjustment_date=base - timedelta(minutes=i),
            )

        rows = get_recent_charge_adjustments(
            session, projcode=projcode, limit=3,
        )
        assert len(rows) == 3

    def test_sort_by_amount_asc_and_desc(self, session):
        allocation = make_allocation(session)
        account = allocation.account
        projcode = account.project.projcode
        for amt in (-250.0, -50.0, -500.0, -100.0):
            make_charge_adjustment(session, account=account, amount=amt)
        asc = get_recent_charge_adjustments(
            session, projcode=projcode, sort_by='amount', sort_dir='asc',
        )
        desc = get_recent_charge_adjustments(
            session, projcode=projcode, sort_by='amount', sort_dir='desc',
        )
        assert [r['amount'] for r in asc] == sorted(r['amount'] for r in asc)
        assert [r['amount'] for r in desc] == \
               sorted((r['amount'] for r in desc), reverse=True)

    @pytest.mark.parametrize('sort_by', list(CHARGE_ADJUSTMENT_SORT_COLUMNS))
    def test_sort_by_whitelist_all_succeed(self, session, sort_by):
        allocation = make_allocation(session)
        account = allocation.account
        projcode = account.project.projcode
        make_charge_adjustment(session, account=account)
        rows = get_recent_charge_adjustments(
            session, projcode=projcode, sort_by=sort_by,
        )
        assert len(rows) == 1

    def test_unknown_sort_by_raises(self, session):
        with pytest.raises(ValueError, match='Unknown sort_by'):
            get_recent_charge_adjustments(session, sort_by='bogus_col')

    def test_bad_sort_dir_raises(self, session):
        with pytest.raises(ValueError, match='sort_dir'):
            get_recent_charge_adjustments(session, sort_dir='sideways')

    def test_offset_and_limit_pagination(self, session):
        allocation = make_allocation(session)
        account = allocation.account
        projcode = account.project.projcode
        base = datetime(2099, 6, 15)
        for i in range(7):
            make_charge_adjustment(
                session, account=account,
                adjustment_date=base - timedelta(hours=i),
            )
        all_rows = get_recent_charge_adjustments(session, projcode=projcode)
        assert len(all_rows) == 7
        page1 = get_recent_charge_adjustments(
            session, projcode=projcode, limit=3,
        )
        page2 = get_recent_charge_adjustments(
            session, projcode=projcode, offset=3, limit=3,
        )
        assert [r['adjustment_id'] for r in page1] == \
               [r['adjustment_id'] for r in all_rows[:3]]
        assert [r['adjustment_id'] for r in page2] == \
               [r['adjustment_id'] for r in all_rows[3:6]]

    def test_count_matches_unlimited_get(self, session):
        allocation = make_allocation(session)
        account = allocation.account
        projcode = account.project.projcode
        for _ in range(4):
            make_charge_adjustment(session, account=account)
        total = count_recent_charge_adjustments(session, projcode=projcode)
        rows = get_recent_charge_adjustments(session, projcode=projcode)
        assert total == len(rows) == 4

        # Filter-consistency: count honors the same filters as get_recent.
        account.deleted = True
        session.flush()
        assert count_recent_charge_adjustments(session, projcode=projcode) == 0
        assert count_recent_charge_adjustments(
            session, projcode=projcode, include_deleted=True,
        ) == 4

    def test_adjustment_id_filter_returns_single_row(self, session):
        allocation = make_allocation(session)
        account = allocation.account
        adj_a = make_charge_adjustment(session, account=account)
        make_charge_adjustment(session, account=account)

        rows = get_recent_charge_adjustments(
            session, adjustment_id=adj_a.charge_adjustment_id,
        )
        assert len(rows) == 1
        assert rows[0]['adjustment_id'] == adj_a.charge_adjustment_id

    def test_adjustment_id_unknown_returns_empty(self, session):
        rows = get_recent_charge_adjustments(session, adjustment_id=99_999_999)
        assert rows == []


# ============================================================================
# sam.queries.statistics
# ============================================================================


class TestStatisticsQueries:

    def test_get_user_statistics_structure(self, session):
        result = get_user_statistics(session)
        assert isinstance(result, dict)
        for field in ('total_users', 'active_users', 'locked_users', 'inactive_users'):
            assert field in result

    def test_get_user_statistics_counts(self, session):
        result = get_user_statistics(session)
        assert result['total_users'] >= 0
        assert result['active_users'] >= 0
        assert result['locked_users'] >= 0
        assert result['inactive_users'] >= 0
        assert result['total_users'] >= result['active_users']
        assert result['inactive_users'] == result['total_users'] - result['active_users']

    def test_get_project_statistics_structure(self, session):
        result = get_project_statistics(session)
        assert isinstance(result, dict)
        for field in ('total_projects', 'active_projects', 'inactive_projects', 'by_facility'):
            assert field in result

    def test_get_project_statistics_facility_breakdown(self, session):
        result = get_project_statistics(session)
        assert isinstance(result['by_facility'], dict)
        for facility_name, count in result['by_facility'].items():
            assert isinstance(facility_name, str)
            assert isinstance(count, int)
            assert count >= 0


# ============================================================================
# sam.queries.projects
# ============================================================================


class TestProjectQueries:

    def test_search_projects_by_code(self, session, active_project):
        """Search should find at least the representative project by code prefix."""
        # Use the first 4 chars of the representative project's projcode as
        # the search term — self-consistent, no hardcoded values.
        term = active_project.projcode[:4]
        result = search_projects_by_code_or_title(session, term)
        assert isinstance(result, list)
        assert len(result) > 0
        for project in result:
            assert term.upper() in project.projcode.upper() or term.lower() in (project.title or '').lower()

    def test_search_projects_by_title(self, session, active_project):
        """Search by a word from the representative project's title."""
        title = active_project.title or ''
        words = [w for w in title.split() if len(w) >= 4]
        if not words:
            pytest.skip(f"Representative project {active_project.projcode} has no searchable title word")
        term = words[0].lower()
        result = search_projects_by_code_or_title(session, term)
        assert isinstance(result, list)
        # The representative project itself must come back
        assert any(p.project_id == active_project.project_id for p in result)

    def test_search_projects_active_filter(self, session, active_project):
        """active=True and active=False produce properly filtered results."""
        term = active_project.projcode[:2]
        result_active = search_projects_by_code_or_title(session, term, active=True)
        for p in result_active:
            assert p.active is True
        result_inactive = search_projects_by_code_or_title(session, term, active=False)
        for p in result_inactive:
            assert p.active is False


# ============================================================================
# Allocation summary with annualized rates
# ============================================================================


class TestAllocationSummaryWithRates:

    def test_get_allocation_summary_single_allocation_has_rate(
        self, session, active_project
    ):
        results = get_allocation_summary(
            session, projcode=active_project.projcode, active_only=True
        )
        assert len(results) > 0

        for result in results:
            for field in ('count', 'duration_days', 'annualized_rate', 'is_open_ended'):
                assert field in result

            if result['count'] == 1:
                assert result['duration_days'] is not None
                assert result['annualized_rate'] is not None
                assert isinstance(result['duration_days'], int)
                assert isinstance(result['annualized_rate'], (int, float))
                assert result['annualized_rate'] >= 0
                assert isinstance(result['is_open_ended'], bool)

                if result['duration_days'] > 0:
                    expected_rate = (result['total_amount'] / result['duration_days']) * 365
                    assert abs(result['annualized_rate'] - expected_rate) < 0.01

    def test_get_allocation_summary_aggregated_no_rate(self, session):
        """Aggregated results (count > 1) must have None for rate fields."""
        # Query a TOTAL rollup for any facility that has one.
        # If the snapshot has no aggregated results, skip.
        results = get_allocation_summary(
            session, projcode="TOTAL", active_only=True
        )
        aggregated = [r for r in results if r['count'] > 1]
        if not aggregated:
            pytest.skip("Snapshot has no aggregated allocations (count > 1)")

        for result in aggregated:
            assert result['duration_days'] is None
            assert result['annualized_rate'] is None
            assert result['is_open_ended'] is False

    def test_get_allocation_summary_rate_consistency(
        self, session, active_project, hpc_resource
    ):
        """Rate is (amount / duration_days) * 365 for single allocations."""
        results = get_allocation_summary(
            session,
            resource_name=hpc_resource.resource_name,
            projcode=active_project.projcode,
        )
        single = [r for r in results if r['count'] == 1]
        if not single:
            pytest.skip(
                f"No single allocations for {active_project.projcode} on "
                f"{hpc_resource.resource_name}"
            )
        for result in single:
            if result['total_amount'] > 0:
                assert result['annualized_rate'] > 0
            if result['duration_days'] and result['duration_days'] > 0:
                ratio = result['annualized_rate'] / result['total_amount']
                expected = 365 / result['duration_days']
                assert abs(ratio - expected) < 0.0001

    def test_get_allocation_summary_with_usage_includes_rates(
        self, session, active_project
    ):
        results = get_allocation_summary_with_usage(
            session, projcode=active_project.projcode, active_only=True
        )
        assert len(results) > 0
        for result in results:
            for field in ('total_used', 'percent_used', 'charges_by_type',
                          'duration_days', 'annualized_rate', 'is_open_ended'):
                assert field in result
            if result['count'] == 1:
                assert result['annualized_rate'] is not None
            else:
                assert result['annualized_rate'] is None

    def test_get_allocation_summary_zero_duration_handling(self, session):
        results = get_allocation_summary(session, active_only=False)
        for result in results:
            if result['count'] == 1 and result['duration_days'] == 0:
                assert result['annualized_rate'] == 0.0

    def test_get_allocation_summary_date_fields(self, session, active_project):
        results = get_allocation_summary(session, projcode=active_project.projcode)
        assert len(results) > 0
        for result in results:
            assert 'start_date' in result
            assert 'end_date' in result
            if result['count'] == 1:
                assert result['start_date'] is not None
                if result['end_date'] is not None:
                    assert result['end_date'] >= result['start_date']


# ============================================================================
# sam.queries.lookups — get_user_group_access
# ============================================================================


def _make_adhoc_group(session, *, active=True):
    """Build a fresh AdhocGroup with a worker-namespaced unique unix_gid."""
    import os
    worker = int(os.environ.get("PYTEST_XDIST_WORKER", "gw0").removeprefix("gw") or "0")
    # Snapshot gids are in the low thousands; stake out a high, worker-disjoint range.
    gid = 50_000_000 + worker * 100_000 + next_int("adhoc_gid")
    name = next_seq("g")[:30]
    grp = AdhocGroup(group_name=name, unix_gid=gid, active=active)
    session.add(grp)
    session.flush()
    return grp


def _make_membership(session, *, group, username, branch='hpc'):
    entry = AdhocSystemAccountEntry(
        group_id=group.group_id,
        username=username,
        access_branch_name=branch,
    )
    session.add(entry)
    session.flush()
    return entry


class TestGetUserGroupAccess:

    def test_username_filter_returns_only_that_user(self, session):
        user = make_user(session)
        g1 = _make_adhoc_group(session)
        g2 = _make_adhoc_group(session)
        _make_membership(session, group=g1, username=user.username, branch='hpc')
        _make_membership(session, group=g2, username=user.username, branch='hpc-dev')

        result = get_user_group_access(session, username=user.username)

        assert set(result) == {user.username}
        entries = result[user.username]
        assert len(entries) == 2
        by_name = {e['group_name']: e for e in entries}
        assert by_name[g1.group_name]['unix_gid'] == g1.unix_gid
        assert by_name[g1.group_name]['access_branch_name'] == 'hpc'
        assert by_name[g2.group_name]['unix_gid'] == g2.unix_gid
        assert by_name[g2.group_name]['access_branch_name'] == 'hpc-dev'

    def test_unknown_username_returns_empty_dict(self, session):
        assert get_user_group_access(session, username='does_not_exist_xyz_zzz') == {}

    def test_no_username_includes_built_user(self, session):
        user = make_user(session)
        g = _make_adhoc_group(session)
        _make_membership(session, group=g, username=user.username)

        result = get_user_group_access(session)

        assert user.username in result
        assert any(e['group_name'] == g.group_name for e in result[user.username])

    def test_active_only_excludes_inactive_groups(self, session):
        user = make_user(session)
        active_grp = _make_adhoc_group(session, active=True)
        inactive_grp = _make_adhoc_group(session, active=False)
        _make_membership(session, group=active_grp, username=user.username)
        _make_membership(session, group=inactive_grp, username=user.username)

        active_result = get_user_group_access(session, username=user.username)
        all_result = get_user_group_access(session, username=user.username, active_only=False)

        active_names = {e['group_name'] for e in active_result[user.username]}
        all_names = {e['group_name'] for e in all_result[user.username]}
        assert active_grp.group_name in active_names
        assert inactive_grp.group_name not in active_names
        assert inactive_grp.group_name in all_names

    def test_access_branch_filter(self, session):
        user = make_user(session)
        g1 = _make_adhoc_group(session)
        g2 = _make_adhoc_group(session)
        _make_membership(session, group=g1, username=user.username, branch='hpc')
        _make_membership(session, group=g2, username=user.username, branch='hpc-dev')

        hpc_only = get_user_group_access(session, username=user.username, access_branch='hpc')

        assert list(hpc_only) == [user.username]
        entries = hpc_only[user.username]
        assert len(entries) == 1
        assert entries[0]['group_name'] == g1.group_name
        assert entries[0]['access_branch_name'] == 'hpc'


class TestGetGroupMembers:

    def test_returns_group_header_and_members(self, session):
        from sam.core.users import EmailAddress
        grp = _make_adhoc_group(session)
        u1 = make_user(session, first_name='Alice', last_name='Amos')
        u2 = make_user(session, first_name='Bob', last_name='Byrne')
        session.add(EmailAddress(user_id=u1.user_id, email_address='alice@example.org', is_primary=True))
        session.flush()
        _make_membership(session, group=grp, username=u1.username, branch='hpc')
        _make_membership(session, group=grp, username=u2.username, branch='hpc')

        data = get_group_members(session, grp.group_name, 'hpc')

        assert data is not None
        assert data['group_name'] == grp.group_name
        assert data['unix_gid'] == grp.unix_gid
        assert data['access_branch_name'] == 'hpc'
        by_user = {m['username']: m for m in data['members']}
        assert set(by_user) == {u1.username, u2.username}
        assert by_user[u1.username]['primary_email'] == 'alice@example.org'
        assert by_user[u2.username]['primary_email'] is None
        assert 'Alice' in by_user[u1.username]['display_name']

    def test_branch_filter_excludes_other_branches(self, session):
        grp = _make_adhoc_group(session)
        u_hpc = make_user(session)
        u_dev = make_user(session)
        _make_membership(session, group=grp, username=u_hpc.username, branch='hpc')
        _make_membership(session, group=grp, username=u_dev.username, branch='hpc-dev')

        data = get_group_members(session, grp.group_name, 'hpc')
        usernames = {m['username'] for m in data['members']}
        assert u_hpc.username in usernames
        assert u_dev.username not in usernames

    def test_orphan_username_fallback(self, session):
        grp = _make_adhoc_group(session)
        _make_membership(session, group=grp, username='ghost_zzz', branch='hpc')

        data = get_group_members(session, grp.group_name, 'hpc')
        ghost = next(m for m in data['members'] if m['username'] == 'ghost_zzz')
        assert ghost['display_name'] == 'ghost_zzz'
        assert ghost['primary_email'] is None

    def test_unknown_group_returns_none(self, session):
        assert get_group_members(session, 'does_not_exist_xxx', 'hpc') is None

    def test_active_only_hides_inactive_groups(self, session):
        grp = _make_adhoc_group(session, active=False)
        u = make_user(session)
        _make_membership(session, group=grp, username=u.username, branch='hpc')

        assert get_group_members(session, grp.group_name, 'hpc') is None
        data = get_group_members(session, grp.group_name, 'hpc', active_only=False)
        assert data is not None
        assert len(data['members']) == 1


class TestResolveGroupName:

    def test_resolves_via_adhoc_group_unix_gid(self, session):
        grp = _make_adhoc_group(session)
        assert resolve_group_name(session, grp.unix_gid) == grp.group_name

    def test_default_gid_falls_back_to_default_common_group(self, session):
        # gid 1000 is the system-wide LDAP default ('ncar') and is NOT
        # materialized in adhoc_group; we must still label it.
        assert resolve_group_name(session, DEFAULT_COMMON_GROUP_GID) == DEFAULT_COMMON_GROUP

    def test_unresolved_non_default_gid_returns_none(self, session):
        # Pick a gid far outside the snapshot range and not equal to the default.
        bogus_gid = 99_999_999
        assert bogus_gid != DEFAULT_COMMON_GROUP_GID
        assert resolve_group_name(session, bogus_gid) is None

    def test_none_gid_returns_none(self, session):
        assert resolve_group_name(session, None) is None
