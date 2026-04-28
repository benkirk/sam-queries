"""Unit tests for ``sam.queries.disk_usage`` — the disk-flavored
Resource Usage Details data layer.

Covers:
  * ``get_disk_usage_timeseries_by_user``: top-N selection by latest
    snapshot, "Others" lump-sum, dense-fill across snapshot dates,
    empty-input edge case.
  * ``build_disk_subtree``: parent + multi-child case, leaf-only case,
    nodes with no disk account on the resource.
"""

from datetime import date as _date, datetime, timedelta

import pytest

from sam import ResourceType
from sam.projects.projects import ProjectDirectory
from sam.activity.disk import DiskActivity, DiskCharge
from sam.queries.disk_usage import (
    build_disk_subtree,
    bulk_get_directory_usage_at,
    get_directory_usage_at,
    get_disk_usage_timeseries_by_user,
)
from sam.summaries.disk_summaries import (
    DiskChargeSummary,
    mark_disk_snapshot_current,
)

from factories import (
    make_account,
    make_allocation,
    make_project,
    make_resource,
    make_resource_type,
    make_user,
)
from factories._seq import next_seq


pytestmark = pytest.mark.unit


BYTES_PER_TIB = 1024 ** 4


def _disk_resource(session):
    rt = session.query(ResourceType).filter_by(resource_type='DISK').first()
    if rt is None:
        rt = make_resource_type(session, resource_type='DISK')
    return make_resource(
        session, resource_type=rt,
        resource_name=f"Campaign_Store_{next_seq('cs')}",
    )


def _seed_row(session, *, account, user, snap, bytes_, files=10):
    session.add(DiskChargeSummary(
        activity_date=snap,
        account_id=account.account_id,
        user_id=user.user_id,
        username=user.username,
        projcode=account.project.projcode,
        number_of_files=files,
        bytes=bytes_,
        terabyte_years=0.0,
        charges=0.0,
    ))


# ============================================================================
# get_disk_usage_timeseries_by_user
# ============================================================================


class TestDiskUsageTimeseries:

    def test_empty_account_ids_returns_empty(self, session):
        out = get_disk_usage_timeseries_by_user(session, account_ids=[])
        assert out == {'dates': [], 'series': []}

    def test_single_user_single_date(self, session):
        resource = _disk_resource(session)
        lead = make_user(session)
        project = make_project(session, lead=lead)
        account = make_account(session, project=project, resource=resource)
        snap = _date(2026, 4, 11)
        _seed_row(session, account=account, user=lead, snap=snap,
                  bytes_=4 * BYTES_PER_TIB)
        session.flush()

        out = get_disk_usage_timeseries_by_user(
            session, account_ids=[account.account_id],
        )
        assert out['dates'] == [snap]
        assert len(out['series']) == 1
        s0 = out['series'][0]
        assert s0['username'] == lead.username
        assert s0['values'] == [4 * BYTES_PER_TIB]

    def test_top_n_with_others_lump(self, session):
        resource = _disk_resource(session)
        lead = make_user(session)
        project = make_project(session, lead=lead)
        account = make_account(session, project=project, resource=resource)
        snap = _date(2026, 4, 11)
        # 12 distinct users with descending bytes (12, 11, ..., 1).
        users = [make_user(session) for _ in range(12)]
        for i, u in enumerate(users):
            _seed_row(session, account=account, user=u, snap=snap,
                      bytes_=(12 - i) * BYTES_PER_TIB)
        session.flush()

        out = get_disk_usage_timeseries_by_user(
            session, account_ids=[account.account_id], top_n=10,
        )
        assert out['dates'] == [snap]
        # Stack-friendly order: Others first (bottom of stack, grey),
        # then named users smallest → largest.
        names = [s['username'] for s in out['series']]
        assert names[0] == 'Others'
        assert len(names) == 11
        # Others = users ranked 11 + 12 = 2 + 1 = 3 TiB (bottom of stack).
        assert out['series'][0]['values'] == [3 * BYTES_PER_TIB]
        # Largest named user (12 TiB) sits on top of the stack.
        assert out['series'][-1]['values'] == [12 * BYTES_PER_TIB]
        # Smallest named user (3 TiB total: rank 10 → 3) sits just above Others.
        assert out['series'][1]['values'] == [3 * BYTES_PER_TIB]

    def test_dense_fill_zero_for_missing_user_dates(self, session):
        resource = _disk_resource(session)
        lead = make_user(session)
        other = make_user(session)
        project = make_project(session, lead=lead)
        account = make_account(session, project=project, resource=resource)
        d1 = _date(2026, 4, 4)
        d2 = _date(2026, 4, 11)
        # lead present on both dates; other only on d2
        _seed_row(session, account=account, user=lead, snap=d1, bytes_=BYTES_PER_TIB)
        _seed_row(session, account=account, user=lead, snap=d2, bytes_=2 * BYTES_PER_TIB)
        _seed_row(session, account=account, user=other, snap=d2, bytes_=3 * BYTES_PER_TIB)
        session.flush()

        out = get_disk_usage_timeseries_by_user(
            session, account_ids=[account.account_id],
        )
        assert out['dates'] == [d1, d2]
        # Both series have length 2; missing date filled with 0.
        for s in out['series']:
            assert len(s['values']) == 2
        by_user = {s['username']: s['values'] for s in out['series']}
        assert by_user[lead.username] == [BYTES_PER_TIB, 2 * BYTES_PER_TIB]
        assert by_user[other.username] == [0, 3 * BYTES_PER_TIB]

    def test_no_others_series_when_under_top_n(self, session):
        resource = _disk_resource(session)
        lead = make_user(session)
        project = make_project(session, lead=lead)
        account = make_account(session, project=project, resource=resource)
        snap = _date(2026, 4, 11)
        # 3 users, top_n=10 → no Others row
        for _ in range(3):
            u = make_user(session)
            _seed_row(session, account=account, user=u, snap=snap, bytes_=BYTES_PER_TIB)
        session.flush()

        out = get_disk_usage_timeseries_by_user(
            session, account_ids=[account.account_id], top_n=10,
        )
        names = [s['username'] for s in out['series']]
        assert 'Others' not in names
        assert len(names) == 3

    def test_date_range_filter(self, session):
        resource = _disk_resource(session)
        lead = make_user(session)
        project = make_project(session, lead=lead)
        account = make_account(session, project=project, resource=resource)
        d1 = _date(2026, 1, 3)
        d2 = _date(2026, 4, 11)
        _seed_row(session, account=account, user=lead, snap=d1, bytes_=BYTES_PER_TIB)
        _seed_row(session, account=account, user=lead, snap=d2, bytes_=2 * BYTES_PER_TIB)
        session.flush()

        out = get_disk_usage_timeseries_by_user(
            session, account_ids=[account.account_id],
            start_date=_date(2026, 4, 1),
        )
        assert out['dates'] == [d2]


# ============================================================================
# build_disk_subtree
# ============================================================================


class TestBuildDiskSubtree:

    def test_leaf_project_no_children(self, session):
        resource = _disk_resource(session)
        lead = make_user(session)
        project = make_project(session, lead=lead)
        account = make_account(session, project=project, resource=resource)
        snap = _date(2026, 4, 11)
        _seed_row(session, account=account, user=lead, snap=snap,
                  bytes_=10 * BYTES_PER_TIB)
        session.flush()
        mark_disk_snapshot_current(session, snap)

        result = build_disk_subtree(session, project, resource.resource_name)
        tree = result['tree']
        assert tree['projcode'] == project.projcode
        assert tree['account_id'] == account.account_id
        assert tree['current_bytes'] == 10 * BYTES_PER_TIB
        assert tree['current_used_tib'] == pytest.approx(10.0, abs=1e-6)
        assert tree['activity_date'] == snap
        assert tree['children'] == []
        assert result['account_ids'] == [account.account_id]

    def test_parent_with_children(self, session):
        resource = _disk_resource(session)
        parent_lead = make_user(session)
        parent = make_project(session, lead=parent_lead)
        child_a = make_project(session, parent=parent, lead=make_user(session))
        child_b = make_project(session, parent=parent, lead=make_user(session))

        account_p = make_account(session, project=parent, resource=resource)
        account_a = make_account(session, project=child_a, resource=resource)
        account_b = make_account(session, project=child_b, resource=resource)

        snap = _date(2026, 4, 11)
        _seed_row(session, account=account_a, user=child_a.lead, snap=snap,
                  bytes_=4 * BYTES_PER_TIB)
        _seed_row(session, account=account_b, user=child_b.lead, snap=snap,
                  bytes_=3 * BYTES_PER_TIB)
        session.flush()
        mark_disk_snapshot_current(session, snap)

        result = build_disk_subtree(session, parent, resource.resource_name)
        tree = result['tree']
        assert tree['projcode'] == parent.projcode
        assert len(tree['children']) == 2
        kids = {c['projcode']: c for c in tree['children']}
        assert kids[child_a.projcode]['current_bytes'] == 4 * BYTES_PER_TIB
        assert kids[child_b.projcode]['current_bytes'] == 3 * BYTES_PER_TIB
        # Children sorted by projcode for determinism.
        assert [c['projcode'] for c in tree['children']] == sorted(kids)
        # Account-id list aggregates the entire subtree.
        assert sorted(result['account_ids']) == sorted(
            [account_p.account_id, account_a.account_id, account_b.account_id]
        )

    def test_node_without_disk_account_still_shown(self, session):
        resource = _disk_resource(session)
        parent = make_project(session, lead=make_user(session))
        child = make_project(session, parent=parent, lead=make_user(session))
        # Only the child has a disk account on this resource.
        account_child = make_account(session, project=child, resource=resource)
        snap = _date(2026, 4, 11)
        _seed_row(session, account=account_child, user=child.lead, snap=snap,
                  bytes_=2 * BYTES_PER_TIB)
        session.flush()
        mark_disk_snapshot_current(session, snap)

        result = build_disk_subtree(session, parent, resource.resource_name)
        tree = result['tree']
        # Parent has no account on this resource → present, but inert.
        assert tree['account_id'] is None
        assert tree['current_bytes'] == 0
        assert tree['activity_date'] is None
        # Child appears as expected.
        assert len(tree['children']) == 1
        assert tree['children'][0]['account_id'] == account_child.account_id
        # Only the child contributes to account_ids.
        assert result['account_ids'] == [account_child.account_id]

    def test_unknown_resource_returns_empty_account_ids(self, session):
        parent = make_project(session, lead=make_user(session))
        result = build_disk_subtree(session, parent, 'NoSuchResource')
        assert result['account_ids'] == []
        assert result['tree']['projcode'] == parent.projcode

    def test_fileset_paths_attached(self, session):
        resource = _disk_resource(session)
        lead = make_user(session)
        project = make_project(session, lead=lead)
        make_account(session, project=project, resource=resource)
        # Backdate start_date by a day so the row is unambiguously
        # "active" under DateRangeMixin.is_active even if the test's
        # Python clock and MySQL NOW() are in different timezones (CI
        # containers can drift by hours).
        backdate = datetime.now() - timedelta(days=1)
        ProjectDirectory.create(
            session, project_id=project.project_id,
            directory_name='/gpfs/csfs1/test/path_a',
            start_date=backdate,
        )
        ProjectDirectory.create(
            session, project_id=project.project_id,
            directory_name='/gpfs/csfs1/test/path_b',
            start_date=backdate,
        )
        session.flush()
        result = build_disk_subtree(session, project, resource.resource_name)
        paths = result['tree']['fileset_paths']
        assert paths == [
            '/gpfs/csfs1/test/path_a',
            '/gpfs/csfs1/test/path_b',
        ]


# ============================================================================
# get_directory_usage_at / bulk_get_directory_usage_at  (Layer 2 tier-1/-2)
# ============================================================================


def _seed_disk_activity(
    session, *, account, user, directory, snap, bytes_, files=10,
    resource_name=None,
):
    """Insert one disk_activity + matching disk_charge row."""
    activity = DiskActivity(
        directory_name=directory,
        username=user.username,
        projcode=account.project.projcode,
        activity_date=snap,
        reporting_interval=7,
        file_size_total=bytes_,
        bytes=bytes_,
        number_of_files=files,
        load_date=datetime.now(),
        disk_cos_id=0,
        processing_status=True,
        resource_name=resource_name,
    )
    session.add(activity)
    session.flush()
    session.add(DiskCharge(
        disk_activity_id=activity.disk_activity_id,
        account_id=account.account_id,
        user_id=user.user_id,
        charge_date=datetime.now(),
        activity_date=snap,
        terabyte_year=0.0,
        charge=0.0,
    ))
    session.flush()
    return activity


class TestPerDirectoryQuery:

    def test_get_directory_usage_at_returns_per_directory_rows(self, session):
        resource = _disk_resource(session)
        lead = make_user(session)
        project = make_project(session, lead=lead)
        account = make_account(session, project=project, resource=resource)
        snap = _date(2026, 4, 11)
        _seed_disk_activity(
            session, account=account, user=lead,
            directory='/gpfs/csfs1/test/data',
            snap=snap, bytes_=2 * BYTES_PER_TIB, files=200,
            resource_name=resource.resource_name,
        )
        _seed_disk_activity(
            session, account=account, user=lead,
            directory='/gpfs/csfs1/test/work',
            snap=snap, bytes_=3 * BYTES_PER_TIB, files=300,
            resource_name=resource.resource_name,
        )

        rows = get_directory_usage_at(
            session,
            project_id=project.project_id,
            resource_name=resource.resource_name,
            activity_date=snap,
        )
        # Sorted desc by bytes.
        assert [r['name'] for r in rows] == [
            '/gpfs/csfs1/test/work',
            '/gpfs/csfs1/test/data',
        ]
        assert rows[0]['bytes'] == 3 * BYTES_PER_TIB
        assert rows[0]['files'] == 300
        assert rows[1]['bytes'] == 2 * BYTES_PER_TIB
        assert rows[1]['files'] == 200

    def test_get_directory_usage_at_filters_by_resource(self, session):
        """Rows on a different resource on the same date must not leak in."""
        resource_a = _disk_resource(session)
        resource_b = _disk_resource(session)
        lead = make_user(session)
        project = make_project(session, lead=lead)
        acct_a = make_account(session, project=project, resource=resource_a)
        acct_b = make_account(session, project=project, resource=resource_b)
        snap = _date(2026, 4, 11)
        _seed_disk_activity(
            session, account=acct_a, user=lead,
            directory='/cs/data', snap=snap, bytes_=BYTES_PER_TIB,
            resource_name=resource_a.resource_name,
        )
        _seed_disk_activity(
            session, account=acct_b, user=lead,
            directory='/quasar/data', snap=snap, bytes_=BYTES_PER_TIB,
            resource_name=resource_b.resource_name,
        )

        rows_a = get_directory_usage_at(
            session,
            project_id=project.project_id,
            resource_name=resource_a.resource_name,
            activity_date=snap,
        )
        assert [r['name'] for r in rows_a] == ['/cs/data']

    def test_get_directory_usage_at_unresolved_excluded(self, session):
        """A disk_activity row with no disk_charge (unresolved) is excluded
        — the join filter requires both tiers."""
        resource = _disk_resource(session)
        lead = make_user(session)
        project = make_project(session, lead=lead)
        account = make_account(session, project=project, resource=resource)
        snap = _date(2026, 4, 11)
        # Resolved row (writes both tiers).
        _seed_disk_activity(
            session, account=account, user=lead,
            directory='/cs/data', snap=snap, bytes_=BYTES_PER_TIB,
            resource_name=resource.resource_name,
        )
        # Tier-1-only row (no tier-2 — simulates unresolved import).
        session.add(DiskActivity(
            directory_name='/cs/orphan',
            username='ghost',
            projcode='ZZZZ9999',
            activity_date=snap,
            reporting_interval=7,
            file_size_total=BYTES_PER_TIB,
            bytes=BYTES_PER_TIB,
            number_of_files=1,
            load_date=datetime.now(),
            disk_cos_id=0,
            processing_status=False,
            error_comment='unresolved',
            resource_name=resource.resource_name,
        ))
        session.flush()

        rows = get_directory_usage_at(
            session,
            project_id=project.project_id,
            resource_name=resource.resource_name,
            activity_date=snap,
        )
        # Only the resolved row.
        assert [r['name'] for r in rows] == ['/cs/data']

    def test_bulk_per_directory_query_pairs(self, session):
        """Bulk variant returns a dict keyed by (project_id, resource_name);
        unrequested pairs are absent or empty."""
        resource = _disk_resource(session)
        lead = make_user(session)
        proj_a = make_project(session, lead=lead)
        proj_b = make_project(session, lead=lead)
        acct_a = make_account(session, project=proj_a, resource=resource)
        acct_b = make_account(session, project=proj_b, resource=resource)
        snap = _date(2026, 4, 11)
        _seed_disk_activity(
            session, account=acct_a, user=lead,
            directory='/cs/a/data', snap=snap, bytes_=BYTES_PER_TIB,
            resource_name=resource.resource_name,
        )
        _seed_disk_activity(
            session, account=acct_a, user=lead,
            directory='/cs/a/work', snap=snap, bytes_=2 * BYTES_PER_TIB,
            resource_name=resource.resource_name,
        )
        _seed_disk_activity(
            session, account=acct_b, user=lead,
            directory='/cs/b/data', snap=snap, bytes_=3 * BYTES_PER_TIB,
            resource_name=resource.resource_name,
        )

        out = bulk_get_directory_usage_at(
            session,
            project_resource_pairs=[
                (proj_a.project_id, resource.resource_name),
                (proj_b.project_id, resource.resource_name),
            ],
            activity_date=snap,
        )
        assert set(out.keys()) == {
            (proj_a.project_id, resource.resource_name),
            (proj_b.project_id, resource.resource_name),
        }
        names_a = [d['name'] for d in out[(proj_a.project_id,
                                           resource.resource_name)]]
        assert names_a == ['/cs/a/work', '/cs/a/data']
        names_b = [d['name'] for d in out[(proj_b.project_id,
                                           resource.resource_name)]]
        assert names_b == ['/cs/b/data']

    def test_bulk_per_directory_query_empty_input(self, session):
        out = bulk_get_directory_usage_at(
            session,
            project_resource_pairs=[],
            activity_date=_date(2026, 4, 11),
        )
        assert out == {}


class TestBuildDiskSubtreeMultifileset:
    """build_disk_subtree attaches per-fileset bytes when >1 active
    ProjectDirectory exists."""

    def test_multifileset_node_carries_directories(self, session):
        resource = _disk_resource(session)
        lead = make_user(session)
        project = make_project(session, lead=lead)
        account = make_account(session, project=project, resource=resource)
        backdate = datetime.now() - timedelta(days=1)
        ProjectDirectory.create(
            session, project_id=project.project_id,
            directory_name='/gpfs/csfs1/test/data',
            start_date=backdate,
        )
        ProjectDirectory.create(
            session, project_id=project.project_id,
            directory_name='/gpfs/csfs1/test/work',
            start_date=backdate,
        )
        snap = _date(2026, 4, 11)
        # Tier-3 row drives node.activity_date.
        _seed_row(session, account=account, user=lead, snap=snap,
                  bytes_=5 * BYTES_PER_TIB)
        # Tier-1/2 rows drive the per-fileset breakdown.
        _seed_disk_activity(
            session, account=account, user=lead,
            directory='/gpfs/csfs1/test/data',
            snap=snap, bytes_=2 * BYTES_PER_TIB, files=200,
            resource_name=resource.resource_name,
        )
        _seed_disk_activity(
            session, account=account, user=lead,
            directory='/gpfs/csfs1/test/work',
            snap=snap, bytes_=3 * BYTES_PER_TIB, files=300,
            resource_name=resource.resource_name,
        )
        mark_disk_snapshot_current(session, snap)
        session.flush()

        result = build_disk_subtree(session, project, resource.resource_name)
        node = result['tree']
        # Multi-fileset → directories attached, sorted desc by bytes.
        assert 'directories' in node
        assert [d['name'] for d in node['directories']] == [
            '/gpfs/csfs1/test/work',
            '/gpfs/csfs1/test/data',
        ]
        # Sum of per-fileset bytes equals the project-level current_bytes.
        assert (sum(d['bytes'] for d in node['directories'])
                == node['current_bytes'])

    def test_single_fileset_node_omits_directories(self, session):
        """Single-fileset projects keep the existing render path — no
        ``directories`` payload."""
        resource = _disk_resource(session)
        lead = make_user(session)
        project = make_project(session, lead=lead)
        account = make_account(session, project=project, resource=resource)
        backdate = datetime.now() - timedelta(days=1)
        ProjectDirectory.create(
            session, project_id=project.project_id,
            directory_name='/gpfs/csfs1/test/only',
            start_date=backdate,
        )
        snap = _date(2026, 4, 11)
        _seed_row(session, account=account, user=lead, snap=snap,
                  bytes_=BYTES_PER_TIB)
        _seed_disk_activity(
            session, account=account, user=lead,
            directory='/gpfs/csfs1/test/only',
            snap=snap, bytes_=BYTES_PER_TIB,
            resource_name=resource.resource_name,
        )
        mark_disk_snapshot_current(session, snap)
        session.flush()

        result = build_disk_subtree(session, project, resource.resource_name)
        node = result['tree']
        assert 'directories' not in node
        assert node['fileset_paths'] == ['/gpfs/csfs1/test/only']
