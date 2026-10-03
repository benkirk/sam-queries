"""Geometry for the allocations calendar: months and allocation bars as percents of a window.

A bar spans its allocation's dates and fills to its % used along the whole span, so
the fill ends at the date an even burn would have reached: short of the view-at line
is under pace. A window edge cuts the bar and its fill alike.
Positions are percents of the window, so CSS alone sets the pixel scale.
Burn mode splits a bar into months, each classed by its charges over an even-pace share, and
marks where the last-90-day rate would spend the balance well before the end date (burn.py).
Design record: docs/plans/implemented/ALLOCATIONS_TABLE_VIEWS.md.
"""
from .burn import burn_class, burnable, month_shares, month_start, runs_out

PAST_MONTHS = 12
FUTURE_MONTHS = 12


def calendar_window(active_at, past=PAST_MONTHS, future=FUTURE_MONTHS):
    """``(start, end)``: the first of the month ``past`` months back, to the end of the month
    ``future`` months ahead (exclusive, a first-of-month)."""
    return month_start(active_at, -past), month_start(active_at, future + 1)


def _pct(d, start, span):
    return (d - start) / span * 100


def calendar_months(start, end):
    """One ``{label, year, left, width}`` per month in the window; ``year`` only on January
    and the first month, where the axis needs it."""
    span, months, m = end - start, [], start
    while m < end:
        nxt = month_start(m, 1)
        months.append({'label': m.strftime('%b'),
                       'year': m.year if m.month == 1 or m == start else None,
                       'left': _pct(m, start, span), 'width': _pct(nxt, start, span) - _pct(m, start, span)})
        m = nxt
    return months


def _bar(row, start, end, active_at):
    a_start, a_end = row['start_date'], row['end_date']
    vis_start, vis_end = max(a_start, start), min(a_end or end, end)
    if vis_end <= vis_start:
        return None
    span, amount, used = end - start, row.get('total_amount') or 0.0, row.get('total_used')
    pct = used * 100 / amount if amount and used is not None else None
    day_end = active_at.replace(hour=23, minute=59, second=59)
    state = ('future' if a_start > day_end else
             'past' if a_end is not None and a_end < active_at else 'current')
    fill = 0.0
    if pct and a_end is not None and state != 'future':
        reached = a_start + (a_end - a_start) * min(pct, 100) / 100
        fill = min(max((reached - vis_start) / (vis_end - vis_start), 0.0), 1.0) * 100
    elif pct and a_end is None:
        fill = 100.0 if pct >= 100 else 0.0   # open-ended: no span to place a burn on
    return {'left': _pct(vis_start, start, span), 'width': _pct(vis_end, start, span) - _pct(vis_start, start, span),
            'clip_left': a_start < start, 'clip_right': a_end is None or a_end > end,
            'state': state, 'pct_used': pct, 'fill': fill, 'over': bool(pct and pct > 100),
            'start_date': a_start, 'end_date': a_end, 'total_amount': amount, 'total_used': used,
            'allocation_id': row.get('allocation_id')}


def _cell(m, charges, share, c_lo, c_hi, origin, width):
    ratio = charges / share
    return {'month': m, 'charges': charges, 'ratio': ratio, 'cls': burn_class(ratio),
            'left': (c_lo - origin) / width * 100, 'width': (c_hi - c_lo) / width * 100}


def burn_cells(row, months_charges, start, end, through):
    """A bar's burn cells, one per month it covers before ``through``, in % of the visible bar;
    ``None`` when the allocation has no even pace (open-ended, no amount, no id)."""
    if not burnable(row):
        return None
    vis_start, vis_end = max(row['start_date'], start), min(row['end_date'], end)
    hi = min(vis_end, through)
    width = vis_end - vis_start
    return [_cell(m, months_charges.get(ym, 0.0), share, c_lo, c_hi, vis_start, width)
            for m, ym, c_lo, c_hi, share in month_shares(row, vis_start, hi) if share > 0]


def group_burn(rows, burns, start, end, through):
    """One cell per window month before ``through``, in % of the window: the group's summed
    charges over its summed even shares. Months with no share in the group get no cell."""
    hi = min(end, through)
    totals = {}
    for r in rows:
        if not burnable(r):
            continue
        cells = burns.get(r['allocation_id'], {})
        for m, ym, _lo, _hi, share in month_shares(r, max(r['start_date'], start),
                                                     min(r['end_date'], hi)):
            t = totals.setdefault(m, [0.0, 0.0])
            t[0] += cells.get(ym, 0.0)
            t[1] += share
    out = []
    for m in sorted(totals):
        charges, share = totals[m]
        if share > 0:
            out.append(_cell(m, charges, share, m, min(month_start(m, 1), hi), start, end - start))
    return out


def calendar_group_burn(rows, groups, burns, start, end, through):
    """``group_burn`` cells for each facility (keyed by name) and type (keyed ``(facility, type)``)."""
    out = {}
    for facility, types in groups:
        f_rows = [r for r in rows if r['facility'] == facility]
        out[facility] = group_burn(f_rows, burns, start, end, through)
        for t in types:
            out[(facility, t)] = group_burn([r for r in f_rows if r['allocation_type'] == t],
                                            burns, start, end, through)
    return out


def _lanes(bars):
    """Greedy lanes, so overlapping allocations on one project stack instead of hiding."""
    ends = []
    for b in sorted(bars, key=lambda b: b['left']):
        lane = next((i for i, e in enumerate(ends) if e <= b['left'] + 1e-9), len(ends))
        ends[lane:lane + 1] = [b['left'] + b['width']]
        b['lane'] = lane
    return len(ends)


def calendar_rows(rows, start, end, active_at, burns=None, through=None):
    """One ``{projcode, lanes, bars}`` per project with a bar in the window, by projcode.
    With ``burns`` (get_allocation_burn's dict) each bar also carries ``burn`` cells to ``through``
    and, when its recent rate runs out early, ``runs_out`` and its position ``runs_out_left``."""
    by_project = {}
    for r in rows:
        bar = _bar(r, start, end, active_at)
        if bar is not None:
            if burns is not None:
                cells = burns.get(r.get('allocation_id'), {})
                bar['burn'] = burn_cells(r, cells, start, end, through)
                date = runs_out(r, cells, through)
                if date is not None and date < end:
                    vis_start = max(r['start_date'], start)
                    bar['runs_out'] = date
                    bar['runs_out_left'] = _pct(date, vis_start, min(r['end_date'], end) - vis_start)
            by_project.setdefault(r['projcode'], []).append(bar)
    out = []
    for projcode in sorted(by_project):
        bars = sorted(by_project[projcode], key=lambda b: b['left'])
        out.append({'projcode': projcode, 'lanes': _lanes(bars), 'bars': bars})
    return out


def calendar_groups(rows, facility_order):
    """``[(facility, [allocation_type])]``, facilities in ``facility_order`` (then by name),
    types by name."""
    groups = {}
    for r in rows:
        groups.setdefault(r['facility'], set()).add(r['allocation_type'])
    rank = {f: i for i, f in enumerate(facility_order)}
    return [(fac, sorted(types, key=lambda t: t or ''))
            for fac, types in sorted(groups.items(), key=lambda kv: (rank.get(kv[0], len(rank)), kv[0] or ''))]
