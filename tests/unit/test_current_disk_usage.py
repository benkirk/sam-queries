"""Tests for Account.current_disk_usage / Project.current_disk_usage / mark_disk_snapshot_current."""

from datetime import date, timedelta

import pytest

from sam.accounting.accounts import Account, CurrentDiskUsage
from sam.resources.resources import ResourceType
from sam.summaries.disk_summaries import (
    DiskChargeSummary, DiskChargeSummaryStatus,
    mark_disk_snapshot_current,
)
from factories.core import make_user
from factories.projects import make_account, make_project
from factories.resources import make_resource, make_resource_type
from factories._seq import next_date, next_seq


def _disk_account(session):
    user = make_user(session)
    project = make_project(session, lead=user)
    rt = session.query(ResourceType).filter_by(resource_type='DISK').first()
    if rt is None:
        rt = make_resource_type(session, resource_type='DISK')
    resource = make_resource(session, resource_type=rt,
                             resource_name=next_seq('DRES'))
    account = make_account(session, project=project, resource=resource)
    return user, project, account


def _seed_summary(session, account, user, *, activity_date, bytes_, ty=1.0, files=10):
    # FK: disk_charge_summary.activity_date → disk_charge_summary_status.activity_date.
    # Ensure the parent status row exists before inserting the child summary.
    if session.get(DiskChargeSummaryStatus, activity_date) is None:
        session.add(DiskChargeSummaryStatus(activity_date=activity_date, current=False))
        session.flush()
    row = DiskChargeSummary(
        activity_date=activity_date,
        user_id=user.user_id,
        account_id=account.account_id,
        username=user.username,
        bytes=bytes_,
        terabyte_years=ty,
        charges=ty,
        number_of_files=files,
    )
    session.add(row)
    session.flush()
    return row


def _mark_current(session, activity_date):
    """Test-only narrow alternative to mark_disk_snapshot_current.

    The prod helper does a bulk UPDATE on every row where current=true, which
    deadlocks under xdist when two workers run it concurrently. These read-path
    tests only need *their* row marked current; per-test SAVEPOINT rollback
    means we don't need to clear others. Use the real helper only in tests
    that are testing it (TestMarkSnapshotCurrent).
    """
    row = session.get(DiskChargeSummaryStatus, activity_date)
    if row is None:
        session.add(DiskChargeSummaryStatus(activity_date=activity_date, current=True))
    else:
        row.current = True
    session.flush()


class TestMarkSnapshotCurrent:

    def test_first_call_creates_row(self, session):
        d = next_date("disk_snap")
        mark_disk_snapshot_current(session, d)
        rows = session.query(DiskChargeSummaryStatus).filter(
            DiskChargeSummaryStatus.activity_date == d,
            DiskChargeSummaryStatus.current == True  # noqa: E712
        ).all()
        assert len(rows) == 1
        assert rows[0].activity_date == d

    def test_subsequent_call_clears_prior(self, session):
        d1 = next_date("disk_snap")
        d2 = d1 + timedelta(days=7)
        mark_disk_snapshot_current(session, d1)
        mark_disk_snapshot_current(session, d2)
        # Only the new date should be current — among rows for these
        # specific dates (other workers may have their own current rows).
        currents = session.query(DiskChargeSummaryStatus).filter(
            DiskChargeSummaryStatus.activity_date.in_((d1, d2)),
            DiskChargeSummaryStatus.current == True  # noqa: E712
        ).all()
        assert [r.activity_date for r in currents] == [d2]
        # The prior row exists but is False.
        prior = session.get(DiskChargeSummaryStatus, d1)
        assert prior is not None and prior.current is False


class TestAccountCurrentDiskUsage:

    def test_returns_latest_snapshot_not_sum(self, session):
        """Two snapshots, only the later marked current → answer is the
        later snapshot's bytes, NOT the sum."""
        user, project, account = _disk_account(session)
        d1 = next_date("disk_snap")
        d2 = d1 + timedelta(days=7)
        _seed_summary(session, account, user, activity_date=d1,
                      bytes_=1 * 1024 ** 4)
        _seed_summary(session, account, user, activity_date=d2,
                      bytes_=2 * 1024 ** 4)
        _mark_current(session, d2)

        usage = account.current_disk_usage(session)
        assert isinstance(usage, CurrentDiskUsage)
        assert usage.activity_date == d2
        assert usage.bytes == 2 * 1024 ** 4
        assert usage.used_tib == pytest.approx(2.0)

    def test_falls_back_to_max_date_when_no_status(self, session):
        """If status table has no current row, fall back to MAX(activity_date)."""
        user, project, account = _disk_account(session)
        d = next_date("disk_snap")
        _seed_summary(session, account, user, activity_date=d,
                      bytes_=3 * 1024 ** 4)
        # Note: NOT calling mark_disk_snapshot_current
        usage = account.current_disk_usage(session)
        assert usage is not None
        assert usage.activity_date == d
        assert usage.bytes == 3 * 1024 ** 4

    def test_returns_none_for_non_disk_resource(self, session):
        """Account whose resource_type is not DISK → None."""
        user = make_user(session)
        project = make_project(session, lead=user)
        rt = make_resource_type(session, resource_type=f"HPC-{next_seq('rt')}")
        resource = make_resource(session, resource_type=rt)
        account = make_account(session, project=project, resource=resource)
        # No disk_charge_summary rows exist anyway, but the type gate
        # should fire first.
        assert account.current_disk_usage(session) is None

    def test_aggregates_multiple_rows_for_same_date(self, session):
        """The 'gap row + per-user rows' case: same activity_date,
        same account_id, different user_ids — sum bytes."""
        user, project, account = _disk_account(session)
        other_user = make_user(session)
        d = next_date("disk_snap")

        _seed_summary(session, account, user, activity_date=d,
                      bytes_=1 * 1024 ** 4)
        _seed_summary(session, account, other_user, activity_date=d,
                      bytes_=4 * 1024 ** 4)
        _mark_current(session, d)

        usage = account.current_disk_usage(session)
        assert usage.bytes == 5 * 1024 ** 4
        assert usage.used_tib == pytest.approx(5.0)


class TestProjectCurrentDiskUsage:

    def test_keyed_by_resource_name(self, session):
        user, project, account = _disk_account(session)
        d = next_date("disk_snap")
        _seed_summary(session, account, user, activity_date=d,
                      bytes_=7 * 1024 ** 4)
        _mark_current(session, d)

        result = project.current_disk_usage()
        res_name = account.resource.resource_name
        assert res_name in result
        assert result[res_name]['bytes'] == 7 * 1024 ** 4
        assert result[res_name]['current_used_tib'] == pytest.approx(7.0)
        assert result[res_name]['activity_date'] == d
