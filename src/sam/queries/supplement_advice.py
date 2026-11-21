"""Suggested Supplement amounts for a lead asking for more time or resources.

The need through the chosen end date is the project's daily rate times the days left, less what
remains. The rate is recent use (the trailing window, when the allocation is live and old enough
to show one), else the last allocation spread over its length. The suggestion rounds the need up
to two significant figures and caps it at the lower of the type's limit and the last allocation;
the lead may still ask for more, up to the type's limit. A resource picked by hand starts at the
lower of the type's default and the last allocation. Compute resources only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, Dict, List, Mapping, Optional

RECENT_WINDOW_DAYS = 90
#: A live allocation younger than this has no trailing rate worth reading.
RECENT_MIN_DAYS = 30


@dataclass(frozen=True)
class Advice:
    remaining: Optional[float]
    rate_per_day: Optional[float]
    basis: Optional[str]            # 'recent' | 'last_allocation' | None
    days: int
    last_amount: Optional[float]
    suggest: Optional[float]
    start: Optional[float] = None       # what a hand-picked row fills when nothing is suggested


def round_up_nice(x: float) -> float:
    """*x* rounded up to two significant figures (137,412 -> 140,000), never below 1."""
    if x <= 1:
        return 1.0
    step = max(10 ** (math.floor(math.log10(x)) - 1), 1)
    return float(math.ceil(x / step) * step)


def suggest_amount(rate: Optional[float], days: int, remaining: Optional[float], *,
                   ceiling: Optional[float] = None,
                   last_amount: Optional[float] = None) -> Optional[float]:
    """The suggested Supplement, or ``None`` when the balance already covers *days*."""
    if not rate or days <= 0:
        return None
    need = rate * days - max(remaining or 0.0, 0.0)
    if need <= 0:
        return None
    caps = [c for c in (ceiling, last_amount) if c]
    return min([round_up_nice(need)] + caps)


def start_amount(default: Optional[float], last_amount: Optional[float]) -> Optional[float]:
    """The lower of the type's default and the last allocation, whichever are known."""
    known = [v for v in (default, last_amount) if v]
    return min(known) if known else None


def _end_of(day: date) -> datetime:
    return datetime.combine(day, time(23, 59, 59))


def _accounts_by_resource(project) -> Dict[str, Any]:
    from sam.enums import ResourceTypeName
    out = {}
    for account in project.accounts:
        res = account.resource
        if (account.deleted or res is None or res.resource_type is None
                or not ResourceTypeName.is_compute(res.resource_type.resource_type)):
            continue
        out[res.resource_name] = account
    return out


def _last_allocation(account):
    """``(amount, rate per day)`` of the account's latest allocation, or ``(None, None)``."""
    from sam.xras.handlers._allocations import latest_allocation
    allocation = latest_allocation(account)
    if allocation is None or not allocation.amount or allocation.end_date is None:
        return None, None
    length = (allocation.end_date - allocation.start_date).days + 1
    amount = float(allocation.amount)
    return amount, (amount / length if length > 0 else None)


def _recent_rates(session, projcode: str, now: datetime) -> Dict[str, float]:
    """``{resource: charges per day}`` over the trailing window, for allocations old enough."""
    from sam.queries.rolling_usage import get_project_rolling_usage
    rates = {}
    for name, row in get_project_rolling_usage(session, projcode,
                                               windows=[RECENT_WINDOW_DAYS]).items():
        covered = min(RECENT_WINDOW_DAYS, (now - row['start_date']).days)
        if covered < RECENT_MIN_DAYS:
            continue
        window = row['windows'][RECENT_WINDOW_DAYS]
        charges = window['self_charges'] if row['is_inheriting'] else window['charges']
        rates[name] = (charges or 0.0) / covered
    return rates


def suggest_supplements(session, project, resources: List[Mapping[str, Any]], *,
                        end_date: Optional[date], floor: Optional[date] = None,
                        now: Optional[datetime] = None) -> Dict[int, Advice]:
    """``{xras resource_id: Advice}`` for the annotated line *resources* (``sam_name``, ``limit``).

    *end_date* is the new end, or the current one (*floor*) for a Supplement alone. Remaining is
    read at the floor when the window has ended: an Extension revives what was left.
    """
    from sam.queries.dashboard import usage_by_resource
    now = now or datetime.now()
    end = end_date or floor
    days = (end - now.date()).days if end else 0
    accounts = _accounts_by_resource(project)
    wanted = [r for r in resources if r.get('sam_name') in accounts and not r.get('yes_no')]
    if not wanted:
        return {}
    active_at = min(now, _end_of(floor)) if floor else now
    usage = usage_by_resource(session, [project], active_at=active_at).get(project.project_id, {})
    recent = _recent_rates(session, project.projcode, now)

    advice = {}
    for r in wanted:
        name = r['sam_name']
        last_amount, last_rate = _last_allocation(accounts[name])
        basis, rate = ('recent', recent[name]) if name in recent else \
            ('last_allocation', last_rate) if last_rate else (None, None)
        remaining = (usage.get(name) or {}).get('remaining')
        ceiling = getattr(r.get('limit'), 'max_amount', None)
        advice[r['resource_id']] = Advice(
            remaining=remaining, rate_per_day=rate, basis=basis, days=max(days, 0),
            last_amount=last_amount,
            suggest=suggest_amount(rate, days, remaining, ceiling=ceiling,
                                   last_amount=last_amount),
            start=start_amount(getattr(r.get('limit'), 'default_amount', None), last_amount))
    return advice
