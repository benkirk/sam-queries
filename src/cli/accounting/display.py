"""Display functions for accounting commands."""

from sam.summaries.disk_summaries import BYTES_PER_TIB
from cli.core.context import Context
from rich.table import Table
from rich.tree import Tree
from rich.panel import Panel
from rich import box
from rich.markup import escape

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


def display_disk_dry_run_table(ctx: Context, entries: list, resource_name: str,
                               *, dry_run: bool = True) -> None:
    """Print a Rich table showing what would be (or was) posted to disk_charge_summary.

    Args:
        ctx: CLI context (for console output)
        entries: List of DiskUsageEntry objects (from a DiskUsageReader plus
                 the import-side terabyte_years/charges already populated)
        resource_name: Resource name (e.g. 'Campaign_Store')
        dry_run: When True, title notes rows were not written
    """
    title = (
        f"Dry Run — {resource_name} disk_charge_summary rows (not written)"
        if dry_run
        else f"{resource_name} disk_charge_summary rows"
    )
    table = Table(
        title=title,
        box=box.SIMPLE_HEAD,
        show_lines=False,
    )
    table.add_column("Date", style="cyan", no_wrap=True)
    table.add_column("Project", style="green")
    table.add_column("User", style="white")
    table.add_column("act_user", style="yellow")
    table.add_column("Files", justify="right", style="dim")
    table.add_column("Bytes", justify="right")
    table.add_column("TiB-yr", justify="right", style="bold")
    table.add_column("Charges", justify="right", style="bold")
    table.add_column("Path", style="dim")

    n_synthetic = 0
    for e in entries:
        is_gap = bool(e.act_username)
        if is_gap:
            n_synthetic += 1
        table.add_row(
            str(e.activity_date),
            e.projcode,
            e.username,
            e.act_username or "",
            fmt.number(e.number_of_files),
            fmt.size(e.bytes),
            f"{e.terabyte_years:.4f}",
            f"{e.charges:.4f}",
            (e.directory_path or "")[:60],
        )

    ctx.console.print(table)
    if n_synthetic:
        ctx.console.print(
            f"[dim]({n_synthetic} synthetic gap rows attributed to project lead "
            "with audit label in act_username)[/dim]"
        )


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


# (title, fix hint) per disk-import category, in print order.
_DISK_CATEGORY_TEXT = {
    'no_project': ("Not charged: no SAM project",
                   "link the path to its project (Admin -> project -> Directories), "
                   "or add the label to KNOWN_UNOWNED_PROJCODES"),
    'no_account': ("Not charged: project has no account on {resource}",
                   "add an account for the project on {resource}"),
    'unknown_user': ("Not charged: user not in SAM", None),
    'known_unowned': ("Skipped: known unowned filesets", None),
    'unlinked_directory': ("Charged via the fileset label; directory not linked",
                           "--reconcile-directories applies reopen/rename/create; "
                           "review rows need a person"),
    'expired_directory': ("Charged via the fileset label; allocation expired", None),
}


def display_disk_import_report(ctx: Context, data: dict) -> None:
    """Print the disk-import summary and one section per non-empty category.

    Unexpected categories always list their rows; the informational ones
    (hundreds of unlinked directories on Campaign_Store) only under --verbose.
    """
    counts = data['counts']
    cats = data['categories']
    resource = data['resource']
    title = f"Disk Import {resource} {data['snapshot_date']}"
    if not data['written']:
        title += " (nothing written)"
    table = Table(title=title, show_header=False, box=None)
    table.add_column("Label", style="dim")
    table.add_column("Count", justify="right", style="bold")
    if data['written']:
        table.add_row("Created", f"[green]{data['created']}[/green]")
        table.add_row("Updated", f"[cyan]{data['updated']}[/cyan]")
    if data.get('layout'):
        table.add_row("File layout", data['layout'])
    table.add_row("Skipped (system account)", str(counts['system_account_rows']))
    table.add_row("Skipped (known unowned)", str(counts['known_unowned']))
    table.add_row("Charged, directory not linked", str(counts['unlinked_directory']))
    table.add_row("Charged, allocation expired", str(counts['expired_directory']))
    for cat in ('no_project', 'no_account', 'unknown_user'):
        n = counts[cat]
        table.add_row(f"Not charged ({cat.replace('_', ' ')})",
                      f"[red]{n}[/red]" if n else "0")
    if data.get('errors'):
        table.add_row("Errors", f"[red]{data['errors']}[/red]")
    ctx.console.print(table)

    actions: dict[str, int] = {}
    for item in cats['unlinked_directory']:
        actions[item['action']] = actions.get(item['action'], 0) + 1
    if actions:
        planned = ', '.join(f"{a} {n}" for a, n in sorted(actions.items()))
        ctx.console.print(f"Directory actions: {planned}")
    if 'directories' in data:
        applied = ', '.join(f"{a} {n}" for a, n in data['directories'].items())
        ctx.console.print(f"[green]Directories reconciled: {applied}[/green]")

    for cat, (heading, hint) in _DISK_CATEGORY_TEXT.items():
        items = cats[cat]
        if not items:
            continue
        unexpected = cat in ('no_project', 'no_account', 'unknown_user')
        if not (unexpected or ctx.verbose):
            continue
        style = "red" if unexpected else "yellow"
        ctx.console.print(
            f"\n[bold {style}]{heading.format(resource=resource)}[/bold {style}] "
            f"({fmt.plural(len(items), 'entry', 'entries')})"
        )
        if hint:
            ctx.console.print(f"  [dim]fix: {hint.format(resource=resource)}[/dim]")
        for item in items:
            if cat == 'unknown_user':
                ctx.console.print(
                    f"  {escape(item['username'])}  projects={','.join(item['projcodes'])}  "
                    f"rows={item['rows']}  {fmt.size(item['bytes'])}"
                )
                continue
            sam = item['sam_projcode']
            sam_txt = f" -> {sam}" if sam and sam != item['projcode'] else ""
            tag = f"\\[{item['action']}] " if item.get('action') else ""
            ctx.console.print(
                f"  {tag}{escape(item['projcode'])}{sam_txt}  {escape(item['path'] or '-')}  "
                f"rows={item['rows']}  {fmt.size(item['bytes'])}"
            )

    system = data['system_accounts']
    if system:
        ctx.console.print(
            f"\n[dim]System accounts dropped: {escape(', '.join(system))}[/dim]"
        )


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


def _fmt_factor(x) -> str:
    """Format a QoS charge multiplier compactly (1.0 -> '1', 1.5 -> '1.5')."""
    if x is None:
        return '—'
    return f"{x:g}"


def display_jobs_table(ctx: Context, rows: list, start_date, end_date, *,
                       mode: str = 'recent', multi_machine: bool = False) -> None:
    """
    Print a Rich table of individual jobs (sam-search accounting --jobs).

    Default columns are kept comfortably within 80 chars: Job ID, Project,
    User, [Machine when >1 queried], Nodes, Cores, [GPUs when any nonzero],
    Wall-h, Charges. The GPUs column is suppressed when every row has zero
    GPUs. --verbose adds Comp-h (the billing-metric hours), Resource, QoS,
    Factor, Queue, and Status. Memory is intentionally never shown.

    Args:
        ctx: CLI context (console + verbose flag)
        rows: List of per-job dicts (already classified: resource/comp_hours/charges)
        start_date / end_date: Queried date range
        mode: 'recent' or 'largest' (shown in the title)
        multi_machine: Whether >1 machine was queried (adds a Machine column)
    """
    show_gpus = any((r.get('numgpus') or 0) > 0 for r in rows)
    verbose = ctx.verbose

    # Column spec: (header, justify, style, cell_fn)
    cols = [
        ("Job ID",  "left",  "cyan",  lambda r: str(r.get('job_id') or '')),
        ("Project", "left",  "green", lambda r: r.get('account') or ''),
        ("User",    "left",  "white", lambda r: r.get('user') or ''),
    ]
    if multi_machine:
        cols.append(("Machine", "left", "dim", lambda r: r.get('machine') or ''))
    cols += [
        ("Nodes", "right", None, lambda r: fmt.number(r.get('numnodes'))),
        ("Cores", "right", None, lambda r: fmt.number(r.get('numcpus'))),
    ]
    if show_gpus:
        cols.append(("GPUs", "right", None, lambda r: fmt.number(r.get('numgpus'))))
    cols.append(("Wall-h", "right", "dim", lambda r: fmt.hours(r.get('elapsed'))))
    if verbose:
        cols.append(("Comp-h", "right", "dim", lambda r: fmt.number(r.get('comp_hours'))))
    cols.append(("Charges", "right", "bold", lambda r: fmt.number(r.get('charges'))))
    if verbose:
        cols += [
            ("Resource", "left",  "white", lambda r: r.get('resource') or ''),
            ("QoS",      "left",  "white", lambda r: r.get('qos') or ''),
            ("Factor",   "right", "dim",   lambda r: _fmt_factor(r.get('qos_factor'))),
            ("Queue",    "left",  "white", lambda r: r.get('queue') or ''),
            ("Exit",     "left",  "dim",   lambda r: str(r.get('exit_status') or '')),
        ]

    table = Table(
        title=f"jobs ({mode})  {start_date} → {end_date}",
        box=box.SIMPLE_HEAD,
        show_lines=False,
    )
    for header, justify, style, _fn in cols:
        kwargs = {'justify': justify}
        if style:
            kwargs['style'] = style
        if header == "Job ID":
            kwargs['no_wrap'] = True
        table.add_column(header, **kwargs)

    total_charges = 0.0
    for r in rows:
        total_charges += r.get('charges') or 0.0
        table.add_row(*[fn(r) for _h, _j, _s, fn in cols])

    # Totals footer: job count (first col) + summed charges (Charges col)
    charges_idx = next(i for i, (h, _j, _s, _f) in enumerate(cols) if h == "Charges")
    footer = [''] * len(cols)
    footer[0] = f"[dim]{len(rows)} jobs[/dim]"
    footer[charges_idx] = f"[bold]{fmt.number(total_charges)}[/bold]"
    table.add_section()
    table.add_row(*footer)

    ctx.console.print(table)


HIGH_UTIL_THRESHOLD = 0.95   # annotate matched filesets >95% full
LOW_UTIL_THRESHOLD = 0.05    # annotate matched filesets <5% full


def _util_suffix(fs: dict) -> str:
    """``⚠ 97%`` (yellow) above 95% of limit, ``↓ 3%`` (cyan) below 5%, else empty."""
    u = fs['utilization']
    if u > HIGH_UTIL_THRESHOLD:
        return f" [yellow]⚠ {int(round(u * 100))}%[/yellow]"
    if u < LOW_UTIL_THRESHOLD and fs['limit_bytes'] > 0:
        return f" [cyan]↓ {int(round(u * 100))}%[/cyan]"
    return ""


def _fs_marker(item: dict) -> str:
    """✓ verified present, ✗ verified missing, — not checked."""
    if item.get('exists') is None:
        return "—"
    return "[green]✓[/green]" if item['exists'] else "[red]✗[/red]"


def _leaf_cells(filesets: list) -> tuple[str, str]:
    """(fileset, path) cells for a one-fileset row; a pointer to the subtree panel otherwise."""
    if len(filesets) == 1:
        fs = filesets[0]
        return fs['fileset'] + _util_suffix(fs), fs['path'] or "—"
    return f"[dim]{len(filesets)} filesets[/dim]", "[dim]↓ see subtree[/dim]"


def _render_subtree_panel(ctx: Context, item: dict) -> None:
    """A project's contributing filesets as a Tree in a Panel, its own marked ★."""
    projcode, filesets = item['projcode'], item['filesets']
    w_pc   = max(len(fs['projcode']) for fs in filesets)
    w_fs   = max(len(fs['fileset']) for fs in filesets)
    w_path = max(len(fs['path'] or "—") for fs in filesets)

    header = (
        f"[bold]{projcode}[/bold] — SAM {fmt.size(item['sam_tib'] * BYTES_PER_TIB)}, "
        f"expected {fmt.size(item['quota_bytes'])} "
        f"([dim]{len(filesets)} fileset"
        f"{'s' if len(filesets) != 1 else ''}[/dim])"
    )
    tree = Tree(header)
    for fs in filesets:
        marker = "[bold yellow]★[/bold yellow]" if fs['projcode'] == projcode else " "
        tree.add(
            f"{marker} [green]{fs['projcode']:<{w_pc}}[/green]  "
            f"[dim]{fs['fileset']:<{w_fs}}[/dim]  "
            f"[dim]{(fs['path'] or '—'):<{w_path}}[/dim]  "
            f"{fmt.size(fs['limit_bytes']):>10}"
            f"{_util_suffix(fs)}"
        )
    ctx.console.print(Panel(tree, title=f"subtree for {projcode}",
                            border_style="blue", expand=False))


def _project_table(ctx: Context, title: str, items: list, *, decimals: int,
                   delta_style: str, action: bool) -> None:
    t = Table(title=title, box=box.SIMPLE_HEAD)
    t.add_column("Project", style="green")
    t.add_column("SAM", justify="right")
    t.add_column("Expected", justify="right")
    t.add_column("Δ", justify="right", style=delta_style)
    t.add_column("Fileset", style="dim")
    t.add_column("Path", style="dim")
    if action:
        t.add_column("Action", style="bold cyan")
    for item in items:
        row = [
            item['projcode'],
            fmt.size(item['sam_tib'] * BYTES_PER_TIB),
            fmt.size(item['quota_bytes']),
            fmt.pct(item['delta_pct'], decimals=decimals),
            *_leaf_cells(item['filesets']),
        ]
        if action:
            row.append(f"set amount → {fmt.size(item['quota_bytes'])}")
        t.add_row(*row)
    ctx.console.print(t)
    for item in items:
        if len(item['filesets']) > 1:
            _render_subtree_panel(ctx, item)


def display_quota_reconcile_plan(ctx: Context, report: dict) -> None:
    """Render the ``quota_reconcile`` report's categories as Rich tables and subtree panels."""
    resource_name = report['resource']
    cats = report['categories']
    show_fs = report['verified_paths']

    if cats['quota_mismatch']:
        _project_table(ctx, f"Mismatched ({resource_name})", cats['quota_mismatch'],
                       decimals=1, delta_style="yellow", action=True)

    orphaned = cats['allocation_without_quota']
    if orphaned:
        t = Table(title=f"Orphaned ({resource_name})",
                  box=box.SIMPLE_HEAD)
        t.add_column("Project", style="green")
        t.add_column("SAM", justify="right")
        t.add_column("ProjectDirectory", style="dim")
        if show_fs:
            t.add_column("FS", justify="center")
        t.add_column("Action", style="bold cyan")
        for item in orphaned:
            directories = item['directories']
            if directories:
                dir_cell = "\n".join(d['path'] for d in directories)
                markers = ("\n".join(_fs_marker(d) for d in directories)
                           if show_fs else "")
                all_live = show_fs and all(d['exists'] for d in directories)
            else:
                dir_cell = "—"
                markers = "—"
                all_live = False
            action_cell = (
                "[bold yellow]paths still live — requires --force[/bold yellow]"
                if all_live else "deactivate (set end_date)"
            )
            row = [item['projcode'], fmt.size(item['sam_tib'] * BYTES_PER_TIB), dir_cell]
            if show_fs:
                row.append(markers)
            row.append(action_cell)
            t.add_row(*row)
        ctx.console.print(t)
        ctx.console.print(
            "[dim italic]Orphaned: project has an active "
            f"{resource_name} allocation in SAM, but no matching "
            "fileset quota exists anywhere in its project subtree. "
            "This could mean the fileset has not yet been created or was "
            "retired from the storage system; deactivation requires "
            "--deactivate-orphaned."
            "[/dim italic]\n"
        )

    unmapped = cats['no_project']
    if unmapped:
        t = Table(title=f"Unmapped quota entries ({resource_name})",
                  box=box.SIMPLE_HEAD)
        t.add_column("Fileset", style="yellow")
        t.add_column("Path", style="dim")
        if show_fs:
            t.add_column("FS", justify="center")
        t.add_column("Limit", justify="right")
        t.add_column("Usage", justify="right")
        for fs in unmapped:
            row = [fs['fileset'], fs['path'] or "—"]
            if show_fs:
                row.append(_fs_marker(fs))
            row += [fmt.size(fs['limit_bytes']), fmt.size(fs['usage_bytes'])]
            t.add_row(*row)
        ctx.console.print(t)
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

    _uncovered_table(ctx, f"Quota after allocation expired ({resource_name})",
                     cats['quota_after_expiry'], expired=True)
    if cats['quota_after_expiry']:
        ctx.console.print(
            f"[dim italic]The project's last {resource_name} allocation has ended, "
            "but its fileset still has a quota. Past the 90-day access window these "
            "are cleanup candidates. Reported only.[/dim italic]\n"
        )
    _uncovered_table(ctx, f"Quota without any allocation ({resource_name})",
                     cats['quota_without_allocation'], expired=False)
    if cats['quota_without_allocation']:
        ctx.console.print(
            f"[dim italic]The fileset belongs to a SAM project with no {resource_name} "
            "allocation, current or past. Reported only.[/dim italic]\n"
        )

    unlinked = cats['unlinked_directory']
    if unlinked and ctx.verbose:
        t = Table(title=f"Directory not linked in SAM ({resource_name})",
                  box=box.SIMPLE_HEAD)
        t.add_column("Project", style="green")
        t.add_column("Fileset", style="dim")
        t.add_column("Path", style="dim")
        t.add_column("Fix", style="cyan")
        for fs in unlinked:
            t.add_row(fs['projcode'], fs['fileset'], fs['path'] or "—", fs['action'])
        ctx.console.print(t)
    if unlinked:
        ctx.console.print(
            f"[dim italic]Directory not linked in SAM: {fmt.number(len(unlinked))}. "
            "The fileset resolves to a project by name or label, but SAM has no "
            "project directory for its path; --disk --reconcile-directories fixes the link"
            f"{'' if ctx.verbose else ' (--verbose lists them)'}.[/dim italic]\n"
        )

    if cats['known_unowned'] and ctx.verbose:
        t = Table(title=f"Known unowned ({resource_name})", box=box.SIMPLE_HEAD)
        t.add_column("Fileset", style="dim")
        t.add_column("Path", style="dim")
        t.add_column("Limit", justify="right")
        for fs in cats['known_unowned']:
            t.add_row(fs['fileset'], fs['path'] or "—", fmt.size(fs['limit_bytes']))
        ctx.console.print(t)

    if cats['matched']:
        _project_table(ctx, f"Matched ({resource_name})", cats['matched'],
                       decimals=2, delta_style="dim", action=False)


def _uncovered_table(ctx: Context, title: str, items: list, *, expired: bool) -> None:
    if not items:
        return
    t = Table(title=title, box=box.SIMPLE_HEAD)
    t.add_column("Project", style="green")
    t.add_column("Active", justify="center")
    if expired:
        t.add_column("Ended", justify="right")
        t.add_column("Days", justify="right", style="yellow")
    t.add_column("Fileset", style="dim")
    t.add_column("Path", style="dim")
    t.add_column("Limit", justify="right")
    t.add_column("Usage", justify="right")
    for fs in items:
        row = [fs['projcode'], "yes" if fs['project_active'] else "[dim]no[/dim]"]
        if expired:
            row += [fmt.date_str(fs['last_end_date']), fmt.number(fs['days_since_end'])]
        row += [fs['fileset'], fs['path'] or "—",
                fmt.size(fs['limit_bytes']), fmt.size(fs['usage_bytes'])]
        t.add_row(*row)
    ctx.console.print(t)


def display_quota_reconcile_summary(
    ctx: Context,
    counts: dict,
    *,
    updated: int = 0,
    deactivated: int = 0,
    errors: int = 0,
    report_only: bool = False,
    will_apply_updates: bool = False,
    will_deactivate_orphans: bool = False,
) -> None:
    """Summary after a reconcile run: report-only, updates only, or updates plus deactivations."""
    if report_only:
        title = "Reconcile Summary (report-only)"
    elif will_deactivate_orphans:
        title = "Reconcile Summary (applied)"
    else:
        title = "Reconcile Summary (updates applied; orphans untouched)"

    t = Table(title=title, show_header=False, box=None)
    t.add_column("Label", style="dim")
    t.add_column("Count", justify="right", style="bold")

    t.add_row("Matched", f"[green]{counts['matched']}[/green]")
    t.add_row("Mismatched", f"[yellow]{counts['quota_mismatch']}[/yellow]")
    t.add_row("Orphaned", f"[yellow]{counts['allocation_without_quota']}[/yellow]")
    t.add_row("Quota, allocation expired", f"[yellow]{counts['quota_after_expiry']}[/yellow]")
    t.add_row("Quota, never allocated", f"[yellow]{counts['quota_without_allocation']}[/yellow]")
    t.add_row("Directory not linked in SAM", str(counts['unlinked_directory']))
    t.add_row("Unmapped quota entries", str(counts['no_project']))
    t.add_row("Known unowned", str(counts['known_unowned']))
    if will_apply_updates:
        t.add_row("Allocations updated", f"[cyan]{updated}[/cyan]")
    if will_deactivate_orphans:
        t.add_row("Allocations deactivated", f"[cyan]{deactivated}[/cyan]")
    if errors:
        t.add_row("Errors", f"[red]{errors}[/red]")

    ctx.console.print(t)
