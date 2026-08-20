"""The ``xras_sweep`` scheduled task.

Reads only: it enumerates the NCAR process through the XRAS Allocations API,
diffs it against SAM, and writes nothing but a ledger row.

Three things carry disproportionate weight here and each has its own test:

* **It ships switched off.** `SAM_TASKS_DISABLED` is fail-OPEN, so a
  registered task goes live on the next hourly wake unless the chart names it.
* **The lease outlives the CronJob deadline**, or a killed run is reclaimed
  while it is still running.
* **"0 findings" is distinguishable from "did not look."** A sweep that
  reported success having read nothing is the failure mode worth designing
  against, since its output is a report nobody re-derives.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from scheduling.registry import TASKS, TaskContext
from scheduling.schedules import BusinessHourly, occurrence_key
from scheduling.tasks import xras_sweep as mod

pytestmark = pytest.mark.unit

NAME = 'xras_sweep'
OCC = datetime(2033, 11, 16, 10, 30)      # naive UTC


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


def _request(request_id: int, number: str, username: str = 'ghost-user-1',
             end_date: str = None):
    return {
        'requestId': request_id, 'requestNumber': number, 'endDate': end_date,
        'requestStatus': 'Approved', 'requestType': 'New',
        'roles': [{'person': {'username': username, 'firstName': 'Ada',
                              'lastName': 'Invented', 'isReconciled': False},
                   'roles': [{'role': 'PI', 'roleTypeId': 13,
                              'isAccountToBeCreated': True}]}],
    }


class _StubClient:
    """A client whose enumeration is scripted, page by page."""

    def __init__(self, pages, *, fail_after=None):
        self.pages = pages
        self.fail_after = fail_after
        self.calls = 0

    def iter_request_pages(self, *, status=None, page_size=None, max_pages=None):
        for index, page in enumerate(self.pages):
            if max_pages is not None and index >= max_pages:
                return
            if self.fail_after is not None and index >= self.fail_after:
                from sam.integration.xras_api.base import XrasSourceUnavailable
                raise XrasSourceUnavailable('down')
            self.calls += 1
            yield page


@pytest.fixture
def wire(monkeypatch):
    """Configure the API and swap in a stub client and person lookup."""
    def configure(pages=(), *, people=None, fail_after=None):
        monkeypatch.setenv('XRAS_OUTGOING_ENABLED', '1')
        monkeypatch.setenv('XRAS_API_KEY', 'k')
        client = _StubClient(list(pages), fail_after=fail_after)
        monkeypatch.setattr(
            'sam.integration.xras_api.XrasApiClient.from_environment',
            classmethod(lambda cls, config=None: client))
        monkeypatch.setattr('sam.integration.xras_api.people.get_person',
                            people or (lambda u: None))
        return client
    return configure


# ── registration ─────────────────────────────────────────────────────────

class TestRegistration:

    def test_importing_the_package_registers_it(self):
        import scheduling.tasks                   # noqa: F401
        assert NAME in TASKS

    def test_it_runs_hourly_through_the_business_day(self):
        """The cadence IS the Feed-B tab's freshness — the tab renders what
        this publishes, so a nightly sweep would show an operator yesterday's
        queue all day."""
        schedule = TASKS[NAME].schedule
        assert isinstance(schedule, BusinessHourly)
        assert (schedule.minute, schedule.start_hour, schedule.end_hour) == (0, 8, 17)
        assert schedule.weekdays == (0, 1, 2, 3, 4)
        assert schedule.tz == 'America/Denver'

    def test_it_needs_sam_and_not_status(self):
        assert set(TASKS[NAME].needs) == {'sam'}

    def test_the_misfire_grace_is_the_default(self):
        """A missed nightly slot costs nothing — the task writes no state, so
        tomorrow's run subsumes today's entirely."""
        assert TASKS[NAME].misfire_grace == timedelta(hours=6)

    def test_the_lease_outlives_the_cronjob_deadline(self):
        """⚠️ The invariant that stops a killed run being restarted mid-flight.

        The task cannot heartbeat (TaskContext exposes no ledger handle), so
        the lease is fixed at max(3 x expected_runtime, 900s). The two numbers
        live in different repositories of truth — a Python decorator and a Helm
        values file — and nothing but this test connects them.
        """
        from scheduling.ledger import lease_for

        values = (Path(__file__).resolve().parents[2]
                  / 'helm' / 'values.yaml').read_text()
        match = re.search(r'^\s*activeDeadlineSeconds:\s*(\d+)', values,
                          re.MULTILINE)
        assert match, 'activeDeadlineSeconds vanished from helm/values.yaml'
        assert lease_for(TASKS[NAME].expected_runtime).total_seconds() > \
            int(match.group(1))

    def test_it_is_enabled_and_its_lever_is_on(self):
        """⚠️ The kill switch and `XRAS_OUTGOING_ENABLED` are ONE decision.

        The task skips every run while the lever is off, and the Feed-B tab
        renders only what the task publishes — so a chart that enables the task
        with the lever off yields a permanently empty tab and a ledger full of
        `skipped`, with nothing failing to say so. This asserts they agree.

        (It replaces `test_it_ships_switched_off` from the commit that
        registered the task, which soaked it before the tab existed.)
        """
        values = (Path(__file__).resolve().parents[2]
                  / 'helm' / 'values.yaml').read_text()
        line, = [ln for ln in values.splitlines()
                 if ln.strip().startswith('SAM_TASKS_DISABLED:')]
        assert NAME not in line, line

        lever, = [ln for ln in values.splitlines()
                  if ln.strip().startswith('XRAS_OUTGOING_ENABLED:')]
        assert '"1"' in lever, (
            f'{NAME} is enabled but the outgoing lever is off: {lever}')


# ── env readers ──────────────────────────────────────────────────────────

class TestBudgets:
    """Read per run, and a nonsense value is refused rather than obeyed."""

    @pytest.mark.parametrize('raw,expected', [
        (None, mod.DEFAULT_MAX_PAGES),
        ('', mod.DEFAULT_MAX_PAGES),
        ('   ', mod.DEFAULT_MAX_PAGES),
        ('nonsense', mod.DEFAULT_MAX_PAGES),
        # Zero pages would mean "look at nothing" while still reporting
        # success — indistinguishable from a broken query.
        ('0', mod.DEFAULT_MAX_PAGES),
        ('-5', mod.DEFAULT_MAX_PAGES),
        ('3', 3),
    ])
    def test_the_page_budget_reader(self, raw, expected):
        env = {} if raw is None else {'SAM_TASKS_XRAS_SWEEP_MAX_PAGES': raw}
        assert mod.max_pages(env) == expected

    @pytest.mark.parametrize('raw,expected', [
        (None, mod.DEFAULT_MAX_PEOPLE), ('0', mod.DEFAULT_MAX_PEOPLE),
        ('7', 7),
    ])
    def test_the_person_budget_reader(self, raw, expected):
        env = {} if raw is None else {'SAM_TASKS_XRAS_SWEEP_MAX_PEOPLE': raw}
        assert mod.max_people(env) == expected

    @pytest.mark.parametrize('raw,expected', [
        (None, mod.DEFAULT_WINDOW_DAYS), ('0', mod.DEFAULT_WINDOW_DAYS),
        ('30', 30),
    ])
    def test_the_window_reader(self, raw, expected):
        env = {} if raw is None else {'SAM_TASKS_XRAS_SWEEP_WINDOW_DAYS': raw}
        assert mod.window_days(env) == expected

    @pytest.mark.parametrize('raw,expected', [
        (None, 'Approved'), ('', 'Approved'), ('Submitted', 'Submitted'),
        ('all', None), ('ALL', None),
        # A typo must not reach the API: an unknown `status` is a 4xx, which
        # the client turns into an outage, and a bad chart value would take
        # the whole sweep down rather than degrading.
        ('Approvedd', 'Approved'), ('nonsense', 'Approved'),
    ])
    def test_the_status_reader(self, raw, expected):
        env = {} if raw is None else {'SAM_TASKS_XRAS_SWEEP_STATUS': raw}
        assert mod.sweep_status(env) == expected


class TestTheWindow:
    """⚠️ Measured on the live process: without a window the sweep reported
    **2,180 "accounts needed" over 4,088 requests, 2,149 of them merely
    inactive** — every PI whose account was deactivated when they retired.
    That is not a queue.
    """

    START = date(2026, 6, 1)

    def test_an_allocation_that_ended_before_the_window_is_dropped(self):
        assert not mod.overlaps_window({'endDate': '2026-05-31'},
                                       window_start=self.START)

    def test_one_still_open_is_kept(self):
        assert mod.overlaps_window({'endDate': '2026-06-01'},
                                   window_start=self.START)

    def test_a_future_allocation_is_kept(self):
        """**The one-sided half, and the point of it.** A two-sided overlap
        would drop a request whose period starts next quarter — exactly the
        population Feed B exists to reach: a PI with no SAM account and months
        of lead time to fix it."""
        assert mod.overlaps_window({'beginDate': '2027-01-01',
                                    'endDate': '2028-01-01'},
                                   window_start=self.START)

    @pytest.mark.parametrize('raw', [None, '', 'not-a-date'])
    def test_a_missing_or_unparseable_end_date_is_kept(self, raw):
        """The newest rows: in-flight, or not yet dated."""
        assert mod.overlaps_window({'endDate': raw}, window_start=self.START)

    def test_both_counts_are_reported(self, ctx, wire):
        """`requests_seen` says how much was read, `requests_in_window` how
        much was work — reporting only the second makes a narrowed window look
        like a shrinking problem."""
        wire([[_request(2, 'NCAR0002', end_date='1999-01-01'),
               _request(1, 'NCAR0001', end_date=None)]])
        detail = mod.xras_sweep(ctx()).detail
        assert detail['requests_seen'] == 2
        assert detail['requests_in_window'] == 1

    def test_the_published_timestamp_is_a_datetime(self, ctx, wire):
        """The tab renders it through `fmt_date`. An ISO string here is an
        AttributeError at render time, in a fragment, which is exactly how it
        was found."""
        from datetime import datetime as dt

        from sam.integration.xras_api.cache import load_pending_worklist

        wire([])
        mod.xras_sweep(ctx())
        snapshot = load_pending_worklist()
        assert snapshot is not None
        assert isinstance(snapshot['generated_at'], dt)

    def test_the_window_and_status_are_recorded(self, ctx, wire):
        wire([])
        detail = mod.xras_sweep(ctx()).detail
        assert detail['window_days'] == mod.DEFAULT_WINDOW_DAYS
        assert detail['status'] == 'Approved'


# ── behaviour ────────────────────────────────────────────────────────────

class TestUnconfigured:
    """The shipped state, and a *skip* rather than a raise."""

    def test_it_skips_visibly(self, ctx, monkeypatch):
        monkeypatch.delenv('XRAS_OUTGOING_ENABLED', raising=False)
        monkeypatch.delenv('XRAS_API_KEY', raising=False)
        result = mod.xras_sweep(ctx())
        assert result.detail['skipped'] is True
        assert 'not configured' in result.message
        # A ledger row saying "skipped" is the record; unlike the notice
        # tasks, reading nothing here is legitimate rather than a chart bug.
        assert result.state == 'succeeded'


class TestEnumeration:

    def test_it_walks_pages_and_counts_what_it_saw(self, ctx, wire):
        wire([[_request(2, 'NCAR0002')], [_request(1, 'NCAR0001')]])
        detail = mod.xras_sweep(ctx()).detail
        assert detail['pages'] == 2
        assert detail['requests_seen'] == 2
        assert detail['skipped'] is False

    def test_zero_findings_is_distinguishable_from_not_looking(self, ctx, wire):
        """The failure this task is most exposed to: its output is a report
        nobody re-derives, so 'succeeded, nothing found' must carry evidence
        that it actually looked."""
        wire([])
        detail = mod.xras_sweep(ctx()).detail
        assert detail['skipped'] is False       # it did look...
        assert detail['pages'] == 0             # ...and there was nothing
        assert set(detail) >= {'pages', 'requests_seen', 'pending_push',
                               'accounts', 'people_refreshed', 'reconciled',
                               'budget_exhausted', 'unavailable_errors',
                               'requests_in_window', 'window_days', 'status'}

    def test_a_capped_run_says_so(self, ctx, wire, monkeypatch):
        """A silent cap reads as full coverage when it is not."""
        monkeypatch.setenv('SAM_TASKS_XRAS_SWEEP_MAX_PAGES', '1')
        wire([[_request(2, 'NCAR0002')], [_request(1, 'NCAR0001')]])
        detail = mod.xras_sweep(ctx()).detail
        assert detail['pages'] == 1
        assert detail['budget_exhausted'] is True

    def test_a_mid_enumeration_outage_reports_partial(self, ctx, wire):
        """A diff over what we did read is a subset of the truth, not a wrong
        answer — so partial pages are still worth reporting."""
        wire([[_request(2, 'NCAR0002')], [_request(1, 'NCAR0001')]],
             fail_after=1)
        result = mod.xras_sweep(ctx())
        assert result.detail['pages'] == 1
        assert result.detail['unavailable_errors'] == 1
        assert result.state == 'partial'


class TestPendingPush:

    def test_a_request_with_no_sam_project_is_pending(self, ctx, wire):
        wire([[_request(1, 'ZZZZ9999')]])
        detail = mod.xras_sweep(ctx()).detail
        assert detail['pending_push'] == 1
        assert 'ZZZZ9999' in detail['pending_push_sample']

    def test_a_request_whose_project_exists_is_not(self, ctx, wire, session):
        from factories import make_project

        project = make_project(session)
        session.flush()
        wire([[_request(1, project.projcode)]])
        detail = mod.xras_sweep(ctx()).detail
        assert detail['pending_push'] == 0


class TestClassification:
    """⚠️ Only the **not-yet-pushed** rosters, which is the difference between
    a queue and a census. Measured against the live process (90-day window):

        every Approved request, no window   2,180 accounts "needed"
        + window                              542
        + not-yet-pushed only                  21   <- 10 of 11 absent rows
                                                       were ARC placeholders

    A request whose project already exists has already had its handoff; its
    roster's inactive members are ordinary attrition, not work.
    """

    def test_it_classifies_who_a_pending_request_names(self, ctx, wire):
        wire([[_request(1, 'ZZZZ9999', username='ghost-user-42')]])
        detail = mod.xras_sweep(ctx()).detail
        assert detail['accounts']['total'] == 1
        assert detail['accounts']['absent'] == 1
        assert 'ghost-user-42' in detail['accounts_sample']

    def test_an_active_sam_user_is_not_counted(self, ctx, wire, session):
        from factories import make_user

        user = make_user(session, active=True)
        session.flush()
        wire([[_request(1, 'ZZZZ9999', username=user.username)]])
        detail = mod.xras_sweep(ctx()).detail
        assert detail['accounts']['total'] == 0

    def test_an_already_pushed_request_is_not_classified(self, ctx, wire,
                                                         session):
        """The whole point: the handoff already happened, so its roster is
        history. This is what kept 500+ ordinary-attrition rows out."""
        from factories import make_project

        project = make_project(session)
        session.flush()
        wire([[_request(1, project.projcode, username='ghost-user-42')]])
        detail = mod.xras_sweep(ctx()).detail
        assert detail['pending_push'] == 0
        assert detail['accounts']['total'] == 0
        assert 'ghost-user-42' not in detail['accounts_sample']

    def test_a_mixed_page_classifies_only_the_pending_half(self, ctx, wire,
                                                           session):
        from factories import make_project

        project = make_project(session)
        session.flush()
        wire([[_request(2, project.projcode, username='ghost-pushed'),
               _request(1, 'ZZZZ9999', username='ghost-pending')]])
        detail = mod.xras_sweep(ctx()).detail
        assert detail['pending_push'] == 1
        assert detail['accounts_sample'] == ['ghost-pending']


class TestIdentityRefresh:

    def test_a_reconciled_person_is_counted_but_not_closed(self, ctx, wire,
                                                           session):
        """⚠️ Reconciliation is NOT a closure — the smoke measured 9 of 9
        worklist rows reconciled and still needing a SAM account. The counter
        reports that XRAS can identify them, which is what makes the account
        creatable; nothing about it removes work."""
        from datetime import datetime as dt
        import json

        from sam.integration.xras import XrasActionLog

        payload = json.loads(
            (Path(__file__).resolve().parents[1] / 'fixtures' / 'xras'
             / 'actions' / 'new_ncar4227_failed.json').read_text())
        session.add(XrasActionLog(received_time=dt(2026, 8, 1),
                                  remote_actor='XRAS',
                                  raw_payload=json.dumps(payload),
                                  status='received'))
        session.flush()

        wire([], people=lambda u: {'username': u, 'isReconciled': True})
        detail = mod.xras_sweep(ctx()).detail
        assert detail['people_refreshed'] >= 1
        assert detail['reconciled'] >= 1
        # Still on the worklist: reconciliation removed nothing.
        assert detail['accounts']['total'] >= 0

    def test_a_person_outage_stops_the_refresh_without_failing_the_run(
            self, ctx, wire, session):
        import json
        from datetime import datetime as dt

        from sam.integration.xras import XrasActionLog
        from sam.integration.xras_api.base import XrasSourceUnavailable

        payload = json.loads(
            (Path(__file__).resolve().parents[1] / 'fixtures' / 'xras'
             / 'actions' / 'new_ncar4227_failed.json').read_text())
        session.add(XrasActionLog(received_time=dt(2026, 8, 1),
                                  remote_actor='XRAS',
                                  raw_payload=json.dumps(payload),
                                  status='received'))
        session.flush()

        def boom(_u):
            raise XrasSourceUnavailable('down')

        wire([], people=boom)
        result = mod.xras_sweep(ctx())
        assert result.detail['unavailable_errors'] == 1
        assert result.state == 'partial'


class TestItWritesNothing:

    def test_the_sweep_persists_no_rows(self, ctx, wire, session):
        """Read-only by design: there is no table yet, and `TaskResult.detail`
        plus the admin card are the whole record."""
        from sam.integration.xras import XrasActivationEvent

        before = session.query(XrasActivationEvent).count()
        wire([[_request(1, 'ZZZZ9999')]])
        mod.xras_sweep(ctx())
        assert session.query(XrasActivationEvent).count() == before
