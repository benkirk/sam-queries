"""Display functions for accounting commands."""

from cli.core.context import Context
from rich.table import Table
from rich import box


def display_dry_run_table(ctx: Context, rows: list, machine: str, adapt_fn,
                          normalize_queue_fn=None, dry_run: bool = True) -> None:
    """
    Print a Rich table showing what would be (or was) posted to comp_charge_summary.

    Args:
        ctx: CLI context (for console output)
        rows: List of dicts from JobQueries.daily_summary_report()
        machine: Machine name ('derecho' or 'casper')
        adapt_fn: adapt_hpc_row callable — passed in to avoid a circular import
        dry_run: When True, title notes rows were not written
    """
    title = (
        f"Dry Run — {machine} charge rows (not written)"
        if dry_run
        else f"{machine} charge rows"
    )
    table = Table(
        title=title,
        box=box.SIMPLE_HEAD,
        show_lines=False,
    )
    table.add_column("Date", style="cyan", no_wrap=True)
    table.add_column("User", style="white")
    table.add_column("Account", style="white")
    table.add_column("Queue", style="white")
    table.add_column("Jobs", justify="right")
    table.add_column("CPU-h", justify="right", style="dim")
    table.add_column("CPU-c", justify="right", style="dim")
    table.add_column("GPU-h", justify="right", style="dim")
    table.add_column("GPU-c", justify="right", style="dim")
    table.add_column("→ Resource", style="green")
    table.add_column("→ Machine", style="dim green")
    table.add_column("charges", justify="right", style="bold")

    n_skipped = 0
    for row in rows:
        result = adapt_fn(row, machine)
        if result is None:
            n_skipped += 1
            continue
        resource_name, machine_name, core_hours, charges = result
        queue = normalize_queue_fn(row["queue"]) if normalize_queue_fn else row["queue"]
        table.add_row(
            str(row["date"]),
            row["user"],
            row["account"],
            queue,
            str(row["job_count"]),
            f"{row['cpu_hours'] or 0.0:.1f}",
            f"{row['cpu_charges'] or 0.0:.1f}",
            f"{row['gpu_hours'] or 0.0:.1f}",
            f"{row['gpu_charges'] or 0.0:.1f}",
            resource_name,
            machine_name,
            f"{charges:.1f}",
        )

    ctx.console.print(table)
    if n_skipped:
        ctx.console.print(f"[dim]({n_skipped} zero-charge rows omitted)[/dim]")


def display_import_summary(ctx: Context, n_created: int, n_updated: int,
                           n_errors: int, n_skipped: int) -> None:
    """
    Print a Rich summary table after a live accounting import run.

    Args:
        ctx: CLI context (for console output)
        n_created: Number of new rows inserted
        n_updated: Number of existing rows updated
        n_errors: Number of rows that raised ValueError (skipped or aborted)
        n_skipped: Number of zero-charge rows skipped by adapt_hpc_row()
    """
    table = Table(title="Import Summary", show_header=False, box=None)
    table.add_column("Label", style="dim")
    table.add_column("Count", justify="right", style="bold")

    table.add_row("Created", f"[green]{n_created}[/green]")
    table.add_row("Updated", f"[cyan]{n_updated}[/cyan]")
    table.add_row("Skipped (zero-charge)", str(n_skipped))
    if n_errors:
        table.add_row("Errors", f"[red]{n_errors}[/red]")

    ctx.console.print(table)


def display_charge_summary_table(ctx: Context, rows: list, start_date, end_date) -> None:
    """
    Print a Rich table of aggregated comp_charge_summary records.

    Used by sam-search accounting to show what has been posted to SAM.
    Columns include Date (verbose/per-day only), User, Project, Resource,
    Machine, Queue, Jobs, Core-h, and Charges.

    Args:
        ctx: CLI context (console + verbose flag)
        rows: List of dicts from query_comp_charge_summaries()
        start_date: Start of the queried date range
        end_date: End of the queried date range
    """
    per_day = ctx.verbose and rows and 'activity_date' in rows[0]

    table = Table(
        title=f"comp_charge_summary  {start_date} → {end_date}",
        box=box.SIMPLE_HEAD,
        show_lines=False,
    )

    if per_day:
        table.add_column("Date", style="cyan", no_wrap=True)
    table.add_column("User", style="cyan")
    table.add_column("Project", style="green")
    table.add_column("Resource", style="white")
    table.add_column("Machine", style="dim")
    table.add_column("Queue", style="white")
    table.add_column("Jobs", justify="right")
    table.add_column("Core-h", justify="right", style="dim")
    table.add_column("Charges", justify="right", style="bold")

    total_jobs = 0
    total_core_hours = 0.0
    total_charges = 0.0

    for row in rows:
        total_jobs += row['total_jobs']
        total_core_hours += row['total_core_hours']
        total_charges += row['total_charges']

        cols = []
        if per_day:
            cols.append(str(row['activity_date']))
        cols += [
            row['username'] or '',
            row['projcode'] or '',
            row['resource'] or '',
            row['machine'] or '',
            row['queue'] or '',
            str(row['total_jobs']),
            f"{row['total_core_hours']:.1f}",
            f"{row['total_charges']:.1f}",
        ]
        table.add_row(*cols)

    # Totals footer
    footer_cols = []
    if per_day:
        footer_cols.append('')
    footer_cols += [
        f"[dim]{len(rows)} rows[/dim]", '',
        '', '',  # resource, machine
        '',  # queue
        f"[bold]{total_jobs}[/bold]",
        f"[dim]{total_core_hours:.1f}[/dim]",
        f"[bold]{total_charges:.1f}[/bold]",
    ]
    table.add_section()
    table.add_row(*footer_cols)

    ctx.console.print(table)
