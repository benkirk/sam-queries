"""Scheduled tasks: occurrence predicates, a ledger, and a runner.

A peer of `sam/`, `system_status/` and `cli/` rather than a subpackage of any
of them, deliberately:

* not `sam.scheduling` — `src/sam/` is the domain package for SAM MySQL and
  imports nothing from `system_status` today. The ledger lives in
  `system_status`, so putting the scheduler under `sam/` would invert that
  dependency: a scheduler in the SAM-DB package writing to the status DB.
* not `cli.tasks` — the CLI is a presentation layer. A future always-on daemon
  must be able to ``from scheduling.runner import run_due`` without importing
  Click, Rich, or the `sam-admin` group callback. `src/cli/tasks/` still exists
  and holds the command classes and display functions.

Nothing in this package imports Click, Flask, or `kubernetes`.

Design: ``docs/plans/SCHEDULED_TASKS.md``.
"""

from scheduling.schedules import (
    CronExpr,
    Daily,
    Hourly,
    MonthlyDay,
    Schedule,
    Weekly,
    occurrence_key,
)

__all__ = [
    'CronExpr',
    'Daily',
    'Hourly',
    'MonthlyDay',
    'Schedule',
    'Weekly',
    'occurrence_key',
]
