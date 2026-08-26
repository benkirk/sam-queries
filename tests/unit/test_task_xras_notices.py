"""The `xras_notices` task — the policy, the guards, and the quiet hour.

The *message* is built by `sam.queries.xras_notices` and covered by
`test_xras_notices_builder.py` and (through the routes) `test_xras_notify.py`.
What matters here is everything the schedule adds: that a service absent from
the policy is never selected, that the threshold comes from the occurrence
rather than the clock, that a Friday afternoon arrival waits for Monday, and
that a quiet hour — the normal result, fifty times a week — writes nothing at
all.

**Session wiring.** The task builds its own `Notifier` with a ledger on a
*fresh* SAM session, because mail cannot be un-sent by a rollback. Under xdist
that would escape the per-test SAVEPOINT into a shared database, so the
`Notifier` constructor is patched to hand back one built on the test session
with `commit` neutered — the same harness `test_task_expiration_notices.py`
uses.
"""

import logging
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
from factories.core import make_user
from factories.projects import make_account, make_allocation, make_project
from factories.resources import make_resource
from factories.xras import make_xras_action, make_xras_activation_event

from sam.core.users import EmailAddress
from sam.notify import NotifyConfig, Notifier, NullTransport
from sam.notify.ledger import NotificationLedger
from sam.queries.xras_activation import xras_dedup_key
from scheduling.registry import TASKS, TaskContext
from scheduling.schedules import BusinessHourly, occurrence_key
from scheduling.tasks import xras_notices as mod
from scheduling.tasks.mail_guards import EmailCapExceeded, NotificationsDisabled

pytestmark = pytest.mark.unit

NAME = 'xras_notices'

#: Wednesday 2033-11-16 10:00 America/Denver == 17:00 UTC (MST, UTC-7 — the
#: fall-back is the first Sunday in November, ten days earlier).
#:
#: WARNING: **2033 on purpose**, the same reason `test_task_expiration_notices.py`
#: gives: the obfuscated snapshot every test container runs holds real
#: `xras_action_log` rows, and a present-day occurrence would put them inside
#: the 14-day lookback and drown assertions about absolute counts. Beyond 2030
#: the window contains nothing but what the test built.
OCC = datetime(2033, 11, 16, 17, 0)
#: The local instant the task measures its threshold from.
SLOT = datetime(2033, 11, 16, 10, 0)

#: Friday 2033-11-18 17:00 local, and the Monday slot that first sees it.
FRIDAY_EVENING = datetime(2033, 11, 18, 17, 0)
MONDAY_OPEN_OCC = datetime(2033, 11, 21, 15, 0)     # 08:00 America/Denver
MONDAY_OPEN_SLOT = datetime(2033, 11, 21, 8, 0)

DAY = timedelta(days=1)


# harness

@pytest.fixture
def transport():
    return NullTransport()


@pytest.fixture
def ledger(session):
    """A ledger on the test session, so its commits stay inside the SAVEPOINT."""
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
def wire(monkeypatch, transport, ledger):
    """Install a Notifier the test can inspect. Returns a configure callable."""
    def configure(*, enabled=True, **config_kwargs):
        config = NotifyConfig(enabled=enabled, **config_kwargs)
        monkeypatch.setattr(
            'sam.notify.Notifier',
            lambda **_: Notifier(config=config, transport=transport,
                                 ledger=ledger))
    configure()
    return configure


@pytest.fixture
def ctx(session):
    """A context for a punctual dispatch, with the test's SAM session preset."""
    def build(occurrence=OCC, *, dry_run=False, lateness=timedelta(minutes=7)):
        return TaskContext(now=occurrence + lateness,
                           occurrence=occurrence,
                           occurrence_key=occurrence_key(occurrence),
                           task_name=NAME,
                           dry_run=dry_run,
                           logger=logging.getLogger('test'),
                           _sam_session=session)
    return build


def _notifiable(session, *, service='supplement', action_type='Supplement',
                received, email='pi@example.edu', status='processed'):
    """A project with a lead who has an address, and one action naming it.

    `get_xras_activity` joins the action to the project through
    ``projcode_result`` OR ``request_number``, so the action must carry one of
    them — a row that names no project is invisible to the query and would make
    a test pass for the wrong reason.
    """
    lead = make_user(session)
    session.add(EmailAddress(user_id=lead.user_id, email_address=email,
                             is_primary=True, active=True))
    session.flush()
    # `User.email_addresses` is lazy='selectin', so a user already in the
    # identity map carries an eagerly-loaded empty collection.
    session.expire(lead, ['email_addresses'])

    project = make_project(session, lead=lead)
    account = make_account(session, project=project,
                           resource=make_resource(session))
    make_allocation(session, account=account, amount=1_000_000.0,
                    start_date=SLOT - timedelta(days=300),
                    end_date=SLOT + timedelta(days=300))
    session.expire(project)

    action = make_xras_action(session, status=status, action_type=action_type,
                              service=service, received_time=received,
                              request_number=project.projcode,
                              projcode_result=project.projcode)
    return project, action


def _row(**overrides):
    """One `get_xras_activity`-shaped row, defaulted to "eligible"."""
    row = {
        'action_log_id': 1,
        'action_type': 'Supplement',
        'service': 'supplement',
        'received_time': SLOT - timedelta(days=2),
        'projcode': 'ABCD0001',
        'project_id': 1,
        'kind': 'xras_supplement',
        'notifiable': True,
        'notified': False,
        'dismissed': False,
    }
    row.update(overrides)
    return row


def _select(*rows, slot=SLOT, env=None):
    return mod.select(rows, slot=slot, delays=mod.policy(env))


# registration

class TestRegistration:

    def test_importing_the_package_registers_it(self):
        import scheduling.tasks                   # noqa: F401
        assert NAME in TASKS

    def test_it_runs_hourly_through_the_business_day(self):
        schedule = TASKS[NAME].schedule
        assert isinstance(schedule, BusinessHourly)
        assert (schedule.minute, schedule.start_hour, schedule.end_hour) == (0, 8, 17)
        assert schedule.weekdays == (0, 1, 2, 3, 4)
        assert schedule.tz == 'America/Denver'

    def test_it_needs_sam_and_not_status(self):
        """The ledger is in `system_status`, but this task never touches it —
        `Notifier`'s ledger is a SAM table."""
        assert set(TASKS[NAME].needs) == {'sam'}

    def test_the_misfire_grace_is_the_default(self):
        """Unlike the other two notice tasks, which inflate it. A misfire costs
        nothing here: the window is rolling, so the next slot an hour later
        covers everything this one would have."""
        assert TASKS[NAME].misfire_grace == timedelta(hours=6)

    def test_the_lease_outlives_the_cronjob_deadline(self):
        """WARNING: The invariant that stops a killed send being restarted mid-flight.

        This task cannot heartbeat (TaskContext exposes no ledger handle), so
        its lease is fixed at max(3 x expected_runtime, 900s). Shorter than the
        pod's activeDeadlineSeconds and a still-running send becomes
        reclaimable while it is still talking to the relay.

        The two numbers live in different repositories of truth — a Python
        decorator and a Helm values file — and nothing but this test connects
        them.
        """
        import re
        from pathlib import Path

        from scheduling.ledger import lease_for

        values = (Path(__file__).resolve().parents[2]
                  / 'helm' / 'values.yaml').read_text()
        match = re.search(r'^\s*activeDeadlineSeconds:\s*(\d+)', values,
                          re.MULTILINE)
        assert match, 'activeDeadlineSeconds vanished from helm/values.yaml'

        lease = lease_for(TASKS[NAME].expected_runtime).total_seconds()
        assert lease > int(match.group(1))

    def test_it_ships_switched_off(self):
        """WARNING: `SAM_TASKS_DISABLED` is fail-OPEN: a registered task dispatches on
        the next hourly wake unless the chart names it. This one is meant to
        soak first, so the name must be in `values.yaml` from the commit that
        registers it — nothing else couples the registry to the chart.

        Delete this test in the commit that clears the switch."""
        from pathlib import Path

        values = (Path(__file__).resolve().parents[2]
                  / 'helm' / 'values.yaml').read_text()
        line, = [ln for ln in values.splitlines()
                 if ln.strip().startswith('SAM_TASKS_DISABLED:')]
        assert NAME in line, line


# the policy

class TestThePolicy:
    """`select()` is pure over `get_xras_activity` rows, so these need no DB."""

    @pytest.mark.parametrize('service', ['update', 'extend', 'supplement',
                                         'adjust'])
    def test_every_auto_service_is_selected(self, service):
        assert _select(_row(service=service))

    def test_add_is_never_selected(self):
        """A New is TWO writes — `active=True` and the notice — and the notice
        says "is now active". An unattended sender has no operator to put them
        in that order, so `add` stays manual."""
        assert _select(_row(service='add', kind='xras_activation')) == []

    def test_transfer_is_never_selected(self):
        """It has no notification kind at all, so it cannot be reached even by
        a policy mistake."""
        assert _select(_row(service='transfer', kind=None,
                            notifiable=False)) == []

    def test_an_unknown_service_is_never_selected(self):
        """Fail-closed: the policy is an allowlist, not a denylist."""
        assert _select(_row(service='something_new')) == []
        assert _select(_row(service=None)) == []

    def test_a_row_badged_New_CAN_auto_send(self):
        """WARNING: The surprising rule, named so nobody "fixes" it.

        The card's badge shows `action_type`. `dispatch.select_service` routes
        a **New** whose projcode already exists to the `update` service — it is
        a renewal in all but name and the project needs no activation — and
        `update` is in the auto set. An operator seeing "New" auto-send will
        reasonably conclude the policy leaked. It did not.
        """
        selected = _select(_row(action_type='New', service='update',
                                kind='xras_update'))
        assert len(selected) == 1
        assert selected[0]['action_type'] == 'New'

    def test_a_New_that_really_is_new_is_still_not_selected(self):
        """The other half: a New with no existing project routes to `add`."""
        assert _select(_row(action_type='New', service='add',
                            kind='xras_activation')) == []

    def test_a_row_younger_than_the_delay_waits(self):
        assert _select(_row(received_time=SLOT - timedelta(hours=23))) == []

    def test_a_row_exactly_at_the_boundary_is_selected(self):
        assert _select(_row(received_time=SLOT - DAY))

    def test_an_unnotifiable_row_is_skipped(self):
        assert _select(_row(notifiable=False)) == []

    def test_an_already_notified_row_is_skipped(self):
        """WARNING: `notified` is true when ANY recipient was reached, so a
        half-delivered action stays manual. Conservative on purpose — never
        double-mail — and it matches what the card shows the operator."""
        assert _select(_row(notified=True)) == []

    def test_a_dismissed_row_is_skipped(self):
        assert _select(_row(dismissed=True)) == []

    def test_a_row_with_no_received_time_is_skipped(self):
        assert _select(_row(received_time=None)) == []


class TestTheDelayOverride:

    def test_absent_means_the_declared_delays(self):
        assert mod.notify_after({}) is None
        assert set(mod.policy({}).values()) == {DAY}

    def test_hours_are_the_unit(self):
        assert mod.notify_after({'SAM_XRAS_NOTIFY_AFTER_HOURS': '6'}) == \
            timedelta(hours=6)

    def test_it_overrides_every_service(self):
        delays = mod.policy({'SAM_XRAS_NOTIFY_AFTER_HOURS': '2'})
        assert set(delays) == {'update', 'extend', 'supplement', 'adjust'}
        assert set(delays.values()) == {timedelta(hours=2)}

    @pytest.mark.parametrize('raw', ['', '   ', 'soon', '0', '-4'])
    def test_junk_and_zero_fall_back_rather_than_removing_the_delay(self, raw):
        """Zero would mean "mail the instant an action lands", which removes
        the whole point of the window — far more likely a typo than an intent."""
        assert mod.notify_after({'SAM_XRAS_NOTIFY_AFTER_HOURS': raw}) is None

    def test_a_shorter_delay_selects_a_younger_row(self):
        row = _row(received_time=SLOT - timedelta(hours=3))
        assert _select(row) == []
        assert _select(row, env={'SAM_XRAS_NOTIFY_AFTER_HOURS': '2'})


class TestTheSendCap:

    def test_the_default(self):
        assert mod.xras_email_max({}) == mod.DEFAULT_XRAS_MAX == 50

    def test_the_env_wins(self):
        assert mod.xras_email_max({'SAM_TASKS_XRAS_MAX': '7'}) == 7

    @pytest.mark.parametrize('raw', ['', 'lots', '0', '-1'])
    def test_junk_and_zero_fall_back(self, raw):
        """Zero would abort every run including the ones that should send
        nothing — indistinguishable from a broken query."""
        assert mod.xras_email_max({'SAM_TASKS_XRAS_MAX': raw}) == 50

    def test_it_is_not_the_shared_expiration_cap(self, monkeypatch):
        """`SAM_TASKS_EMAIL_MAX` is 2500, ~50x this task's realistic volume.
        Sharing it would make the guard useless here."""
        monkeypatch.setenv('SAM_TASKS_EMAIL_MAX', '2500')
        monkeypatch.delenv('SAM_TASKS_XRAS_MAX', raising=False)
        assert mod.xras_email_max() == 50


# the weekend

class TestTheFridayCase:
    """A Friday-afternoon arrival must not mail on Saturday, and must not be
    forgotten either. The effective delay is longer than the nominal one, and
    that is the schedule doing its job rather than a stuck queue."""

    def test_saturday_has_no_slot_to_send_from(self):
        schedule = TASKS[NAME].schedule
        # 20:00 UTC Saturday 2033-11-19 is 13:00 MST. The newest slot at or
        # before it is Friday 17:00 MST, which in UTC is already Saturday.
        occ = schedule.last_occurrence(datetime(2033, 11, 19, 20, 0))
        assert occ == datetime(2033, 11, 19, 0, 0)

    def test_the_weekend_holds_at_fridays_last_slot(self):
        schedule = TASKS[NAME].schedule
        friday_close = schedule.last_occurrence(datetime(2033, 11, 19, 1, 0))
        for probe in (datetime(2033, 11, 19, 20, 0),      # Sat
                      datetime(2033, 11, 20, 20, 0),      # Sun
                      datetime(2033, 11, 21, 14, 0)):     # Mon 07:00 MST
            assert schedule.last_occurrence(probe) == friday_close

    def test_it_is_not_eligible_at_fridays_last_slot(self):
        """Received 17:00 Friday; the 17:00 slot is the same instant, so a
        one-day delay has not elapsed."""
        friday_close = datetime(2033, 11, 18, 17, 0)
        assert _select(_row(received_time=FRIDAY_EVENING),
                       slot=friday_close) == []

    def test_it_is_not_eligible_anywhere_over_the_weekend(self):
        """There is no slot at all — but assert the predicate too, so the test
        still means something if the window is ever widened."""
        saturday_afternoon = datetime(2033, 11, 19, 17, 0)
        assert saturday_afternoon - FRIDAY_EVENING == DAY
        # Eligible by age, and yet nothing sends: no slot exists on Saturday.
        assert _select(_row(received_time=FRIDAY_EVENING),
                       slot=saturday_afternoon)
        assert TASKS[NAME].schedule._candidates_on(saturday_afternoon.date()) == []

    def test_it_goes_out_at_mondays_first_slot(self):
        selected = _select(_row(received_time=FRIDAY_EVENING),
                           slot=MONDAY_OPEN_SLOT)
        assert len(selected) == 1
        aged = MONDAY_OPEN_SLOT - FRIDAY_EVENING
        assert aged == timedelta(days=2, hours=15), (
            'the effective delay of a Friday evening arrival is ~2.6 days, '
            'and `detail` reports it so this never reads as a backlog')


# end to end

class TestASendableAction:

    def test_the_approver_note_rides_the_context(self, session, ctx, wire,
                                                 transport, monkeypatch):
        import sam.integration.xras_api as api
        seen = []

        def note(action, **_):
            seen.append(action.xras_action_log_id)
            return 'Approved with caveats.'
        monkeypatch.setattr(api, 'approver_comment_for_action', note)
        _project, action = _notifiable(session, received=SLOT - timedelta(days=2))

        result = mod.xras_notices(ctx())

        assert result.detail['sent'] == 1
        assert seen == [action.xras_action_log_id]
        message, _rendered = transport.delivered[0]
        assert message.context['approver_comment'] == 'Approved with caveats.'

    def test_an_unconfigured_xras_api_never_withholds_the_mail(self, session, ctx,
                                                               wire, transport):
        """The real resolver under the test config (``XRAS_API_KEY=''``):
        refuses, returns None, and the notice still goes."""
        _notifiable(session, received=SLOT - timedelta(days=2))

        result = mod.xras_notices(ctx())

        assert result.detail['sent'] == 1
        message, _rendered = transport.delivered[0]
        assert message.context['approver_comment'] is None

    def test_it_mails_the_lead_and_records_the_event(self, session, ctx, wire,
                                                     transport):
        project, action = _notifiable(session, received=SLOT - timedelta(days=2))

        result = mod.xras_notices(ctx())

        assert result.detail['sent'] == 1
        assert result.detail['audience'] == 1
        assert result.detail['suppressed'] == 0
        assert result.detail['by_service'] == {'supplement': 1}
        assert result.detail['actions_notified'] == [action.xras_action_log_id]
        assert result.detail['oldest_sent_age_hours'] == 48.0
        assert result.partial_failures == 0

        message, _rendered = transport.delivered[0]
        assert message.recipient.address == 'pi@example.edu'
        assert message.kind == 'xras_supplement'
        assert message.requested_by == 'task:xras_notices'
        assert message.dedup_key == xras_dedup_key(
            'xras_supplement', project.projcode, action.xras_action_log_id,
            'pi@example.edu')

    def test_the_event_names_who_was_reached_and_which_action(self, session,
                                                              ctx, wire):
        from sam.queries.xras_activation import get_xras_activation_events

        project, action = _notifiable(session, received=SLOT - timedelta(days=2))
        mod.xras_notices(ctx())

        event, = [e for e in get_xras_activation_events(
            session, project.project_id) if e['event_type'] == 'notified']
        assert event['created_by'] == 'task:xras_notices'
        assert event['action_log_id'] == action.xras_action_log_id
        assert 'pi@example.edu' in event['notified_to']

    def test_the_window_comes_from_the_occurrence_not_the_clock(self, session,
                                                               ctx, wire):
        """Dispatched for a slot an hour BEFORE the action was old enough, a
        late run must still decline it — that is what makes a reclaimed run
        agree with the punctual one it replaced."""
        _notifiable(session, received=SLOT - timedelta(hours=23))

        # The action is >24h old by the time this "runs", but not at the slot.
        result = mod.xras_notices(ctx(lateness=timedelta(hours=5)))
        assert result.detail['selected'] == 0

    def test_an_action_outside_the_lookback_is_left_to_the_operator(self, session,
                                                                    ctx, wire):
        _notifiable(session, received=SLOT - timedelta(days=20))
        result = mod.xras_notices(ctx())
        assert result.detail['actions'] == 0
        assert result.detail['window_start'] == (SLOT - mod.LOOKBACK).isoformat()

    def test_a_failed_action_is_not_notified(self, session, ctx, wire):
        """`get_xras_activity` defaults to processed actions only: a failure
        needs an operator to fix something, not a PI to be mailed."""
        _notifiable(session, received=SLOT - timedelta(days=2), status='failed')
        assert mod.xras_notices(ctx()).detail['actions'] == 0

    def test_a_dismissed_action_is_not_notified(self, session, ctx, wire):
        project, _action = _notifiable(session, received=SLOT - timedelta(days=2))
        make_xras_activation_event(session, project, 'dismissed',
                                   when=SLOT - timedelta(days=1))
        assert mod.xras_notices(ctx()).detail['selected'] == 0

    def test_a_project_with_no_addresses_on_file_is_reported_not_crashed(
            self, session, ctx, wire):
        lead = make_user(session)               # no EmailAddress
        project = make_project(session, lead=lead)
        make_xras_action(session, status='processed', action_type='Extension',
                         service='extend', received_time=SLOT - timedelta(days=2),
                         request_number=project.projcode,
                         projcode_result=project.projcode)

        result = mod.xras_notices(ctx())
        assert result.detail['actions'] == 1        # selected...
        assert result.detail['audience'] == 0       # ...and nobody to tell


class TestTheQuietHour:
    """The normal result, fifty times a week."""

    def test_nothing_due_still_reports_the_window(self, session, ctx, wire):
        detail = mod.xras_notices(ctx()).detail
        assert detail['selected'] == 0
        assert detail['suppressed'] == 0
        assert detail['sent'] == 0
        assert detail['window_start'] and detail['window_end']
        assert detail['delays_hours'] == {'adjust': 24.0, 'extend': 24.0,
                                          'supplement': 24.0, 'update': 24.0}

    def test_an_already_notified_recipient_writes_no_suppressed_row(
            self, session, ctx, wire, ledger, transport):
        """WARNING: The pre-filter is permanent, not an optimization.

        Left to `Notifier` this would be suppressed by *recording a
        `suppressed` row*. At fifty wakes a week that is a steady drip into
        `notification_log` — the table the admin Notifications card, its facet
        chips and the last-notified badge all read.
        """
        from sam.notify.models import NotificationLog

        project, action = _notifiable(session, received=SLOT - timedelta(days=2))
        # An operator pressed Notify for one of the two recipients. The
        # row-level `notified` flag catches this one; the per-address check is
        # what closes the race when only SOME addresses were reached.
        NotificationLog.create(
            session, kind='xras_supplement', channel='email', transport='null',
            status='sent', recipient='pi@example.edu', subject='s',
            projcode=project.projcode,
            dedup_key=xras_dedup_key('xras_supplement', project.projcode,
                                     action.xras_action_log_id,
                                     'pi@example.edu'),
            requested_by='benkirk')
        session.flush()
        before = session.query(NotificationLog).count()

        result = mod.xras_notices(ctx())

        assert result.detail['sent'] == 0
        assert transport.delivered == []
        assert session.query(NotificationLog).count() == before, (
            'a quiet hour must write no notification_log rows at all')


class TestTheGuards:

    def test_mail_disabled_raises_rather_than_reporting_success(self, session,
                                                                ctx, wire,
                                                                transport):
        """Without this the run would record every message `suppressed`, report
        `succeeded`, go green, and mail nobody. The CronJob does not inherit
        `webapp.env`, so this is a live failure mode."""
        _notifiable(session, received=SLOT - timedelta(days=2))
        wire(enabled=False)

        with pytest.raises(NotificationsDisabled):
            mod.xras_notices(ctx())
        assert transport.delivered == []

    def test_the_cap_aborts_before_any_transport(self, session, ctx, wire,
                                                 transport, monkeypatch):
        for _ in range(2):
            _notifiable(session, received=SLOT - timedelta(days=2))
        monkeypatch.setenv('SAM_TASKS_XRAS_MAX', '1')

        with pytest.raises(EmailCapExceeded) as exc:
            mod.xras_notices(ctx())

        assert exc.value.task_detail == {'audience': 2, 'cap': 1,
                                         'aborted_before_sending': True}
        assert transport.delivered == [], 'nothing may go out past the cap'


class TestDryRun:

    def test_it_previews_and_sends_nothing(self, session, ctx, wire, transport):
        """WARNING: A rollback undoes rows; it does not unsend mail. This is the case
        `TaskContext.dry_run`'s note warns about."""
        from sam.notify.models import NotificationLog

        _notifiable(session, received=SLOT - timedelta(days=2))
        before = session.query(NotificationLog).count()

        result = mod.xras_notices(ctx(dry_run=True))

        assert result.detail['dry_run'] is True
        assert result.detail['audience'] == 1
        assert result.detail['sent'] == 0
        assert transport.delivered == []
        assert session.query(NotificationLog).count() == before, (
            'preview() must write no ledger row — a stray one would poison the '
            'dedup query for the real send')

    def test_it_writes_no_activation_event(self, session, ctx, wire):
        from sam.queries.xras_activation import get_xras_activation_events

        project, _action = _notifiable(session, received=SLOT - timedelta(days=2))
        mod.xras_notices(ctx(dry_run=True))
        assert get_xras_activation_events(session, project.project_id) == []
