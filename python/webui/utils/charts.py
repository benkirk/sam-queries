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


def generate_usage_sparkline(daily_charges: List[Dict], width: int = 1000, height: int = 200) -> str:
    """
    Generate a simple SVG sparkline chart for usage data.

    Args:
        daily_charges: List of dicts with keys: date, comp, dav, disk, archive
        width: Chart width in pixels
        height: Chart height in pixels

    Returns:
        HTML string with embedded SVG chart

    Example:
        >>> charges = [
        ...     {'date': date(2024, 1, 1), 'comp': 1234.5, 'dav': 567.8, 'disk': 0, 'archive': 0},
        ...     {'date': date(2024, 1, 2), 'comp': 2345.6, 'dav': 678.9, 'disk': 0, 'archive': 0}
        ... ]
        >>> html = generate_usage_sparkline(charges)
    """
    if not daily_charges:
        return '<div class="text-muted">No usage data available for selected date range.</div>'

    # Chart dimensions
    padding = 20
    chart_width = width - (2 * padding)
    chart_height = height - (2 * padding)

    # Calculate totals for each day
    totals = []
    for day in daily_charges:
        total = (day.get('comp', 0) + day.get('dav', 0) +
                 day.get('disk', 0) + day.get('archive', 0))
        totals.append(total)

    # Find max value for scaling
    max_value = max(totals) if totals else 1
    if max_value == 0:
        max_value = 1  # Avoid division by zero

    # Calculate points for line
    num_points = len(daily_charges)
    x_step = chart_width / (num_points - 1) if num_points > 1 else chart_width

    points = []
    for i, total in enumerate(totals):
        x = padding + (i * x_step)
        y = padding + chart_height - (total / max_value * chart_height)
        points.append(f"{x:.2f},{y:.2f}")

    polyline_points = " ".join(points)

    # Create area fill points (add bottom corners)
    area_points = polyline_points
    if points:
        last_x = padding + ((num_points - 1) * x_step)
        area_points += f" {last_x:.2f},{padding + chart_height} {padding:.2f},{padding + chart_height}"

    # Format dates for x-axis labels (show ~5 labels)
    label_indices = []
    if num_points <= 5:
        label_indices = list(range(num_points))
    else:
        step = num_points // 5
        label_indices = [i * step for i in range(5)]
        if label_indices[-1] != num_points - 1:
            label_indices.append(num_points - 1)

    x_labels = ""
    for i in label_indices:
        if i < len(daily_charges):
            x = padding + (i * x_step)
            date_obj = daily_charges[i]['date']
            date_str = date_obj.strftime('%m/%d') if hasattr(date_obj, 'strftime') else str(date_obj)
            x_labels += f'<text x="{x:.2f}" y="{height - 10}" text-anchor="middle" font-size="12" fill="#666">{date_str}</text>\n'

    # Y-axis labels (show max and mid value)
    y_labels = f"""
        <text x="10" y="{padding + 5}" font-size="12" fill="#666">{format_number(max_value)}</text>
        <text x="10" y="{padding + chart_height/2 + 5}" font-size="12" fill="#666">{format_number(max_value/2)}</text>
        <text x="10" y="{padding + chart_height + 5}" font-size="12" fill="#666">0</text>
    """

    # Generate SVG
    svg = f"""
    <svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">
        <!-- Background -->
        <rect width="{width}" height="{height}" fill="#f8f9fa"/>

        <!-- Grid lines -->
        <line x1="{padding}" y1="{padding}" x2="{padding}" y2="{padding + chart_height}" stroke="#dee2e6" stroke-width="1"/>
        <line x1="{padding}" y1="{padding + chart_height}" x2="{padding + chart_width}" y2="{padding + chart_height}" stroke="#dee2e6" stroke-width="1"/>
        <line x1="{padding}" y1="{padding + chart_height/2}" x2="{padding + chart_width}" y2="{padding + chart_height/2}" stroke="#dee2e6" stroke-width="1" stroke-dasharray="5,5"/>

        <!-- Area fill -->
        <polygon points="{area_points}" fill="#007bff" fill-opacity="0.1" stroke="none"/>

        <!-- Line -->
        <polyline points="{polyline_points}" fill="none" stroke="#007bff" stroke-width="2"/>

        <!-- Data points -->
        {chr(10).join(f'<circle cx="{x.split(",")[0]}" cy="{x.split(",")[1]}" r="3" fill="#007bff"/>' for x in points)}

        <!-- Axis labels -->
        {x_labels}
        {y_labels}

        <!-- Chart title -->
        <text x="{width/2}" y="20" text-anchor="middle" font-size="14" font-weight="bold" fill="#333">Daily Usage Trend</text>
    </svg>
    """

    return svg



import matplotlib
matplotlib.use('Agg')  # Non-interactive backend
import matplotlib.pyplot as plt
from io import StringIO
from typing import List, Dict

def generate_usage_timeseries_matplotlib(daily_charges: List[Dict]) -> str:
    """
    Generate advanced time-series chart using Matplotlib.

    Args:
        daily_charges: List of {date, comp, dav, disk, archive} dicts

    Returns:
        SVG string ready for template rendering
    """
    if not daily_charges:
        return '<p class="text-muted">No data available</p>'

    # Extract data
    dates = [d['date'] for d in daily_charges]
    comp = [d.get('comp', 0) for d in daily_charges]
    dav = [d.get('dav', 0) for d in daily_charges]

    # Create figure
    fig, ax = plt.subplots(figsize=(10, 4))

    # Plot stacked area chart
    ax.plot(dates, comp)

    # Styling
    ax.set_xlabel('Date')
    ax.set_ylabel('Charges (core-hours)')
    ax.set_title('Resource Usage Over Time')
    ax.legend(loc='upper left')
    ax.grid(True, alpha=0.3)

    # Format dates on x-axis
    fig.autofmt_xdate()

    # Render to SVG
    svg_io = StringIO()
    fig.savefig(svg_io, format='svg', bbox_inches='tight')
    plt.close(fig)

    return svg_io.getvalue()
