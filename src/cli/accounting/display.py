"""Display functions for accounting commands."""

from cli.core.context import Context
from rich.table import Table
from rich import box

from sam import fmt


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


def _expected_delta_pct(sam_tib: float, expected_bytes: int) -> float:
    expected_tib = expected_bytes / (1024 ** 4)
    if expected_tib == 0:
        return 0.0
    return (sam_tib - expected_tib) / expected_tib * 100.0


def _render_contributor_table(ctx: Context, projcode: str, contributors: list) -> None:
    """Indented per-project table showing each fileset rolled into the expected value.

    contributors: list of (child_projcode, QuotaEntry) tuples, "self" first.
    """
    t = Table(
        title=f"  subtree for {projcode}",
        box=box.SIMPLE,
        show_header=True,
        padding=(0, 1),
    )
    t.add_column("Project", style="dim green")
    t.add_column("Fileset", style="dim")
    t.add_column("Path", style="dim")
    t.add_column("Limit", justify="right")
    for child_pc, qe in contributors:
        marker = "[bold]★[/bold] " if child_pc == projcode else "  "
        t.add_row(
            f"{marker}{child_pc}",
            qe.fileset_name,
            qe.path or "—",
            fmt.size(qe.limit_bytes),
        )
    ctx.console.print(t)


def display_quota_reconcile_plan(
    ctx: Context,
    resource_name: str,
    matched: list,
    mismatched: list,
    orphaned: list,
    unmapped: list,
    *,
    dry_run: bool,
) -> None:
    """Render the four reconcile buckets as Rich tables.

    Bucket item shapes:
      matched, mismatched: (projcode, sam_tib, expected_bytes, contributors)
                           where contributors = [(child_projcode, QuotaEntry), ...]
                           with "self" (if present) sorted first, then depth-first.
      orphaned:            (projcode, sam_tib)
      unmapped:            QuotaEntry
    """
    suffix = " — dry run" if dry_run else ""
    show_breakdown = bool(ctx.verbose)

    if mismatched:
        t = Table(title=f"Mismatched ({resource_name}){suffix}",
                  box=box.SIMPLE_HEAD)
        t.add_column("Project", style="green")
        t.add_column("SAM", justify="right")
        t.add_column("Expected", justify="right")
        t.add_column("Δ", justify="right", style="yellow")
        t.add_column("Filesets", justify="right", style="dim")
        t.add_column("Action", style="bold cyan")
        for projcode, sam_tib, expected_bytes, contributors in mismatched:
            t.add_row(
                projcode,
                fmt.size(sam_tib * (1024 ** 4)),
                fmt.size(expected_bytes),
                fmt.pct(_expected_delta_pct(sam_tib, expected_bytes), decimals=1),
                str(len(contributors)),
                f"set amount → {fmt.size(expected_bytes)}",
            )
        ctx.console.print(t)
        if show_breakdown:
            for projcode, _, _, contributors in mismatched:
                _render_contributor_table(ctx, projcode, contributors)

    if orphaned:
        t = Table(title=f"Orphaned ({resource_name}){suffix}",
                  box=box.SIMPLE_HEAD)
        t.add_column("Project", style="green")
        t.add_column("SAM", justify="right")
        t.add_column("Action", style="bold cyan")
        for projcode, sam_tib in orphaned:
            t.add_row(
                projcode,
                fmt.size(sam_tib * (1024 ** 4)),
                "set end_date → today",
            )
        ctx.console.print(t)

    if unmapped:
        t = Table(title=f"Unmapped quota entries ({resource_name})",
                  box=box.SIMPLE_HEAD)
        t.add_column("Fileset", style="yellow")
        t.add_column("Path", style="dim")
        t.add_column("Limit", justify="right")
        t.add_column("Usage", justify="right")
        for qe in unmapped:
            t.add_row(
                qe.fileset_name,
                qe.path or "—",
                fmt.size(qe.limit_bytes),
                fmt.size(qe.usage_bytes),
            )
        ctx.console.print(t)

    if ctx.verbose and matched:
        t = Table(title=f"Matched ({resource_name})", box=box.SIMPLE_HEAD)
        t.add_column("Project", style="green")
        t.add_column("SAM", justify="right")
        t.add_column("Expected", justify="right")
        t.add_column("Δ", justify="right", style="dim")
        t.add_column("Filesets", justify="right", style="dim")
        for projcode, sam_tib, expected_bytes, contributors in matched:
            t.add_row(
                projcode,
                fmt.size(sam_tib * (1024 ** 4)),
                fmt.size(expected_bytes),
                fmt.pct(_expected_delta_pct(sam_tib, expected_bytes), decimals=2),
                str(len(contributors)),
            )
        ctx.console.print(t)


def display_quota_reconcile_summary(
    ctx: Context,
    *,
    matched: int,
    mismatched: int,
    orphaned: int,
    unmapped: int,
    updated: int = 0,
    deactivated: int = 0,
    errors: int = 0,
    dry_run: bool,
) -> None:
    """One-shot summary after a reconcile run."""
    t = Table(title="Reconcile Summary", show_header=False, box=None)
    t.add_column("Label", style="dim")
    t.add_column("Count", justify="right", style="bold")

    t.add_row("Matched", f"[green]{matched}[/green]")
    t.add_row("Mismatched", f"[yellow]{mismatched}[/yellow]")
    t.add_row("Orphaned", f"[yellow]{orphaned}[/yellow]")
    t.add_row("Unmapped quota entries", str(unmapped))
    if not dry_run:
        t.add_row("Allocations updated", f"[cyan]{updated}[/cyan]")
        t.add_row("Allocations deactivated", f"[cyan]{deactivated}[/cyan]")
        if errors:
            t.add_row("Errors", f"[red]{errors}[/red]")

    ctx.console.print(t)
