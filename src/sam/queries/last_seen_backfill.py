"""Per-(user, machine) activity windows from SAM's job charge summaries.

Seeds ``system_status.user_last_seen`` with the history that predates live
collection (``sam-admin last-seen --backfill``). Summaries are daily and
Mountain-dated, so a backfilled window is day-granular: each date becomes 00:00.
Not exported from ``sam.queries``: it is a one-shot operator path.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from sam.summaries.comp_summaries import CompChargeSummary
from sam.summaries.dav_summaries import DavChargeSummary
from sam.summaries.hpc_summaries import HPCChargeSummary

CHARGE_SUMMARIES = (CompChargeSummary, DavChargeSummary, HPCChargeSummary)

#: ``status_users.username`` width; SAM allows 35.
MAX_USERNAME = 32


def system_for_machine(machine: str) -> str:
    """SAM charge ``machine`` -> status ``systems.name``: 'Casper-gpu' -> 'casper'."""
    return machine.strip().lower().removesuffix('-gpu')


def charge_summary_windows(session: Session, model) -> list[tuple]:
    """(username, machine, first_date, last_date) per user and machine; username may be None."""
    who = func.coalesce(model.username, model.act_username)
    return (session.query(who, model.machine,
                          func.min(model.activity_date), func.max(model.activity_date))
            .group_by(who, model.machine)
            .all())


def plan_backfill(rows) -> tuple[dict, dict, dict]:
    """Merge windows into ``{system: {username: (first, last)}}``.

    Returns ``(plan, machines, skipped)``: ``machines`` maps each SAM machine to
    its system and user count, ``skipped`` counts the groups dropped and why.
    """
    plan: dict[str, dict[str, tuple[datetime, datetime]]] = {}
    machines: dict[str, dict] = {}
    skipped = {'no_username': 0, 'username_too_long': 0}
    for username, machine, first, last in rows:
        system = system_for_machine(machine)
        entry = machines.setdefault(machine, {'system': system, 'users': 0})
        if not username:
            skipped['no_username'] += 1
            continue
        if len(username) > MAX_USERNAME:
            skipped['username_too_long'] += 1
            continue
        entry['users'] += 1
        first, last = _midnight(first), _midnight(last)
        users = plan.setdefault(system, {})
        if username in users:
            f, l = users[username]
            users[username] = (min(f, first), max(l, last))
        else:
            users[username] = (first, last)
    return plan, machines, skipped


def _midnight(d) -> datetime:
    return d if isinstance(d, datetime) else datetime.combine(d, datetime.min.time())
