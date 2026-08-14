"""The `expiration_notices` task — window math, the guards, and the quiet week.

The *message* is built by `sam.queries.expiration_notices` and covered by
`test_expiration_message_builder.py` and `test_expiration_notices.py`. What
matters here is everything the schedule adds: that the window comes from the
occurrence rather than the clock, that the bands tile, that the cap and the
mail-disabled guard fire before any transport is touched, and — the one that
protects `notification_log` from ~26,000 rows a year — that a quiet week
writes nothing at all.

**Session wiring.** The task builds its own `Notifier` with a ledger on a
*fresh* SAM session, because mail cannot be un-sent by a rollback. Under
xdist that would escape the per-test SAVEPOINT into a shared database, so
`_new_sam_session` is patched to hand back the test session with `commit`
neutered — the same trick `test_notify_ledger.py` uses, and the reason that
function exists as a named module-level seam rather than an inline lambda.
"""

from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
from factories.core import make_user
from factories.notify import make_notification_log
from factories.projects import make_account, make_allocation, make_project
from factories.resources import make_resource

from sam.core.users import EmailAddress
from sam.notify import NotifyConfig, Notifier, NullTransport
from sam.notify.ledger import NotificationLedger
from sam.queries.expiration_notices import Milestone, legacy_dedup_key
from scheduling.registry import TASKS, TaskContext
from scheduling.schedules import Weekly, occurrence_key
from scheduling.tasks import expiration_notices as mod

pytestmark = pytest.mark.unit

NAME = 'expiration_notices'

#: Monday 2033-11-21 09:00 America/Denver == 16:00 UTC.
#:
#: ⚠️ **2033 on purpose.** These tests assert absolute counts, and the
#: obfuscated snapshot every test container runs holds ~22,000 real
#: allocations — the latest ending 2030-12-31. An occurrence in 2026 selects
#: ~800 of them, which drowns the fixtures and makes the module take two
#: minutes. Beyond 2030 the band contains nothing but what the test built.
OCC = datetime(2033, 11, 21, 16, 0)
#: The local midnight the task measures its bands from.
START = datetime(2033, 11, 21, 0, 0)


# ── harness ──────────────────────────────────────────────────────────────────

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
def wire(monkeypatch, session, transport, ledger):
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
    def build(occurrence=OCC, *, dry_run=False, lateness=timedelta(minutes=5)):
        import logging
        return TaskContext(now=occurrence + lateness,
                           occurrence=occurrence,
                           # Derived, not a literal: the summary's dedup key
                           # is built from it, so a constant would make two
                           # different slots look like one re-run.
                           occurrence_key=occurrence_key(occurrence),
                           task_name=NAME,
                           dry_run=dry_run,
                           logger=logging.getLogger('test'),
                           _sam_session=session)
    return build


def _expiring(session, *, days, facility='UNIV', email='pi@example.edu'):
    """A project whose single allocation expires `days` from START."""
    lead = make_user(session)
    session.add(EmailAddress(user_id=lead.user_id, email_address=email,
                             is_primary=True, active=True))
    session.flush()
    session.refresh(lead)

    project = make_project(session, lead=lead, facility_name=facility)
    account = make_account(session, project=project,
                           resource=make_resource(session))
    make_allocation(session, account=account, amount=1_000_000.0,
                    start_date=START - timedelta(days=300),
                    end_date=START + timedelta(days=days))
    session.expire(project)
    return project


# ── registration ─────────────────────────────────────────────────────────────

class TestRegistration:

    def test_importing_the_package_registers_it(self):
        import scheduling.tasks                   # noqa: F401
        assert NAME in TASKS

    def test_it_runs_monday_morning_mountain(self):
        schedule = TASKS[NAME].schedule
        assert isinstance(schedule, Weekly)
        assert (schedule.weekday, schedule.hour) == (0, 9)
        assert schedule.tz == 'America/Denver'

    def test_it_is_the_first_task_that_needs_sam(self):
        assert set(TASKS[NAME].needs) == {'sam', 'status'}

    def test_the_misfire_grace_is_generous(self):
        """A late run is byte-identical because the window comes from the
        occurrence, so refusing one buys nothing and writes a `skipped` row
        that looks like a problem."""
        assert TASKS[NAME].misfire_grace == timedelta(hours=24)

    def test_the_lease_outlives_the_cronjob_deadline(self):
        """⚠️ THE invariant that stops every PI getting a second copy.

        This task cannot heartbeat (TaskContext exposes no ledger handle), so
        its lease is fixed at max(3 x expected_runtime, 900s). If that is
        shorter than the pod's activeDeadlineSeconds, a still-running send
        becomes reclaimable and the next hourly dispatch starts a second one
        while the first is still talking to the relay.

        The two numbers live in different repositories of truth — a Python
        decorator and a Helm values file — and nothing but this test connects
        them."""
        import re
        from pathlib import Path

        from scheduling.ledger import lease_for

        values = (Path(__file__).resolve().parents[2]
                  / 'helm' / 'values.yaml').read_text()
        match = re.search(r'^\s*activeDeadlineSeconds:\s*(\d+)', values,
                          re.MULTILINE)
        assert match, 'activeDeadlineSeconds vanished from helm/values.yaml'
        deadline = int(match.group(1))

        lease = lease_for(TASKS[NAME].expected_runtime).total_seconds()
        assert lease > deadline, (
            f'lease {lease}s must exceed activeDeadlineSeconds {deadline}s, '
            f'or a killed send is reclaimed while it is still sending')


# ── the window ───────────────────────────────────────────────────────────────

class TestTheWindowComesFromTheOccurrence:

    def test_utc_is_converted_to_the_schedule_zone(self):
        """`ctx.occurrence` is naive UTC; `Allocation.end_date` is naive
        Mountain. Comparing them raw shifts every band by 6-7 hours."""
        assert mod.window_start(OCC) == START

    def test_it_truncates_to_local_midnight(self):
        assert mod.window_start(OCC).time() == datetime.min.time()

    @pytest.mark.parametrize('lateness', [timedelta(minutes=5),
                                          timedelta(hours=20),
                                          timedelta(hours=23, minutes=59)])
    def test_lateness_cannot_move_the_window_or_the_cohort(
            self, session, wire, ctx, transport, lateness):
        """The property that makes a reclaimed run safe to re-execute. The
        input is the OCCURRENCE, so however late the dispatch, the window —
        and therefore who gets mail — is identical.

        The allocation sits one day inside the upper edge, where a window
        that tracked the clock instead would drop it."""
        _expiring(session, days=39)
        late = mod.expiration_notices(ctx(OCC, lateness=lateness)).detail

        assert late['window_start'] == START.isoformat()
        assert late['window_end'] == (START + timedelta(days=40)).isoformat()
        assert late['sent'] == 1

    def test_a_dst_boundary_still_lands_on_local_midnight(self):
        """2026-11-01 is fall-back in America/Denver."""
        occ = datetime(2026, 11, 2, 16, 0)        # Monday after the fold
        assert mod.window_start(occ) == datetime(2026, 11, 2, 0, 0)


class TestBandsAreHalfOpen:
    """⚠️ `get_all_expiring_allocations` filters `end_date <= end_date`.

    Passing `start + hi_days` straight through would make adjacent bands
    overlap on their shared boundary — invisible with one rung, a double-send
    to whoever lands on the seam with three.
    """

    def test_the_upper_bound_excludes_the_boundary_instant(self):
        _lower, upper = mod.band_bounds(START, Milestone('r', 0, 40))
        assert upper < START + timedelta(days=40)
        assert upper == START + timedelta(days=40) - timedelta(microseconds=1)

    def test_the_lower_bound_includes_its_boundary(self):
        lower, _upper = mod.band_bounds(START, Milestone('r', 7, 14))
        assert lower == START + timedelta(days=7)

    def test_adjacent_bands_do_not_overlap(self):
        low = mod.band_bounds(START, Milestone('a', 7, 14))
        high = mod.band_bounds(START, Milestone('b', 14, 21))
        assert low[1] < high[0]

    def test_the_bands_leave_no_gap_either(self):
        """A gap is the failure this whole design is about: an expiration
        that falls between two rungs is never notified at all."""
        low = mod.band_bounds(START, Milestone('a', 7, 14))
        high = mod.band_bounds(START, Milestone('b', 14, 21))
        assert high[0] - low[1] == timedelta(microseconds=1)


# ── the cap ──────────────────────────────────────────────────────────────────

class TestTheSendCap:

    def test_the_default_is_well_above_the_measured_peak(self):
        """~535 is the measured peak; 250 (as § 12 proposed) is BELOW normal
        volume and would fail every loaded run."""
        assert mod.email_max({}) == 2500
        assert mod.DEFAULT_EMAIL_MAX > 535 * 4

    @pytest.mark.parametrize('raw,expected', [
        ('100', 100), ('  100  ', 100), ('', 2500), ('nonsense', 2500),
        ('0', 2500), ('-1', 2500),
    ])
    def test_the_env_override_refuses_nonsense(self, raw, expected):
        """A zero or negative cap would abort every run, including the ones
        that should send nothing — indistinguishable from a broken query."""
        assert mod.email_max({'SAM_TASKS_EMAIL_MAX': raw}) == expected

    def test_exceeding_it_raises_before_anything_is_sent(self, monkeypatch,
                                                         session, wire, ctx,
                                                         transport):
        monkeypatch.setenv('SAM_TASKS_EMAIL_MAX', '1')
        _expiring(session, days=35)
        _expiring(session, days=35, email='pi2@example.edu')

        with pytest.raises(mod.EmailCapExceeded):
            mod.expiration_notices(ctx())

        assert transport.delivered == [], 'the cap must fire before the relay'
        assert transport.open_count == 0

    def test_the_cap_reports_structured_detail(self, monkeypatch, session,
                                               wire, ctx):
        """`TaskResult` has no failed state, so this rides on the exception
        and `runner._execute` merges it into the ledger row."""
        monkeypatch.setenv('SAM_TASKS_EMAIL_MAX', '1')
        _expiring(session, days=35)
        _expiring(session, days=35, email='pi2@example.edu')

        with pytest.raises(mod.EmailCapExceeded) as caught:
            mod.expiration_notices(ctx())

        assert caught.value.task_detail == {'audience': 2, 'cap': 1,
                                            'aborted_before_sending': True}

    def test_it_is_not_reported_as_partial(self):
        """`partial` means "some sent"; here the count is zero and an
        operator reading it would go looking for the ones that got through."""
        assert not hasattr(mod.EmailCapExceeded('x', audience=1, cap=0),
                           'partial_failures')


# ── the mail-disabled guard ──────────────────────────────────────────────────

class TestItRefusesToRunMailDisabled:

    def test_disabled_raises_rather_than_reporting_success(self, session,
                                                           wire, ctx):
        """Without this the run records ~600 `suppressed` rows, reports
        `succeeded`, exits 0, and nobody learns the mail stopped. The CronJob
        does not inherit webapp.env, so this is live, not hypothetical."""
        wire(enabled=False)
        _expiring(session, days=35)

        with pytest.raises(mod.NotificationsDisabled, match='NOTIFY_ENABLED'):
            mod.expiration_notices(ctx())

    def test_it_writes_no_ledger_rows(self, session, wire, ctx):
        from sam import NotificationLog
        wire(enabled=False)
        _expiring(session, days=35)
        before = session.query(NotificationLog).count()

        with pytest.raises(mod.NotificationsDisabled):
            mod.expiration_notices(ctx())

        assert session.query(NotificationLog).count() == before


# ── selection ────────────────────────────────────────────────────────────────

class TestSelection:

    def test_an_expiration_inside_the_band_is_notified(self, session, wire,
                                                       ctx, transport):
        _expiring(session, days=35)
        result = mod.expiration_notices(ctx())
        assert result.detail['sent'] == 1
        assert len(transport.delivered) == 1

    @pytest.mark.parametrize('days', [-1, 41, 400])
    def test_an_expiration_outside_the_band_is_not(self, session, wire, ctx,
                                                   transport, days):
        _expiring(session, days=days)
        result = mod.expiration_notices(ctx())
        assert result.detail['audience'] == 0
        assert transport.delivered == []

    def test_facilities_are_explicit_not_inherited(self, session, wire, ctx,
                                                   transport):
        """§ 12 item 3. A CLI default is a presentation choice someone may
        reasonably change; the task must not silently change audience."""
        assert mod.FACILITIES == ('UNIV', 'WNA')
        _expiring(session, days=35, facility='UNIV', email='univ@example.edu')
        _expiring(session, days=35, facility='WNA', email='wna@example.edu')
        _expiring(session, days=35, facility='NCAR', email='ncar@example.edu')

        mod.expiration_notices(ctx())
        addressed = {m.recipient.address for m, _ in transport.delivered}
        assert addressed == {'univ@example.edu', 'wna@example.edu'}

    def test_the_detail_always_carries_the_window_and_the_counts(self, session,
                                                                 wire, ctx):
        """⚠️ ~40 runs a year legitimately send nothing, so "0 sent,
        succeeded" is the NORMAL result and cannot be distinguished from a
        query that silently stopped matching — unless the run says how many
        it selected and how many it suppressed."""
        _expiring(session, days=35)
        detail = mod.expiration_notices(ctx()).detail
        assert detail['window_start'] == START.isoformat()
        assert detail['window_end'] == (START + timedelta(days=40)).isoformat()
        assert set(detail) >= {'selected', 'suppressed', 'audience',
                               'projects', 'sent', 'failed', 'milestones'}

    def test_days_remaining_is_measured_from_the_slot(self, session, wire, ctx,
                                                      transport):
        """Not the wall clock. Dispatching the SAME occurrence 20 hours late
        must still render 35 — `get_all_expiring_allocations(now=start)` is
        what makes that true, and without the kwarg it would say 34."""
        _expiring(session, days=35)
        mod.expiration_notices(ctx(OCC, lateness=timedelta(hours=20)))
        message, _rendered = transport.delivered[0]
        assert message.context['resources'][0]['days_remaining'] == 35

    def test_a_different_occurrence_does_shift_the_window(self, session, wire,
                                                          ctx, transport):
        """The other half of the same rule, and the reason the test above is
        about lateness rather than the occurrence: a genuinely different slot
        SHOULD select a different band. Only the dispatch instant is inert."""
        _expiring(session, days=35)
        mod.expiration_notices(ctx(OCC + timedelta(days=1)))
        message, _rendered = transport.delivered[0]
        assert message.context['resources'][0]['days_remaining'] == 34


# ── the cadence properties ───────────────────────────────────────────────────

class TestTheQuietWeek:
    """THE regression gate on the pre-filter.

    ~40 of 52 runs a year select a cohort that is entirely already-notified.
    Left to `Notifier`'s own dedup each of those would write a `suppressed`
    ledger row — on the order of 26,000 a year, into the same table the admin
    Notifications card, its facet chips and the last-notified badge all read.
    """

    def test_a_second_run_sends_nothing_and_writes_nothing(self, session, wire,
                                                           ctx, transport):
        from sam import NotificationLog
        _expiring(session, days=35)

        first = mod.expiration_notices(ctx())
        assert first.detail['sent'] == 1
        rows_after_first = session.query(NotificationLog).count()

        second = mod.expiration_notices(ctx(OCC + timedelta(days=7)))

        assert second.detail['audience'] == 0
        assert second.detail['suppressed'] == 1
        assert second.detail['sent'] == 0
        assert len(transport.delivered) == 1, 'nothing new went out'
        assert session.query(NotificationLog).count() == rows_after_first, \
            'a suppressed row per quiet week is ~26,000 rows a year'

    def test_the_count_is_still_reported(self, session, wire, ctx):
        """Nothing is lost by dropping them — only the rows."""
        _expiring(session, days=35)
        mod.expiration_notices(ctx())
        second = mod.expiration_notices(ctx(OCC + timedelta(days=7)))
        assert second.detail['selected'] == 1
        assert second.detail['suppressed'] == 1


class TestSelfHealing:

    def test_a_skipped_week_is_recovered_by_the_next(self, session, wire, ctx,
                                                     transport):
        """A 40-day band with 7-day runs selects each expiration on 5-6
        consecutive runs, so one lost week costs nothing. This is what
        retires the "monthly means one shot" risk."""
        _expiring(session, days=35)

        # The run that would have caught it never happens...
        # ...and the next Monday still sends.
        result = mod.expiration_notices(ctx(OCC + timedelta(days=7)))
        assert result.detail['sent'] == 1
        assert len(transport.delivered) == 1


class TestTheLadderTiles:

    def test_three_rungs_produce_disjoint_audiences(self, monkeypatch, session,
                                                    wire, ctx, transport):
        """The property that makes enabling the ladder a one-tuple edit: no
        project falls in two bands, and each rung mints its own key."""
        monkeypatch.setattr(mod, 'MILESTONES', None, raising=False)
        rungs = (Milestone('60d', 56, 63), Milestone('30d', 28, 35),
                 Milestone('7d', 7, 14))
        monkeypatch.setattr('sam.queries.expiration_notices.MILESTONES', rungs)

        _expiring(session, days=60, email='a@example.edu')
        _expiring(session, days=30, email='b@example.edu')
        _expiring(session, days=10, email='c@example.edu')

        mod.expiration_notices(ctx())

        keys = [m.dedup_key for m, _ in transport.delivered]
        assert len(keys) == len(set(keys)), 'a project landed in two bands'
        labels = sorted(k.split(':')[3] for k in keys)
        assert labels == ['30d', '60d', '7d']


class TestTheLegacyKeyBridge:

    def test_a_pre_rung_label_key_still_suppresses(self, session, wire, ctx,
                                                   transport):
        """Every manual CLI run before the label existed wrote the old
        format. Without this bridge the first scheduled run re-notifies the
        whole overlap cohort."""
        project = _expiring(session, days=35)
        expires = (START + timedelta(days=35)).strftime('%Y-%m-%d')
        make_notification_log(
            session, kind='expiration', status='sent',
            projcode=project.projcode,
            dedup_key=legacy_dedup_key(project.projcode, expires,
                                       'pi@example.edu'))

        result = mod.expiration_notices(ctx())

        assert result.detail['suppressed'] == 1
        assert transport.delivered == []

    def test_a_notifier_without_a_ledger_drops_nothing(self, session, ctx,
                                                       monkeypatch):
        """`Notifier(ledger=None)` is a documented configuration ("record
        nothing"). It cannot answer the suppression question, so the
        pre-filter must fall through rather than crash — and
        `_pre_transport_guard` will not suppress either, so the two layers
        agree about what no-ledger means."""
        transport = NullTransport()
        monkeypatch.setattr('sam.notify.Notifier', lambda **_: Notifier(
            config=NotifyConfig(enabled=True), transport=transport,
            ledger=None))
        _expiring(session, days=35)

        result = mod.expiration_notices(ctx())
        assert result.detail['suppressed'] == 0
        assert result.detail['sent'] == 1

    def test_an_unrelated_legacy_key_does_not_suppress(self, session, wire,
                                                       ctx, transport):
        project = _expiring(session, days=35)
        make_notification_log(
            session, kind='expiration', status='sent',
            dedup_key=legacy_dedup_key(project.projcode, '2020-01-01',
                                       'pi@example.edu'))
        assert mod.expiration_notices(ctx()).detail['sent'] == 1


# ── dry run and output discipline ────────────────────────────────────────────

class TestDryRun:

    def test_it_sends_nothing_and_writes_no_ledger_row(self, session, wire,
                                                       ctx, transport):
        from sam import NotificationLog
        _expiring(session, days=35)
        before = session.query(NotificationLog).count()

        result = mod.expiration_notices(ctx(dry_run=True))

        assert result.detail['dry_run'] is True
        assert transport.delivered == []
        assert session.query(NotificationLog).count() == before, \
            'a preview is not an attempt; a stray row would poison the dedup'

    def test_it_does_not_suppress_the_real_send(self, session, wire, ctx,
                                                transport):
        _expiring(session, days=35)
        mod.expiration_notices(ctx(dry_run=True))
        assert mod.expiration_notices(ctx()).detail['sent'] == 1


class TestTheRunSummary:
    """One operator email per run, whatever the outcome.

    A red Kubernetes Job says something went wrong and nothing about what; a
    green one says nothing at all — including on the ~40 weeks a year that
    legitimately send no mail, where "green and silent" is indistinguishable
    from a query that stopped matching.
    """

    @pytest.fixture(autouse=True)
    def _to(self, monkeypatch):
        monkeypatch.setenv('SAM_TASKS_SUMMARY_TO', 'ops@example.edu')

    @staticmethod
    def _summary(transport):
        return [m for m, _ in transport.delivered if m.kind == 'task_summary']

    def test_one_summary_per_run(self, session, wire, ctx, transport):
        _expiring(session, days=35)
        mod.expiration_notices(ctx())
        assert len(self._summary(transport)) == 1

    def test_it_reports_the_counts(self, session, wire, ctx, transport):
        _expiring(session, days=35)
        mod.expiration_notices(ctx())
        summary, = self._summary(transport)
        assert summary.context['sent'] == 1
        assert summary.context['selected'] == 1
        assert summary.context['suppressed'] == 0
        assert summary.context['window_start'] == START.isoformat()

    def test_a_quiet_week_still_gets_one(self, session, wire, ctx, transport):
        """"No summary" must not mean both "nothing was due" and "the task
        never ran" — that is the ambiguity it exists to remove."""
        result = mod.expiration_notices(ctx())
        assert result.detail['audience'] == 0
        summary, = self._summary(transport)
        assert 'nothing due' in summary.subject

    def test_the_cap_trip_is_summarised_before_the_raise(self, monkeypatch,
                                                         session, wire, ctx,
                                                         transport):
        """⚠️ Otherwise the one run Ben most needs to hear about is the only
        one that emails him nothing — he learns of it as a red Job with no
        explanation attached."""
        monkeypatch.setenv('SAM_TASKS_EMAIL_MAX', '1')
        _expiring(session, days=35)
        _expiring(session, days=35, email='pi2@example.edu')

        with pytest.raises(mod.EmailCapExceeded):
            mod.expiration_notices(ctx())

        summary, = self._summary(transport)
        assert summary.context['aborted'] is True
        assert 'exceeds SAM_TASKS_EMAIL_MAX' in summary.context['abort_reason']
        assert 'ABORTED' in summary.subject
        # ...and still nothing else went out.
        assert len(transport.delivered) == 1

    def test_failures_are_itemised(self, session, wire, ctx, ledger,
                                   monkeypatch):
        from sam.notify import TransportError

        class BouncesTheNotice(NullTransport):
            def deliver(self, message, rendered):
                if message.kind == 'expiration':
                    raise TransportError('550 mailbox unavailable')
                return super().deliver(message, rendered)

        bouncing = BouncesTheNotice()
        monkeypatch.setattr('sam.notify.Notifier', lambda **_: Notifier(
            config=NotifyConfig(enabled=True), transport=bouncing,
            ledger=ledger))
        _expiring(session, days=35)

        result = mod.expiration_notices(ctx())

        assert result.partial_failures == 1
        summary, = [m for m, _ in bouncing.delivered
                    if m.kind == 'task_summary']
        assert summary.context['failures'][0]['recipient'] == 'pi@example.edu'
        assert '550' in summary.context['failures'][0]['detail']
        assert 'FAILED' in summary.subject

    def test_per_project_recipient_counts_are_included(self, session, wire,
                                                       ctx, transport):
        _expiring(session, days=35, email='a@example.edu')
        _expiring(session, days=35, email='b@example.edu')
        mod.expiration_notices(ctx())
        summary, = self._summary(transport)
        assert len(summary.context['per_project']) == 2
        assert all(row['count'] == 1 for row in summary.context['per_project'])

    def test_it_is_keyed_on_the_occurrence_not_the_clock(self, session, wire,
                                                        ctx, transport):
        """A reclaimed run filling the same slot reports once."""
        _expiring(session, days=35)
        mod.expiration_notices(ctx())
        mod.expiration_notices(ctx())
        assert len(self._summary(transport)) == 1

    def test_a_different_occurrence_gets_its_own_summary(self, session, wire,
                                                         ctx, transport):
        _expiring(session, days=35)
        mod.expiration_notices(ctx())
        mod.expiration_notices(ctx(OCC + timedelta(days=7)))
        assert len(self._summary(transport)) == 2

    def test_an_unset_recipient_sends_nothing(self, monkeypatch, session,
                                              wire, ctx, transport):
        """The default, so a developer running the task locally mails no one."""
        monkeypatch.delenv('SAM_TASKS_SUMMARY_TO')
        _expiring(session, days=35)
        mod.expiration_notices(ctx())
        assert self._summary(transport) == []

    def test_a_dry_run_sends_no_summary(self, session, wire, ctx, transport):
        """A dry run must write no ledger rows at all, and a summary is a real
        message with a real row."""
        _expiring(session, days=35)
        mod.expiration_notices(ctx(dry_run=True))
        assert self._summary(transport) == []

    def test_a_broken_summary_does_not_fail_the_run(self, session, wire, ctx,
                                                    ledger, monkeypatch,
                                                    caplog):
        """By this point the real mail has gone out. Turning "the summary
        bounced" into a failed task would misreport several hundred
        successful deliveries and invite a re-run of them."""
        transport = NullTransport()
        notifier = Notifier(config=NotifyConfig(enabled=True),
                            transport=transport, ledger=ledger)
        monkeypatch.setattr('sam.notify.Notifier', lambda **_: notifier)
        _expiring(session, days=35)

        def explode(message, **kwargs):
            if message.kind == 'task_summary':
                raise RuntimeError('renderer fell over')
            return Notifier.send(notifier, message, **kwargs)
        monkeypatch.setattr(notifier, 'send', explode)

        result = mod.expiration_notices(ctx())

        assert result.detail['sent'] == 1
        assert 'run summary could not be sent' in caplog.text


class TestItWritesNothingToStdout:

    def test_the_task_is_silent(self, session, wire, ctx, capsys):
        """The CronJob runs `sam-admin --format json tasks --run-due`, whose
        stdout is a JSON envelope operators pipe to jq. A rich table or a
        progress bar in the middle of it is not parseable."""
        _expiring(session, days=35)
        mod.expiration_notices(ctx())
        assert capsys.readouterr().out == ''
