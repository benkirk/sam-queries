"""Test AllocationWithUsageSchema's new disk current_used_* fields.

The schema gains four fields that answer "how full is this allocation
right now?" alongside the existing cumulative ``used`` field. They are
disk-only — null for HPC / DAV / ARCHIVE.
"""

from datetime import date, datetime, timedelta

import pytest

from sam.accounting.allocations import Allocation
from sam.resources.resources import ResourceType
from sam.schemas.allocation import AllocationWithUsageSchema
from sam.summaries.disk_summaries import (
    BYTES_PER_TIB, DiskChargeSummary, DiskChargeSummaryStatus,
)
from factories.core import make_user
from factories.projects import make_account, make_allocation, make_project
from factories.resources import make_resource, make_resource_type
from factories._seq import next_date, next_seq


pytestmark = pytest.mark.unit


def _ensure_status(session, activity_date):
    """FK: disk_charge_summary.activity_date → disk_charge_summary_status.
    Materialize the parent row before inserting the child summary."""
    if session.get(DiskChargeSummaryStatus, activity_date) is None:
        session.add(DiskChargeSummaryStatus(activity_date=activity_date, current=False))
        session.flush()


def _mark_current(session, activity_date):
    """Test-only narrow alternative to mark_disk_snapshot_current — see
    note in test_current_disk_usage.py: prod helper does a bulk UPDATE
    that deadlocks under xdist."""
    row = session.get(DiskChargeSummaryStatus, activity_date)
    if row is None:
        session.add(DiskChargeSummaryStatus(activity_date=activity_date, current=True))
    else:
        row.current = True
    session.flush()


def _disk_alloc(session, *, amount_tib: float = 100.0):
    user = make_user(session)
    project = make_project(session, lead=user)
    rt = session.query(ResourceType).filter_by(resource_type='DISK').first()
    if rt is None:
        rt = make_resource_type(session, resource_type='DISK')
    resource = make_resource(session, resource_type=rt,
                             resource_name=next_seq('DRES'))
    account = make_account(session, project=project, resource=resource)
    alloc = make_allocation(session, account=account, amount=amount_tib)
    return user, project, account, alloc


class TestAllocationDiskCurrentFields:

    def test_disk_allocation_has_current_used_fields(self, session):
        user, project, account, alloc = _disk_alloc(session, amount_tib=100.0)
        # Worker-unique snapshot date so xdist workers don't collide on
        # the disk_charge_summary_status PK. Push the allocation window
        # back so this date falls inside it.
        snap_date = next_date("disk_snap")
        alloc.start_date = datetime(snap_date.year - 1, 1, 1)
        alloc.end_date = datetime(snap_date.year + 1, 1, 1)
        session.flush()
        _ensure_status(session, snap_date)
        session.add(DiskChargeSummary(
            activity_date=snap_date,
            user_id=user.user_id,
            account_id=account.account_id,
            username=user.username,
            bytes=47 * BYTES_PER_TIB,
            terabyte_years=0.5,
            charges=0.5,
            number_of_files=1,
        ))
        session.flush()
        _mark_current(session, snap_date)

        schema = AllocationWithUsageSchema()
        schema.context = {
            'account': account, 'session': session, 'include_adjustments': True,
        }
        out = schema.dump(alloc)
        assert out['current_used_bytes'] == 47 * BYTES_PER_TIB
        assert out['current_used_tib'] == pytest.approx(47.0)
        assert out['current_pct_used'] == pytest.approx(47.0)
        # Schema is JSON-friendly — date may serialize to string.
        assert str(out['current_snapshot_date']).startswith(snap_date.isoformat())

    def test_non_disk_resource_has_null_current_fields(self, session):
        """HPC resource → all current_* fields are null (None)."""
        user = make_user(session)
        project = make_project(session, lead=user)
        rt = make_resource_type(session, resource_type=f"HPC-{next_seq('rt')}")
        resource = make_resource(session, resource_type=rt)
        account = make_account(session, project=project, resource=resource)
        alloc = make_allocation(session, account=account, amount=10_000.0)

        schema = AllocationWithUsageSchema()
        schema.context = {'account': account, 'session': session}
        out = schema.dump(alloc)
        assert out['current_used_bytes'] is None
        assert out['current_used_tib'] is None
        assert out['current_pct_used'] is None
        assert out['current_snapshot_date'] is None

    def test_disk_allocation_no_snapshots_returns_null(self, session):
        """Disk resource but no disk_charge_summary rows → null current_*."""
        user, project, account, alloc = _disk_alloc(session, amount_tib=10.0)
        # Don't seed any rows.
        schema = AllocationWithUsageSchema()
        schema.context = {'account': account, 'session': session}
        out = schema.dump(alloc)
        # current_disk_usage returns None when there's no row → fields null.
        assert out['current_used_bytes'] is None
        assert out['current_used_tib'] is None
        assert out['current_pct_used'] is None

    def test_current_used_distinct_from_cumulative_used(self, session):
        """Two snapshots, different sizes: cumulative SUMs both,
        current reflects only the latest. They differ — that's the
        whole point of the new fields."""
        user, project, account, alloc = _disk_alloc(session, amount_tib=100.0)
        # Worker-unique snapshot dates so xdist workers don't collide on
        # the disk_charge_summary_status PK. Set allocation window to
        # bracket both dates.
        d2 = next_date("disk_snap")
        d1 = d2 - timedelta(days=7)
        alloc.start_date = datetime(d1.year - 1, 1, 1)
        alloc.end_date = datetime(d2.year + 1, 1, 1)
        session.flush()
        for d, b, ch in [(d1, 10 * BYTES_PER_TIB, 1.0),
                         (d2, 20 * BYTES_PER_TIB, 2.0)]:
            _ensure_status(session, d)
            session.add(DiskChargeSummary(
                activity_date=d,
                user_id=user.user_id,
                account_id=account.account_id,
                username=user.username,
                bytes=b,
                terabyte_years=ch,
                charges=ch,
                number_of_files=1,
            ))
        session.flush()
        _mark_current(session, d2)

        schema = AllocationWithUsageSchema()
        schema.context = {
            'account': account, 'session': session, 'include_adjustments': True,
        }
        out = schema.dump(alloc)
        # Cumulative `used` sums charges over the allocation window: 1.0 + 2.0
        assert out['used'] == pytest.approx(3.0)
        # Current `used` is the latest snapshot only — 20 TiB.
        assert out['current_used_tib'] == pytest.approx(20.0)
        assert out['current_used_tib'] != out['used']
