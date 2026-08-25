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
from scheduling.schedules import Hourly, occurrence_key
from scheduling.tasks import xras_sweep as mod

from factories import make_xras_opportunity_mapping

pytestmark = pytest.mark.unit

# WARNING: One worker at a time for this file. The opportunity-mapping tests insert
# rows under FIXED `opportunity_id` PKs — 535388 and the 999-series are
# documented production measurements, not sequence values — and two xdist
# workers inserting the same PK inside their savepoints deadlock; InnoDB's
# deadlock rollback then destroys the savepoint ("SAVEPOINT sa_savepoint_1
# does not exist"). Bites nearly every single-file parallel run; see
# `serial_file_lock` in tests/conftest.py for why this is a lock and not
# `--dist loadgroup`.
@pytest.fixture(autouse=True)
def _one_worker_at_a_time(serial_file_lock):
    with serial_file_lock('xras_sweep_fixed_pks'):
        yield


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
             end_date: str = None, email: str = None):
    person = {'username': username, 'firstName': 'Ada',
              'lastName': 'Invented', 'isReconciled': False}
    if email:
        person['email'] = email
    return {
        'requestId': request_id, 'requestNumber': number, 'endDate': end_date,
        'requestStatus': 'Approved', 'requestType': 'New',
        'roles': [{'person': person,
                   'roles': [{'role': 'PI', 'roleTypeId': 13,
                              'isAccountToBeCreated': True}]}],
    }


class _StubClient:
    """A client whose enumeration is scripted, page by page."""

    def __init__(self, pages, *, fail_after=None, opportunities=(),
                 open_opportunities=(), extra_pages=None, resources=()):
        self.pages = pages
        self.fail_after = fail_after
        self.opportunities = list(opportunities)
        self.open_opportunities = list(open_opportunities)
        #: The `/v1/resources` catalog the preflight joins resourceId ->
        #: resourceRepositoryKey against. Empty by default: an action's resource
        #: then reads as unmapped and its verdict is `incomplete`, which is the
        #: honest answer for a test that scripts no catalog.
        self.resources = list(resources)
        #: Per-status pages for the Remediations index passes. Empty by
        #: default, which is the honest shape: a process with nothing awaiting
        #: review returns nothing, and every pre-existing test here describes
        #: only the Approved pass.
        self.extra_pages = dict(extra_pages or {})
        self.statuses_asked = []
        self.calls = 0

    def get_resources(self):
        """The resource catalog for the preflight join. Empty unless scripted."""
        return list(self.resources)

    def get_open_opportunities(self):
        """Currently-open opportunities. Empty unless a test scripts it.

        Empty is the honest default: it means the sweep still discovers ids from
        the request enumeration alone, which is what every pre-existing test here
        exercises.
        """
        return list(self.open_opportunities)

    def get_opportunities(self, opportunity_ids):
        """Resolve opportunities by id. Empty unless a test scripts it.

        Defaulting to empty keeps every pre-existing test honest: the sweep asks,
        XRAS answers with nothing it can map, and no row is written.
        """
        return list(self.opportunities)

    def iter_request_pages(self, *, status=None, page_size=None, max_pages=None):
        self.statuses_asked.append(status)
        pages = (self.extra_pages.get(status, [])
                 if status in mod.EXTRA_STATUSES else self.pages)
        for index, page in enumerate(pages):
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
    def configure(pages=(), *, people=None, fail_after=None,
                  open_opportunities=(), extra_pages=None):
        monkeypatch.setenv('XRAS_OUTGOING_ENABLED', '1')
        monkeypatch.setenv('XRAS_API_KEY', 'k')
        client = _StubClient(list(pages), fail_after=fail_after,
                             open_opportunities=open_opportunities,
                             extra_pages=extra_pages)
        monkeypatch.setattr(
            'sam.integration.xras_api.XrasApiClient.from_environment',
            classmethod(lambda cls, config=None: client))
        monkeypatch.setattr('sam.integration.xras_api.people.get_person',
                            people or (lambda u: None))
        return client
    return configure


# registration

class TestRegistration:

    def test_importing_the_package_registers_it(self):
        import scheduling.tasks                   # noqa: F401
        assert NAME in TASKS

    def test_the_decorator_is_bound_to_the_task_body(self):
        """WARNING: Registration checks the *name*, which is an argument to the
        decorator and therefore right no matter what it decorates.

        Adding a module-level helper between `@task(...)` and `def xras_sweep`
        silently registers the helper instead, and every unit test here keeps
        passing because they call `mod.xras_sweep` directly. It only fails at
        dispatch, as `task.fn(ctx)` with the wrong signature. That happened; this
        is the guard.
        """
        assert TASKS[NAME].fn.__name__ == 'xras_sweep'
        assert TASKS[NAME].fn is not None
        import inspect
        params = list(inspect.signature(TASKS[NAME].fn).parameters)
        assert params == ['ctx'], (
            f'the registered callable takes {params}; a task body takes only ctx')

    def test_it_runs_hourly_around_the_clock(self):
        """The cadence IS the Feed-B tab's freshness — the tab renders what
        this publishes, so a nightly sweep would show an operator yesterday's
        queue all day, and a business-day one gave the first operator in on a
        Monday a snapshot from Friday afternoon.

        WARNING: `Hourly`, not `BusinessHourly`: this task only refreshes a cache,
        unlike its sibling `xras_notices`, which mails people and therefore
        stays inside the business day. `Hourly` is UTC and accepts no `tz` —
        which is the point, since a local-wall hourly schedule drops a slot
        each fall and risks merging one each spring.
        """
        schedule = TASKS[NAME].schedule
        assert isinstance(schedule, Hourly)
        assert schedule.minute == 0

    def test_it_needs_sam_and_not_status(self):
        assert set(TASKS[NAME].needs) == {'sam'}

    def test_the_misfire_grace_is_the_default(self):
        """A missed nightly slot costs nothing — the task writes no state, so
        tomorrow's run subsumes today's entirely."""
        assert TASKS[NAME].misfire_grace == timedelta(hours=6)

    def test_the_lease_outlives_the_cronjob_deadline(self):
        """WARNING: The invariant that stops a killed run being restarted mid-flight.

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
        """WARNING: The kill switch and `XRAS_OUTGOING_ENABLED` are ONE decision.

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


# env readers

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
    """WARNING: Measured on the live process: without a window the sweep reported
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


# behavior

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
    """WARNING: Only the **not-yet-pushed** rosters, which is the difference between
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

    def test_a_placeholder_sam_already_holds_is_ready_to_merge(self, ctx, wire,
                                                               session):
        from factories import make_email_address, make_user
        mail = make_email_address(session, make_user(session))
        wire([[_request(1, 'ZZZZ9998', username='ghost-user-43',
                        email=mail.email_address)]])
        detail = mod.xras_sweep(ctx()).detail
        assert detail['accounts']['merge_ready'] == 1

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
        """WARNING: Reconciliation is NOT a closure — the smoke measured 9 of 9
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


class TestPublishHonesty:
    """WARNING: Found on the first production run, and the reason `published` is not
    just "did a write return".

    `BucketedTTLCache.adapter()` falls back to a per-worker in-process cache
    when `CACHE_REDIS_URL` is unset or Redis is unreachable. That fallback is
    load-bearing for the webapp — an unreachable Redis must never take it down
    — but this task runs in a **one-shot CronJob pod**: the write succeeds, the
    pod exits, the cache dies with it. Producer reports success, dashboard sees
    nothing, nothing errors.

    Production did exactly that: `cronjob-tasks.yaml` carried the four XRAS
    keys but not `CACHE_REDIS_URL`, the ledger said `published: true`, and the
    tab said "no sweep has published yet".
    """

    def test_a_process_local_store_is_not_published(self, ctx, wire,
                                                    monkeypatch):
        from sam.integration.xras_api import cache as xc

        wire([[_request(1, 'ZZZZ9999')]])
        # The in-process fallback adapter — what a pod with no CACHE_REDIS_URL
        # gets.
        monkeypatch.setattr(xc, 'is_shared_backend', lambda adapter: False)
        detail = mod.xras_sweep(ctx()).detail
        assert detail['publish_backend'] == 'local'
        assert detail['published'] is False, (
            'a cache that dies with the pod must not report as published')

    def test_a_shared_store_is_published(self, ctx, wire, monkeypatch):
        from sam.integration.xras_api import cache as xc

        wire([[_request(1, 'ZZZZ9999')]])
        monkeypatch.setattr(xc, 'is_shared_backend', lambda adapter: True)
        detail = mod.xras_sweep(ctx()).detail
        assert detail['publish_backend'] == 'redis'
        assert detail['published'] is True

    def test_the_backend_is_always_reported(self, ctx, wire):
        """"0 findings, succeeded" already had to be distinguishable from "did
        not look"; this is the same rule for the handoff."""
        wire([])
        detail = mod.xras_sweep(ctx()).detail
        assert detail['publish_backend'] in ('redis', 'local', 'disabled', 'full')


class TestTheChartCarriesTheSharedCache:
    """The wiring half of the same bug. `cronjob-tasks.yaml` renders
    `.Values.tasks.env` plus a hand-listed set and does NOT inherit
    `webapp.env` — the same cross-referencing trap `NOTIFY_*` hit."""

    def test_the_cronjob_template_sets_cache_redis_url(self):
        manifest = (Path(__file__).resolve().parents[2] / 'helm' / 'templates'
                    / 'cronjob-tasks.yaml').read_text()
        assert 'CACHE_REDIS_URL' in manifest, (
            'without the shared Redis the sweep publishes into a cache that '
            'dies with its pod')


# unmapped opportunity ids

class TestUnmappedOpportunities:
    """The ``opportunityId`` map's read-side report.

    WARNING: **This must cost nothing extra.** Every ``reports/requests`` payload already
    carries ``opportunityId``, so the count comes off data the sweep has in hand —
    which is the entire reason it lives in this task rather than behind a
    ``/v1/opportunities`` fetch. A test that let it grow a round trip would not fail;
    the stub client simply has no such method, so a fetch would raise.

    An unmapped id is **not** a fault. It falls through to the extractor ladder,
    which is what resolved it before the map existed. It is reported so a *new*
    opportunity is visible before any action is pushed against it.
    """

    def _mapped_type(self, session):
        from sam.accounting.allocations import AllocationType
        from sam.resources.facilities import Panel
        return (session.query(AllocationType)
                .join(Panel, AllocationType.panel_id == Panel.panel_id)
                .filter(Panel.panel_name == 'CHAP')
                .filter(AllocationType.allocation_type == 'CHAP').one())

    def _req(self, request_id, number, opportunity_id):
        payload = _request(request_id, number)
        payload['opportunityId'] = opportunity_id
        return payload

    def test_ids_with_no_map_row_are_counted_and_sampled(self, ctx, wire, session):
        wire([[self._req(1, 'NCAR9001', 771001),
               self._req(2, 'NCAR9002', 771002),
               self._req(3, 'NCAR9003', 771001)]])
        result = mod.xras_sweep(ctx())
        assert result.detail['opportunities_seen'] == 2      # distinct, not rows
        assert result.detail['opportunities_unmapped'] == 2
        assert result.detail['opportunities_unmapped_sample'] == [771001, 771002]

    def test_a_mapped_id_drops_out_of_the_count(self, ctx, wire, session):
        make_xras_opportunity_mapping(session,
                                      allocation_type=self._mapped_type(session),
                                      opportunity_id=771003)
        wire([[self._req(1, 'NCAR9001', 771003),
               self._req(2, 'NCAR9002', 771004)]])
        result = mod.xras_sweep(ctx())
        assert result.detail['opportunities_seen'] == 2
        assert result.detail['opportunities_unmapped'] == 1
        assert result.detail['opportunities_unmapped_sample'] == [771004]

    def test_payloads_without_an_opportunity_id_are_not_counted(self, ctx, wire, session):
        wire([[_request(1, 'NCAR9001')]])
        result = mod.xras_sweep(ctx())
        assert result.detail['opportunities_seen'] == 0
        assert result.detail['opportunities_unmapped'] == 0
        assert result.detail['opportunities_unmapped_sample'] == []

    def test_an_unresolvable_opportunity_writes_nothing(self, ctx, wire, session):
        """The sweep may now write mapping rows, but only ones it can corroborate.

        WARNING: This replaced a flat "the sweep never writes" assertion, and the
        distinction is the whole design. What must stay true is not that the
        sweep never writes — it is that **ingestion never calls out**: the
        handler path reads one local table, so an inbound action does not depend
        on `XRAS_OUTGOING_ENABLED` or on `api.xras.org` being up. Writing here is
        out of band; if it stops, the map stops growing and the free-text ladder
        covers the gap exactly as it did before the table existed.

        Here XRAS resolves nothing for the id, so there is nothing to corroborate
        and nothing is written.
        """
        from sam.integration.xras import XrasOpportunityAllocationType

        before = session.query(XrasOpportunityAllocationType).count()
        wire([[self._req(1, 'NCAR9001', 771005)]])
        result = mod.xras_sweep(ctx())
        assert session.query(XrasOpportunityAllocationType).count() == before
        assert result.detail['opportunities_written'] == 0

    def test_the_count_is_not_published_to_the_dashboard(self, ctx, wire, session,
                                                         monkeypatch):
        """It belongs in the ledger row, not in the Feed-B payload — that bucket
        renders the account worklist, and this is a different question."""
        captured = {}
        monkeypatch.setattr(
            'sam.integration.xras_api.cache.store_pending_worklist',
            lambda payload: captured.update(payload) or True)
        wire([[self._req(1, 'NCAR9001', 771006)]])
        result = mod.xras_sweep(ctx())
        assert result.detail['opportunities_unmapped'] == 1
        assert not [k for k in captured if 'opportunit' in k]


# auto-mapping new opportunities

def _opportunity(oid, name, alloc_type, type_id, panel_id, *, extra_panels=()):
    """One `/v1/opportunities/list/:ids` entry, shaped like the live payload."""
    panels = [{'panelId': p, 'isPrimary': False} for p in extra_panels]
    panels.append({'panelId': panel_id, 'isPrimary': True})
    return {'opportunityId': oid, 'opportunityName': name,
            'allocationType': alloc_type,
            'allocationTypeInfo': {'allocationTypeId': type_id,
                                   'allocationType': alloc_type},
            'panels': panels}


#: The two real opportunities where XRAS and SAM genuinely disagree, verbatim
#: from `/v1/opportunities/list/:ids` on 2026-08-20. These are the regression
#: cases the agree-only rule exists for — see `sam.xras.opportunity_types`.
_UNSPONSORED = _opportunity(
    530900, 'University small request — unsponsored', 'Educational', 500026, 500021)
_NCAR_ASD = _opportunity(
    531461, 'NCAR - ASD Opportunity', 'NCAR Strategic Computing', 500088, 500045)

#: A reissue of each kind the operator actually posts, twice a year.
_LARGE_2026 = _opportunity(
    999801, 'Large Allocation (University) - Fall 2026', 'Large', 500023, 500022,
    extra_panels=(500032,))
_NSC_2026 = _opportunity(
    999802, 'NCAR - NSC Allocation Request - Fall 2026', 'NCAR Strategic Computing',
    500088, 500045)


class TestOpportunitiesAreMappedOnlyWhenBothDerivationsAgree:
    """WARNING: The safety rule, and the two production cases that justify it.

    XRAS's own `allocationTypeId` is **not** authoritative about SAM. Measured
    across all 27 opportunities in the NCAR process, it disagrees with the
    free-text ladder twice — and both times it is XRAS that is wrong, in a way
    that changes the *facility* and therefore the projcode series.
    """

    def _wire_opportunities(self, wire, monkeypatch, payloads):
        client = wire([[]])
        monkeypatch.setattr(type(client), 'get_opportunities',
                            lambda self, ids: list(payloads), raising=False)
        return client

    def _run(self, ctx, wire, monkeypatch, session, payloads, unmapped_ids):
        self._wire_opportunities(wire, monkeypatch, payloads)
        detail = {'unavailable_errors': 0}
        from sam.integration.xras_api import XrasApiClient
        mod._map_new_opportunities(ctx(), session, XrasApiClient.from_environment(),
                                   unmapped_ids, detail)
        return detail

    def test_the_unsponsored_opportunity_is_withheld(self, ctx, wire, session,
                                                     monkeypatch):
        """530900: XRAS files it under `Educational` — the same allocationTypeId
        as Classroom/Training — but SAM means `Small (No NSF award)`. Writing
        XRAS's answer would silently retype every unsponsored request."""
        detail = self._run(ctx, wire, monkeypatch, session, [_UNSPONSORED], [530900])
        assert detail['opportunities_written'] == 0
        review = {r['opportunity_id'] for r in detail['opportunities_needing_review']}
        assert review == {530900}
        row, = detail['opportunities_needing_review']
        assert tuple(row['ladder']) == ('UNIV USS', 'Small (No NSF award)')
        assert tuple(row['xras']) == ('UNIV USS', 'Classroom')

    def test_the_ncar_asd_opportunity_is_withheld(self, ctx, wire, session,
                                                  monkeypatch):
        """531461: XRAS gives ASD **NSC's own** allocationTypeId and panel, so the
        two are indistinguishable from the API. SAM means `ASD-NCAR`, which is
        facility 7 rather than facility 1 — a different projcode series, and not
        undoable once minted."""
        detail = self._run(ctx, wire, monkeypatch, session, [_NCAR_ASD], [531461])
        assert detail['opportunities_written'] == 0
        row, = detail['opportunities_needing_review']
        assert row['opportunity_id'] == 531461
        assert tuple(row['ladder']) == ('ASD-NCAR', 'ASD-NCAR')
        assert tuple(row['xras']) == ('NCAR-ARP', 'NSC')

    def test_a_large_reissue_maps_itself(self, ctx, wire, session, monkeypatch):
        """The point of the feature. Every University Large since 2021 shares one
        (allocationTypeId, panelId), so a new one needs no SQL at all.

        Also pins that the **primary** panel is chosen: Large carries 500032
        (external reviewers) alongside 500022, and only the latter is SAM's CHAP.
        """
        from sam.integration.xras import SOURCE_SWEEP, XrasOpportunityAllocationType

        detail = self._run(ctx, wire, monkeypatch, session, [_LARGE_2026], [999801])
        assert detail['opportunities_written'] == 1
        row = session.get(XrasOpportunityAllocationType, 999801)
        assert row is not None
        assert row.source == SOURCE_SWEEP
        assert row.allocation_type.panel.panel_name == 'CHAP'
        assert row.allocation_type.allocation_type == 'CHAP'

    def test_an_nsc_reissue_maps_itself(self, ctx, wire, session, monkeypatch):
        """The other half of the operator's twice-yearly churn."""
        from sam.integration.xras import XrasOpportunityAllocationType

        detail = self._run(ctx, wire, monkeypatch, session, [_NSC_2026], [999802])
        assert detail['opportunities_written'] == 1
        row = session.get(XrasOpportunityAllocationType, 999802)
        assert row.allocation_type.panel.panel_name == 'NCAR-ARP'
        assert row.allocation_type.allocation_type == 'NSC'

    def test_an_existing_row_is_never_overwritten(self, ctx, wire, session,
                                                  monkeypatch):
        """WARNING: A `manual` row is a human's answer to a question the API cannot
        settle. The sweep inserts only where nothing exists — checked against the
        database, not against `source`."""
        from sam.accounting.allocations import AllocationType
        from sam.integration.xras import XrasOpportunityAllocationType
        from sam.resources.facilities import Panel

        keep = (session.query(AllocationType)
                .join(Panel, AllocationType.panel_id == Panel.panel_id)
                .filter(Panel.panel_name == 'UNIV USS')
                .filter(AllocationType.allocation_type == 'Data').one())
        make_xras_opportunity_mapping(session, allocation_type=keep,
                                      opportunity_id=999801)

        detail = self._run(ctx, wire, monkeypatch, session, [_LARGE_2026], [999801])
        assert detail['opportunities_written'] == 0
        row = session.get(XrasOpportunityAllocationType, 999801)
        assert row.allocation_type.allocation_type == 'Data'      # untouched

    def test_an_unknown_pair_is_reported_not_guessed(self, ctx, wire, session,
                                                     monkeypatch):
        """A genuinely new allocation product. Adding it is a one-line edit to the
        constant — a code review, not a silent write."""
        novel = _opportunity(999803, 'Quantum Allocation (University)', 'Quantum',
                             777777, 500021)
        detail = self._run(ctx, wire, monkeypatch, session, [novel], [999803])
        assert detail['opportunities_written'] == 0
        assert [r['opportunity_id'] for r in detail['opportunities_unknown_pair']] == [999803]

    def test_a_missing_primary_panel_writes_nothing(self, ctx, wire, session,
                                                    monkeypatch):
        payload = dict(_LARGE_2026,
                       panels=[{'panelId': 500032, 'isPrimary': False}])
        detail = self._run(ctx, wire, monkeypatch, session, [payload], [999801])
        assert detail['opportunities_written'] == 0
        assert detail['opportunities_unknown_pair']

    def test_the_per_run_cap_bounds_the_blast_radius(self, ctx, wire, session,
                                                     monkeypatch):
        monkeypatch.setenv('SAM_TASKS_XRAS_MAP_MAX', '1')
        payloads = [_LARGE_2026, _NSC_2026]
        detail = self._run(ctx, wire, monkeypatch, session, payloads, [999801, 999802])
        assert detail['opportunities_written'] == 1
        assert detail['map_budget_exhausted'] is True

    def test_an_api_outage_costs_only_this_step(self, ctx, wire, session,
                                                monkeypatch):
        """The enumeration and worklist are already in hand and still get reported;
        the same ids are retried next slot."""
        from sam.integration.xras_api import XrasApiClient, XrasSourceUnavailable

        client = wire([[]])

        def boom(self, ids):
            raise XrasSourceUnavailable('down')

        monkeypatch.setattr(type(client), 'get_opportunities', boom, raising=False)
        detail = {'unavailable_errors': 0}
        mod._map_new_opportunities(ctx(), session, XrasApiClient.from_environment(),
                                   [999801], detail)
        assert detail['unavailable_errors'] == 1
        assert detail.get('opportunities_written', 0) == 0


class TestTheDryRunIsAFullRehearsal:
    """`--dry-run` is the operator's preview, and it must write nothing.

    Not a special code path: `TaskContext.close_sessions` rolls back instead of
    committing when `dry_run` is set, so the task body is identical and the
    report is exactly what a real run would have done.
    """

    def test_a_dry_run_reports_but_does_not_commit(self, ctx, wire, session,
                                                   monkeypatch):
        from sam.integration.xras_api import XrasApiClient

        client = wire([[]])
        monkeypatch.setattr(type(client), 'get_opportunities',
                            lambda self, ids: [_LARGE_2026], raising=False)
        context = ctx(dry_run=True)
        detail = {'unavailable_errors': 0}
        mod._map_new_opportunities(context, session,
                                   XrasApiClient.from_environment(), [999801], detail)

        # The report is identical to a live run — that is the point of a rehearsal.
        assert detail['opportunities_written'] == 1

        # ...and the runner throws the work away rather than committing it.
        context._sam_session = session
        context.close_sessions(commit=True)


class TestABrandNewOpportunityIsSeenBeforeAnyRequestExists:
    """WARNING: The lead-time property, and the reason the open list is queried at all.

    The request enumeration **cannot** mention an opportunity that has no
    requests. So a newly-posted one is invisible there until its first request is
    *approved* — which can be weeks later, and is exactly the window the map is
    supposed to buy.

    Measured against production on 2026-08-20: `Large Allocation (University) -
    Fall 2026` (535388) was returned by `/v1/opportunities` the moment it was
    posted, while `/v1/reports/requests?status=Approved` knew nothing of it.
    """

    #: 535388 as XRAS actually returned it, trimmed to the fields that matter.
    FALL_2026 = _opportunity(535388, 'Large Allocation (University) - Fall 2026',
                             'Large', 500023, 500022, extra_panels=(500032,))

    @pytest.fixture(autouse=True)
    def _unmapped(self, session):
        """Guarantee 535388 is unmapped, instead of assuming the snapshot is.

        WARNING: **This class describes a BRAND-NEW opportunity, so every assertion
        below depends on that id being absent — and the obfuscated snapshot is
        not a fixed thing.** `xras_sweep` writes mapping rows itself
        (`source='task:xras_sweep'`), so the dev database accumulates them, and
        a regeneration carries whatever it had accumulated into CI.

        That is exactly what happened on 2026-08-21: the snapshot went from 13
        mapping rows to 43, 535388 among them, and all three tests here failed
        in CI while passing locally against the older blob. The id stays real
        because the measurement is the documentation — this fixture just stops
        the test borrowing a precondition it does not control.

        Deleted inside the per-test SAVEPOINT, so nothing leaks to other tests.
        """
        from sam.integration.xras import XrasOpportunityAllocationType

        session.query(XrasOpportunityAllocationType).filter_by(
            opportunity_id=535388).delete(synchronize_session=False)
        session.flush()

    def test_it_is_mapped_with_no_requests_anywhere(self, ctx, wire, session):
        """No pages at all — nothing has ever been submitted against it."""
        from sam.integration.xras import SOURCE_SWEEP, XrasOpportunityAllocationType

        wire([[]], open_opportunities=[self.FALL_2026])
        result = mod.xras_sweep(ctx())

        assert result.detail['opportunities_open'] == 1
        assert result.detail['opportunities_written'] == 1
        row = session.get(XrasOpportunityAllocationType, 535388)
        assert row.source == SOURCE_SWEEP
        assert row.allocation_type.panel.panel_name == 'CHAP'
        assert row.allocation_type.allocation_type == 'CHAP'

    def test_without_the_open_list_it_would_be_invisible(self, ctx, wire, session):
        """The negative half, so the property cannot be quietly removed: with the
        open list empty and no requests, nothing is seen and nothing is written."""
        from sam.integration.xras import XrasOpportunityAllocationType

        wire([[]])
        result = mod.xras_sweep(ctx())
        assert result.detail['opportunities_seen'] == 0
        assert result.detail['opportunities_written'] == 0
        assert session.get(XrasOpportunityAllocationType, 535388) is None

    def test_an_open_opportunity_costs_no_by_id_round_trip(self, ctx, wire, session,
                                                          monkeypatch):
        """The open list arrives with `allocationTypeInfo` and `panels` attached,
        so re-resolving it by id would be a wasted call."""
        client = wire([[]], open_opportunities=[self.FALL_2026])
        calls = []
        original = client.get_opportunities
        monkeypatch.setattr(type(client), 'get_opportunities',
                            lambda self, ids: calls.append(list(ids)) or original(ids),
                            raising=False)
        mod.xras_sweep(ctx())
        assert calls == [[]], f'expected no by-id fetch, got {calls}'

    def test_an_open_list_outage_does_not_lose_the_request_half(self, ctx, wire,
                                                               session, monkeypatch):
        """It is an enrichment. Losing it must not cost the ids the enumeration
        already found."""
        from sam.integration.xras_api import XrasSourceUnavailable

        client = wire([[self._req_with(1, 'NCAR9001', 999801)]])

        def boom(self):
            raise XrasSourceUnavailable('down')

        monkeypatch.setattr(type(client), 'get_open_opportunities', boom, raising=False)
        monkeypatch.setattr(type(client), 'get_opportunities',
                            lambda self, ids: [_LARGE_2026], raising=False)
        result = mod.xras_sweep(ctx())
        assert result.detail['unavailable_errors'] == 1
        assert result.detail['opportunities_seen'] == 1        # the request half survived
        assert result.detail['opportunities_written'] == 1

    def _req_with(self, request_id, number, opportunity_id):
        payload = _request(request_id, number)
        payload['opportunityId'] = opportunity_id
        return payload


class TestTheCapTakesTheNewestFirst:
    """Under the cap, a historical backfill must not crowd out a new arrival.

    `opportunity_id` ascends with time. A newly-posted opportunity is the whole
    point of the feature — it is the one an imminent action might reference —
    while a closed 2018 opportunity has no pending handoff and can wait an hour.
    Measured: the first production backfill proposed 30 rows against a cap of 20,
    and the newly-posted 535388 was the highest id of the set.
    """

    def test_the_newest_win_when_the_cap_bites(self, ctx, wire, session, monkeypatch):
        from sam.integration.xras import XrasOpportunityAllocationType

        monkeypatch.setenv('SAM_TASKS_XRAS_MAP_MAX', '2')
        old_a = _opportunity(999101, 'Large Allocation (University) - Fall 2019',
                             'Large', 500023, 500022)
        old_b = _opportunity(999102, 'Large Allocation (University) - Fall 2020',
                             'Large', 500023, 500022)
        newest = _opportunity(999999, 'Large Allocation (University) - Fall 2026',
                              'Large', 500023, 500022)
        wire([[]], open_opportunities=[old_a, old_b, newest])

        result = mod.xras_sweep(ctx())
        assert result.detail['map_budget_exhausted'] is True
        assert result.detail['opportunities_written'] == 2
        assert session.get(XrasOpportunityAllocationType, 999999) is not None, \
            'the newly-posted opportunity was crowded out by the backfill'
        assert session.get(XrasOpportunityAllocationType, 999101) is None


# the Remediations index (second cache key)

def _pending_request(request_id, number, *, status='Approved',
                     actions=(), username='ghost-user-1', reconciled=False):
    return {
        'requestId': request_id, 'requestNumber': number, 'endDate': None,
        'requestStatus': status, 'requestType': 'New',
        'opportunityId': 5, 'opportunity_name': 'Small',
        'roles': [{'person': {'username': username, 'firstName': 'Ada',
                              'lastName': 'Invented', 'isReconciled': reconciled},
                   'roles': [{'roleId': 1, 'role': 'PI', 'roleTypeId': 13}]}],
        'actions': [{'actionId': a, 'actionType': 'New', 'actionStatus': s}
                    for (a, s) in actions],
    }


class TestTheRequestsIndex:
    """The Remediations card's feed — a second key, same bucket, same run."""

    def test_the_worklist_payload_shape_is_unchanged(self, ctx, wire):
        """A refactor guard, NOT a compatibility pin.

        The commit that added the index also moved `store_pending_worklist`
        onto a shared helper and restructured this task's publish region, and
        this asserts that pass left the worklist payload alone.

        Nothing external consumes this shape — the sweep and the webapp ship in
        one image — so **change this test freely when you mean to change the
        payload**. It exists to make an accidental change loud, not to freeze
        anything.
        """
        from sam.integration.xras_api.cache import load_pending_worklist
        wire([[_request(1, 'NCAR0001')]])
        mod.xras_sweep(ctx())
        worklist = load_pending_worklist()
        assert set(worklist) == {'generated_at', 'window_days', 'status',
                                 'requests_seen', 'requests_in_window',
                                 'budget_exhausted', 'pending_push',
                                 'pending_push_sample', 'counts', 'rows'}

    def test_it_publishes_a_second_key(self, ctx, wire):
        from sam.integration.xras_api.cache import load_requests_index
        wire([[_pending_request(1, 'NCAR0001')]])
        mod.xras_sweep(ctx())
        index = load_requests_index()
        assert [r['request_number'] for r in index['rows']] == ['NCAR0001']

    def test_the_extra_statuses_are_swept_too(self, ctx, wire):
        from sam.integration.xras_api.cache import load_requests_index
        wire([[_pending_request(1, 'NCAR0001')]],
             extra_pages={'Submitted': [[_pending_request(2, 'NCAR0002',
                                                          status='Submitted')]],
                          'Under Review': [[_pending_request(3, 'NCAR0003',
                                                             status='Under Review')]]})
        mod.xras_sweep(ctx())
        rows = load_requests_index()['rows']
        assert {r['request_number'] for r in rows} == {'NCAR0001', 'NCAR0002',
                                                       'NCAR0003'}

    def test_the_status_env_var_governs_only_the_primary_pass(self, ctx, wire,
                                                              monkeypatch):
        """A typo'd chart value must not silently drop the remediation feed."""
        monkeypatch.setenv('SAM_TASKS_XRAS_SWEEP_STATUS', 'Rejected')
        client = wire([[_pending_request(1, 'NCAR0001', status='Rejected')]],
                      extra_pages={'Submitted': [[_pending_request(
                          2, 'NCAR0002', status='Submitted')]]})
        mod.xras_sweep(ctx())
        assert client.statuses_asked == ['Rejected', 'Submitted', 'Under Review']

    def test_an_already_pushed_approved_request_is_excluded(self, ctx, wire,
                                                            session):
        """Its handoff happened; there is nothing here to remediate."""
        from factories.projects import make_project

        from sam.integration.xras_api.cache import load_requests_index
        project = make_project(session)
        wire([[_pending_request(1, project.projcode),
               _pending_request(2, 'NCAR9999')]])
        mod.xras_sweep(ctx())
        numbers = {r['request_number'] for r in load_requests_index()['rows']}
        assert project.projcode not in numbers
        assert 'NCAR9999' in numbers

    def test_a_submitted_request_is_kept_even_when_sam_knows_it(self, ctx, wire,
                                                                session):
        """Worth an operator's eye precisely because it is unexpected."""
        from factories.projects import make_project

        from sam.integration.xras_api.cache import load_requests_index
        project = make_project(session)
        wire([], extra_pages={'Submitted': [[_pending_request(
            2, project.projcode, status='Submitted')]]})
        mod.xras_sweep(ctx())
        numbers = {r['request_number'] for r in load_requests_index()['rows']}
        assert project.projcode in numbers

    def test_the_index_ignores_the_period_of_performance_window(self, ctx, wire):
        """WARNING: The stale ones are the point — a window would hide exactly them."""
        from sam.integration.xras_api.cache import (load_pending_worklist,
                                                    load_requests_index)
        ancient = _pending_request(1, 'NCAR0001')
        ancient['endDate'] = '2015-12-31'
        wire([[ancient]])
        detail = mod.xras_sweep(ctx()).detail

        assert detail['requests_in_window'] == 0, 'the worklist drops it'
        assert load_pending_worklist()['rows'] == []
        assert [r['request_number'] for r in load_requests_index()['rows']] \
            == ['NCAR0001'], 'the index keeps it'

    def test_each_extra_status_reports_its_own_budget(self, ctx, wire,
                                                      monkeypatch):
        """One cohort outgrowing its cap must not read as the whole index
        truncating — nor be hidden by another cohort that fitted."""
        monkeypatch.setattr(mod, 'EXTRA_STATUS_MAX_PAGES', 1)
        wire([], extra_pages={
            'Submitted': [[_pending_request(2, 'NCAR0002', status='Submitted')],
                          [_pending_request(3, 'NCAR0003', status='Submitted')]],
            'Under Review': [],
        })
        extra = mod.xras_sweep(ctx()).detail['extra_statuses']
        assert extra['Submitted']['budget_exhausted'] is True
        assert extra['Under Review']['budget_exhausted'] is False

    def test_an_extra_pass_outage_costs_that_status_only(self, ctx, wire,
                                                         monkeypatch):
        from sam.integration.xras_api.base import XrasSourceUnavailable

        from sam.integration.xras_api.cache import load_requests_index
        client = wire([[_pending_request(1, 'NCAR0001')]])

        real = client.iter_request_pages

        def flaky(*, status=None, **kw):
            if status == 'Submitted':
                raise XrasSourceUnavailable('down')
            return real(status=status, **kw)

        monkeypatch.setattr(client, 'iter_request_pages', flaky)
        detail = mod.xras_sweep(ctx()).detail
        assert detail['extra_statuses']['Submitted']['seen'] == 0
        assert detail['unavailable_errors'] == 1
        # The Approved half still published.
        assert [r['request_number'] for r in load_requests_index()['rows']] \
            == ['NCAR0001']

    def test_the_entries_are_the_shared_builders_output(self, ctx, wire):
        """The sweep has no private copy of this derivation.

        Feed the row's own preflight cells back into the shared builder: the
        published row must equal what the builder produces for the same inputs.
        The preflight verdicts themselves are pinned in `test_xras_preflight`;
        re-feeding them keeps this from pinning their non-deterministic
        `checked_at` while still proving the sweep adds no private derivation.
        """
        from sam.integration.xras_api.cache import load_requests_index
        from sam.queries.xras_requests import request_index_entry

        payload = _pending_request(1, 'NCAR0001', actions=((7, 'Approved'),))
        wire([[payload]])
        mod.xras_sweep(ctx())
        row = load_requests_index()['rows'][0]
        preflights = {a['action_id']: a['preflight']
                      for a in row['actions'] if a.get('preflight')}
        assert row == request_index_entry(payload, pending_push=True,
                                          preflights=preflights)

    def test_the_detail_carries_counts_not_rows(self, ctx, wire):
        """`detail` is JSON truncated at 60 kB — the rows live in the cache."""
        wire([[_pending_request(1, 'NCAR0001')]])
        detail = mod.xras_sweep(ctx()).detail
        assert detail['index_requests'] == 1
        assert 'rows' not in detail['extra_statuses']
        assert detail['index_publish_backend'] in ('redis', 'local', 'disabled')


class TestPreflightInTheSweep:
    """Push-readiness rides the index build: a verdict per candidate action."""

    def test_a_candidate_action_carries_a_preflight_cell(self, ctx, wire):
        from sam.integration.xras_api.cache import load_requests_index
        wire([[_pending_request(1, 'NCAR0001', actions=((7, 'Approved'),))]])
        detail = mod.xras_sweep(ctx()).detail
        row = load_requests_index()['rows'][0]
        assert row['actions'][0]['preflight'] is not None
        assert row['preflight_rollup'] is not None
        assert detail['preflight']['candidates'] == 1

    def test_detail_preflight_distinguishes_checked_from_did_not_look(self, ctx,
                                                                       wire):
        wire([[_pending_request(1, 'NCAR0001')]])          # a request, no actions
        detail = mod.xras_sweep(ctx()).detail
        assert detail['preflight']['candidates'] == 0
        assert set(detail['preflight']) >= {'candidates', 'rechecked', 'failed',
                                            'manual', 'incomplete', 'by_stage',
                                            'by_push_state', 'window_days'}

    def test_an_already_pushed_request_with_a_candidate_action_is_kept(
            self, ctx, wire, session):
        """The new cohort member: an existing project with a fresh action."""
        from factories.projects import make_project

        from sam.integration.xras_api.cache import load_requests_index
        project = make_project(session)
        wire([[_pending_request(1, project.projcode, actions=((7, 'Approved'),))]])
        mod.xras_sweep(ctx())
        rows = {r['request_number']: r for r in load_requests_index()['rows']}
        assert project.projcode in rows
        assert rows[project.projcode]['pending_push'] is False

    def test_the_pending_users_worklist_rows_get_the_preflight(self, ctx, wire):
        """The Feed-B fill: a pending-request roster action carries the verdict."""
        from sam.integration.xras_api.cache import load_pending_worklist
        wire([[_pending_request(1, 'NCAR0001', actions=((7, 'Approved'),),
                                username='ghost-user-1')]])
        mod.xras_sweep(ctx())
        rows = load_pending_worklist()['rows']
        actions = [a for r in rows for a in r['actions']
                   if a['request_number'] == 'NCAR0001']
        assert actions, 'the pending roster produced no worklist row'
        assert all(a['preflight_status'] is not None for a in actions)

    def test_a_raising_preflight_costs_one_row_not_the_run(self, ctx, wire,
                                                           monkeypatch):
        from sam.integration.xras_api.cache import load_requests_index

        def boom(*a, **k):
            raise RuntimeError('preflight exploded')
        monkeypatch.setattr('sam.xras.preflight.preflight_action', boom)
        wire([[_pending_request(1, 'NCAR0001', actions=((7, 'Approved'),))]])
        detail = mod.xras_sweep(ctx()).detail                 # does not raise
        assert detail['preflight']['incomplete'] == 1
        # The row still publishes, just without a verdict.
        assert load_requests_index()['rows'][0]['actions'][0]['preflight'] is None

    def test_detail_carries_the_calibration_plumbing(self, ctx, wire):
        wire([[_pending_request(1, 'NCAR0001', actions=((7, 'Approved'),))]])
        detail = mod.xras_sweep(ctx()).detail
        assert set(detail['preflight_calibration']) == {'compared', 'agree',
                                                        'sample'}


class TestCalibration:
    """Grading a prediction against the real push outcome."""

    def _verdict(self, status, action_id=7):
        from types import SimpleNamespace
        return SimpleNamespace(status=status, action_id=action_id)

    def test_a_correct_prediction_agrees(self):
        cal = {'compared': 0, 'agree': 0, 'sample': []}
        mod._calibrate(cal, self._verdict('failed'), {'status': 'failed'})
        mod._calibrate(cal, self._verdict('rechecked', 8), {'status': 'processed'})
        assert cal['compared'] == 2 and cal['agree'] == 2

    def test_a_wrong_prediction_is_compared_but_disagrees(self):
        cal = {'compared': 0, 'agree': 0, 'sample': []}
        mod._calibrate(cal, self._verdict('rechecked'), {'status': 'failed'})
        assert cal['compared'] == 1 and cal['agree'] == 0

    def test_unchecked_and_non_terminal_make_no_comparison(self):
        cal = {'compared': 0, 'agree': 0, 'sample': []}
        mod._calibrate(cal, self._verdict('incomplete'), {'status': 'failed'})
        mod._calibrate(cal, self._verdict('failed'), {'status': 'received'})
        mod._calibrate(cal, self._verdict('failed'), None)
        assert cal['compared'] == 0


class TestAFailedIndexBuildDoesNotPublish:
    """WARNING: ``[]`` is a real answer (nothing to remediate) and publishes; a
    *failed* build must publish nothing, because replacing the last good
    snapshot with ``[]`` renders the healthy empty state over a blind hour —
    and the 24h TTL exists precisely to carry the good snapshot across it."""

    def _seed_good_index(self):
        from sam.integration.xras_api.cache import store_requests_index
        store_requests_index({'generated_at': datetime(2033, 11, 16, 9, 0),
                              'statuses': ['Approved'], 'extra_statuses': {},
                              'rows': [{'request_number': 'NCAR0001'}]})

    def test_a_projcode_lookup_failure_fails_the_build(self, ctx, wire):
        """None, not [] — without the pending classification every entry
        would be wrong."""
        client = wire([])

        class Boom:
            def query(self, *args, **kwargs):
                raise RuntimeError('db went away')

        detail = {'unavailable_errors': 0, 'extra_statuses': {},
                  'index_requests': 0}
        result = mod._build_requests_index(
            ctx(), client, Boom(), [_pending_request(1, 'NCAR0001')], detail)
        assert result is None

    def test_a_failed_build_keeps_the_previous_index(self, ctx, wire,
                                                     monkeypatch):
        from sam.integration.xras_api.cache import load_requests_index
        wire([[_pending_request(1, 'NCAR0002')]])
        self._seed_good_index()
        monkeypatch.setattr(mod, '_build_requests_index',
                            lambda *args, **kwargs: None)

        detail = mod.xras_sweep(ctx()).detail

        assert detail['index_published'] is False
        assert detail['index_publish_backend'] == 'skipped'
        assert detail['index_skipped'] == 'build failed'
        assert [r['request_number'] for r in load_requests_index()['rows']] \
            == ['NCAR0001'], 'the last good snapshot must survive'

    def test_a_total_outage_keeps_the_previous_index(self, ctx, wire):
        """Primary enumeration down, extras empty: nothing was read anywhere,
        so an empty cohort is not evidence of an empty queue."""
        from sam.integration.xras_api.cache import load_requests_index
        wire([[_pending_request(1, 'NCAR0002')]], fail_after=0)
        self._seed_good_index()

        detail = mod.xras_sweep(ctx()).detail

        assert detail['index_published'] is False
        assert 'index_skipped' in detail
        assert [r['request_number'] for r in load_requests_index()['rows']] \
            == ['NCAR0001']

    def test_a_genuinely_empty_index_still_publishes(self, ctx, wire):
        """The healthy answer must keep publishing — this guard is for
        failures, not for quiet weeks."""
        from sam.integration.xras_api.cache import load_requests_index
        self._seed_good_index()
        wire([])
        detail = mod.xras_sweep(ctx()).detail
        assert 'index_skipped' not in detail
        assert load_requests_index()['rows'] == []


class TestAnOverlappingPrimaryStatusDoesNotDuplicate:
    """`SAM_TASKS_XRAS_SWEEP_STATUS=all` re-reads the extras' cohorts in the
    primary pass; without a dedupe the same request rendered twice, with two
    Withdraw buttons — and the post-write patch rewrites only the first."""

    def test_the_index_carries_each_request_once(self, ctx, wire, monkeypatch):
        from sam.integration.xras_api.cache import load_requests_index
        monkeypatch.setenv('SAM_TASKS_XRAS_SWEEP_STATUS', 'all')
        submitted = _pending_request(2, 'NCAR0002', status='Submitted')
        wire([[_pending_request(1, 'NCAR0001'), submitted]],
             extra_pages={'Submitted': [[submitted]]})

        mod.xras_sweep(ctx())

        numbers = [r['request_number'] for r in load_requests_index()['rows']]
        assert sorted(numbers) == ['NCAR0001', 'NCAR0002']
