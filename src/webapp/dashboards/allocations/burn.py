"""Burn math: an allocation's monthly charges against an even pace, and its recent rate.

The even pace spreads the amount uniformly from start to end date; a month's charges over its
share of that is the burn ratio. The recent rate (charges per day over the last 90 days)
projects when the balance runs out. Charges come as get_allocation_burn's ``{yyyymm: charges}``.
No Flask, no matplotlib: the calendar and the Pace chart both read it.
Design record: docs/plans/implemented/ALLOCATIONS_TABLE_VIEWS.md (Burn).
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
        # lo past hi in hi's month (a start after the as-of day) would yield a negative share.
        if c_hi > c_lo:
            yield m, m.year * 100 + m.month, c_lo, c_hi, amount * (c_hi - c_lo).total_seconds() / span
        m = nxt


def _days(lo, hi):
    return (hi - lo).total_seconds() / 86400


def even_rate(row):
    """A burnable allocation's amount per day over its span."""
    return row['total_amount'] / _days(row['start_date'], row['end_date'])


def charges_between(row, months_charges, lo, through):
    """Charges in ``[lo, through)``, ``through`` being where the cells stop; a month cell partly
    inside counts pro rata, so the current cell runs from its start to ``through``."""
    lo = max(lo, row['start_date'])
    total, m = 0.0, month_start(lo)
    while m < through:
        nxt = month_start(m, 1)
        c_lo, c_hi = max(m, row['start_date']), min(nxt, through)
        inside = (c_hi - max(c_lo, lo)).total_seconds()
        if inside > 0:
            total += months_charges.get(m.year * 100 + m.month, 0.0) * inside / (c_hi - c_lo).total_seconds()
        m = nxt
    return total


def recent_rate(row, months_charges, through, days=RUNOUT_LOOKBACK_DAYS):
    """Charges per day over the ``days`` before ``through``, or since the start date if later.
    A month cell partly inside the look-back counts pro rata; 0.0 before the start date."""
    lo = max(through - timedelta(days=days), row['start_date'])
    if through <= lo:
        return 0.0
    return charges_between(row, months_charges, lo, through) / _days(lo, through)


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


def project_ratios(rows, burns, through, days=RUNOUT_LOOKBACK_DAYS):
    """``{projcode: charges / even share}`` over the look-back, summed across each project's
    burnable allocations, so a renewal days old inherits its predecessor's pace."""
    lo, acc = through - timedelta(days=days), {}
    for r in rows:
        if not burnable(r):
            continue
        a_lo, a_hi = max(lo, r['start_date']), min(through, r['end_date'])
        if a_hi <= a_lo:
            continue
        t = acc.setdefault(r['projcode'], [0.0, 0.0])
        t[0] += charges_between(r, burns.get(r['allocation_id'], {}), a_lo, a_hi)
        t[1] += even_rate(r) * _days(a_lo, a_hi)
    return {pc: c / e for pc, (c, e) in acc.items() if e > 0}


def _until(lo, end, balance, rate):
    """Where ``balance`` at ``rate`` per day runs out from ``lo``, capped at ``end``."""
    return end if balance / rate >= _days(lo, end) else lo + timedelta(days=balance / rate)


def pace_segments(rows, burns, through, at):
    """Copies of ``rows`` with ``pace``: ``past``, ``projected`` and ``committed`` lists of
    ``(lo, hi, rate per day)``, split at ``at``; ``recent`` is its charges inside the look-back
    over the look-back's full length, so a project's allocations add up to its actual rate.

    Past: each month cell's charges over its days. Projected: from ``at``, the project's recent
    ratio (``project_ratios``; 1 with no history) x the allocation's even rate, until the balance
    runs out or the end date. Committed: the balance over the days left, what the allocation
    promises to deliver. ``burns=None`` (disk) uses the lifetime average for past and projected.
    """
    ratios = project_ratios(rows, burns, through) if burns is not None else {}
    out = []
    for r in rows:
        s, e = r['start_date'], r['end_date']
        if s is None or e is None or e <= s:
            continue
        amount, used = float(r.get('total_amount') or 0.0), float(r.get('total_used') or 0.0)
        balance, past, projected, committed, recent = amount - used, [], [], [], 0.0
        if burns is not None and burnable(r):
            cells = burns.get(r['allocation_id'], {})
            if s < through:
                for _m, ym, c_lo, c_hi, _share in month_shares(r, s, min(e, through)):
                    past.append((c_lo, min(c_hi, at), cells.get(ym, 0.0) / _days(c_lo, c_hi)))
                look = through - timedelta(days=RUNOUT_LOOKBACK_DAYS)
                recent = charges_between(r, cells, look, min(through, e)) / RUNOUT_LOOKBACK_DAYS
            rate = ratios.get(r['projcode'], 1.0) * even_rate(r)
        else:
            elapsed = _days(s, min(e, through)) if s < through else 0.0
            recent = rate = used / elapsed if elapsed > 0 else 0.0
            if elapsed > 0:
                past.append((s, min(e, at), recent))
        f_lo = max(at, s)
        if e > f_lo and balance > 0:
            committed.append((f_lo, e, balance / _days(f_lo, e)))
            if rate > 0:
                projected.append((f_lo, _until(f_lo, e, balance, rate), rate))
        out.append({**r, 'pace': {'past': [p for p in past if p[1] > p[0]], 'projected': projected,
                                  'committed': committed, 'recent': recent}})
    return out
