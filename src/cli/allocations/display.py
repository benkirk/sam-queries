"""Display functions for allocation commands."""

from typing import List, Dict, Optional, Union
from cli.core.context import Context
from rich.table import Table
from rich import box


def parse_comma_list(value: Optional[str]) -> Optional[Union[str, List[str]]]:
    """
    Parse a comma-separated string into a list, or return as-is.

    Returns:
        - None if value is None
        - "TOTAL" if value is "TOTAL"
        - List of strings if comma-separated
        - Single string otherwise
    """
    if value is None or value == "TOTAL":
        return value

    # Check if contains comma
    if ',' in value:
        # Split and strip whitespace
        return [v.strip() for v in value.split(',') if v.strip()]

    return value


def display_allocation_summary(ctx: Context, results: List[Dict], show_usage: bool = False):
    """Display allocation summary results in a table."""
    if not results:
        return

    # Determine which columns to show based on first result
    sample = results[0]
    has_resource = 'resource' in sample
    has_facility = 'facility' in sample
    has_type = 'allocation_type' in sample
    has_project = 'projcode' in sample
    has_usage = 'total_used' in sample

    # Check if all rows have count=1 (useful for date column decision)
    all_single_allocations = all(row['count'] == 1 for row in results)
    has_annual_rate = any(row.get('annualized_rate') is not None for row in results)
    show_dates = all_single_allocations #and ctx.verbose
    show_count = not all_single_allocations  # Only show count when aggregating multiple allocations

    # Build table
    table = Table(title="Allocation Summary", box=box.SIMPLE_HEAD, show_header=True)

    if has_resource:
        table.add_column("Resource", style="cyan")
    if has_facility:
        table.add_column("Facility", style="magenta")
    if has_type:
        table.add_column("Type", style="yellow")
    if has_project:
        table.add_column("Project", style="green")

    # Add date columns if showing dates (after project columns, before usage/amount columns)
    if show_dates:
        table.add_column("Begin Date", justify="right", style="dim")
        table.add_column("End Date", justify="right", style="dim")
        table.add_column("# Days", justify="right", style="dim")

    # Conditional columns based on mode
    if show_usage:
        # Usage mode: show allocated, used, remaining, % used
        table.add_column("Allocated", justify="right", style="bold blue")
        table.add_column("Used", justify="right", style="yellow")
        table.add_column("Remaining", justify="right", style="green")
        table.add_column("% Used", justify="right", style="magenta")
    else:
        # Standard mode: show amounts
        table.add_column("Total Amount", justify="right", style="bold blue")
        if ctx.verbose and not all_single_allocations:
            table.add_column("Avg Amount", justify="right", style="dim")

    if has_annual_rate:
        table.add_column("Annual Rate", justify="right", style="cyan")

    # Count column always last, only when aggregating multiple allocations
    if show_count:
        table.add_column("Count", justify="right", style="dim")

    # Add rows
    total_count = 0
    total_amount = 0.0
    total_used = 0.0

    for row in results:
        table_row = []

        if has_resource:
            table_row.append(row['resource'])
        if has_facility:
            table_row.append(row['facility'])
        if has_type:
            table_row.append(row['allocation_type'])
        if has_project:
            table_row.append(row['projcode'])

        # Add dates if showing dates (after project, before usage/amounts)
        if show_dates:
            start_str = row['start_date'].strftime("%Y-%m-%d") if row.get('start_date') else "N/A"
            end_str = row['end_date'].strftime("%Y-%m-%d") if row.get('end_date') else "N/A"
            duration = '{:,}'.format(row['duration_days']) if row['duration_days'] else "N/A"
            table_row.extend([start_str, end_str, duration])

        count = row['count']
        amount = row['total_amount']
        total_count += count
        total_amount += amount

        # Show different columns based on mode
        if show_usage:
            used = row.get('total_used', 0.0)
            allocated = row.get('total_allocated', amount)
            remaining = allocated - used
            pct = row.get('percent_used', 0.0)
            total_used += used

            # Color code percent used
            pct_style = "green"
            if pct > 80: pct_style = "yellow"
            if pct > 100: pct_style = "red bold"

            table_row.extend([
                f"{allocated:,.0f}",
                f"{used:,.0f}",
                f"{remaining:,.0f}",
                f"[{pct_style}]{pct:,.1f}%[/]"
            ])
        else:
            # Standard mode: amounts
            table_row.append(f"{amount:,.0f}")
            if ctx.verbose and not all_single_allocations:
                table_row.append(f"{row['avg_amount']:,.0f}")

        # Add annual rate column if present
        if has_annual_rate:
            if row.get('annualized_rate') is not None:
                rate_str = f"{row['annualized_rate']:,.0f}"
                if row.get('is_open_ended', False):
                    rate_str += "*"  # Mark open-ended allocations
                table_row.append(rate_str)
            else:
                table_row.append("N/A")

        # Count column always last (only when aggregating)
        if show_count:
            table_row.append(str(count))

        table.add_row(*table_row)

    ctx.console.print(table)

    # Calculate sum of annualized rates
    total_annual_rate = sum(
        row.get('annualized_rate', 0.0) or 0.0
        for row in results
        if row.get('annualized_rate') is not None
    )

    # Print totals
    if show_usage:
        total_remaining = total_amount - total_used
        total_pct = (total_used / total_amount * 100) if total_amount > 0 else 0
        ctx.console.print(f"\n[bold]Grand Total:[/] {total_count:,} allocations")
        ctx.console.print(f"  Allocated: {total_amount:,.0f}")
        ctx.console.print(f"  Used: {total_used:,.0f}")
        ctx.console.print(f"  Remaining: {total_remaining:,.0f}")
        ctx.console.print(f"  Percent Used: {total_pct:.1f}%")
        if has_annual_rate and total_annual_rate > 0:
            ctx.console.print(f"  Annualized Rate: {total_annual_rate:,.0f}")
    else:
        ctx.console.print(f"\n[bold]Grand Total:[/] {total_count:,} allocations, {total_amount:,.0f} total allocation units")
        if has_annual_rate and total_annual_rate > 0:
            ctx.console.print(f"  Annualized Rate: {total_annual_rate:,.0f}")

    # Add footnote if any open-ended allocations
    if has_annual_rate and any(row.get('is_open_ended', False) for row in results):
        ctx.console.print("  * = Open-ended allocation (1-year period from now assumed)", style="dim italic")
