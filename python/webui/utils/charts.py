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
        return '<p class="text-muted">No data available</p>'

    # Extract data
    dates = daily_charges['dates']
    comp = daily_charges['values']

    # 1. Combine using zip() & sort the tuples
    combined = sorted(zip(dates, comp))

    # 2. Unpack using zip(*...)
    dates, comp = zip(*combined)

    # 3. Convert tuples back to lists
    dates = list(dates)
    comp = list(comp)

    # Create figure
    fig, ax = plt.subplots(figsize=(12, 4))

    # Plot stacked area chart
    ax.bar(dates, comp, width=1)

    # Styling
    ax.set_xlabel('Date')
    ax.set_ylabel('Charges (core-hours)')
    ax.set_title('Resource Usage Over Time')
    #ax.legend(loc='upper left')
    ax.grid(True, alpha=0.3)

    # Format dates on x-axis
    fig.autofmt_xdate()

    # Render to SVG
    svg_io = StringIO()
    fig.savefig(svg_io, format='svg', bbox_inches='tight')
    plt.close(fig)

    return svg_io.getvalue()
