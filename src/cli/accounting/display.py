"""Display functions for accounting commands."""

from cli.core.context import Context
from rich.table import Table
from rich import box


def display_dry_run_table(ctx: Context, rows: list, machine: str, adapt_fn,
                          normalize_queue_fn=None) -> None:
    """
    Print a Rich table showing what would be posted to comp_charge_summary.

    Args:
        ctx: CLI context (for console output)
        rows: List of dicts from JobQueries.daily_summary_report()
        machine: Machine name ('derecho' or 'casper')
        adapt_fn: adapt_hpc_row callable — passed in to avoid a circular import
    """
    table = Table(
        title=f"Dry Run — {machine} charge rows (not written)",
        box=box.SIMPLE_HEAD,
        show_lines=False,
    )
    table.add_column("Date", style="cyan", no_wrap=True)
    table.add_column("User", style="white")
    table.add_column("Account", style="white")
    table.add_column("Queue", style="white")
    table.add_column("Jobs", justify="right")
    table.add_column("CPU-h", justify="right", style="dim")
    table.add_column("GPU-h", justify="right", style="dim")
    table.add_column("→ Resource", style="green")
    table.add_column("→ Machine", style="dim green")
    table.add_column("core_h", justify="right", style="bold")
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
            f"{row['gpu_hours'] or 0.0:.1f}",
            resource_name,
            machine_name,
            f"{core_hours:.1f}",
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
