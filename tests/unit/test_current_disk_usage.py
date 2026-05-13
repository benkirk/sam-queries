"""Tests for Account.current_disk_usage / Project.current_disk_usage / mark_disk_snapshot_current."""

from datetime import date, timedelta

import pytest

from sam.accounting.accounts import Account, CurrentDiskUsage
from sam.queries.disk_usage import bulk_current_disk_usage
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
    """Test helper kept for symmetry with `mark_disk_snapshot_current`.

    Functionally equivalent to the prod helper after we corrected the
    semantics in 2026-05 (upsert single row, do not touch others). Used
    by read-path tests to seed an explicit current=True row without
    importing the prod helper directly.
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

    def test_does_not_clobber_prior_current_rows(self, session):
        """Calling mark_disk_snapshot_current(d2) must NOT flip d1 to False.

        Legacy semantics (and prod state on sam-sql.ucar.edu) keep every
        successfully imported date at current=True; the flag is per-date,
        not a single-pointer. The earlier "clear all, set one" behavior
        would mass-invalidate historical disk summaries on first prod run
        and break legacy reports that key off current=False for recalc.
        """
        d1 = next_date("disk_snap")
        d2 = d1 + timedelta(days=7)
        mark_disk_snapshot_current(session, d1)
        mark_disk_snapshot_current(session, d2)
        rows = session.query(DiskChargeSummaryStatus).filter(
            DiskChargeSummaryStatus.activity_date.in_((d1, d2))
        ).order_by(DiskChargeSummaryStatus.activity_date).all()
        assert [(r.activity_date, r.current) for r in rows] == [(d1, True), (d2, True)]

    def test_revalidates_invalidated_date(self, session):
        """A pre-existing current=False row (legacy invalidation) flips to
        current=True when re-imported."""
        d = next_date("disk_snap")
        session.add(DiskChargeSummaryStatus(activity_date=d, current=False))
        session.flush()
        mark_disk_snapshot_current(session, d)
        row = session.get(DiskChargeSummaryStatus, d)
        assert row is not None and row.current is True


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


class TestBulkCurrentDiskUsage:
    """Tests for the bulk variant that backs build_disk_subtree, the
    Project method, and Account.current_disk_usage itself."""

    def test_empty_input_returns_empty_dict(self, session):
        assert bulk_current_disk_usage(session, []) == {}

    def test_returns_per_account_snapshots_at_current_date(self, session):
        """Multiple accounts on the same current snapshot date — each
        gets its own entry keyed by account_id."""
        user_a, _, acct_a = _disk_account(session)
        user_b, _, acct_b = _disk_account(session)
        d = next_date("disk_snap")
        _seed_summary(session, acct_a, user_a, activity_date=d,
                      bytes_=1 * 1024 ** 4, ty=1.0, files=10)
        _seed_summary(session, acct_b, user_b, activity_date=d,
                      bytes_=3 * 1024 ** 4, ty=3.0, files=30)
        _mark_current(session, d)

        out = bulk_current_disk_usage(
            session, [acct_a.account_id, acct_b.account_id],
        )
        assert set(out.keys()) == {acct_a.account_id, acct_b.account_id}
        assert isinstance(out[acct_a.account_id], CurrentDiskUsage)
        assert out[acct_a.account_id].bytes == 1 * 1024 ** 4
        assert out[acct_a.account_id].terabyte_years == pytest.approx(1.0)
        assert out[acct_a.account_id].number_of_files == 10
        assert out[acct_a.account_id].activity_date == d
        assert out[acct_b.account_id].bytes == 3 * 1024 ** 4
        assert out[acct_b.account_id].terabyte_years == pytest.approx(3.0)
        assert out[acct_b.account_id].number_of_files == 30

    def test_mixed_current_and_fallback_accounts(self, session):
        """Some accounts have rows on the current snapshot date; others
        only have older rows. The bulk helper must apply the per-account
        max() fallback for the latter, in the same call."""
        user_curr, _, acct_curr = _disk_account(session)
        user_old, _, acct_old = _disk_account(session)
        d_current = next_date("disk_snap")
        d_old = d_current - timedelta(days=14)

        _seed_summary(session, acct_curr, user_curr, activity_date=d_current,
                      bytes_=2 * 1024 ** 4)
        # acct_old has NO row at d_current — only at d_old
        _seed_summary(session, acct_old, user_old, activity_date=d_old,
                      bytes_=5 * 1024 ** 4)
        _mark_current(session, d_current)

        out = bulk_current_disk_usage(
            session, [acct_curr.account_id, acct_old.account_id],
        )
        assert out[acct_curr.account_id].activity_date == d_current
        assert out[acct_curr.account_id].bytes == 2 * 1024 ** 4
        # Fallback path picked up the older row.
        assert out[acct_old.account_id].activity_date == d_old
        assert out[acct_old.account_id].bytes == 5 * 1024 ** 4

    def test_account_with_no_rows_is_absent(self, session):
        """An account with no disk_charge_summary rows is omitted from
        the result dict — matches Account.current_disk_usage's None."""
        _, _, acct_with = _disk_account(session)
        _, _, acct_without = _disk_account(session)
        user, _, _ = _disk_account(session)
        d = next_date("disk_snap")
        _seed_summary(session, acct_with, user, activity_date=d,
                      bytes_=1 * 1024 ** 4)
        _mark_current(session, d)

        out = bulk_current_disk_usage(
            session, [acct_with.account_id, acct_without.account_id],
        )
        assert acct_with.account_id in out
        assert acct_without.account_id not in out

    def test_aggregates_multiple_rows_for_same_date(self, session):
        """Same (account_id, activity_date), multiple users — bytes are
        summed (matches single-account semantics for the gap-row case)."""
        user_a, _, account = _disk_account(session)
        user_b = make_user(session)
        d = next_date("disk_snap")
        _seed_summary(session, account, user_a, activity_date=d,
                      bytes_=1 * 1024 ** 4, ty=1.0, files=5)
        _seed_summary(session, account, user_b, activity_date=d,
                      bytes_=4 * 1024 ** 4, ty=4.0, files=20)
        _mark_current(session, d)

        out = bulk_current_disk_usage(session, [account.account_id])
        usage = out[account.account_id]
        assert usage.bytes == 5 * 1024 ** 4
        assert usage.terabyte_years == pytest.approx(5.0)
        assert usage.number_of_files == 25
