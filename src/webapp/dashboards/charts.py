"""
Chart generation utilities for server-side rendering.

All chart functions use functools.lru_cache to avoid regenerating identical
SVGs on repeated requests. The public API is unchanged -- callers pass normal
Python objects; hashability is handled internally.

NOTE: lru_cache is per-process. This is safe with gunicorn sync workers
(each worker is a forked process). If workers are changed to gthread,
Matplotlib rendering on cache misses may need a threading lock.
"""

from functools import lru_cache
from io import StringIO
from typing import List, Dict
from datetime import date, datetime, timedelta

import matplotlib
matplotlib.use('Agg')
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from sam import fmt


def format_number(value: float, decimals: int = 2) -> str:
    """Format a number with thousands separators."""
    if value >= 1000000:
        return f"{value/1000000:.{decimals}f}M"
    elif value >= 1000:
        return f"{value/1000:.{decimals}f}K"
    else:
        return f"{value:.{decimals}f}"


# ---------------------------------------------------------------------------
# Hashability helpers
# ---------------------------------------------------------------------------

def _hashable_list_of_dicts(data: List[Dict]) -> tuple:
    """Convert list-of-dicts to a hashable tuple for lru_cache keys."""
    return tuple(tuple(sorted(d.items())) for d in data)


def _hashable_timeseries(daily_charges) -> tuple:
    """Convert timeseries dict (dates/values keys) to a hashable tuple."""
    return (
        tuple(daily_charges.get('dates', [])),
        tuple(daily_charges.get('values', [])),
    )


def _attach_cache_methods(public_fn, cached_fn):
    """Proxy cache_info/cache_clear from the inner cached function."""
    public_fn.cache_info = cached_fn.cache_info
    public_fn.cache_clear = cached_fn.cache_clear


# ---------------------------------------------------------------------------
# 1. Usage timeseries (user dashboard)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=128)
def _cached_usage_timeseries(data_key: tuple) -> str:
    dates_raw, values_raw = data_key
    dates = list(dates_raw)
    comp = list(values_raw)

    combined = sorted(zip(dates, comp))
    if not combined:
        return '<div class="text-center text-muted">No usage data recorded for this period</div>'

    dates, comp = zip(*combined)
    dates = list(dates)
    comp = list(comp)

    fig, ax = plt.subplots(figsize=(12, 4))
    ax.bar(dates, comp, width=1, lw=2)
    ax.set_ylabel('Charges')
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()

    svg_io = StringIO()
    fig.savefig(svg_io, format='svg', bbox_inches='tight', transparent=True)
    plt.close(fig)
    return svg_io.getvalue()


def generate_usage_timeseries_matplotlib(daily_charges) -> str:
    """
    Generate time-series bar chart using Matplotlib.

    Args:
        daily_charges: Dict with 'dates' and 'values' keys

    Returns:
        SVG string ready for template rendering
    """
    if not daily_charges:
        return '<div class="text-center text-muted">No usage data recorded for this period</div>'
    return _cached_usage_timeseries(_hashable_timeseries(daily_charges))


_attach_cache_methods(generate_usage_timeseries_matplotlib, _cached_usage_timeseries)


# ---------------------------------------------------------------------------
# 2. Node type history (status dashboard)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=64)
def _cached_nodetype_history(data_key: tuple) -> str:
    import matplotlib.cm as cm

    history_data = [dict(items) for items in data_key]

    timestamps = [d['timestamp'] for d in history_data]
    nodes_available = [d.get('nodes_available', 0) for d in history_data]
    nodes_down = [d.get('nodes_down', 0) for d in history_data]
    nodes_allocated = [d.get('nodes_allocated', 0) for d in history_data]
    utilization = [d.get('utilization_percent') for d in history_data]
    memory_utilization = [d.get('memory_utilization_percent') for d in history_data]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 8), sharex=True)

    ax1.stackplot(timestamps, nodes_down, nodes_allocated, nodes_available,
                  labels=['Down', 'Fully Allocated', 'Resources Available'],
                  colors=['C3', 'C0', 'C9'])
    ax1.set_ylabel('Number of Nodes', fontsize=11)
    ax1.set_ylim([0, None])
    ax1.legend(loc=2, fontsize=10)
    ax1.grid(True, alpha=0.3, color='grey')

    if any(u is not None for u in utilization):
        util_times = [timestamps[i] for i, u in enumerate(utilization) if u is not None]
        util_vals = [u for u in utilization if u is not None]
        ax2.plot(util_times, util_vals, 'b', linewidth=3, label='CPU/GPU Utilization')

    if any(m is not None for m in memory_utilization):
        mem_times = [timestamps[i] for i, m in enumerate(memory_utilization) if m is not None]
        mem_vals = [m for m in memory_utilization if m is not None]
        ax2.plot(mem_times, mem_vals, 'c', linewidth=3, label='Memory Utilization')

    ax2.set_ylabel('Utilization (%)', fontsize=11)
    ax2.set_xlabel('Time', fontsize=11)
    ax2.set_ylim(0, 100)
    ax2.legend(loc='best', fontsize=10)
    ax2.grid(True, alpha=0.3)

    fig.autofmt_xdate()

    svg_io = StringIO()
    fig.savefig(svg_io, format='svg', bbox_inches='tight', transparent=True)
    plt.close(fig)
    return svg_io.getvalue()


def generate_nodetype_history_matplotlib(history_data: List[Dict]) -> str:
    """
    Generate node type history chart showing availability and utilization.
    Title is rendered in the surrounding HTML (see status dashboard template).

    Args:
        history_data: List of dicts with timestamp, nodes_*, utilization_percent

    Returns:
        SVG string ready for template rendering
    """
    if not history_data:
        return '<div class="text-center text-muted">No history data available for this node type</div>'
    return _cached_nodetype_history(_hashable_list_of_dicts(history_data))


_attach_cache_methods(generate_nodetype_history_matplotlib, _cached_nodetype_history)


# ---------------------------------------------------------------------------
# 3. Queue history (status dashboard)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=64)
def _cached_queue_history(data_key: tuple) -> str:
    history_data = [dict(items) for items in data_key]

    timestamps = [d['timestamp'] for d in history_data]
    running_jobs = [d.get('running_jobs', 0) for d in history_data]
    pending_jobs = [d.get('pending_jobs', 0) for d in history_data]
    held_jobs = [d.get('held_jobs', 0) for d in history_data]
    active_users = [d.get('active_users', 0) for d in history_data]
    cores_allocated = [d.get('cores_allocated', 0) for d in history_data]
    cores_pending = [d.get('cores_pending', 0) for d in history_data]
    gpus_allocated = [d.get('gpus_allocated', 0) for d in history_data]
    gpus_pending = [d.get('gpus_pending', 0) for d in history_data]

    has_gpus = any(gpus_allocated) or any(gpus_pending)

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), sharex=True)

    ax1.plot(timestamps, running_jobs, 'g-', linewidth=3, label='Running')
    ax1.plot(timestamps, pending_jobs, 'orange', linewidth=3, label='Pending')
    ax1.plot(timestamps, held_jobs, 'r-', linewidth=3, label='Held')
    ax1.plot(timestamps, active_users, 'b--', linewidth=2, label='Active Users')
    ax1.set_ylim([0, None])
    ax1.set_ylabel('Count', fontsize=11)
    ax1.legend(loc=2, fontsize=10)
    ax1.grid(True, alpha=0.3)

    if has_gpus:
        ax2.plot(timestamps, gpus_allocated, 'b', linewidth=3, label='GPUs Running')
        ax2.plot(timestamps, gpus_pending, 'c', linewidth=3, label='GPUs Pending')
    else:
        ax2.plot(timestamps, cores_allocated, 'b', linewidth=3, label='Cores Running')
        ax2.plot(timestamps, cores_pending, 'c', linewidth=3, label='Cores Pending')

    ax2.set_ylim([0, None])
    ax2.set_ylabel('Resources', fontsize=11)
    ax2.set_xlabel('Time', fontsize=11)
    ax2.legend(loc=2, fontsize=10)
    ax2.grid(True, alpha=0.3)

    fig.autofmt_xdate()

    svg_io = StringIO()
    fig.savefig(svg_io, format='svg', bbox_inches='tight', transparent=True)
    plt.close(fig)
    return svg_io.getvalue()


def generate_queue_history_matplotlib(history_data: List[Dict]) -> str:
    """
    Generate queue history chart showing job flow and resource demand.
    Title is rendered in the surrounding HTML (see status dashboard template).

    Args:
        history_data: List of dicts with timestamp, job counts, resources

    Returns:
        SVG string ready for template rendering
    """
    if not history_data:
        return '<div class="text-center text-muted">No history data available for this queue</div>'
    return _cached_queue_history(_hashable_list_of_dicts(history_data))


_attach_cache_methods(generate_queue_history_matplotlib, _cached_queue_history)


# ---------------------------------------------------------------------------
# 4. Facility pie chart (allocations dashboard)
# ---------------------------------------------------------------------------

_PIE_START_ANGLE = 60
_PIE_MAX_ENTITIES = 10


def _pie_trim(names: list, values: list) -> tuple[list, list]:
    """Sort by value descending, cap at _PIE_MAX_ENTITIES, group remainder as 'Others (N)'."""
    paired = sorted(zip(names, values), key=lambda x: x[1], reverse=True)
    names_s = [p[0] for p in paired]
    values_s = [p[1] for p in paired]
    if len(names_s) > _PIE_MAX_ENTITIES:
        n_others = len(names_s) - _PIE_MAX_ENTITIES
        others_sum = sum(values_s[_PIE_MAX_ENTITIES:])
        names_s = names_s[:_PIE_MAX_ENTITIES] + [f'Others ({n_others})']
        values_s = values_s[:_PIE_MAX_ENTITIES] + [others_sum]
    return names_s, values_s


@lru_cache(maxsize=32)
def _cached_facility_pie(data_key: tuple) -> str:
    facility_data = [dict(items) for items in data_key]

    raw_names = [d['facility'] for d in facility_data]
    raw_values = [d['annualized_rate'] for d in facility_data]
    names, values = _pie_trim(raw_names, raw_values)

    legend_labels = [f'{n} ({fmt.number(v)})' for n, v in zip(names, values)]
    colors = plt.cm.tab20.colors[:len(names)]

    fig, ax = plt.subplots(figsize=(7, 4))
    wedges, _texts, autotexts = ax.pie(
        values,
        labels=None,
        autopct=lambda pct: f'{pct:.1f}%' if pct >= 5 else '',
        startangle=_PIE_START_ANGLE,
        counterclock=False,
        colors=colors,
        pctdistance=0.85,
    )
    for at in autotexts:
        at.set_color('white')
        at.set_fontweight('bold')
        at.set_fontsize(8)

    ax.legend(wedges, legend_labels, loc='center left', bbox_to_anchor=(1.0, 0.5), fontsize=9)

    svg_io = StringIO()
    fig.savefig(svg_io, format='svg', bbox_inches='tight', transparent=True)
    plt.close(fig)
    return svg_io.getvalue()


def generate_facility_pie_chart_matplotlib(facility_data: List[Dict]) -> str:
    """
    Generate pie chart showing distribution by facility. Title is rendered
    in the surrounding HTML (see allocations dashboard template).

    Args:
        facility_data: List of dicts with facility, annualized_rate, count, percent

    Returns:
        SVG string ready for template rendering
    """
    if not facility_data:
        return '<div class="text-center text-muted">No facility data available</div>'
    return _cached_facility_pie(_hashable_list_of_dicts(facility_data))


_attach_cache_methods(generate_facility_pie_chart_matplotlib, _cached_facility_pie)


# ---------------------------------------------------------------------------
# 5. Allocation type pie chart (allocations dashboard)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=64)
def _cached_alloc_type_pie(data_key: tuple) -> str:
    type_data = [dict(items) for items in data_key]

    raw_names = [d['allocation_type'] for d in type_data]
    raw_values = [d['total_amount'] for d in type_data]
    names, values = _pie_trim(raw_names, raw_values)

    legend_labels = [f'{n} ({fmt.number(v)})' for n, v in zip(names, values)]
    colors = plt.cm.tab20.colors[:len(names)]

    fig, ax = plt.subplots(figsize=(7, 4))
    wedges, _texts, autotexts = ax.pie(
        values,
        labels=None,
        autopct=lambda pct: f'{pct:.1f}%' if pct >= 5 else '',
        startangle=_PIE_START_ANGLE,
        counterclock=False,
        colors=colors,
        pctdistance=0.85,
    )
    for at in autotexts:
        at.set_color('white')
        at.set_fontweight('bold')
        at.set_fontsize(8)

    ax.legend(wedges, legend_labels, loc='center left', bbox_to_anchor=(1.0, 0.5), fontsize=9)

    svg_io = StringIO()
    fig.savefig(svg_io, format='svg', bbox_inches='tight', transparent=True)
    plt.close(fig)
    return svg_io.getvalue()


def generate_allocation_type_pie_chart_matplotlib(type_data: List[Dict]) -> str:
    """
    Generate pie chart showing allocation distribution by type within a facility.
    Title is rendered in the surrounding HTML (see allocations dashboard template).

    Args:
        type_data: List of dicts with allocation_type, total_amount, count, avg_amount

    Returns:
        SVG string ready for template rendering
    """
    if not type_data:
        return '<div class="text-center text-muted">No allocation type data available</div>'
    return _cached_alloc_type_pie(_hashable_list_of_dicts(type_data))


_attach_cache_methods(generate_allocation_type_pie_chart_matplotlib, _cached_alloc_type_pie)


# ---------------------------------------------------------------------------
# 6. Allocation pace chart (allocations dashboard)
#
# Stacked-area chart where each allocation is one band with a step at
# `active_at`. Left of the step: constant past-burn-rate (used/elapsed_days).
# Right of the step: constant required-future-rate (remaining/remaining_days).
# Past and future of the same allocation share a color (one band = one color).
# Top-N projcodes (by total allocated in-scope) get distinct colors; the rest
# share a muted "Other" color.
# ---------------------------------------------------------------------------

_PACE_OTHER_COLOR = (0.78, 0.78, 0.78, 0.85)
_PACE_TODAY_LINE_COLOR = (0.2, 0.2, 0.2, 0.7)


def _pace_bands(allocations: List[Dict], active_at: datetime,
                window_start: datetime, window_end: datetime):
    """Build per-allocation rate arrays on a daily grid.

    Returns (days, bands) where bands is a list of
    ``(projcode, total_amount, rates_list)`` tuples — one per allocation
    that intersects the window and has nonzero area.
    """
    n_days = (window_end - window_start).days + 1
    today_idx = (active_at - window_start).days

    bands = []
    for a in allocations:
        s, e = a['start_date'], a['end_date']
        if s is None or e is None or e <= s:
            continue
        if e < window_start or s > window_end:
            continue

        amount = float(a.get('total_amount') or 0.0)
        used = float(a.get('total_used') or 0.0)

        # past region: S → min(active_at, E); height = used / elapsed
        past_end = min(active_at, e)
        past_days = max((past_end - s).days, 0)
        past_rate = (used / past_days) if past_days > 0 else 0.0

        # future region: max(active_at, S) → E; height = remaining / remaining
        future_start = max(active_at, s)
        future_days = max((e - future_start).days, 0)
        future_rate = ((amount - used) / future_days) if future_days > 0 else 0.0

        if past_rate <= 0 and future_rate <= 0:
            continue

        rates = [0.0] * n_days
        s_idx = max(0, (s - window_start).days)
        e_idx = min(n_days, (e - window_start).days + 1)

        # Past fills [s_idx, min(today_idx, e_idx))
        if past_rate > 0:
            for i in range(s_idx, min(today_idx, e_idx)):
                rates[i] = past_rate
        # Future fills [max(today_idx, s_idx), e_idx)
        if future_rate > 0:
            for i in range(max(today_idx, s_idx), e_idx):
                rates[i] = future_rate

        bands.append((a.get('projcode', ''), amount, rates))

    days = [window_start + timedelta(days=i) for i in range(n_days)]
    return days, bands


def _pace_hashable(allocations: List[Dict]) -> tuple:
    """Hashable digest of the only fields the chart consumes."""
    def _d(x):
        return x.isoformat() if x is not None else None
    return tuple(
        (
            a.get('projcode', ''),
            _d(a.get('start_date')),
            _d(a.get('end_date')),
            float(a.get('total_amount') or 0.0),
            float(a.get('total_used') or 0.0),
        )
        for a in allocations
    )


@lru_cache(maxsize=64)
def _cached_pace_chart(data_key: tuple, active_at_iso: str,
                       window_days: int, top_n: int, resource_name: str) -> str:
    active_at = datetime.fromisoformat(active_at_iso)
    window_start = active_at - timedelta(days=window_days)
    window_end = active_at + timedelta(days=window_days)

    # Reconstitute minimal allocation dicts from the hashable tuple.
    allocations = [
        {
            'projcode': pc,
            'start_date': datetime.fromisoformat(s) if s else None,
            'end_date': datetime.fromisoformat(e) if e else None,
            'total_amount': amt,
            'total_used': used,
        }
        for (pc, s, e, amt, used) in data_key
    ]

    days, bands = _pace_bands(allocations, active_at, window_start, window_end)
    if not bands:
        return '<div class="text-center text-muted">No allocations in the ±{}d window</div>'.format(window_days)

    # Rank projcodes by total_amount across bands in scope
    proj_totals: Dict[str, float] = {}
    for pc, amount, _ in bands:
        proj_totals[pc] = proj_totals.get(pc, 0.0) + amount
    top_projs = [pc for pc, _ in sorted(
        proj_totals.items(), key=lambda kv: kv[1], reverse=True
    )[:top_n]]
    palette = plt.cm.tab10.colors if len(top_projs) <= 10 else plt.cm.tab20.colors
    color_map = {pc: palette[i] for i, pc in enumerate(top_projs)}

    n_other_projs = len(proj_totals) - len(top_projs)
    other_label = f'Other ({n_other_projs} project{"s" if n_other_projs != 1 else ""})'

    # Sort bands: top-N first (grouped by projcode, largest amount at bottom
    # of its group), "Other" last so it caps the stack.
    def _sort_key(band):
        pc, amount, _ = band
        if pc in color_map:
            return (0, top_projs.index(pc), -amount)
        return (1, 0, -amount)
    bands.sort(key=_sort_key)

    rates_matrix = [b[2] for b in bands]
    colors = [color_map.get(b[0], _PACE_OTHER_COLOR) for b in bands]

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.stackplot(days, rates_matrix, colors=colors, edgecolor='none',
                 linewidth=0, antialiased=True)

    # Today marker
    ax.axvline(active_at, color=_PACE_TODAY_LINE_COLOR, linestyle='--', linewidth=1)
    ymin, ymax = ax.get_ylim()
    ax.text(active_at, ymax, ' today', color=_PACE_TODAY_LINE_COLOR,
            fontsize=8, va='top', ha='left')

    # Deduplicated legend: one handle per top-N projcode + one Other
    import matplotlib.patches as mpatches
    handles = [mpatches.Patch(color=color_map[pc],
                              label=f'{pc} ({fmt.number(proj_totals[pc])})')
               for pc in top_projs]
    if n_other_projs > 0:
        other_total = sum(v for pc, v in proj_totals.items() if pc not in color_map)
        handles.append(mpatches.Patch(
            color=_PACE_OTHER_COLOR,
            label=f'{other_label} ({fmt.number(other_total)})'
        ))
    ax.legend(handles=handles, loc='center left', bbox_to_anchor=(1.0, 0.5),
              fontsize=9, frameon=False)

    # Axes
    ax.set_xlim(window_start, window_end)
    ax.set_ylim(bottom=0)
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
    ax.yaxis.set_major_formatter(fmt.mpl_number_formatter())
    ax.set_ylabel('Rate (per day)')
    ax.grid(True, alpha=0.2)
    fig.autofmt_xdate()

    svg_io = StringIO()
    fig.savefig(svg_io, format='svg', bbox_inches='tight', transparent=True)
    plt.close(fig)
    return svg_io.getvalue()


def generate_pace_chart_matplotlib(
    allocations: List[Dict],
    active_at: datetime,
    window_days: int = 180,
    top_n: int = 10,
    resource_name: str = '',
) -> str:
    """Stacked-area pace chart: one band per allocation, past-rate | future-rate
    step at ``active_at``. Top-N projcodes by total allocated get distinct
    colors; the rest share an "Other" color.

    Args:
        allocations: per-allocation rows (from ``cached_allocation_usage``)
            with at least ``projcode``, ``start_date``, ``end_date``,
            ``total_amount``, ``total_used``.
        active_at: chart centerline ("today").
        window_days: half-window on each side of ``active_at`` (default 180).
        top_n: projects with their own color + legend entry (default 5).
        resource_name: used only for cache key disambiguation.

    Returns:
        SVG string ready for template rendering.
    """
    if not allocations:
        return '<div class="text-center text-muted">No allocations available</div>'
    return _cached_pace_chart(
        _pace_hashable(allocations),
        active_at.isoformat(),
        int(window_days),
        int(top_n),
        resource_name,
    )


_attach_cache_methods(generate_pace_chart_matplotlib, _cached_pace_chart)
