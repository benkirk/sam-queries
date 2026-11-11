"""Display functions for allocation commands."""

from typing import List, Dict, Optional, Union
from cli.core.context import Context
from rich.table import Table
from rich import box
from cli.core.output import print_table
from sam import fmt


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
    has_root = has_project and any(not row.get('is_root', True) for row in results)

    all_single_allocations = all(row['count'] == 1 for row in results)
    has_annual_rate = any(row.get('annualized_rate') is not None for row in results)
    show_dates = all_single_allocations
    show_count = not all_single_allocations  # Only show count when aggregating multiple allocations

    table = Table(title="Allocation Summary", box=box.SIMPLE_HEAD, show_header=True)

    if has_resource:
        table.add_column("Resource", style="cyan")
    if has_facility:
        table.add_column("Facility", style="magenta")
    if has_type:
        table.add_column("Type", style="yellow")
    if has_project:
        table.add_column("Project", style="green")
    if has_root:
        table.add_column("Root", style="dim")

    if show_dates:
        table.add_column("Begin Date", justify="right", style="dim")
        table.add_column("End Date", justify="right", style="dim")
        table.add_column("# Days", justify="right", style="dim")

    if show_usage:
        table.add_column("Allocated", justify="right", style="bold blue")
        table.add_column("Used", justify="right", style="yellow")
        table.add_column("Remaining", justify="right", style="green")
        table.add_column("% Used", justify="right", style="magenta")
    else:
        table.add_column("Total Amount", justify="right", style="bold blue")
        if ctx.verbose and not all_single_allocations:
            table.add_column("Avg Amount", justify="right", style="dim")

    if has_annual_rate:
        table.add_column("Annual Rate", justify="right", style="cyan")

    if show_count:
        table.add_column("Count", justify="right", style="dim")

    for row in results:
        # A pool member's amount, rate and remaining are its root's pool: dim them.
        member = bool(row.get('is_inheriting'))

        def pool(text):
            return f"[dim]{text}[/]" if member else text

        table_row = []
        if has_resource:
            table_row.append(row['resource'])
        if has_facility:
            table_row.append(row['facility'])
        if has_type:
            table_row.append(row['allocation_type'])
        if has_project:
            table_row.append(row['projcode'])
        if has_root:
            table_row.append('' if row.get('is_root', True) else f"↳ {row.get('project_root_projcode')}")

        if show_dates:
            start_str = fmt.date_str(row.get('start_date'), null='N/A')
            end_str   = fmt.date_str(row.get('end_date'), null='N/A')
            duration  = fmt.number(row['duration_days']) if row['duration_days'] else 'N/A'
            table_row.extend([start_str, end_str, duration])

        amount = row['total_amount']

        if show_usage:
            allocated = row.get('total_allocated', amount)
            pool_used = row.get('total_used', 0.0)
            # The web table's rule: a member's Used is its own, not the pool's.
            used = row['self_used'] if member and 'self_used' in row else pool_used
            pct = row.get('percent_used', 0.0)

            pct_style = "green"
            if pct > 80: pct_style = "yellow"
            if pct > 100: pct_style = "red bold"

            table_row.extend([
                pool(fmt.number(allocated)),
                fmt.number(used),
                pool(fmt.number(allocated - pool_used)),
                pool(f"[{pct_style}]{fmt.pct(pct)}[/]"),
            ])
        else:
            table_row.append(pool(fmt.number(amount)))
            if ctx.verbose and not all_single_allocations:
                table_row.append(fmt.number(row['avg_amount']))

        if has_annual_rate:
            if row.get('annualized_rate') is not None:
                rate_str = fmt.number(row['annualized_rate'])
                if row.get('is_open_ended', False):
                    rate_str += "*"  # Mark open-ended allocations
                table_row.append(pool(rate_str))
            else:
                table_row.append("N/A")

        if show_count:
            table_row.append(str(row['count']))

        table.add_row(*table_row)

    print_table(ctx.console, table)

    # Totals count tree roots only, as the rows' rollups do: a member's row repeats its
    # root's award. A filter naming only child projects has no roots: sum what is shown.
    root_rows = [r for r in results if r.get('is_root', True)]
    totals_rows = root_rows if root_rows else results
    left_out = len(results) - len(totals_rows)

    total_count = sum(r['count'] for r in totals_rows)
    total_amount = sum(r['total_amount'] for r in totals_rows)
    total_used = sum(r.get('total_used', 0.0) for r in totals_rows)
    total_annual_rate = sum(r.get('annualized_rate') or 0.0 for r in totals_rows)

    label = "Grand Total"
    if left_out:
        label += f" (roots only; {fmt.plural(left_out, 'sub-project row', 'sub-project rows')} not summed)"
    if show_usage:
        total_remaining = total_amount - total_used
        total_pct = (total_used / total_amount * 100) if total_amount > 0 else 0
        ctx.console.print(f"\n[bold]{label}:[/] {fmt.plural(total_count, 'allocation', 'allocations')}")
        ctx.console.print(f"  Allocated: {fmt.number(total_amount)}")
        ctx.console.print(f"  Used: {fmt.number(total_used)}")
        ctx.console.print(f"  Remaining: {fmt.number(total_remaining)}")
        ctx.console.print(f"  Percent Used: {fmt.pct(total_pct)}")
    else:
        ctx.console.print(f"\n[bold]{label}:[/] {fmt.plural(total_count, 'allocation', 'allocations')}, "
                          f"{fmt.number(total_amount)} total allocation units")
    if has_annual_rate and total_annual_rate > 0:
        ctx.console.print(f"  Annualized Rate: {fmt.number(total_annual_rate)}")

    if any(r.get('is_inheriting') for r in results):
        own = ", Used is the member's own" if show_usage else ""
        ctx.console.print(f"  ↳ dim figures on a shared-pool member are its root's pool{own}",
                          style="dim italic")
    if has_annual_rate and any(row.get('is_open_ended', False) for row in results):
        ctx.console.print("  * = Open-ended allocation (1-year period from now assumed)", style="dim italic")
