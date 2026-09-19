"""The `account_requests_reconcile` task — wiring around the manage function.

The reconcile logic itself is covered in `test_account_requests_manage.py`;
what the schedule adds is registration, the ships-disabled switch, the
Mountain clock, the purge knob, and that the ledger detail carries every count.
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from _paths import REPO_ROOT

import pytest
from factories import make_account_request, make_email_address, make_user

from sam.core.account_requests import CREATED_BY_SELF, AccountRequest
from scheduling.registry import TASKS, TaskContext
from scheduling.schedules import Hourly, occurrence_key
from scheduling.tasks import account_requests_reconcile as mod

pytestmark = pytest.mark.unit

NAME = 'account_requests_reconcile'
#: 2026-09-16 15:20 UTC == 09:20 Mountain (MDT).
OCC = datetime(2026, 9, 16, 15, 20)
VALUES = REPO_ROOT / 'helm'


@pytest.fixture
def ctx(session):
    def build(occurrence=OCC, *, dry_run=False):
        return TaskContext(now=occurrence + timedelta(minutes=3),
                           occurrence=occurrence,
                           occurrence_key=occurrence_key(occurrence),
                           task_name=NAME, dry_run=dry_run,
                           logger=logging.getLogger('test'),
                           _sam_session=session)
    return build


class TestRegistration:

    def test_importing_the_package_registers_it(self):
        import scheduling.tasks                   # noqa: F401
        assert NAME in TASKS
        assert TASKS[NAME].fn is mod.account_requests_reconcile

    def test_it_runs_hourly_at_twenty_past_and_needs_sam_only(self):
        assert TASKS[NAME].schedule == Hourly(minute=20)
        assert TASKS[NAME].needs == ('sam',)

    @pytest.mark.parametrize('values', ['values.yaml', 'values-dev.yaml'])
    def test_it_ships_switched_off(self, values):
        """WARNING: `SAM_TASKS_DISABLED` is fail-OPEN: a registered task dispatches
        on the next hourly wake unless the chart names it. This one writes
        memberships, so it soaks first. Delete this test in the commit that
        clears the switch."""
        text = (VALUES / values).read_text()
        line, = [ln for ln in text.splitlines()
                 if ln.strip().startswith('SAM_TASKS_DISABLED:')]
        assert NAME in line, line


class TestTheKnob:

    @pytest.mark.parametrize('raw,expected', [
        (None, 7), ('', 7), ('abc', 7), ('0', 7), ('-3', 7), ('14', 14)])
    def test_the_purge_days_reader(self, raw, expected):
        env = {} if raw is None else {'SAM_TASKS_ACCOUNT_PURGE_DAYS': raw}
        assert mod.purge_days(env) == expected


class TestTheRun:

    def test_it_fulfills_and_reports_every_count(self, ctx, session):
        row = make_account_request(session, email='here@example.edu')
        user = make_user(session)
        make_email_address(session, user, email='here@example.edu')
        result = mod.account_requests_reconcile(ctx())
        assert row.user_id == user.user_id
        assert result.detail['fulfilled'] == 1
        for key in ('checked', 'fulfilled', 'enrolled', 'enroll_failed',
                    'ambiguous', 'purged', 'purge_days', 'clock'):
            assert key in result.detail, key
        assert result.state == 'succeeded'

    def test_the_stamp_is_the_slot_in_mountain_time(self, ctx, session):
        row = make_account_request(session, email='when@example.edu')
        make_email_address(session, make_user(session), email='when@example.edu')
        result = mod.account_requests_reconcile(ctx())
        assert row.fulfilled_at == datetime(2026, 9, 16, 9, 20)
        assert result.detail['clock'] == '2026-09-16T09:20:00'

    def test_it_purges_by_the_knob(self, ctx, session, monkeypatch):
        monkeypatch.setenv('SAM_TASKS_ACCOUNT_PURGE_DAYS', '3')
        stale = make_account_request(session, by=CREATED_BY_SELF, verified_by=None,
                                     when=datetime(2026, 9, 10))
        fresh = make_account_request(session, by=CREATED_BY_SELF, verified_by=None,
                                     when=datetime(2026, 9, 15))
        result = mod.account_requests_reconcile(ctx())
        assert result.detail['purged'] == 1 and result.detail['purge_days'] == 3
        ids = {r.account_request_id for r in session.query(AccountRequest).all()}
        assert stale.account_request_id not in ids
        assert fresh.account_request_id in ids

    def test_an_enrollment_failure_is_not_a_red_job(self, ctx, session):
        from factories import make_project
        row = make_account_request(session, purpose='enrollment',
                                   project=make_project(session),  # no accounts
                                   email='fail@example.edu')
        make_email_address(session, make_user(session), email='fail@example.edu')
        result = mod.account_requests_reconcile(ctx())
        assert result.detail['enroll_failed'] == 1
        assert result.state == 'succeeded'
        assert row.fulfill_error
