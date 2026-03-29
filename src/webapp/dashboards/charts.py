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
from datetime import date

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


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
def _cached_nodetype_history(data_key: tuple, node_type: str) -> str:
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
    ax1.set_title(f'{node_type} - Node Availability Over Time', fontsize=13, fontweight='bold')
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


def generate_nodetype_history_matplotlib(history_data: List[Dict], node_type: str) -> str:
    """
    Generate node type history chart showing availability and utilization.

    Args:
        history_data: List of dicts with timestamp, nodes_*, utilization_percent
        node_type: Name of the node type (for title)

    Returns:
        SVG string ready for template rendering
    """
    if not history_data:
        return '<div class="text-center text-muted">No history data available for this node type</div>'
    return _cached_nodetype_history(_hashable_list_of_dicts(history_data), node_type)


_attach_cache_methods(generate_nodetype_history_matplotlib, _cached_nodetype_history)


# ---------------------------------------------------------------------------
# 3. Queue history (status dashboard)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=64)
def _cached_queue_history(data_key: tuple, queue_name: str, system_name: str) -> str:
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

    ax1.plot(timestamps, running_jobs, 'g-', linewidth=2, label='Running', marker='o', markersize=3)
    ax1.plot(timestamps, pending_jobs, 'orange', linewidth=2, label='Pending', marker='o', markersize=3)
    ax1.plot(timestamps, held_jobs, 'r-', linewidth=2, label='Held', marker='o', markersize=3)
    ax1.plot(timestamps, active_users, 'b--', linewidth=1.5, label='Active Users', marker='s', markersize=2)
    ax1.set_ylim([0, None])
    ax1.set_ylabel('Count', fontsize=11)
    ax1.set_title(f'{system_name.upper()} - {queue_name} Queue Activity', fontsize=13, fontweight='bold')
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


def generate_queue_history_matplotlib(history_data: List[Dict], queue_name: str, system_name: str) -> str:
    """
    Generate queue history chart showing job flow and resource demand.

    Args:
        history_data: List of dicts with timestamp, job counts, resources
        queue_name: Name of the queue (for title)
        system_name: System name (for title)

    Returns:
        SVG string ready for template rendering
    """
    if not history_data:
        return '<div class="text-center text-muted">No history data available for this queue</div>'
    return _cached_queue_history(_hashable_list_of_dicts(history_data), queue_name, system_name)


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
def _cached_facility_pie(data_key: tuple, title: str) -> str:
    facility_data = [dict(items) for items in data_key]

    raw_names = [d['facility'] for d in facility_data]
    raw_values = [d['annualized_rate'] for d in facility_data]
    names, values = _pie_trim(raw_names, raw_values)

    legend_labels = [f'{n} ({v:,.0f})' for n, v in zip(names, values)]
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

    ax.set_title(title, fontsize=11, fontweight='bold', pad=12)
    ax.legend(wedges, legend_labels, loc='center left', bbox_to_anchor=(1.0, 0.5), fontsize=9)

    svg_io = StringIO()
    fig.savefig(svg_io, format='svg', bbox_inches='tight', transparent=True)
    plt.close(fig)
    return svg_io.getvalue()


def generate_facility_pie_chart_matplotlib(facility_data: List[Dict], title: str = 'Annualized Rate Distribution by Facility') -> str:
    """
    Generate pie chart showing annualized rate distribution by facility.

    Args:
        facility_data: List of dicts with facility, annualized_rate, count, percent
        title: Chart title

    Returns:
        SVG string ready for template rendering
    """
    if not facility_data:
        return '<div class="text-center text-muted">No facility data available</div>'
    return _cached_facility_pie(_hashable_list_of_dicts(facility_data), title)


_attach_cache_methods(generate_facility_pie_chart_matplotlib, _cached_facility_pie)


# ---------------------------------------------------------------------------
# 5. Allocation type pie chart (allocations dashboard)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=64)
def _cached_alloc_type_pie(data_key: tuple, resource_type: str, resource_name: str, facility_name: str) -> str:
    type_data = [dict(items) for items in data_key]

    raw_names = [d['allocation_type'] for d in type_data]
    raw_values = [d['total_amount'] for d in type_data]
    names, values = _pie_trim(raw_names, raw_values)

    legend_labels = [f'{n} ({v:,.0f})' for n, v in zip(names, values)]
    colors = plt.cm.tab20.colors[:len(names)]

    if resource_type in ['DISK', 'ARCHIVE']:
        chart_title = f'Data Volume by Type\n{resource_name} — {facility_name}'
    else:
        chart_title = f'Allocation by Type\n{resource_name} — {facility_name}'

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

    ax.set_title(chart_title, fontsize=11, fontweight='bold', pad=12)
    ax.legend(wedges, legend_labels, loc='center left', bbox_to_anchor=(1.0, 0.5), fontsize=9)

    svg_io = StringIO()
    fig.savefig(svg_io, format='svg', bbox_inches='tight', transparent=True)
    plt.close(fig)
    return svg_io.getvalue()


def generate_allocation_type_pie_chart_matplotlib(type_data: List[Dict], resource_type: str, resource_name: str, facility_name: str) -> str:
    """
    Generate pie chart showing allocation distribution by type within a facility.

    Args:
        type_data: List of dicts with allocation_type, total_amount, count, avg_amount
        resource_type: Resource type string ('HPC', 'DAV', 'DISK', 'ARCHIVE')
        resource_name: Resource name for chart title
        facility_name: Facility name for chart title

    Returns:
        SVG string ready for template rendering
    """
    if not type_data:
        return '<div class="text-center text-muted">No allocation type data available</div>'
    return _cached_alloc_type_pie(_hashable_list_of_dicts(type_data), resource_type, resource_name, facility_name)


_attach_cache_methods(generate_allocation_type_pie_chart_matplotlib, _cached_alloc_type_pie)
