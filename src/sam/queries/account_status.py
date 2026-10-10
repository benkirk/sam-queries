"""Legacy ``DefaultAccountStatusCalculator`` statuses and ``NDayUsagePeriod`` threshold math.

Shared by fstree and the HEUV usage report. Legacy's overall precedence is
No Account > Waiting / Expired / No Allocation (no active allocation) > Disabled
(inactive project) > Normal (charging-exempt project, chargeable resource) >
the parent's non-Normal charging status > Overspent > Exceed Two / One Threshold
> Normal. :func:`lifecycle_status` covers the first three, :func:`charge_status`
the last three; the exemption and parent cascade are the caller's.
"""

import math
from datetime import datetime
from typing import Optional

NORMAL = 'Normal'
OVERSPENT = 'Overspent'
EXCEED_ONE = 'Exceed One Threshold'
EXCEED_TWO = 'Exceed Two Thresholds'
EXPIRED = 'Expired'
WAITING = 'Waiting'
NO_ALLOCATION = 'No Allocation'
DISABLED = 'Disabled'
NO_ACCOUNT = 'No Account'

#: The statuses computed from charges; legacy's usage report sets thresholdLimited on these.
CHARGE_STATUSES = frozenset({NORMAL, OVERSPENT, EXCEED_ONE, EXCEED_TWO})

#: (period days, which account threshold) pairs, legacy ``UsageThresholdPeriod``.
THRESHOLD_PERIODS = (30, 90)


def java_round(value: float) -> int:
    """Java ``Math.round``: half-up (``floor(x + 0.5)``), unlike Python's half-even ``round``."""
    return math.floor(value + 0.5)


def legacy_divisor(start: datetime, end: datetime) -> int:
    """Legacy ``inclusiveDays(start, end) - 1``; zero for a one-day allocation."""
    return (end.date() - start.date()).days


def fstree_divisor(start: datetime, end: Optional[datetime], now: datetime) -> int:
    """fstree's divisor: one day shorter than :func:`legacy_divisor` (plan HEUV_API_PORT.md §7 g)."""
    return max(((end or now) - start).days - 1, 1)


def threshold_allocation(period_days: int, amount: float, divisor: float) -> float:
    """The steady-burn share of *amount* for one window."""
    return period_days * amount / divisor


def exceeded_count(amount: float, thresholds, windows, divisor: float) -> int:
    """How many windows' charges exceed their ``threshold%`` of the steady-burn share.

    *thresholds* and *windows* are parallel to :data:`THRESHOLD_PERIODS`; a None threshold is skipped.
    """
    n = 0
    for period, pct, charges in zip(THRESHOLD_PERIODS, thresholds, windows):
        if pct is not None and charges > threshold_allocation(period, amount, divisor) * (pct / 100.0):
            n += 1
    return n


def charge_status(adjusted_usage: float, amount: Optional[float], thresholds, windows,
                  divisor: Optional[float]) -> str:
    """Overspent, Exceed Two/One Threshold or Normal; *divisor* None skips the thresholds."""
    if amount is None:
        return NORMAL
    if adjusted_usage > amount:
        return OVERSPENT
    n = 0
    if divisor is not None and any(t is not None for t in thresholds):
        n = exceeded_count(amount, thresholds, windows, divisor)
    if n == 1:
        return EXCEED_ONE
    if n >= 2:
        return EXCEED_TWO
    return NORMAL


def lifecycle_status(*, has_account: bool, has_active: bool, has_future: bool,
                     has_prior: bool, project_active: bool) -> Optional[str]:
    """The status that outranks charges, or None when the charges decide."""
    if not has_account:
        return NO_ACCOUNT
    if not has_active:
        return WAITING if has_future else EXPIRED if has_prior else NO_ALLOCATION
    if not project_active:
        return DISABLED
    return None
