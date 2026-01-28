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

    import matplotlib.cm as cm
    colors = cm.tab10.colors

    # Extract data
    timestamps = [d['timestamp'] for d in history_data]
    nodes_total = [d.get('nodes_total', 0) for d in history_data]
    nodes_available = [d.get('nodes_available', 0) for d in history_data]
    nodes_down = [d.get('nodes_down', 0) for d in history_data]
    nodes_allocated = [d.get('nodes_allocated', 0) for d in history_data]
    utilization = [d.get('utilization_percent') for d in history_data]
    memory_utilization = [d.get('memory_utilization_percent') for d in history_data]

    # Create figure with subplots
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 8), sharex=True)

    # Plot 1: Node Availability
    #ax1.plot(timestamps, nodes_total, 'k-', linewidth=2, label='Total', marker='o', markersize=3)
    #ax1.plot(timestamps, nodes_available, 'g-', linewidth=2, label='Available', marker='o', markersize=3)
    #ax1.plot(timestamps, nodes_allocated, 'orange', linewidth=2, label='Allocated', marker='o', markersize=3)
    #ax1.plot(timestamps, nodes_down, 'r-', linewidth=2, label='Down', marker='o', markersize=3)
    ax1.stackplot(timestamps, nodes_down, nodes_allocated, nodes_available,
                  labels=['Down', 'Fully Allocated', 'Resources Available'],
                  colors=['C3', 'C0', 'C9'])

    ax1.set_ylabel('Number of Nodes', fontsize=11)
    ax1.set_ylim([0,None])
    ax1.set_title(f'{node_type} - Node Availability Over Time', fontsize=13, fontweight='bold')
    ax1.legend(loc=2, fontsize=10)
    ax1.grid(True, alpha=0.3,color='grey')

    # Plot 2: Utilization Percentages
    if any(u is not None for u in utilization):
        # Filter out None values for plotting
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

    # Format dates on x-axis
    fig.autofmt_xdate()

    # Render to SVG
    svg_io = StringIO()
    fig.savefig(svg_io, format='svg', bbox_inches='tight')
    plt.close(fig)

    return svg_io.getvalue()


def generate_queue_history_matplotlib(history_data: List[Dict], queue_name: str, system_name: str) -> str:
    """
    Generate queue history chart showing job flow and resource demand over time.

    Args:
        history_data: List of dicts with timestamp, job counts, resources
        queue_name: Name of the queue (for title)
        system_name: System name (for title)

    Returns:
        SVG string ready for template rendering
    """
    if not history_data:
        return '<div class="text-center text-muted">No history data available for this queue</div>'

    # Extract data
    timestamps = [d['timestamp'] for d in history_data]
    running_jobs = [d.get('running_jobs', 0) for d in history_data]
    pending_jobs = [d.get('pending_jobs', 0) for d in history_data]
    held_jobs = [d.get('held_jobs', 0) for d in history_data]
    active_users = [d.get('active_users', 0) for d in history_data]

    cores_allocated = [d.get('cores_allocated', 0) for d in history_data]
    cores_pending = [d.get('cores_pending', 0) for d in history_data]
    gpus_allocated = [d.get('gpus_allocated', 0) for d in history_data]
    gpus_pending = [d.get('gpus_pending', 0) for d in history_data]

    # Determine if this queue uses GPUs
    has_gpus = any(gpus_allocated) or any(gpus_pending)

    # Create figure with subplots
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), sharex=True)

    # Plot 1: Job Flow
    ax1.plot(timestamps, running_jobs, 'g-', linewidth=2, label='Running', marker='o', markersize=3)
    ax1.plot(timestamps, pending_jobs, 'orange', linewidth=2, label='Pending', marker='o', markersize=3)
    ax1.plot(timestamps, held_jobs, 'r-', linewidth=2, label='Held', marker='o', markersize=3)
    ax1.plot(timestamps, active_users, 'b--', linewidth=1.5, label='Active Users', marker='s', markersize=2)

    ax1.set_ylim([0,None])
    ax1.set_ylabel('Count', fontsize=11)
    ax1.set_title(f'{system_name.upper()} - {queue_name} Queue Activity', fontsize=13, fontweight='bold')
    ax1.legend(loc=2, fontsize=10)
    ax1.grid(True, alpha=0.3)

    # Plot 2: Resource Demand
    if has_gpus:
        ax2.plot(timestamps, gpus_allocated, 'b', linewidth=3, label='GPUs Running')
        ax2.plot(timestamps, gpus_pending, 'c', linewidth=3, label='GPUs Pending')

    else:
        ax2.plot(timestamps, cores_allocated, 'b', linewidth=3, label='Cores Running')
        ax2.plot(timestamps, cores_pending, 'c', linewidth=3, label='Cores Pending')

    ax2.set_ylim([0,None])
    ax2.set_ylabel('Resources', fontsize=11)
    ax2.set_xlabel('Time', fontsize=11)
    ax2.legend(loc=2, fontsize=10)
    ax2.grid(True, alpha=0.3)

    # Format dates on x-axis
    fig.autofmt_xdate()

    # Render to SVG
    svg_io = StringIO()
    fig.savefig(svg_io, format='svg', bbox_inches='tight')
    plt.close(fig)

    return svg_io.getvalue()


def generate_facility_pie_chart_matplotlib(facility_data: List[Dict], title: str = 'Annualized Rate Distribution by Facility') -> str:
    """
    Generate pie chart showing annualized rate distribution by facility.

    Args:
        facility_data: List of dicts with keys:
            - facility: Facility name (e.g., 'UNIV', 'WNA', 'NCAR')
            - annualized_rate: Annual allocation rate
            - count: Number of allocations
            - percent: Percentage of total
        title: Chart title (default: 'Annualized Rate Distribution by Facility')

    Returns:
        SVG string ready for template rendering
    """
    if not facility_data:
        return '<div class="text-center text-muted">No facility data available</div>'

    # Extract data
    facilities = [d['facility'] for d in facility_data]
    rates = [d['annualized_rate'] for d in facility_data]
    percentages = [d['percent'] for d in facility_data]

    # Create figure
    fig, ax = plt.subplots(figsize=(8, 6))

    # Generate pie chart
    colors = plt.cm.tab10.colors[:len(facilities)]
    wedges, texts, autotexts = ax.pie(
        rates,
        labels=facilities,
        autopct='%1.1f%%',
        startangle=90,
        colors=colors,
        textprops={'fontsize': 11}
    )

    # Make percentage text bold
    for autotext in autotexts:
        autotext.set_color('white')
        autotext.set_fontweight('bold')

    ax.set_title(title, fontsize=13, fontweight='bold', pad=20)

    # Render to SVG
    svg_io = StringIO()
    fig.savefig(svg_io, format='svg', bbox_inches='tight')
    plt.close(fig)

    return svg_io.getvalue()


def generate_allocation_type_pie_chart_matplotlib(type_data: List[Dict], resource_type: str, facility_name: str) -> str:
    """
    Generate pie chart showing allocation distribution by allocation type within a facility.

    Args:
        type_data: List of dicts with keys:
            - allocation_type: Type name (e.g., 'NSC', 'Small', 'University')
            - total_amount: Total allocated amount
            - count: Number of allocations
            - avg_amount: Average allocation amount
        resource_type: Resource type string ('HPC', 'DAV', 'DISK', 'ARCHIVE')
        facility_name: Facility name for chart title

    Returns:
        SVG string ready for template rendering
    """
    if not type_data:
        return '<div class="text-center text-muted">No allocation type data available</div>'

    # Extract data
    allocation_types = [d['allocation_type'] for d in type_data]
    amounts = [d['total_amount'] for d in type_data]

    # Create figure
    fig, ax = plt.subplots(figsize=(8, 6))

    # Generate pie chart
    colors = plt.cm.tab10.colors[:len(allocation_types)]
    wedges, texts, autotexts = ax.pie(
        amounts,
        labels=allocation_types,
        autopct='%1.1f%%',
        startangle=90,
        colors=colors,
        textprops={'fontsize': 11}
    )

    # Make percentage text bold and white
    for autotext in autotexts:
        autotext.set_color('white')
        autotext.set_fontweight('bold')

    # Dynamic title based on resource type
    if resource_type in ['DISK', 'ARCHIVE']:
        title = f'Data Volume Distribution by Type - {facility_name}'
    else:
        title = f'Allocation Distribution by Type - {facility_name}'

    ax.set_title(title, fontsize=13, fontweight='bold', pad=20)

    # Render to SVG
    svg_io = StringIO()
    fig.savefig(svg_io, format='svg', bbox_inches='tight')
    plt.close(fig)

    return svg_io.getvalue()
