"""The `account_queue_digest` task — the guards, the stamp, and the quiet week.

The message is built by `sam.queries.account_notices.build_queue_summary`
and covered in `test_account_requests_builders.py`. What matters here is
what the schedule adds: reconcile-before-select, the recipient/enabled/cap
guards firing before any transport, the dry run writing no ledger row, and
`requested_at` stamped only after a delivered send.

Session wiring is the `test_task_expiration_notices.py` trick: the task's
fresh ledger session is the test session with `commit` neutered, so the
ledger rows stay inside the per-test SAVEPOINT.
"""

import logging
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from _paths import REPO_ROOT

import pytest
from factories import (
    make_account_request,
    make_email_address,
    make_notification_log,
    make_user,
)

from sam.notify import NotifyConfig, Notifier, NullTransport
from sam.notify.ledger import NotificationLedger
from sam.notify.models import NotificationLog
from scheduling.ledger import lease_for
from scheduling.registry import TASKS, TaskContext
from scheduling.schedules import Weekly, occurrence_key
from scheduling.tasks import account_queue_digest as mod
from scheduling.tasks.mail_guards import EmailCapExceeded, NotificationsDisabled

pytestmark = pytest.mark.unit

NAME = 'account_queue_digest'
#: Monday 2026-09-14 08:00 America/Denver == 14:00 UTC.
OCC = datetime(2026, 9, 14, 14, 0)
LOCAL = datetime(2026, 9, 14, 8, 0)
HELM = REPO_ROOT / 'helm'


@pytest.fixture
def transport():
    return NullTransport()


@pytest.fixture
def ledger(session):
    @contextmanager
    def factory():
        real_commit = session.commit
        session.commit = session.flush
        try:
            yield session
        finally:
            session.commit = real_commit
    return NotificationLedger(factory, config=NotifyConfig())


@pytest.fixture
def wire(monkeypatch, session, transport, ledger):
    """Install an inspectable Notifier and a digest recipient."""
    def configure(*, enabled=True, recipient='nusd@example.edu', **config_kwargs):
        config = NotifyConfig(enabled=enabled, **config_kwargs)
        monkeypatch.setattr(
            'sam.notify.Notifier',
            lambda **_: Notifier(config=config, transport=transport, ledger=ledger))
        if recipient is None:
            monkeypatch.delenv('NOTIFY_ACCOUNT_QUEUE_TO', raising=False)
        else:
            monkeypatch.setenv('NOTIFY_ACCOUNT_QUEUE_TO', recipient)
    configure()
    return configure


@pytest.fixture
def ctx(session):
    def build(occurrence=OCC, *, dry_run=False):
        return TaskContext(now=occurrence + timedelta(minutes=5),
                           occurrence=occurrence,
                           occurrence_key=occurrence_key(occurrence),
                           task_name=NAME, dry_run=dry_run,
                           logger=logging.getLogger('test'),
                           _sam_session=session)
    return build


def _digest_rows(session):
    return (session.query(NotificationLog)
            .filter(NotificationLog.kind == 'account_queue_summary').all())


class TestRegistration:

    def test_importing_the_package_registers_it(self):
        import scheduling.tasks                   # noqa: F401
        assert NAME in TASKS and TASKS[NAME].fn is mod.account_queue_digest

    def test_it_runs_monday_morning_mountain_and_needs_sam_only(self):
        assert TASKS[NAME].schedule == Weekly(0, 8, 0, tz='America/Denver')
        assert TASKS[NAME].needs == ('sam',)

    def test_the_lease_outlives_the_cronjob_deadline(self):
        import re
        values = (HELM / 'values.yaml').read_text()
        match = re.search(r'^\s*activeDeadlineSeconds:\s*(\d+)', values, re.MULTILINE)
        assert match
        assert lease_for(TASKS[NAME].expected_runtime).total_seconds() > int(match.group(1))

    @pytest.mark.parametrize('values', ['values.yaml', 'values-dev.yaml'])
    def test_it_ships_switched_off(self, values):
        """WARNING: `SAM_TASKS_DISABLED` is fail-OPEN. This one mails an external
        team, so its name is in the chart from the commit that registers it.
        Delete this test in the commit that clears the switch."""
        text = (HELM / values).read_text()
        line, = [ln for ln in text.splitlines()
                 if ln.strip().startswith('SAM_TASKS_DISABLED:')]
        assert NAME in line, line


class TestTheKnobs:

    @pytest.mark.parametrize('raw,expected', [
        (None, 500), ('', 500), ('x', 500), ('0', 500), ('40', 40)])
    def test_the_cap_reader(self, raw, expected):
        env = {} if raw is None else {'SAM_TASKS_ACCOUNT_MAX': raw}
        assert mod.account_max(env) == expected

    def test_the_recipient_and_url_readers(self):
        assert mod.digest_recipient({}) == ''
        assert mod.digest_recipient({'NOTIFY_ACCOUNT_QUEUE_TO': ' a@b.edu '}) == 'a@b.edu'
        assert mod.queue_url({}) == ''
        assert mod.queue_url({'NOTIFY_ACCOUNT_QUEUE_URL': ' https://sam.x/q '}) == \
            'https://sam.x/q'


class TestTheGuards:

    def test_no_recipient_means_inert_and_says_so(self, ctx, wire, session):
        wire(recipient=None)
        make_account_request(session)
        result = mod.account_queue_digest(ctx())
        assert result.detail['reason'] == 'NOTIFY_ACCOUNT_QUEUE_TO is unset'
        assert result.detail['sent'] == 0 and _digest_rows(session) == []

    def test_mail_disabled_raises_before_any_transport(self, ctx, wire, session):
        wire(enabled=False)
        make_account_request(session)
        with pytest.raises(NotificationsDisabled):
            mod.account_queue_digest(ctx())
        assert _digest_rows(session) == []

    def test_the_cap_trips_before_sending(self, ctx, wire, session, monkeypatch):
        monkeypatch.setenv('SAM_TASKS_ACCOUNT_MAX', '1')
        make_account_request(session)
        make_account_request(session)
        with pytest.raises(EmailCapExceeded) as excinfo:
            mod.account_queue_digest(ctx())
        assert excinfo.value.task_detail['audience'] == 2
        assert _digest_rows(session) == []

    def test_an_empty_queue_sends_nothing_and_says_so(self, ctx, wire, session):
        before = len(_digest_rows(session))
        result = mod.account_queue_digest(ctx())
        assert result.detail['reason'] == 'queue empty'
        assert len(_digest_rows(session)) == before


class TestTheSend:

    def test_it_sends_one_digest_and_stamps_every_row(self, ctx, wire, session):
        a = make_account_request(session)
        b = make_account_request(session).claim('op')
        result = mod.account_queue_digest(ctx())
        assert result.detail['sent'] == 1 and result.state == 'succeeded'
        assert result.detail['selected'] == 2 and result.detail['new'] == 2
        assert a.requested_at == LOCAL and b.requested_at == LOCAL
        assert b.state == 'claimed', 'the stamp is orthogonal to the state'
        row, = _digest_rows(session)
        assert row.dedup_key == 'account_queue_summary:2026-09-14:nusd@example.edu'
        assert row.requested_by == 'task:account_queue_digest'

    def test_a_row_whose_account_now_exists_is_reconciled_out_first(
            self, ctx, wire, session):
        gone = make_account_request(session, email='gone@example.edu')
        make_email_address(session, make_user(session), email='gone@example.edu')
        make_account_request(session)
        result = mod.account_queue_digest(ctx())
        assert result.detail['reconciled']['fulfilled'] == 1
        assert result.detail['selected'] == 1
        assert gone.requested_at is None, 'never sent to NUSD'

    def test_next_weeks_digest_does_not_move_requested_at(self, ctx, wire, session):
        row = make_account_request(session)
        mod.account_queue_digest(ctx())
        result = mod.account_queue_digest(ctx(OCC + timedelta(days=7)))
        assert result.detail['sent'] == 1 and result.detail['new'] == 0
        assert row.requested_at == LOCAL, 'first told; later digests are in the ledger'

    def test_a_second_run_on_the_same_day_is_suppressed_not_resent(
            self, ctx, wire, session):
        row = make_account_request(session)
        mod.account_queue_digest(ctx())
        row.requested_at = None
        session.flush()
        result = mod.account_queue_digest(ctx(OCC + timedelta(hours=2)))
        assert result.detail['status'] == 'suppressed'
        assert result.state == 'succeeded'
        assert row.requested_at is None, 'a suppressed send stamps nothing'
        assert len(_digest_rows(session)) == 2, 'sent, then suppressed'

    def test_a_dry_run_previews_and_writes_no_row(self, ctx, wire, session):
        row = make_account_request(session)
        result = mod.account_queue_digest(ctx(dry_run=True))
        assert result.detail['dry_run'] is True and result.detail['sent'] == 0
        assert row.requested_at is None
        assert _digest_rows(session) == []
