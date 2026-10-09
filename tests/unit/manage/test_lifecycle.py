"""Finishing a deactivation and its fingerprint undo (``sam.manage.lifecycle``)."""

from datetime import datetime, timedelta

import pytest

from factories import make_account, make_project, make_resource, make_user
from sam.accounting.accounts import AccountUser
from sam.manage.ldapsync import SyncValidationError, sync_user
from sam.manage.lifecycle import (
    RESTORED,
    SKIPPED_ACCOUNT_GONE,
    find_deactivation_closure,
    finish_user_deactivation,
    pending_deactivations,
    restore_user_deactivation,
)
from sam.schemas.forms.ldapsync import UserSyncInput

NOW = datetime(2026, 10, 9, 14, 23, 11)
LONG_AGO = NOW - timedelta(days=400)


def _member(session, user, *, start=LONG_AGO, end=None, account=None):
    account = account or make_account(session, project=make_project(session),
                                      resource=make_resource(session))
    row = AccountUser(account_id=account.account_id, user_id=user.user_id,
                      start_date=start, end_date=end)
    session.add(row)
    session.flush()
    return row


def _pending_user(session, n_memberships=2, **user_kw):
    user = make_user(session, upid=True, active=True, deactivate=NOW - timedelta(days=2),
                     **user_kw)
    rows = [_member(session, user) for _ in range(n_memberships)]
    return user, rows


class TestPendingAndFinish:

    def test_pending_respects_the_failsafe_hours(self, session):
        old = make_user(session, active=True, deactivate=NOW - timedelta(hours=30))
        new = make_user(session, active=True, deactivate=NOW - timedelta(hours=10))
        done = make_user(session, active=False, deactivate=NOW - timedelta(hours=30))
        names = pending_deactivations(session, 24, now=NOW)
        assert old.username in names
        assert new.username not in names and done.username not in names

    def test_finish_closes_every_live_row_at_one_instant(self, session):
        user, rows = _pending_user(session)
        future = _member(session, user, start=NOW + timedelta(days=5))
        history = _member(session, user, end=NOW - timedelta(days=100))
        user.primary_gid = 12345
        session.flush()
        assert finish_user_deactivation(session, user.username, now=NOW) == 2
        assert len({r.end_date for r in rows}) == 1
        assert rows[0].end_date < NOW
        assert future.end_date is None and history.end_date == NOW - timedelta(days=100)
        assert (user.active, user.deactivate, user.primary_gid) == (False, None, 12345)

    @pytest.mark.parametrize('state', [{'active': True}, {'active': False}])
    def test_finish_needs_a_pending_user(self, session, state):
        user = make_user(session, **state)
        with pytest.raises(SyncValidationError,
                           match=f'User {user.username} is not active and marked'):
            finish_user_deactivation(session, user.username, now=NOW)


class TestUndo:

    def test_round_trip_and_idempotent(self, session):
        user, rows = _pending_user(session)
        finish_user_deactivation(session, user.username, now=NOW)
        report = restore_user_deactivation(session, user, now=NOW + timedelta(days=3))
        assert report.restored == 2
        assert all(r.end_date is None for r in rows)
        again = restore_user_deactivation(session, user, now=NOW + timedelta(days=4))
        assert again.instant is None and again.restored == 0

    def test_dry_run_writes_nothing(self, session):
        user, rows = _pending_user(session)
        finish_user_deactivation(session, user.username, now=NOW)
        stamp = rows[0].end_date
        report = restore_user_deactivation(session, user, now=NOW + timedelta(days=1),
                                           dry_run=True)
        assert [o for _, o in report.outcomes] == [RESTORED, RESTORED]
        assert rows[0].end_date == stamp

    def test_legacy_shaped_closure_is_found(self, session):
        """Legacy closed rows at exactly `now`; the same signature is restorable."""
        user = make_user(session, active=True)
        rows = [_member(session, user, end=NOW), _member(session, user, end=NOW)]
        closure = find_deactivation_closure(session, user, now=NOW + timedelta(days=1))
        assert closure.instant == NOW
        assert {r.account_user_id for r in closure.rows} == {r.account_user_id for r in rows}

    def test_removal_from_one_of_several_projects_is_not_a_closure(self, session):
        user = make_user(session, active=True)
        _member(session, user)                       # still live
        _member(session, user, end=NOW - timedelta(days=5))
        assert find_deactivation_closure(session, user, now=NOW) is None

    def test_scheduled_end_of_day_is_not_a_closure(self, session):
        user = make_user(session, active=True)
        _member(session, user, end=datetime(2026, 6, 30, 23, 59, 59))
        assert find_deactivation_closure(session, user, now=NOW) is None

    def test_re_added_since_means_no_closure(self, session):
        user, _ = _pending_user(session)
        finish_user_deactivation(session, user.username, now=NOW)
        _member(session, user, start=NOW + timedelta(days=1))
        assert find_deactivation_closure(session, user, now=NOW + timedelta(days=2)) is None

    def test_open_row_on_the_same_account_means_no_closure(self, session):
        """Re-added (or never fully closed): reopening would duplicate the membership."""
        user, rows = _pending_user(session, n_memberships=1)
        finish_user_deactivation(session, user.username, now=NOW)
        _member(session, user, start=LONG_AGO, account=rows[0].account)
        assert find_deactivation_closure(session, user, now=NOW + timedelta(days=1)) is None

    def test_deleted_account_is_skipped(self, session):
        user, rows = _pending_user(session, n_memberships=2)
        finish_user_deactivation(session, user.username, now=NOW)
        rows[0].account.deleted = True
        session.flush()
        report = restore_user_deactivation(session, user, now=NOW + timedelta(days=1))
        assert dict((r.account_user_id, o) for r, o in report.outcomes) == {
            rows[0].account_user_id: SKIPPED_ACCOUNT_GONE, rows[1].account_user_id: RESTORED}
        assert rows[0].end_date is not None


def test_window_leaves_an_old_departure_alone(session):
    user, rows = _pending_user(session)
    finish_user_deactivation(session, user.username, now=NOW)
    report = restore_user_deactivation(session, user, now=NOW + timedelta(days=200),
                                       within_days=90)
    assert report.instant is None and rows[0].end_date is not None


def test_idm_reactivation_runs_the_undo(session):
    """The PUT ldapsync/user hook: IdM brings the user back, SAM restores their access."""
    user, rows = _pending_user(session)
    finish_user_deactivation(session, user.username, now=NOW)
    later = NOW + timedelta(days=10)
    body = {'userName': user.username, 'unixUid': user.unix_uid, 'upid': user.upid,
            'active': True, 'locked': False, 'chargingExempt': False}
    sync_user(session, UserSyncInput().load(body), now=later,
              on_reactivate=lambda u: restore_user_deactivation(session, u, now=later))
    assert user.active is True
    assert all(r.end_date is None for r in rows)
