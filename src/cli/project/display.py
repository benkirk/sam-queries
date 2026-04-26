"""Display functions for project commands. Operate on plain dicts produced
by `cli.project.builders`; never touch ORM objects directly."""

from cli.core.context import Context
from cli.project.builders import (
    build_project_core,
    build_project_detail,
    build_project_allocations,
    build_project_rolling,
    build_project_tree,
    build_project_users,
)
from sam import fmt
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box
from rich.tree import Tree


def display_project(ctx: Context, data: dict, extra_title_info: str = "",
                    list_users: bool = False):
    """Display project information from `build_project_core` output.

    Sub-builders may have already populated:
      data['detail']      — from build_project_detail   (verbose)
      data['allocations'] — from build_project_allocations
      data['rolling']     — from build_project_rolling  (verbose)
      data['tree']        — from build_project_tree     (verbose)
      data['users']       — from build_project_users    (list_users)
    """
    grid = Table(show_header=False, box=None, padding=(0, 2))
    grid.add_column("Field", style="cyan bold")
    grid.add_column("Value")

    grid.add_row("Title", data['title'])
    grid.add_row("Code", data['projcode'])
    grid.add_row("GID", str(data['unix_gid']))
    grid.add_row("Status",
                 "[green]Active[/]" if data['active'] else "[red]Inactive[/]")

    if data['lead']:
        lead = data['lead']
        grid.add_row(
            "Lead",
            f"{lead['display_name']} ({lead['username']}) <{lead['primary_email'] or 'N/A'}>"
        )
        if ctx.very_verbose and 'detail' in data and data['detail']['pi_institutions']:
            grid.add_row(
                "PI Institution",
                "\n".join(f"{i['name']} ({i['acronym']})"
                          for i in data['detail']['pi_institutions'])
            )

    if data['admin'] and (not data['lead']
                          or data['admin']['username'] != data['lead']['username']):
        adm = data['admin']
        grid.add_row(
            "Admin",
            f"{adm['display_name']} ({adm['username']}) <{adm['primary_email'] or 'N/A'}>"
        )

    if data['area_of_interest']:
        grid.add_row("Area", data['area_of_interest'])

    if data['allocation_type']:
        grid.add_row("Type", data['allocation_type'])
    if data['panel']:
        grid.add_row("Panel", data['panel'])
    if data['facility']:
        grid.add_row("Facility", data['facility'])

    if data['organizations']:
        grid.add_row(
            "Organizations",
            "\n".join(f"- {o['name']} ({o['acronym']})" for o in data['organizations'])
        )

    if data['contracts']:
        grid.add_row(
            "Contracts",
            "\n".join(
                f"- {c['source']} {c['number']} {c['title']}"
                for c in data['contracts']
            )
        )

    if data['charging_exempt']:
        grid.add_row("Exempt", "[bold magenta]** Charging Exempt **[/]")

    if ctx.very_verbose and 'detail' in data:
        detail = data['detail']
        grid.add_row("Project ID", str(detail['project_id']))
        if detail['ext_alias']:
            grid.add_row("External Alias", detail['ext_alias'])
        if detail['creation_time']:
            grid.add_row("Created",
                         fmt.date_str(detail['creation_time'], fmt="%Y-%m-%d %H:%M:%S"))
        if detail['modified_time']:
            grid.add_row("Modified",
                         fmt.date_str(detail['modified_time'], fmt="%Y-%m-%d %H:%M:%S"))
        if detail['membership_change_time']:
            grid.add_row("Membership Changed",
                         fmt.date_str(detail['membership_change_time'],
                                      fmt="%Y-%m-%d %H:%M:%S"))
        if detail['inactivate_time']:
            grid.add_row("Inactivated",
                         fmt.date_str(detail['inactivate_time'], fmt="%Y-%m-%d %H:%M:%S"))
        if detail['latest_allocation_end']:
            grid.add_row("Allocation End", fmt.date_str(detail['latest_allocation_end']))

    panel = Panel(grid,
                  title=f"Project Information: [bold]{data['projcode']}[/]{extra_title_info}",
                  expand=False, border_style="green")
    ctx.console.print(panel)

    # Allocations table
    usage = data.get('allocations') or {}
    if usage:
        alloc_table = Table(title="Allocations & Usage", box=box.SIMPLE, show_header=True)
        alloc_table.add_column("Resource")
        alloc_table.add_column("Type")
        alloc_table.add_column("Dates")
        alloc_table.add_column("Allocation", justify="right")
        alloc_table.add_column("Remaining", justify="right")
        alloc_table.add_column("Used", justify="right")
        alloc_table.add_column("% Used", justify="right")

        show_rolling = ctx.verbose or ctx.very_verbose
        rolling_data = data.get('rolling') or {}
        if show_rolling:
            alloc_table.add_column("30d Used", justify="right", style="dim")
            alloc_table.add_column("90d Used", justify="right", style="dim")

        if ctx.very_verbose:
            alloc_table.add_column("Jobs", justify="right", style="dim")
            alloc_table.add_column("Days Left", justify="right", style="dim")

        for resource_name, resource_usage in usage.items():
            days_remaining = resource_usage.get('days_remaining')
            is_expired = days_remaining is not None and days_remaining < 0

            if is_expired:
                resource_style = "dim"
                date_style = "dim red"
                expired_indicator = " (Expired)"
            else:
                resource_style = "cyan"
                date_style = "dim"
                expired_indicator = ""

            start_str = fmt.date_str(resource_usage.get('start_date'), null='N/A')
            end_str = fmt.date_str(resource_usage.get('end_date'), null='N/A')
            date_range = f"[{date_style}]{start_str}\n{end_str}[/]"

            pct = resource_usage['percent_used']
            pct_style = "green"
            if pct > 80: pct_style = "yellow"
            if pct > 100: pct_style = "red bold"
            if is_expired:
                pct_style = "dim"

            allocated = resource_usage.get('allocated', 0)
            alloc_str = fmt.number(allocated)
            remaining_str = fmt.number(resource_usage['remaining'])
            used_str = fmt.number(resource_usage['used'])
            # Shared (inheriting) allocation: annotate this project's
            # contribution inline, e.g. "700 (200 yours)", and tag the
            # resource cell so the user knows the pool is shared.
            is_shared = resource_usage.get('is_inheriting', False)
            self_used = resource_usage.get('self_used')
            if is_shared and self_used is not None:
                used_str = f"{used_str} [dim]({fmt.number(self_used)} yours)[/]"
            shared_indicator = " [dim](shared)[/]" if is_shared else ""
            row = [
                f"[{resource_style}]{resource_name}{expired_indicator}[/]{shared_indicator}",
                (f"[{resource_style}]{resource_usage['resource_type']}[/]"
                 if is_expired else resource_usage['resource_type']),
                date_range,
                f"[{resource_style}]{alloc_str}[/]" if is_expired else alloc_str,
                f"[{resource_style}]{remaining_str}[/]" if is_expired else remaining_str,
                f"[{resource_style}]{used_str}[/]" if is_expired else used_str,
                f"[{pct_style}]{fmt.pct(pct)}[/]"
            ]

            if show_rolling:
                rdata = rolling_data.get(resource_name, {})
                for wdays in (30, 90):
                    winfo = rdata.get('windows', {}).get(wdays)
                    if winfo:
                        charges_str = fmt.number(winfo['charges'])
                        pct_str = fmt.pct(winfo['pct_of_prorated'])
                        if winfo.get('threshold_pct') is not None:
                            cell = f"{charges_str}\n({pct_str} vs. {winfo['threshold_pct']}% lim)"
                        else:
                            cell = f"{charges_str}\n({pct_str})"
                    else:
                        cell = "—"
                    row.append(cell)

            if ctx.very_verbose:
                jobs = resource_usage.get('total_jobs')
                jobs_str = fmt.number(jobs) if jobs is not None else 'N/A'
                row.append(
                    f"[{resource_style}]{jobs_str}[/]"
                    if jobs is not None and is_expired else jobs_str
                )
                row.append(
                    f"[{resource_style}]{days_remaining}[/]"
                    if days_remaining is not None and is_expired
                    else (str(days_remaining) if days_remaining is not None else "N/A")
                )

            alloc_table.add_row(*row)
        ctx.console.print(alloc_table)

    if data['active_directories']:
        dir_text = Text("Active Directories:\n", style="bold")
        for d in data['active_directories']:
            dir_text.append(f"  - {d}\n", style="reset")
        ctx.console.print(dir_text)

    if list_users and 'users' in data:
        display_project_users(ctx, data['users'], data['projcode'])
    else:
        ctx.console.print(f"\nActive Users: [bold]{data['active_user_count']}[/]")
        if not ctx.verbose and not ctx.very_verbose:
            ctx.console.print(
                " (Use --list-users to see user details, --verbose/-vv for more project information.)",
                style="dim italic"
            )

    if (ctx.verbose or ctx.very_verbose) and 'detail' in data:
        abstract = data['detail']['abstract']
        if abstract:
            if ctx.verbose and not ctx.very_verbose and len(abstract) > 500:
                abstract = abstract[:500] + "..."
            ctx.console.print(Panel(abstract, title="Abstract",
                                    border_style="dim", expand=False))

    tree_data = data.get('tree')
    if (ctx.verbose or ctx.very_verbose) and tree_data and tree_data.get('children'):
        # Render hierarchy from dict tree
        show_inactive = ctx.very_verbose

        def label_for(node: dict) -> str:
            if node['is_current']:
                if not node['active']:
                    return f"[bold yellow]→ {node['projcode']} - {node['title']}[/bold yellow] [dim italic](Inactive)[/dim italic]"
                return f"[bold yellow]→ {node['projcode']} - {node['title']}[/bold yellow]"
            if not node['active']:
                return f"[dim]{node['projcode']} - {node['title']} [italic](Inactive)[/italic][/dim]"
            return f"{node['projcode']} - {node['title']}"

        def add_children(node: dict, parent_tree: Tree):
            for child in node['children']:
                if not child['active'] and not show_inactive and not child['is_current']:
                    continue
                child_tree = parent_tree.add(label_for(child))
                if child['children']:
                    add_children(child, child_tree)

        tree = Tree(label_for(tree_data))
        add_children(tree_data, tree)
        ctx.console.print(Panel(tree, title="Project Hierarchy",
                                border_style="blue", expand=False))


def display_project_users(ctx: Context, users: list, projcode: str):
    """Display users for a project from `build_project_users`."""
    if not users:
        ctx.console.print("No active users found.", style="yellow")
        return

    count = len(users)
    plural = "s" if count > 1 else ""

    ctx.console.print(f"\n[bold]{count} Active user{plural} for {projcode}:[/]")

    table = Table(box=box.SIMPLE)
    table.add_column("#", style="dim", width=4)
    table.add_column("Username", style="green")
    table.add_column("Name")
    table.add_column("Access Notes", style="yellow italic")

    if ctx.verbose:
        table.add_column("Email")
        table.add_column("UID")

    for i, u in enumerate(users, 1):
        access_notes = ""
        if u['inaccessible_resources']:
            access_notes = f"no access to {', '.join(u['inaccessible_resources'])}"

        row = [str(i), u['username'], u['display_name'], access_notes]
        if ctx.verbose:
            row.append(u['primary_email'] or 'N/A')
            row.append(str(u['unix_uid']))
        table.add_row(*row)

    ctx.console.print(table)


def display_project_search_results(ctx: Context, data: dict):
    """Display project pattern search results from `build_project_search_results`."""
    ctx.console.print(f"✅ Found {data['count']} project(s):\n", style="green bold")

    for i, p in enumerate(data['projects'], 1):
        ctx.console.print(f"{i}. {p['projcode']}", style="cyan bold")
        ctx.console.print(f"   {p['title']}")

        if ctx.verbose and 'project_id' in p:
            lead_name = p['lead']['display_name'] if p.get('lead') else 'N/A'
            ctx.console.print(f"   ID: {p['project_id']}")
            ctx.console.print(f"   Lead: {lead_name}")
            ctx.console.print(f"   Users: {p['active_user_count']}")

        ctx.console.print("")


def display_expiring_projects(ctx: Context, expiring_data: list,
                              list_users: bool = False, upcoming: bool = True):
    """Display upcoming or recently expired projects.

    `expiring_data` is the raw list of (project, allocation, resource_name,
    days) tuples returned by the queries module.  We keep ORM access
    confined to this function (and only the verbose path) so we can build
    a full per-project payload only when actually rendering verbose detail.
    """
    if upcoming:
        ctx.console.print(f"Found {len(expiring_data)} allocations expiring",
                          style="yellow")
        for proj, alloc, res_name, days in expiring_data:
            if ctx.verbose:
                _display_project_verbose(ctx, proj, f" - {days} days remaining",
                                         list_users=list_users)
            else:
                ctx.console.print(f"  {proj.projcode} - {days} days remaining")
    else:
        ctx.console.print(
            f"Found {len(expiring_data)} recently expired projects:", style="yellow"
        )
        for proj, alloc, res_name, days_expired in expiring_data:
            if ctx.verbose:
                _display_project_verbose(ctx, proj,
                                         f" - {days_expired} days since expiration",
                                         list_users=list_users)
            else:
                ctx.console.print(f"  {proj.projcode} - {days_expired} days since expiration")

    if not ctx.verbose:
        ctx.console.print("\n (Use --verbose for more project information.)",
                          style="dim italic")


def _display_project_verbose(ctx: Context, project, extra_title: str, list_users: bool):
    """Build full payload for one project and render it.  Used by the
    verbose path of `display_expiring_projects`."""
    data = build_project_core(project)
    data['allocations'] = build_project_allocations(project)
    data['detail'] = build_project_detail(project)
    data['rolling'] = build_project_rolling(project.session, project.projcode)
    data['tree'] = build_project_tree(project)
    if list_users:
        data['users'] = build_project_users(project)
    display_project(ctx, data, extra_title_info=extra_title, list_users=list_users)


def display_abandoned_users_from_expired_projects(ctx: Context, abandoned_users):
    """Display users whose only active projects have expired.

    Accepts either a set of ORM users (from the existing command path)
    or a list of dicts with username/display_name/primary_email keys.
    """
    rows = []
    for u in abandoned_users:
        if isinstance(u, dict):
            rows.append((u['username'], u['display_name'], u['primary_email']))
        else:
            rows.append((u.username, u.display_name, u.primary_email))
    rows.sort(key=lambda r: r[0])

    ctx.console.print(f"Found {len(rows)} expiring users:", style="bold red")
    table = Table(show_header=False, box=None)
    table.add_column("User")
    for username, display_name, email in rows:
        table.add_row(f"{username:12} {display_name:30} <{email}>")
    ctx.console.print(table)


def display_notification_results(ctx: Context, results: dict, total_projects: int):
    """Display notification results summary.

    Args:
        ctx: Context object
        results: Dict with 'success' and 'failed' lists
        total_projects: Total number of projects with expiring allocations
    """
    success_count = len(results['success'])
    failed_count = len(results['failed'])

    grid = Table(show_header=False, box=None, padding=(0, 2))
    grid.add_column("Field", style="cyan bold")
    grid.add_column("Value")

    grid.add_row("Expiring Projects", str(total_projects))
    grid.add_row("Emails Sent",
                 f"[green]{success_count}[/]" if success_count > 0 else "0")
    grid.add_row("Failed",
                 f"[red]{failed_count}[/]" if failed_count > 0 else "0")

    panel = Panel(grid, title="Notification Results", expand=False, border_style="blue")
    ctx.console.print(panel)

    if failed_count > 0:
        ctx.console.print("\n[red bold]Failed Notifications:[/]")
        for notification in results['failed']:
            ctx.console.print(
                f"  {notification['recipient']}: {notification.get('error', 'Unknown error')}",
                style="red"
            )

    if ctx.verbose and success_count > 0:
        ctx.console.print("\n[green]Successful Notifications:[/]")
        for notification in results['success']:
            ctx.console.print(
                f"  {notification['recipient']} ({notification['project_code']})",
                style="green"
            )


def display_notification_preview(ctx: Context, results: dict, total_projects: int):
    """Display notification preview in dry-run mode.

    Args:
        ctx: Context object
        results: Dict with 'success', 'failed', and 'preview_samples' lists
        total_projects: Total number of projects with expiring allocations
    """
    from collections import defaultdict

    success_count = len(results['success'])
    failed_count = len(results['failed'])

    ctx.console.print(f"\n[bold yellow]DRY-RUN MODE: Preview only, no emails will be sent[/]\n")

    grid = Table(show_header=False, box=None, padding=(0, 2))
    grid.add_column("Field", style="cyan bold")
    grid.add_column("Value")

    grid.add_row("Projects with Expiring Allocations", str(total_projects))
    grid.add_row("Emails That Would Be Sent", str(success_count))

    unique_recipients = set(n['recipient'] for n in results['success'])
    grid.add_row("Unique Recipients", str(len(unique_recipients)))

    if failed_count > 0:
        grid.add_row("Preview Errors", f"[red]{failed_count}[/]")

    panel = Panel(grid, title="Dry-Run Summary", expand=False, border_style="yellow")
    ctx.console.print(panel)

    if failed_count > 0:
        ctx.console.print("\n[red bold]Preview Errors:[/]")
        for notification in results['failed']:
            ctx.console.print(
                f"  {notification['recipient']}: {notification.get('error', 'Unknown error')}",
                style="red"
            )

    by_project = defaultdict(list)
    for notification in results['success']:
        by_project[notification['project_code']].append(notification)

    ctx.console.print("\n[bold]Email Preview by Project:[/]\n")

    for projcode, project_notifications in sorted(by_project.items()):
        first = project_notifications[0]
        recipients = [n['recipient'] for n in project_notifications]

        ctx.console.print(f"[cyan bold]{projcode}[/] - {first['project_title']}")
        ctx.console.print(f"  Recipients ({len(recipients)}): {', '.join(sorted(recipients))}")

        for resource in first['resources']:
            urgency = ("🔴 URGENT" if resource['days_remaining'] <= 7
                       else "🟠 WARNING" if resource['days_remaining'] <= 14
                       else "🔵 NOTICE")
            ctx.console.print(
                f"    {urgency} {resource['resource_name']}: "
                f"{resource['days_remaining']} days remaining "
                f"(expires {resource['expiration_date']})"
            )

        ctx.console.print()

    if ctx.verbose and 'preview_samples' in results and results['preview_samples']:
        ctx.console.print("\n[bold]Sample Rendered Emails:[/]\n")

        for i, sample in enumerate(results['preview_samples'], 1):
            meta_info = (f"To: {sample['recipient']} ({sample['recipient_role']})"
                         f" | Project: {sample['project_code']}")
            if sample['facility']:
                meta_info += f" | Facility: {sample['facility']}"
            if sample['html_content']:
                meta_info += (f" | Templates: {sample['text_template']},"
                              f" {sample['html_template']}")
            else:
                meta_info += f" | Template: {sample['text_template']} (text-only)"

            ctx.console.print(f"[dim]{meta_info}[/dim]")

            ctx.console.print(Panel(
                sample['text_content'],
                title=f"Sample Email #{i} to {sample['recipient_name']}",
                border_style="dim"
            ))

            if i < len(results['preview_samples']):
                ctx.console.print()

    if not ctx.verbose:
        ctx.console.print("[dim]Use --verbose to see sample rendered email content[/]")
