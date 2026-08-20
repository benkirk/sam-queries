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

#: Opportunity mapping rows writable per run. See :func:`map_max`.
DEFAULT_MAP_MAX = 20

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


def map_max(env: Optional[dict] = None) -> int:
    """Opportunity rows the sweep may write per run, from ``$SAM_TASKS_XRAS_MAP_MAX``.

    A **blast-radius bound, not a target**, on the only thing this task writes.
    The steady state is zero or one — roughly four new opportunities a year — so
    a run proposing dozens means the derivation has gone wrong, and the cap turns
    that from a table rewrite into a truncated run that says so in `detail`.

    The one legitimately large run is the first, which backfills whatever the
    seed left unmapped. Rehearse it with ``--dry-run`` before it writes.
    """
    return _positive_int(env, 'SAM_TASKS_XRAS_MAP_MAX', DEFAULT_MAP_MAX)


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


def _map_new_opportunities(ctx, session, client, unmapped_ids, detail,
                           *, known=()) -> None:
    """Map the opportunities XRAS and the ladder agree about; report the rest.

    ⚠️ **The only write this task performs**, and the only one it may perform.
    Everything else here is a read published to a cache bucket.

    Why writing at all does not break the design's central promise: ingestion
    still reads one local table and never calls out, so handling an inbound
    action does not depend on ``XRAS_OUTGOING_ENABLED`` or on ``api.xras.org``
    being up. This runs out of band; if it stops, the map stops growing and the
    free-text ladder covers the gap exactly as it did before the table existed.

    What it must never do is *overwrite*. A ``manual`` row is a human's answer to
    a question the API cannot settle — there are two, both documented in
    ``sam.xras.opportunity_types`` — so this inserts only where no row exists.
    That is checked against the database rather than against ``source``, which
    keeps the property even for rows some future process adds.

    The decision itself lives in ``sam.queries.xras_actions`` so the CLI and the
    tests share it rather than reimplementing it; this function is only budget,
    persistence and reporting.

    ⚠️ **Must stay above the ``@task`` decorator.** A module-level function
    defined between ``@task(...)`` and ``def xras_sweep`` gets registered as the
    task body — silently, since the name is a decorator argument. It fails only
    at dispatch. ``test_the_decorator_is_bound_to_the_task_body`` is the guard.
    """
    from sam.integration.xras_api import XrasSourceUnavailable
    from sam.integration.xras import SOURCE_SWEEP, XrasOpportunityAllocationType
    from sam.queries.xras_actions import propose_opportunity_mapping

    # Anything the open list already handed us needs no second round trip; only
    # the rest — the closed ones — has to be resolved by id.
    have = {o['opportunityId']: o for o in (known or ())
            if isinstance(o, dict) and o.get('opportunityId') is not None}
    wanted = [i for i in unmapped_ids if i not in have]
    payloads = [have[i] for i in unmapped_ids if i in have]

    try:
        payloads += client.get_opportunities(wanted)
    except XrasSourceUnavailable as exc:
        # The enumeration and the account worklist are already in hand and worth
        # reporting; failing to resolve opportunities costs only this step, and
        # the next slot retries the same ids.
        detail['unavailable_errors'] += 1
        ctx.logger.warning('xras_sweep: opportunity resolve failed: %s', exc)
        if not payloads:
            return
        # Keep going on the open ones already in hand — a partial answer is a
        # subset of the truth here, not a wrong one.

    proposal = propose_opportunity_mapping(session, payloads)
    detail['opportunities_needing_review'] = proposal['review'][:_MAX_REPORTED]
    detail['opportunities_unknown_pair'] = proposal['unknown_pair'][:_MAX_REPORTED]

    budget = map_max()
    agreed = proposal['agree']
    if len(agreed) > budget:
        # ⚠️ **Newest first when the cap bites**, not lowest id first.
        # `opportunity_id` ascends with time, and the rows worth having soonest
        # are the ones an imminent action might reference — a newly-posted
        # opportunity is the entire reason this feature exists. A historical
        # backfill has no pending handoffs and can wait for the next slot, so
        # letting it crowd out the new arrival would invert the priority.
        # (Only the *selection* is newest-first; the writes below stay in id
        # order so the log reads predictably.)
        ctx.logger.warning(
            'xras_sweep: %d opportunities agreed but the per-run cap is %d; '
            'taking the %d newest', len(agreed), budget, budget)
        detail['map_budget_exhausted'] = True
        newest = sorted(agreed, key=lambda e: e['opportunity_id'], reverse=True)
        agreed = sorted(newest[:budget], key=lambda e: e['opportunity_id'])

    written = []
    for entry in agreed:
        opportunity_id = entry['opportunity_id']
        # Re-checked here rather than trusted from the audit above: that ran
        # before the network call, and this is the assertion that a manual row is
        # never clobbered.
        exists = (session.query(XrasOpportunityAllocationType.opportunity_id)
                  .filter(XrasOpportunityAllocationType.opportunity_id == opportunity_id)
                  .first())
        if exists:
            continue
        XrasOpportunityAllocationType.create(
            session,
            opportunity_id=opportunity_id,
            allocation_type_id=entry['allocation_type_id'],
            opportunity_name=(entry.get('opportunity_name') or None),
            source=SOURCE_SWEEP)
        written.append(entry)
        ctx.logger.info('xras_sweep: mapped opportunity %s -> %s/%s',
                        opportunity_id, *entry['pair'])

    detail['opportunities_written'] = len(written)
    detail['opportunities_written_sample'] = [
        {'opportunity_id': w['opportunity_id'], 'pair': list(w['pair'])}
        for w in written[:_MAX_REPORTED]]

    # No commit here, deliberately: the runner commits `ctx.sam_session` on
    # success and rolls it back on failure or `--dry-run`, which is what makes a
    # dry run a full rehearsal that reports exactly what it would have written.


@task(name='xras_sweep',
      schedule=SCHEDULE,
      needs=('sam',),
      # Drives the LEASE, not a timeout. max(3x20min, 900s) = 3600s must exceed
      # the CronJob's activeDeadlineSeconds (3000s), or a run killed by the pod
      # deadline becomes reclaimable while still running and two sweeps race.
      # `TaskContext` exposes no ledger handle, so the task cannot heartbeat
      # instead. The drift test asserts the inequality against values.yaml.
      expected_runtime=timedelta(minutes=20),
      # The 6h default. A missed slot costs little: the enumeration window is
      # rolling, so the next run subsumes it. The one piece of state this now
      # writes — opportunity mapping rows — is insert-if-absent, so a skipped
      # slot delays a row rather than losing it.
      misfire_grace=timedelta(hours=6),
      description='Enumerate XRAS, diff it against SAM, map new opportunities')
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
    from sam.queries.xras_actions import (audit_opportunity_mapping,
                                          propose_opportunity_mapping)

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
        'opportunities_open': 0,
        'opportunities_seen': 0,
        'opportunities_unmapped': 0,
        'opportunities_unmapped_sample': [],
        'opportunities_written': 0,
        'opportunities_written_sample': [],
        'opportunities_needing_review': [],
        'opportunities_unknown_pair': [],
        'people_refreshed': 0,
        'reconciled': 0,
        'published': False,
        'publish_backend': '',
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

    # ── 1c. opportunities SAM cannot resolve to an allocation type ──────
    #
    # Free: every `reports/requests` payload already carries `opportunityId`,
    # so this costs no round trips — which is the whole reason it lives here
    # rather than behind a `/v1/opportunities` fetch.
    #
    # ⚠️ The reports payload spells it **snake_case** `opportunity_name`,
    # while the inbound action wire spells the sibling field
    # `opportunityName`. Only the id is read here, but the two vocabularies
    # meeting in one task is exactly the shape of bug that cost this repo a
    # sprint on `key` vs `resourceRepositoryKey`.
    #
    # **Read-only, and deliberately not published.** An unmapped id is not a
    # fault — it falls through to the extractor ladder, which is what resolved
    # it before the map existed. It is reported so a *new* opportunity is
    # visible here before any action is pushed against it, which is the lead
    # time the map exists to buy. The sweep must never write the table.
    seen_ids = {int(oid) for p in payloads
                if (oid := p.get('opportunityId')) is not None}

    # ⚠️ **Requests cannot mention an opportunity that has none**, so the
    # enumeration above is blind to a brand-new one until its first request is
    # *approved* — which can be weeks after it is posted, and is precisely the
    # lead time this map exists to buy. The open list closes that gap for one
    # cheap call, and it arrives with `allocationTypeInfo` and `panels` already
    # attached, so an open opportunity never needs the by-id round trip.
    #
    # Measured 2026-08-20: `Large Allocation (University) - Fall 2026` (535388)
    # was returned here the moment it was posted, while the Approved
    # enumeration knew nothing about it.
    #
    # Guarded rather than fatal: this is an enrichment, and losing it must not
    # cost the request-derived half.
    open_payloads = []
    try:
        open_payloads = client.get_open_opportunities()
    except XrasSourceUnavailable as exc:
        detail['unavailable_errors'] += 1
        ctx.logger.warning('xras_sweep: open opportunity list failed: %s', exc)
    detail['opportunities_open'] = len(open_payloads)
    seen_ids |= {int(oid) for o in open_payloads
                 if isinstance(o, dict) and (oid := o.get('opportunityId')) is not None}

    if seen_ids:
        audit = audit_opportunity_mapping(session, opportunity_ids=seen_ids)
        detail['opportunities_seen'] = len(seen_ids)
        detail['opportunities_unmapped'] = len(audit['unmapped_ids'])
        detail['opportunities_unmapped_sample'] = audit['unmapped_ids'][:_MAX_REPORTED]

        if audit['unmapped_ids']:
            _map_new_opportunities(ctx, session, client, audit['unmapped_ids'],
                                   detail, known=open_payloads)

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
    backend = store_pending_worklist({
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

    # ⚠️ `published` means "the dashboard can read this", NOT "a write
    # returned". The bucket falls back to a per-worker in-process cache when
    # CACHE_REDIS_URL is unset or Redis is unreachable, and this task runs in a
    # ONE-SHOT pod — so a process-local write succeeds and then dies with the
    # pod. The first production run did exactly that and reported success.
    detail['publish_backend'] = backend
    detail['published'] = backend == 'redis'
    if backend != 'redis':
        ctx.logger.warning(
            'xras_sweep: worklist went to the %s cache, so the dashboard tab '
            'will NOT see it (CACHE_REDIS_URL unset or Redis unreachable?)',
            backend)

    counts = detail['accounts']
    message = (f"{detail['requests_in_window']}/{detail['requests_seen']} in-window request(s), "
               f"{detail['pending_push']} pending push, "
               f"{counts.get('total', 0)} account(s) needed, "
               f"{detail['reconciled']} reconciled in XRAS, "
               f"{detail['opportunities_unmapped']}/{detail['opportunities_seen']} "
               f"opportunity id(s) unmapped, "
               f"{detail['opportunities_written']} mapped automatically, "
               f"{len(detail['opportunities_needing_review'])} needing review")
    ctx.logger.info('xras_sweep: %s', message)

    # "0 findings, succeeded" must be distinguishable from "did not look" —
    # which is what `pages`, `requests_seen` and `skipped` are for.
    return TaskResult(detail=detail, message=message,
                      partial_failures=detail['unavailable_errors'])
