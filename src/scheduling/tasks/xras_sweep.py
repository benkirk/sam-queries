"""``xras_sweep`` — enumerate XRAS nightly and diff it against SAM.

Hourly 08:00-17:00 Mon-Fri Mountain. The first task that calls **out** to XRAS
rather than reading only SAM's own tables, and the third declaring
``needs=('sam',)``.

It is also the first task that **publishes to the dashboard**. The Feed-B tab
on Allocations → XRAS renders what this writes to the ``xras_pending`` cache
bucket, because the enumeration behind it (21 pages, 60-90s) is far outside
what an htmx round-trip can afford. The cadence here is therefore the tab's
freshness, which is why this runs hourly rather than nightly.

What it is for
--------------
The account-creation worklist built from ``xras_action_log`` can only see
people XRAS has already pushed. ``GET /v1/reports/requests`` sees the whole
NCAR process, so this task reaches three things the card cannot:

1. **People ahead of the push** — a brand-new PI on a solo New request,
   connected to nobody SAM knows, is visible here *before* the action arrives.
   That is the hardest population to prepare for and the one manual account
   creation most needs lead time on. Only the **not-yet-pushed** rosters are
   classified: a request whose project already exists has had its handoff, so
   its roster is history (see the note at step 3, with the measurements).
2. **Dropped or pending pushes** — a set difference between the Approved
   requests' ``requestNumber`` and ``project.projcode``. Cheap, because both
   sides are already in hand.
3. **Identity detail** — re-fetching ``/v1/people`` for everyone currently on
   the worklist refreshes ``isReconciled`` and leaves a warm cache for the
   morning's first card render. ⚠️ Reconciliation is **not** a closure: it
   means XRAS has linked the username to a real identity, not that SAM has an
   account. A row leaves the worklist when its ``users`` row exists and is
   active, which classification already checks on every render — for free.

⚠️ **Ships switched off.** ``SAM_TASKS_DISABLED`` is *fail-open*: registering
a task here puts it into production live on the next hourly wake unless its
name is added to ``helm/values.yaml`` in the **same change**. The registry is
code-side, the list is chart-side, and nothing couples them but the reviewer —
so ``test_task_xras_sweep.py`` greps ``values.yaml`` for the name.

⚠️ **Unconfigured is a skip, not a raise.** Unlike the notice tasks, whose
guards raise because a chart mistake that mails nobody must not report
success, this task *reading* nothing is a legitimate state — it is the shipped
one. The ledger row carrying ``skipped: true`` is the record.

**What was deliberately not built**: a per-identity ``/v1/requests`` polling
sweep (measured at 0.84 s x 1,518 seed identities nightly) and its transitive
roster crawl. The reports enumeration reaches strictly more people — including
wholly-new solo PIs the crawl could never connect to — in a handful of
paginated calls. Do not resurrect the crawl.

Design: ``docs/xras/outgoing/XRAS_OUTGOING_QUERIES.md``.
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from scheduling.registry import TaskResult, task
from scheduling.schedules import BusinessHourly, to_local_naive

#: Overnight on purpose: nothing here is time-critical, the enumeration is the
#: heaviest outbound call SAM makes, and a warm people-cache at 03:30 is still
#: warm for the first card render of the working day.
#: Hourly through the business day, matching `xras_notices`. The Feed-B tab
#: reads what this publishes, so the cadence IS the tab's freshness — a
#: nightly sweep would show an operator yesterday's queue all day.
#:
#: `minute=0` for the same reason `xras_notices` uses it: the CronJob wakes at
#: :07, so a :00 slot dispatches about seven minutes later where a :20 slot
#: would wait for the next wake. Both tasks share the wake and run
#: sequentially under `concurrencyPolicy: Forbid`; this one takes 60-90s.
SCHEDULE = BusinessHourly(minute=0, tz='America/Denver')

#: Pages of ``reports/requests`` per run, overridable via
#: ``$SAM_TASKS_XRAS_SWEEP_MAX_PAGES``. At the default page size that is 5,000
#: requests — comfortably the whole NCAR process today.
#:
#: A **bound, not a target**: `detail.budget_exhausted` reports when it bit, so
#: a cap that starts truncating is visible rather than silently reading as full
#: coverage.
DEFAULT_MAX_PAGES = 25

#: Requests per page. The API's own default is smaller; 200 keeps the whole
#: process inside a handful of round trips.
PAGE_SIZE = 200

#: The sweep's window, in days back from the slot — the Feed-B analogue of the
#: worklist card's 7D/30D/90D pills.
#:
#: ⚠️ **Without a window the sweep is meaningless, and the smoke measured it.**
#: Unfiltered, the enumeration returns every Approved request the NCAR process
#: has ever held — 4,088 of them — and classifying that whole corpus reported
#: **2,180 "accounts needed", 2,149 of them merely inactive**. Those are not
#: work: they are every PI and admin whose SAM account was deactivated when
#: they retired, moved institution, or simply finished.
#:
#: **Which date, and why.** The card filters Feed A on ``received_time`` —
#: when the action *arrived*. Feed B's honest analogue is the **period of
#: performance**, not ``submitDate``: the question is who needs an account for
#: a handoff, and a handoff only ever lands against an allocation that is
#: live. A request whose allocation ended before the window opened cannot
#: produce one.
DEFAULT_WINDOW_DAYS = 90

#: ⚠️ Requests to enumerate. ``Approved`` is the default because it is the
#: only status that produces a handoff — but the full filter vocabulary is
#: reachable (``Submitted``, ``Under Review``, ``Incomplete``, ``Rejected``),
#: and ``all`` drops the filter entirely, so a sweep can surface the pipeline
#: ahead of approval when someone wants to look.
DEFAULT_STATUS = 'Approved'

#: People to re-fetch per run for identity detail. Each is one round trip,
#: and the cache means the card's own renders do not repeat them.
DEFAULT_MAX_PEOPLE = 250

#: Cap on rows echoed into the ledger row. `detail` is TEXT and the runner
#: truncates the JSON at 60 kB.
_MAX_REPORTED = 100


def _positive_int(env: Optional[dict], key: str, default: int) -> int:
    """Read a positive int from the environment, per run.

    Same shape and reasoning as `xras_notices.xras_email_max`: read per run so
    a `values.yaml` change lands on the next dispatch rather than the next pod
    restart, and a zero, negative or unparseable value is **refused rather than
    obeyed** — zero pages would mean "look at nothing" while still reporting
    success, which is indistinguishable from a broken query.
    """
    raw = (env if env is not None else os.environ).get(key)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def max_pages(env: Optional[dict] = None) -> int:
    """Page budget, from ``$SAM_TASKS_XRAS_SWEEP_MAX_PAGES``."""
    return _positive_int(env, 'SAM_TASKS_XRAS_SWEEP_MAX_PAGES', DEFAULT_MAX_PAGES)


def max_people(env: Optional[dict] = None) -> int:
    """Person-refresh budget, from ``$SAM_TASKS_XRAS_SWEEP_MAX_PEOPLE``."""
    return _positive_int(env, 'SAM_TASKS_XRAS_SWEEP_MAX_PEOPLE',
                         DEFAULT_MAX_PEOPLE)


def window_days(env: Optional[dict] = None) -> int:
    """Window size, from ``$SAM_TASKS_XRAS_SWEEP_WINDOW_DAYS``."""
    return _positive_int(env, 'SAM_TASKS_XRAS_SWEEP_WINDOW_DAYS',
                         DEFAULT_WINDOW_DAYS)


def sweep_status(env: Optional[dict] = None) -> Optional[str]:
    """Which request status to enumerate; ``None`` means every status.

    ``all`` (any case) drops the filter. An unrecognised value falls back to
    the default rather than being passed through — the API rejects an unknown
    ``status`` with a 4xx, which the client turns into an outage, and a typo
    in a chart value must not take the sweep down.
    """
    raw = str((env if env is not None else os.environ)
              .get('SAM_TASKS_XRAS_SWEEP_STATUS', '') or '').strip()
    if not raw:
        return DEFAULT_STATUS
    if raw.casefold() == 'all':
        return None
    # Deferred: `scheduling/` must not pay for `requests` on the CLI's --list
    # path, which imports this package only to print a table.
    from sam.integration.xras_api.client import REQUEST_STATUSES
    return raw if raw in REQUEST_STATUSES else DEFAULT_STATUS


def overlaps_window(payload: dict, *, window_start: date) -> bool:
    """Was this request's allocation still open when the window began?

    **One-sided on purpose.** The predicate is ``endDate is None or endDate >=
    window_start`` — it drops what has already ended and keeps everything
    live *or future*. A two-sided overlap (also requiring ``beginDate <=
    window_end``) would discard requests whose period starts next quarter,
    and those are precisely the population Feed B exists to reach: a PI with
    no SAM account and months of lead time to fix it.

    A missing or unparseable ``endDate`` counts as open — those are the newest
    rows, in-flight or not yet dated.

    Pure and injectable so the boundary never floats: a test that read the
    wall clock would pass or fail depending on the day it ran. Dates arrive as
    ``YYYY-MM-DD`` and are compared as dates; no timezone reasoning applies to
    a calendar date.
    """
    raw = payload.get('endDate')
    if not raw:
        return True
    try:
        return date.fromisoformat(str(raw)[:10]) >= window_start
    except ValueError:
        return True


@task(name='xras_sweep',
      schedule=SCHEDULE,
      needs=('sam',),
      # Drives the LEASE, not a timeout. max(3x20min, 900s) = 3600s must exceed
      # the CronJob's activeDeadlineSeconds (3000s), or a run killed by the pod
      # deadline becomes reclaimable while still running and two sweeps race.
      # `TaskContext` exposes no ledger handle, so the task cannot heartbeat
      # instead. The drift test asserts the inequality against values.yaml.
      expected_runtime=timedelta(minutes=20),
      # The 6h default. A missed nightly slot costs nothing: this task writes
      # no state, so tomorrow's run subsumes today's entirely.
      misfire_grace=timedelta(hours=6),
      description='Enumerate XRAS and diff it against SAM (reads only)')
def xras_sweep(ctx) -> TaskResult:
    """Enumerate Approved XRAS requests and report what SAM is missing."""
    # Deferred: `scheduling/` is imported by the CLI's --list path, which must
    # not pay for the ORM and `requests` to print a table.
    from sam.integration.xras_api import (
        XrasApiClient,
        XrasSourceUnavailable,
        xras_api_configured,
    )
    from sam.integration.xras_api.cache import store_pending_worklist
    from sam.projects.projects import Project
    from sam.queries.xras_accounts import (
        classify_accounts,
        get_account_worklist,
        records_from_report_requests,
        worklist_counts,
    )

    detail = {
        'skipped': False,
        'pages': 0,
        'requests_seen': 0,
        'requests_in_window': 0,
        'window_days': 0,
        'status': '',
        'budget_exhausted': False,
        'pending_push': 0,
        'pending_push_sample': [],
        'accounts': {},
        'people_refreshed': 0,
        'reconciled': 0,
        'published': False,
        'unavailable_errors': 0,
    }

    if not xras_api_configured():
        # The shipped state. A visible skip, not a raise — see the module
        # docstring on why this differs from the notice tasks' guards.
        detail['skipped'] = True
        ctx.logger.info('xras_sweep: outgoing API not configured; skipping')
        return TaskResult(detail=detail,
                          message='skipped — XRAS outgoing API not configured')

    session = ctx.sam_session
    client = XrasApiClient.from_environment()
    page_budget = max_pages()
    status = sweep_status()
    window = window_days()
    window_start = to_local_naive(
        ctx.occurrence, ZoneInfo(SCHEDULE.tz)).date() - timedelta(days=window)
    detail['window_days'] = window
    detail['status'] = status or 'all'

    # ── 1. enumerate ────────────────────────────────────────────────────
    payloads = []
    try:
        for page in client.iter_request_pages(status=status,
                                              page_size=PAGE_SIZE,
                                              max_pages=page_budget):
            detail['pages'] += 1
            payloads.extend(page)
    except XrasSourceUnavailable as exc:
        # Partial pages are still worth reporting: a diff over what we did
        # read is a subset of the truth, not a wrong answer.
        detail['unavailable_errors'] += 1
        ctx.logger.warning('xras_sweep: enumeration failed after %d page(s): %s',
                           detail['pages'], exc)

    detail['requests_seen'] = len(payloads)
    detail['budget_exhausted'] = detail['pages'] >= page_budget

    # ── 1b. drop what had already ended when the window opened ──────────
    #
    # Both counts are reported: `requests_seen` says how much was read,
    # `requests_in_window` how much was work. Reporting only the second would
    # make a narrowed window look like a shrinking problem.
    payloads = [p for p in payloads
                if overlaps_window(p, window_start=window_start)]
    detail['requests_in_window'] = len(payloads)

    # ── 2. dropped / pending pushes ─────────────────────────────────────
    numbers = {str(p.get('requestNumber')).strip() for p in payloads
               if p.get('requestNumber')}
    pending_set: set = set()
    if numbers:
        known = {code for (code,) in session.query(Project.projcode)
                 .filter(Project.projcode.in_(sorted(numbers))).all()}
        pending_set = numbers - known
        pending = sorted(pending_set)
        detail['pending_push'] = len(pending)
        detail['pending_push_sample'] = pending[:_MAX_REPORTED]

    # ── 3. classify the rosters of what has NOT been pushed ─────────────
    #
    # ⚠️ **Only the pending set, and this is the difference between a queue
    # and a census.** Measured against the live process, 90-day window:
    #
    #     every Approved request, no window   2,180 accounts "needed"
    #     + window                              542
    #     + not-yet-pushed only                  21   <- 10 of 11 absent
    #                                                    rows are placeholders
    #
    # A request whose project already exists has **already had its handoff**.
    # Its roster's inactive members are ordinary attrition — a grad student
    # who left, a PI whose account was locked — not accounts anyone must
    # create for a handoff to succeed, which is what this worklist is for
    # (§ 1). Classifying them buried the eleven real rows under five hundred.
    #
    # Restricting to the pending set is also strictly cheaper: 40 rosters
    # instead of 1,640.
    pending_payloads = [p for p in payloads
                        if str(p.get('requestNumber') or '').strip() in pending_set]
    enumerated = classify_accounts(session,
                                   records_from_report_requests(pending_payloads))
    detail['accounts'] = worklist_counts(enumerated)
    detail['accounts_sample'] = [r['username'] for r in enumerated][:_MAX_REPORTED]

    # ── 4. warm the person cache for the card's morning renders ────────
    #
    # Feed A only: Feed B carried its person objects inline, so re-fetching
    # them would be a round trip for something already in hand.
    from sam.integration.xras_api.people import get_person

    people_budget = max_people()
    for row in get_account_worklist(session, validate=False)[:people_budget]:
        try:
            person = get_person(row['username'])
        except XrasSourceUnavailable as exc:
            detail['unavailable_errors'] += 1
            ctx.logger.warning('xras_sweep: person refresh failed (%s)', exc)
            break
        detail['people_refreshed'] += 1
        if person and person.get('isReconciled'):
            # ⚠️ NOT a closure. XRAS having linked this username to a real
            # identity says nothing about whether SAM has a usable row — the
            # smoke measured 9 of 9 worklist rows reconciled and still needing
            # work. It is reported because it says the account can be created
            # from real detail, not because anything closed.
            detail['reconciled'] += 1

    # ── 5. publish for the dashboard ────────────────────────────────────
    #
    # Send first, record second — the ledger row must not claim a snapshot the
    # tab cannot read. A disabled bucket is not an error: the findings are
    # still in `detail`, and the tab renders its "no sweep data yet" state.
    detail['published'] = store_pending_worklist({
        # A datetime, not an ISO string: this payload is pickled into the
        # cache and read straight by a Jinja `fmt_date`, whereas the ledger's
        # `detail` beside it is JSON and must stay stringly-typed. The two
        # have different serialisation contracts and this is the seam.
        'generated_at': to_local_naive(ctx.occurrence, ZoneInfo(SCHEDULE.tz)),
        'window_days': window,
        'status': detail['status'],
        'requests_seen': detail['requests_seen'],
        'requests_in_window': detail['requests_in_window'],
        'budget_exhausted': detail['budget_exhausted'],
        'pending_push': detail['pending_push'],
        'pending_push_sample': detail['pending_push_sample'],
        'counts': detail['accounts'],
        'rows': enumerated,
    })

    counts = detail['accounts']
    message = (f"{detail['requests_in_window']}/{detail['requests_seen']} in-window request(s), "
               f"{detail['pending_push']} pending push, "
               f"{counts.get('total', 0)} account(s) needed, "
               f"{detail['reconciled']} reconciled in XRAS")
    ctx.logger.info('xras_sweep: %s', message)

    # "0 findings, succeeded" must be distinguishable from "did not look" —
    # which is what `pages`, `requests_seen` and `skipped` are for.
    return TaskResult(detail=detail, message=message,
                      partial_failures=detail['unavailable_errors'])
