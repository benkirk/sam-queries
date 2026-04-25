"""Display functions for accounting commands."""

from cli.core.context import Context
from rich.table import Table
from rich.tree import Tree
from rich.panel import Panel
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


HIGH_UTIL_THRESHOLD = 0.95   # annotate matched filesets >95% full
LOW_UTIL_THRESHOLD = 0.05    # annotate matched filesets <5% full


def _util_suffix(qe) -> str:
    """Return a util-annotation suffix.

    - High-side warning (`⚠ 97%`, yellow) when usage > 95% of limit.
    - Low-side note    (`↓ 3%`,  cyan)   when usage < 5%  of limit.
    - Empty string in the comfortable middle band.

    The low-side flag surfaces over-allocated filesets that might be
    candidates for downsizing — the bookend to the high-util warning.
    Filesets with limit==0 (impossible to be "under-used" meaningfully)
    are skipped via the QuotaEntry.utilization property which clamps
    to 0.0 when limit is zero, BUT we also require usage_bytes > 0 so
    a brand-new empty fileset doesn't get flagged as under-used.
    """
    u = getattr(qe, 'utilization', 0.0)
    if u > HIGH_UTIL_THRESHOLD:
        return f" [yellow]⚠ {int(round(u * 100))}%[/yellow]"
    if u < LOW_UTIL_THRESHOLD and getattr(qe, 'limit_bytes', 0) > 0:
        return f" [cyan]↓ {int(round(u * 100))}%[/cyan]"
    return ""


def _fs_marker(path: str | None, path_exists: dict | None) -> str:
    """Return a ✓/✗/— marker for a path.

    - ``—`` when verification wasn't requested (path_exists is None) or
      the path is None.
    - ``✓`` when verified present.
    - ``✗`` when verified missing.
    """
    if path_exists is None or not path:
        return "—"
    if path_exists.get(path, False):
        return "[green]✓[/green]"
    return "[red]✗[/red]"


def _leaf_cells(contributors: list) -> tuple[str, str, str]:
    """(fileset, path, limit) for single-contributor rows; marker pair otherwise.

    Returns strings ready for a Rich Table cell.
    """
    if len(contributors) == 1:
        _, qe = contributors[0]
        return (qe.fileset_name + _util_suffix(qe),
                (qe.path or "—"),
                fmt.size(qe.limit_bytes))
    return (f"[dim]{len(contributors)} filesets[/dim]",
            "[dim]↓ see subtree[/dim]", "")


def _render_subtree_panel(ctx: Context, projcode: str, sam_tib: float,
                          expected_bytes: int, contributors: list) -> None:
    """Render a Rich Tree inside a Panel titled ``subtree for <projcode>``.

    Mirrors the "Project Hierarchy" style used by `sam-search project` so
    the visual language is consistent across the CLI. Contributors arrive
    in depth-first order (self first if present); we list them flat under
    the root — the actual project-tree nesting isn't preserved beyond
    that in the current roll-up.
    """
    # Column widths for aligned labels inside the tree.
    w_pc   = max(len(pc) for pc, _ in contributors)
    w_fs   = max(len(qe.fileset_name) for _, qe in contributors)
    w_path = max(len(qe.path or "—") for _, qe in contributors)

    header = (
        f"[bold]{projcode}[/bold] — SAM {fmt.size(sam_tib * (1024 ** 4))}, "
        f"expected {fmt.size(expected_bytes)} "
        f"([dim]{len(contributors)} fileset"
        f"{'s' if len(contributors) != 1 else ''}[/dim])"
    )
    tree = Tree(header)
    for child_pc, qe in contributors:
        marker = "[bold yellow]★[/bold yellow]" if child_pc == projcode else " "
        label = (
            f"{marker} [green]{child_pc:<{w_pc}}[/green]  "
            f"[dim]{qe.fileset_name:<{w_fs}}[/dim]  "
            f"[dim]{(qe.path or '—'):<{w_path}}[/dim]  "
            f"{fmt.size(qe.limit_bytes):>10}"
            f"{_util_suffix(qe)}"
        )
        tree.add(label)
    ctx.console.print(Panel(tree, title=f"subtree for {projcode}",
                            border_style="blue", expand=False))


def display_quota_reconcile_plan(
    ctx: Context,
    resource_name: str,
    matched: list,
    mismatched: list,
    orphaned: list,
    unmapped: list,
    *,
    dry_run: bool,
    path_exists: dict | None = None,
) -> None:
    """Render the four reconcile buckets as Rich tables.

    Bucket item shapes:
      matched, mismatched: (projcode, sam_tib, expected_bytes, contributors)
                           where contributors = [(child_projcode, QuotaEntry), ...]
                           with "self" (if present) sorted first, then depth-first.
      orphaned:            (projcode, sam_tib, directories: list[str])
      unmapped:            QuotaEntry

    ``path_exists`` is provided when ``--verify-paths`` was requested —
    a mapping of absolute path → presence bool. When None, the FS column
    is omitted entirely from the Orphaned / Unmapped tables.
    """
    suffix = " — dry run" if dry_run else ""
    show_breakdown = bool(ctx.verbose)
    show_fs = path_exists is not None

    if mismatched:
        t = Table(title=f"Mismatched ({resource_name}){suffix}",
                  box=box.SIMPLE_HEAD)
        t.add_column("Project", style="green")
        t.add_column("SAM", justify="right")
        t.add_column("Expected", justify="right")
        t.add_column("Δ", justify="right", style="yellow")
        t.add_column("Fileset", style="dim")
        t.add_column("Path", style="dim")
        t.add_column("Action", style="bold cyan")
        for projcode, sam_tib, expected_bytes, contributors in mismatched:
            fileset, path, _ = _leaf_cells(contributors)
            t.add_row(
                projcode,
                fmt.size(sam_tib * (1024 ** 4)),
                fmt.size(expected_bytes),
                fmt.pct(_expected_delta_pct(sam_tib, expected_bytes), decimals=1),
                fileset, path,
                f"set amount → {fmt.size(expected_bytes)}",
            )
        ctx.console.print(t)
        if show_breakdown:
            for projcode, sam_tib, expected_bytes, contributors in mismatched:
                if len(contributors) > 1:
                    _render_subtree_panel(ctx, projcode, sam_tib,
                                          expected_bytes, contributors)

    if orphaned:
        t = Table(title=f"Orphaned ({resource_name}){suffix}",
                  box=box.SIMPLE_HEAD)
        t.add_column("Project", style="green")
        t.add_column("SAM", justify="right")
        t.add_column("ProjectDirectory", style="dim")
        if show_fs:
            t.add_column("FS", justify="center")
        t.add_column("Action", style="bold cyan")
        for projcode, sam_tib, directories in orphaned:
            if directories:
                dir_cell = "\n".join(directories)
                if show_fs:
                    markers = "\n".join(
                        _fs_marker(d, path_exists) for d in directories
                    )
                else:
                    markers = ""
                all_live = (
                    show_fs
                    and directories
                    and all(path_exists.get(d, False) for d in directories)
                )
            else:
                dir_cell = "—"
                markers = "—"
                all_live = False
            action_cell = (
                "[bold yellow]paths still live — requires --force[/bold yellow]"
                if all_live else "set end_date → today"
            )
            row = [projcode, fmt.size(sam_tib * (1024 ** 4)), dir_cell]
            if show_fs:
                row.append(markers)
            row.append(action_cell)
            t.add_row(*row)
        ctx.console.print(t)
        if ctx.verbose:
            ctx.console.print(
                "[dim italic]Orphaned: project has an active "
                f"{resource_name} allocation in SAM, but no matching "
                "fileset quota exists anywhere in its project subtree. "
                "Typically means the fileset was retired from the "
                "storage system; the allocation is deactivated by "
                "setting end_date = today.[/dim italic]\n"
            )

    if unmapped:
        t = Table(title=f"Unmapped quota entries ({resource_name})",
                  box=box.SIMPLE_HEAD)
        t.add_column("Fileset", style="yellow")
        t.add_column("Path", style="dim")
        if show_fs:
            t.add_column("FS", justify="center")
        t.add_column("Limit", justify="right")
        t.add_column("Usage", justify="right")
        for qe in unmapped:
            row = [qe.fileset_name, qe.path or "—"]
            if show_fs:
                row.append(_fs_marker(qe.path, path_exists))
            row += [fmt.size(qe.limit_bytes), fmt.size(qe.usage_bytes)]
            t.add_row(*row)
        ctx.console.print(t)
        if ctx.verbose:
            if show_fs:
                extra = (
                    " FS ✓ indicates a real mapping gap (admin should "
                    "add a ProjectDirectory); FS ✗ indicates the quota "
                    "snapshot is likely stale."
                )
            else:
                extra = ""
            ctx.console.print(
                "[dim italic]Unmapped: storage has a fileset quota, but "
                f"SAM has no {resource_name} allocation that matches — "
                "neither by projcode nor by active ProjectDirectory "
                "path. Typically means a fileset was provisioned "
                "outside SAM, or the project mapping drifted. "
                f"Reported only; no action taken.{extra}[/dim italic]\n"
            )

    if ctx.verbose and matched:
        t = Table(title=f"Matched ({resource_name})", box=box.SIMPLE_HEAD)
        t.add_column("Project", style="green")
        t.add_column("SAM", justify="right")
        t.add_column("Expected", justify="right")
        t.add_column("Δ", justify="right", style="dim")
        t.add_column("Fileset", style="dim")
        t.add_column("Path", style="dim")
        for projcode, sam_tib, expected_bytes, contributors in matched:
            fileset, path, _ = _leaf_cells(contributors)
            t.add_row(
                projcode,
                fmt.size(sam_tib * (1024 ** 4)),
                fmt.size(expected_bytes),
                fmt.pct(_expected_delta_pct(sam_tib, expected_bytes), decimals=2),
                fileset, path,
            )
        ctx.console.print(t)
        # Subtree panels only for matched projects that actually have a subtree.
        for projcode, sam_tib, expected_bytes, contributors in matched:
            if len(contributors) > 1:
                _render_subtree_panel(ctx, projcode, sam_tib,
                                      expected_bytes, contributors)


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
