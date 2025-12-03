"""
Chart generation utilities for server-side rendering.

Provides simple SVG-based chart generation without requiring JavaScript
charting libraries.
"""

from typing import List, Dict
from datetime import date


def format_number(value: float, decimals: int = 2) -> str:
    """Format a number with thousands separators."""
    if value >= 1000000:
        return f"{value/1000000:.{decimals}f}M"
    elif value >= 1000:
        return f"{value/1000:.{decimals}f}K"
    else:
        return f"{value:.{decimals}f}"


import matplotlib
matplotlib.use('Agg')  # Non-interactive backend
import matplotlib.pyplot as plt
from io import StringIO
from typing import List, Dict

def generate_usage_timeseries_matplotlib(daily_charges: List[Dict]) -> str:
    """
    Generate advanced time-series chart using Matplotlib.

    Args:
        daily_charges: List of {date, values}

    Returns:
        SVG string ready for template rendering
    """
    if not daily_charges:
        return '<div class="text-center text-muted">No usage data recorded for this period</div>'

    # Extract data
    dates = daily_charges['dates']
    comp = daily_charges['values']

    # 1. Combine using zip() & sort the tuples
    combined = sorted(zip(dates, comp))

    # Check if there's any data to plot
    if not combined:
        return '<div class="text-center text-muted">No usage data recorded for this period</div>'

    # 2. Unpack using zip(*...)
    dates, comp = zip(*combined)

    # 3. Convert tuples back to lists
    dates = list(dates)
    comp = list(comp)

    # Create figure
    fig, ax = plt.subplots(figsize=(12, 4))

    # Plot stacked area chart
    ax.bar(dates, comp, width=1, lw=2)

    # Styling
    #ax.set_xlabel('Date')
    ax.set_ylabel('Charges')
    #ax.set_title('Resource Usage Over Time')
    #ax.legend(loc='upper left')
    ax.grid(True, alpha=0.3)

    # Format dates on x-axis
    fig.autofmt_xdate()

    # Render to SVG
    svg_io = StringIO()
    fig.savefig(svg_io, format='svg', bbox_inches='tight')
    plt.close(fig)

    return svg_io.getvalue()


def generate_nodetype_history_matplotlib(history_data: List[Dict], node_type: str) -> str:
    """
    Generate node type history chart showing availability and utilization over time.

    Args:
        history_data: List of dicts with timestamp, nodes_*, utilization_percent, etc.
        node_type: Name of the node type (for title)

    Returns:
        SVG string ready for template rendering
    """
    if not history_data:
        return '<div class="text-center text-muted">No history data available for this node type</div>'

    # Extract data
    timestamps = [d['timestamp'] for d in history_data]
    nodes_total = [d.get('nodes_total', 0) for d in history_data]
    nodes_available = [d.get('nodes_available', 0) for d in history_data]
    nodes_down = [d.get('nodes_down', 0) for d in history_data]
    nodes_allocated = [d.get('nodes_allocated', 0) for d in history_data]
    utilization = [d.get('utilization_percent') for d in history_data]
    memory_utilization = [d.get('memory_utilization_percent') for d in history_data]

    # Create figure with subplots
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), sharex=True)

    # Plot 1: Node Availability
    ax1.plot(timestamps, nodes_total, 'k-', linewidth=2, label='Total', marker='o', markersize=3)
    ax1.plot(timestamps, nodes_available, 'g-', linewidth=2, label='Available', marker='o', markersize=3)
    ax1.plot(timestamps, nodes_allocated, 'orange', linewidth=2, label='Allocated', marker='o', markersize=3)
    ax1.plot(timestamps, nodes_down, 'r-', linewidth=2, label='Down', marker='o', markersize=3)

    ax1.set_ylabel('Number of Nodes', fontsize=11)
    ax1.set_title(f'{node_type} - Node Availability Over Time', fontsize=13, fontweight='bold')
    ax1.legend(loc='best', fontsize=10)
    ax1.grid(True, alpha=0.3)

    # Plot 2: Utilization Percentages
    if any(u is not None for u in utilization):
        # Filter out None values for plotting
        util_times = [timestamps[i] for i, u in enumerate(utilization) if u is not None]
        util_vals = [u for u in utilization if u is not None]
        ax2.plot(util_times, util_vals, 'b-', linewidth=2, label='CPU/GPU Utilization', marker='o', markersize=3)

    if any(m is not None for m in memory_utilization):
        mem_times = [timestamps[i] for i, m in enumerate(memory_utilization) if m is not None]
        mem_vals = [m for m in memory_utilization if m is not None]
        ax2.plot(mem_times, mem_vals, 'c-', linewidth=2, label='Memory Utilization', marker='o', markersize=3)

    ax2.set_ylabel('Utilization (%)', fontsize=11)
    ax2.set_xlabel('Time', fontsize=11)
    ax2.set_ylim(0, 100)
    ax2.legend(loc='best', fontsize=10)
    ax2.grid(True, alpha=0.3)

    # Format dates on x-axis
    fig.autofmt_xdate()

    # Render to SVG
    svg_io = StringIO()
    fig.savefig(svg_io, format='svg', bbox_inches='tight')
    plt.close(fig)

    return svg_io.getvalue()
