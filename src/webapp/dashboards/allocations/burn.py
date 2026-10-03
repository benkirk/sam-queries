"""Burn math: an allocation's monthly charges against an even pace, and its recent rate.

The even pace spreads the amount uniformly from start to end date; a month's charges over its
share of that is the burn ratio. The recent rate (charges per day over the last 90 days)
projects when the balance runs out. Charges come as get_allocation_burn's ``{yyyymm: charges}``.
No Flask, no matplotlib: the calendar and the Pace chart both read it.
Design record: docs/plans/ALLOCATIONS_TABLE_VIEWS.md (Burn).
"""
from datetime import datetime, timedelta

#: Lower edges of burn classes 1-4 (charges / even-pace share); below the first is class 0.
BURN_EDGES = (0.25, 0.75, 1.25, 2.0)
#: The recent rate's look-back, and how far before its end date a run-out must fall to count.
RUNOUT_LOOKBACK_DAYS = 90
RUNOUT_MARGIN_DAYS = 30


def month_start(d, offset=0):
    m = d.year * 12 + d.month - 1 + offset
    return datetime(m // 12, m % 12 + 1, 1)


def burn_class(ratio):
    return sum(ratio >= edge for edge in BURN_EDGES)


def burn_key():
    """``[(class, label)]`` for the legend, from BURN_EDGES: ``<0.25×`` ... ``≥2×``."""
    edges = [f'{e:g}' for e in BURN_EDGES]
    return ([(0, f'<{edges[0]}×')]
            + [(i + 1, f'{lo}–{hi}×') for i, (lo, hi) in enumerate(zip(edges, edges[1:]))]
            + [(len(edges), f'≥{edges[-1]}×')])


def burn_through(active_at, today):
    """Where shading stops (exclusive): the end of the as-of day, or today's midnight if sooner,
    because a day's charges land the day after."""
    return min(active_at.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1), today)


def burnable(row):
    """An allocation with an even pace: an id, an amount, and a finite span."""
    return (row.get('allocation_id') is not None and row['end_date'] is not None
            and (row.get('total_amount') or 0) > 0 and row['end_date'] > row['start_date'])


def month_shares(row, lo, hi):
    """``(month, yyyymm, cell_start, cell_end, even_share)`` per calendar month meeting
    ``[lo, hi)``; even share = amount x the cell's part of the allocation's span."""
    a_start, a_end, amount = row['start_date'], row['end_date'], row.get('total_amount') or 0.0
    span = (a_end - a_start).total_seconds()
    m = month_start(lo)
    while m < hi:
        nxt = month_start(m, 1)
        c_lo, c_hi = max(m, lo), min(nxt, hi)
        yield m, m.year * 100 + m.month, c_lo, c_hi, amount * (c_hi - c_lo).total_seconds() / span
        m = nxt


def recent_rate(row, months_charges, through, days=RUNOUT_LOOKBACK_DAYS):
    """Charges per day over the ``days`` before ``through``, or since the start date if later.
    A month cell partly inside the look-back counts pro rata; 0.0 before the start date."""
    lo = max(through - timedelta(days=days), row['start_date'])
    if through <= lo:
        return 0.0
    total, m = 0.0, month_start(lo)
    while m < through:
        nxt = month_start(m, 1)
        c_lo, c_hi = max(m, row['start_date']), min(nxt, through)
        inside = (c_hi - max(c_lo, lo)).total_seconds()
        if inside > 0:
            total += months_charges.get(m.year * 100 + m.month, 0.0) * inside / (c_hi - c_lo).total_seconds()
        m = nxt
    return total / ((through - lo).total_seconds() / 86400)


def runs_out(row, months_charges, through):
    """The date the recent rate would spend the balance, if at least RUNOUT_MARGIN_DAYS before
    the end date; else ``None``. Only for a current allocation with a balance left."""
    if not burnable(row) or not row['start_date'] < through <= row['end_date']:
        return None
    remaining = row['total_amount'] - (row.get('total_used') or 0.0)
    daily = recent_rate(row, months_charges, through)
    if remaining <= 0 or daily <= 0:
        return None
    days, latest = remaining / daily, row['end_date'] - timedelta(days=RUNOUT_MARGIN_DAYS)
    # Compare in days first: a trickle of charges puts the date past datetime.max.
    if days > (latest - through).total_seconds() / 86400:
        return None
    return through + timedelta(days=days)
