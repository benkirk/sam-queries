"""``xras_sweep`` — enumerate XRAS nightly and diff it against SAM.

Daily 03:30 Mountain. The first task that calls **out** to XRAS rather than
reading only SAM's own tables, and the third declaring ``needs=('sam',)``.

What it is for
--------------
The account-creation worklist built from ``xras_action_log`` can only see
people XRAS has already pushed. ``GET /v1/reports/requests`` sees the whole
NCAR process, so this task reaches three things the card cannot:

1. **People ahead of the push** — a brand-new PI on a solo New request,
   connected to nobody SAM knows, is visible here *before* the action arrives.
   That is the hardest population to prepare for and the one manual account
   creation most needs lead time on.
2. **Dropped or pending pushes** — a set difference between the Approved
   requests' ``requestNumber`` and ``project.projcode``. Cheap, because both
   sides are already in hand.
3. **Closure** — re-fetching ``/v1/people`` for everyone currently on the
   worklist refreshes ``isReconciled``, which is how an item closes itself,
   and leaves a warm cache for the morning's first card render.

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
from datetime import timedelta
from typing import Optional

from scheduling.registry import TaskResult, task
from scheduling.schedules import Daily

#: Overnight on purpose: nothing here is time-critical, the enumeration is the
#: heaviest outbound call SAM makes, and a warm people-cache at 03:30 is still
#: warm for the first card render of the working day.
SCHEDULE = Daily(3, 30, tz='America/Denver')

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

#: People to re-fetch per run for the closure signal. Each is one round trip,
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
        'budget_exhausted': False,
        'pending_push': 0,
        'pending_push_sample': [],
        'accounts': {},
        'people_refreshed': 0,
        'closures': 0,
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

    # ── 1. enumerate ────────────────────────────────────────────────────
    payloads = []
    try:
        for page in client.iter_request_pages(status='Approved',
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

    # ── 2. dropped / pending pushes ─────────────────────────────────────
    numbers = {str(p.get('requestNumber')).strip() for p in payloads
               if p.get('requestNumber')}
    if numbers:
        known = {code for (code,) in session.query(Project.projcode)
                 .filter(Project.projcode.in_(sorted(numbers))).all()}
        pending = sorted(numbers - known)
        detail['pending_push'] = len(pending)
        detail['pending_push_sample'] = pending[:_MAX_REPORTED]

    # ── 3. classify what the enumeration names ──────────────────────────
    enumerated = classify_accounts(session,
                                   records_from_report_requests(payloads))
    detail['accounts'] = worklist_counts(enumerated)
    detail['accounts_sample'] = [r['username'] for r in enumerated][:_MAX_REPORTED]

    # ── 4. warm the closure signal for the card's morning renders ───────
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
            # XRAS reconciled them; the worklist item should now close.
            detail['closures'] += 1

    counts = detail['accounts']
    message = (f"{detail['requests_seen']} request(s), "
               f"{detail['pending_push']} pending push, "
               f"{counts.get('total', 0)} account(s) needed, "
               f"{detail['closures']} closure(s)")
    ctx.logger.info('xras_sweep: %s', message)

    # "0 findings, succeeded" must be distinguishable from "did not look" —
    # which is what `pages`, `requests_seen` and `skipped` are for.
    return TaskResult(detail=detail, message=message,
                      partial_failures=detail['unavailable_errors'])
