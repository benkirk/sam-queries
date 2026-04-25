"""
Chart generation utilities for server-side rendering.

All chart functions are decorated with @_chart_cache, which caches rendered
SVGs by content hash. The public API is unchanged -- callers pass normal Python
objects; hashing and caching are handled internally.

Cache keys are stable MD5 hex digests of the input data, so key computation is
O(n) time but O(1) memory regardless of input size. This is safe for large
inputs (e.g. a year of 5-minute history) where materialising the full data as
a hashable tuple would allocate several MB per call even on a cache hit.

NOTE: _ChartCache is per-process and thread-safe. It is safe with both gunicorn
sync workers (each worker is a forked process) and gthread workers.
"""

import functools
import hashlib
import json
import threading
from collections import OrderedDict, namedtuple
from io import StringIO
from typing import List, Dict
from datetime import date, datetime, timedelta

import matplotlib
matplotlib.use('Agg')
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np

from sam import fmt


# ---------------------------------------------------------------------------
# Cache infrastructure
# ---------------------------------------------------------------------------

_CacheInfo = namedtuple('CacheInfo', ['hits', 'misses', 'maxsize', 'currsize'])


def _content_hash(data) -> str:
    """Stable MD5 hex digest of arbitrary JSON-serialisable data.

    O(n) compute, O(1) memory — suitable as a cache key for large inputs
    where materialising a hashable tuple would be prohibitive.
    """
    return hashlib.md5(
        json.dumps(data, default=str, sort_keys=True).encode(),
        usedforsecurity=False,
    ).hexdigest()


class _ChartCache:
    """Thread-safe bounded LRU cache for rendered SVG strings."""

    def __init__(self, maxsize: int):
        self._data: OrderedDict[str, str] = OrderedDict()
        self._maxsize = maxsize
        self._lock = threading.Lock()
        self._hits = self._misses = 0

    def get(self, key: str):
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                self._hits += 1
                return self._data[key]
            self._misses += 1
            return None

    def put(self, key: str, value: str) -> None:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
            else:
                if len(self._data) >= self._maxsize:
                    self._data.popitem(last=False)
                self._data[key] = value

    def cache_info(self) -> _CacheInfo:
        with self._lock:
            return _CacheInfo(
                hits=self._hits,
                misses=self._misses,
                maxsize=self._maxsize,
                currsize=len(self._data),
            )

    def cache_clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._hits = self._misses = 0


def _chart_cache(maxsize: int, key_fn=None):
    """Decorator factory that caches chart SVG output by content hash.

    Args:
        maxsize:  Maximum number of SVGs to keep (LRU eviction).
        key_fn:   Optional callable(*args, **kwargs) -> str that computes the
                  cache key.  Defaults to _content_hash of the first argument,
                  which covers every single-argument chart function.  Pass an
                  explicit key_fn for functions with multiple meaningful args
                  (e.g. the pace chart).

    The decorated function gains cache_info() and cache_clear() attributes
    matching the functools.lru_cache interface.
    """
    def decorator(fn):
        cache = _ChartCache(maxsize=maxsize)
        _key = key_fn or (lambda *args, **kwargs: _content_hash(args[0]))

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            key = _key(*args, **kwargs)
            result = cache.get(key)
            if result is None:
                result = fn(*args, **kwargs)
                cache.put(key, result)
            return result

        wrapper.cache_info = cache.cache_info
        wrapper.cache_clear = cache.cache_clear
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# 1. Usage timeseries (user dashboard)
# ---------------------------------------------------------------------------

@_chart_cache(maxsize=128)
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

    dates = list(daily_charges.get('dates', []))
    comp = list(daily_charges.get('values', []))

    combined = sorted(zip(dates, comp))
    if not combined:
        return '<div class="text-center text-muted">No usage data recorded for this period</div>'

    dates, comp = zip(*combined)
    dates = list(dates)
    comp = list(comp)

    fig, ax = plt.subplots(figsize=(12, 4))
    ax.bar(dates, comp, width=1, lw=2)
    ax.set_ylabel('Charges')
    ax.yaxis.set_major_formatter(fmt.mpl_number_formatter())
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()

    svg_io = StringIO()
    fig.savefig(svg_io, format='svg', bbox_inches='tight', transparent=True)
    plt.close(fig)
    return svg_io.getvalue()


# ---------------------------------------------------------------------------
# 2. Node type history (status dashboard)
# ---------------------------------------------------------------------------

@_chart_cache(maxsize=64)
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
    ax1.yaxis.set_major_formatter(fmt.mpl_number_formatter())
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

    ax2.set_ylabel('Utilization', fontsize=11)
    ax2.set_xlabel('Time', fontsize=11)
    ax2.set_ylim(0, 100)
    ax2.yaxis.set_major_formatter(fmt.mpl_pct_formatter())
    ax2.legend(loc='best', fontsize=10)
    ax2.grid(True, alpha=0.3)

    fig.autofmt_xdate()

    svg_io = StringIO()
    fig.savefig(svg_io, format='svg', bbox_inches='tight', transparent=True)
    plt.close(fig)
    return svg_io.getvalue()


# ---------------------------------------------------------------------------
# 3. Queue history (status dashboard)
# ---------------------------------------------------------------------------

@_chart_cache(maxsize=64)
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
    ax1.yaxis.set_major_formatter(fmt.mpl_number_formatter())
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
    ax2.yaxis.set_major_formatter(fmt.mpl_number_formatter())
    ax2.legend(loc=2, fontsize=10)
    ax2.grid(True, alpha=0.3)

    fig.autofmt_xdate()

    svg_io = StringIO()
    fig.savefig(svg_io, format='svg', bbox_inches='tight', transparent=True)
    plt.close(fig)
    return svg_io.getvalue()


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


@_chart_cache(maxsize=32)
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

    raw_names = [d['facility'] for d in facility_data]
    raw_values = [d['annualized_rate'] for d in facility_data]
    names, values = _pie_trim(raw_names, raw_values)

    legend_labels = [f'{n} ({fmt.number(v)})' for n, v in zip(names, values)]
    colors = plt.cm.tab20.colors[:len(names)]

    fig, ax = plt.subplots(figsize=(7, 4))
    wedges, _texts, autotexts = ax.pie(
        values,
        labels=None,
        autopct=lambda p: fmt.pct(p, decimals=1) if p >= 5 else '',
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


# ---------------------------------------------------------------------------
# 5. Allocation type pie chart (allocations dashboard)
# ---------------------------------------------------------------------------

@_chart_cache(maxsize=64)
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

    raw_names = [d['allocation_type'] for d in type_data]
    raw_values = [d['total_amount'] for d in type_data]
    names, values = _pie_trim(raw_names, raw_values)

    legend_labels = [f'{n} ({fmt.number(v)})' for n, v in zip(names, values)]
    colors = plt.cm.tab20.colors[:len(names)]

    fig, ax = plt.subplots(figsize=(7, 4))
    wedges, _texts, autotexts = ax.pie(
        values,
        labels=None,
        autopct=lambda p: fmt.pct(p, decimals=1) if p >= 5 else '',
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

        rates = np.zeros(n_days)
        s_idx = max(0, (s - window_start).days)
        e_idx = min(n_days, (e - window_start).days + 1)

        if past_rate > 0:
            rates[s_idx:min(today_idx, e_idx)] = past_rate
        if future_rate > 0:
            rates[max(today_idx, s_idx):e_idx] = future_rate

        bands.append((a.get('projcode', ''), amount, rates))

    days = [window_start + timedelta(days=i) for i in range(n_days)]
    return days, bands


def _pace_key_fields(allocations: List[Dict]) -> list:
    """Extract only the fields the pace chart consumes, for a compact hash input."""
    def _d(x):
        return x.isoformat() if x is not None else None
    return [
        (
            a.get('projcode', ''),
            _d(a.get('start_date')),
            _d(a.get('end_date')),
            float(a.get('total_amount') or 0.0),
            float(a.get('total_used') or 0.0),
        )
        for a in allocations
    ]


def _pace_cache_key(allocations, active_at, window_days=180, top_n=10, resource_name=''):
    return _content_hash([_pace_key_fields(allocations), active_at.isoformat(),
                          int(window_days), int(top_n), resource_name])


@_chart_cache(maxsize=64, key_fn=_pace_cache_key)
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

    window_start = active_at - timedelta(days=window_days)
    window_end = active_at + timedelta(days=window_days)

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

    # Collapse per-allocation bands into one band per color group BEFORE
    # handing to matplotlib. Stackplot emits one <path> per band; without
    # this aggregation, a ~1000-project resource produces 1000 paths and a
    # ~20 MB SVG. Stacking is associative, so element-wise summing the rate
    # arrays within each color group is mathematically identical and
    # visually identical (the group shares one color anyway).
    n_days = len(days)
    OTHER_KEY = '__other__'
    group_keys = list(top_projs) + [OTHER_KEY]
    group_rates: Dict[str, np.ndarray] = {k: np.zeros(n_days) for k in group_keys}
    group_totals: Dict[str, float] = {k: 0.0 for k in group_keys}

    for pc, amount, rates in bands:
        key = pc if pc in color_map else OTHER_KEY
        group_totals[key] += amount
        group_rates[key] += rates

    # Stack order: top-N (ranked) first, Other capping the top. Drop empty
    # groups so stackplot doesn't emit a zero-area path.
    ordered = [(k, group_rates[k]) for k in top_projs] + [(OTHER_KEY, group_rates[OTHER_KEY])]
    ordered = [(k, r) for k, r in ordered if r.any()]

    rates_matrix = [r for _, r in ordered]
    colors = [color_map.get(k, _PACE_OTHER_COLOR) for k, _ in ordered]

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
        handles.append(mpatches.Patch(
            color=_PACE_OTHER_COLOR,
            label=f'{other_label} ({fmt.number(group_totals[OTHER_KEY])})'
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
