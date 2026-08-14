"""``deactivate_expired_projects`` — the monthly sweep behind the admin button.

The admin Projects page has had a manual **Deactivate Expired** button for as
long as anyone remembers, and it only ever ran when somebody remembered to press
it. This is that button, on a schedule.

⚠️ **A task computes from ``ctx.occurrence``, never the wall clock.** Said again
here because this task has *two* occurrence-derived values, not one: the
reference instant for "90 days expired", and the ``inactivate_time`` stamp. Get
either from ``datetime.now()`` and a run dispatched late stops agreeing with the
punctual one it replaced.

The selection window and the write both live in
``sam.queries.expirations`` / ``sam.manage`` and are shared with the button, so
the two cannot drift. See ``docs/plans/implemented/SCHEDULED_TASKS.md`` § 6.2.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

from scheduling.registry import TaskResult, task
from scheduling.schedules import MonthlyDay, to_local_naive

#: The 3rd of the month at 04:30 Mountain. Disjoint from the 02:15 nightly prune
#: and the Monday 09:00 expiration notices, and otherwise arbitrary — nothing
#: downstream cares which day this lands on, only that it is once a month.
#:
#: `MonthlyDay` clamps a day past the end of a short month rather than skipping
#: it (so 31 means "end of month"); day 3 never clamps, but a future edit might.
SCHEDULE = MonthlyDay(3, 4, 30, tz='America/Denver')

#: Every facility, deliberately — and note this is the one place this task
#: differs in audience from `expiration_notices`, which pins ('UNIV', 'WNA')
#: because it mails external PIs. Deactivation sends nothing, so there is no
#: reason to exempt internal projects. `None` means "no facility filter"; it is
#: stated here rather than defaulted at the call site so the audience is
#: greppable next to the schedule.
FACILITIES: Optional[Tuple[str, ...]] = None

#: Cap on projcodes echoed into the ledger row. `detail` is TEXT and the runner
#: truncates the JSON at 60 kB; a run that deactivated thousands should say so
#: without pushing the counts out of the record. Same shape as
#: `expiration_notices._MAX_REPORTED_FAILURES`.
_MAX_REPORTED = 200


@task(name='deactivate_expired_projects',
      schedule=SCHEDULE,
      needs=('sam',),
      # Drives the LEASE, not a timeout. The resulting 900s floor is SHORTER
      # than the CronJob's activeDeadlineSeconds (3000s) — the inverse of
      # `expiration_notices`, which has to inflate this to 20 min so a killed
      # send is never reclaimed mid-flight. Harmless here: a reclaimed
      # deactivation is transactional, and the second run's query already
      # excludes whatever the first one deactivated. Copying that task's
      # 20-minute value would be ceremony with no hazard behind it.
      expected_runtime=timedelta(minutes=2),
      # 7 days, not the 6h default. Past the grace the runner records a
      # `skipped`/misfire row INSTEAD of running, and that row settles the slot
      # — so for a monthly task one misfire forfeits the whole month, and 6h
      # would forfeit it to a weekend outage.
      #
      # Not unbounded either, and that is the policy: `CatchUp.SKIP` means
      # `last_occurrence` is always the most recent slot, so lateness is bounded
      # at ~31 days for this schedule; a grace above that would make the misfire
      # branch unreachable. A week covers realistic outages, and past a week
      # rolling into next month is the right answer — the work is cumulative
      # (next month's run does everything this one would have, plus more) and
      # the `skipped` row makes a multi-week outage visible rather than letting
      # it discharge silently.
      misfire_grace=timedelta(days=7),
      description='Deactivate projects whose allocations expired 90+ days ago')
def deactivate_expired_projects(ctx) -> TaskResult:
    """Deactivate projects whose most recent allocation expired long enough ago."""
    # Deferred: `scheduling/` is imported by the CLI's --list path, which must
    # not pay for the ORM to print a table.
    from sam.manage import deactivate_projects
    from sam.queries.expirations import (
        DEACTIVATION_MIN_DAYS_EXPIRED,
        get_projects_with_expired_allocations,
        unique_projects,
    )

    # ONE conversion, and the zone is read off SCHEDULE rather than repeated as
    # a literal. `ctx.occurrence` is naive UTC; `Allocation.end_date` and
    # `Project.inactivate_time` are both naive Mountain, so the raw value would
    # shift the window by 6-7 hours and stamp projects as inactive since a time
    # that has not happened yet — rendered as "since <future date>" on the user
    # project card.
    #
    # No truncation to local midnight, unlike `expiration_notices.window_start`:
    # there the occurrence defines selection *bands* that must tile identically
    # across a re-run. Here it is a stamp, and nothing selects on it.
    slot = to_local_naive(ctx.occurrence, ZoneInfo(SCHEDULE.tz))

    session = ctx.sam_session

    selected = get_projects_with_expired_allocations(
        session,
        min_days_expired=DEACTIVATION_MIN_DAYS_EXPIRED,
        max_days_expired=None,
        facility_names=list(FACILITIES) if FACILITIES else None,
        now=slot,
    )
    # The result's shape is one row per (project, allocation). Today this query
    # pins one allocation per project so the collapse is a no-op — but count
    # PROJECTS explicitly anyway, or a later swap to `get_all_expiring_allocations`
    # would make `selected` report allocations while `deactivated` reports
    # projects, and the two would look like a bug rather than a units mismatch.
    projects = unique_projects(selected)

    ctx.logger.info('as of %s: %d allocation row(s) -> %d project(s) '
                    'expired %d+ days',
                    slot.isoformat(), len(selected), len(projects),
                    DEACTIVATION_MIN_DAYS_EXPIRED)

    # No commit and no `management_transaction`: the runner owns the
    # transaction and commits via `close_sessions(commit=True)`.
    #
    # ⚠️ Do NOT add a `ctx.dry_run` branch here expecting `--dry-run` to reach
    # it. `run_due` short-circuits at the CLAIM (runner.py:164) and never calls
    # `_execute` under dry_run, so `ctx.dry_run` is always False inside a task
    # body. `--dry-run` answers "would this slot be claimed?", not "what would
    # this task do?". To preview the *content*, read the set instead — the admin
    # Expired tab and `sam-search project --recent-expirations` share this
    # task's window and facility scope by construction.
    outcome = deactivate_projects(session, projects, when=slot)

    detail = {
        'as_of': slot.isoformat(),
        'min_days_expired': DEACTIVATION_MIN_DAYS_EXPIRED,
        'facilities': list(FACILITIES) if FACILITIES else 'all',
        # Always present, both of them. A month that deactivated nothing — the
        # expected result most months — must be visibly different from a query
        # that quietly stopped matching.
        'selected': len(projects),
        'deactivated': outcome.count,
        'inactivate_time': outcome.when.isoformat(),
        'projcodes': list(outcome.projcodes[:_MAX_REPORTED]),
    }
    if outcome.count > _MAX_REPORTED:
        detail['projcodes_truncated'] = outcome.count - _MAX_REPORTED

    return TaskResult(detail=detail,
                      message=f'{outcome.count} project(s) deactivated')
