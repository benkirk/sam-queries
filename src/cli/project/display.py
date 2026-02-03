"""Display functions for project commands."""

from cli.core.context import Context
from sam import Project
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box
from rich.tree import Tree


def display_project(ctx: Context, project: Project, extra_title_info: str = "", list_users: bool = False):
    """Display project information."""

    # Header Grid
    grid = Table(show_header=False, box=None, padding=(0, 2))
    grid.add_column("Field", style="cyan bold")
    grid.add_column("Value")

    grid.add_row("Title", project.title)
    grid.add_row("Code", project.projcode)
    grid.add_row("GID", str(project.unix_gid))
    grid.add_row("Status", f"[green]Active[/]" if project.active else f"[red]Inactive[/]")

    if project.lead:
        grid.add_row("Lead", f"{project.lead.display_name} ({project.lead.username}) <{project.lead.primary_email or 'N/A'}>")
        # Show PI institution in very verbose mode
        if ctx.very_verbose and project.lead.institutions:
            pi_insts = []
            for ui in project.lead.institutions:
                if ui.is_currently_active:
                    inst = ui.institution
                    pi_insts.append(f"{inst.name} ({inst.acronym})")
            if pi_insts:
                grid.add_row("PI Institution", "\n".join(pi_insts))
    if project.admin and project.admin != project.lead:
        grid.add_row("Admin", f"{project.admin.display_name} ({project.admin.username}) <{project.admin.primary_email or 'N/A'}>")

    if project.area_of_interest:
        grid.add_row("Area", project.area_of_interest.area_of_interest)

    grid.add_row("Type", project.allocation_type.allocation_type)

    if project.allocation_type.panel:
        grid.add_row("Panel", project.allocation_type.panel.panel_name)
        if project.allocation_type.panel.facility:
            grid.add_row("Facility", project.allocation_type.panel.facility.facility_name)

    if project.organizations:
        orgs = []
        for po in project.organizations:
            if True:
                org = po.organization
                orgs.append(f"- {org.name} ({org.acronym})")
        if orgs:
            grid.add_row(f"Organizations", "\n".join(orgs))

    if project.contracts:
        contracts = []
        for pc in project.contracts:
            contracts.append(f"- {pc.contract.contract_source.contract_source} {str(pc.contract.contract_number)} {pc.contract.title}")
        grid.add_row("Contracts", "\n".join(contracts))

    if project.charging_exempt:
        grid.add_row("Exempt", "[bold magenta]** Charging Exempt **[/]")

    # Very verbose: show additional IDs and timestamps
    if ctx.very_verbose:
        grid.add_row("Project ID", str(project.project_id))
        if project.ext_alias:
            grid.add_row("External Alias", project.ext_alias)
        if project.creation_time:
            grid.add_row("Created", project.creation_time.strftime("%Y-%m-%d %H:%M:%S"))
        if project.modified_time:
            grid.add_row("Modified", project.modified_time.strftime("%Y-%m-%d %H:%M:%S"))
        if project.membership_change_time:
            grid.add_row("Membership Changed", project.membership_change_time.strftime("%Y-%m-%d %H:%M:%S"))
        if project.inactivate_time:
            grid.add_row("Inactivated", project.inactivate_time.strftime("%Y-%m-%d %H:%M:%S"))

        # Show latest allocation end date
        latest_end = None
        for account in project.accounts:
            for alloc in account.allocations:
                if alloc.end_date and (latest_end is None or alloc.end_date > latest_end):
                    latest_end = alloc.end_date
        if latest_end:
            grid.add_row("Allocation End", latest_end.strftime("%Y-%m-%d"))

    # Main Panel
    panel = Panel(grid, title=f"Project Information: [bold]{project.projcode}[/]{extra_title_info}", expand=False, border_style="green")
    ctx.console.print(panel)

    # Allocations Table
    try:
        usage = project.get_detailed_allocation_usage()
        allocations = project.get_all_allocations_by_resource()

        if usage:
            alloc_table = Table(title="Allocations & Usage", box=box.SIMPLE, show_header=True)
            alloc_table.add_column("Resource", style="cyan")
            alloc_table.add_column("Type")
            alloc_table.add_column("Dates", style="dim")
            alloc_table.add_column("Allocation", justify="right")
            alloc_table.add_column("Remaining", justify="right")
            alloc_table.add_column("Used", justify="right")
            alloc_table.add_column("% Used", justify="right")

            # Very verbose: add extra columns for jobs and time metrics
            if ctx.very_verbose:
                alloc_table.add_column("Jobs", justify="right", style="dim")
                alloc_table.add_column("Days Left", justify="right", style="dim")

            for resource_name, alloc in allocations.items():
                if resource_name in usage:
                    resource_usage = usage[resource_name]

                    start_str = alloc.start_date.strftime("%Y-%m-%d")
                    end_str = alloc.end_date.strftime("%Y-%m-%d") if alloc.end_date else "N/A"
                    date_range = f"{start_str}\n{end_str}"

                    pct = resource_usage['percent_used']
                    pct_style = "green"
                    if pct > 80: pct_style = "yellow"
                    if pct > 100: pct_style = "red bold"

                    row = [
                        resource_name,
                        resource_usage['resource_type'],
                        date_range,
                        f"{alloc.amount:,.0f}",
                        f"{resource_usage['remaining']:,.0f}",
                        f"{resource_usage['used']:,.0f}",
                        f"[{pct_style}]{pct:,.1f}%[/]"
                    ]

                    # Very verbose: add jobs and days remaining
                    if ctx.very_verbose:
                        jobs = resource_usage.get('total_jobs')
                        days_remaining = resource_usage.get('days_remaining')
                        row.append(f"{jobs:,}" if jobs is not None else "N/A")
                        row.append(str(days_remaining) if days_remaining is not None else "N/A")

                    alloc_table.add_row(*row)
            ctx.console.print(alloc_table)


    except Exception as e:
         ctx.console.print(f"Warning: Could not fetch allocations: {e}", style="yellow")

    # Directories
    if project.active_directories:
        dir_text = Text("Active Directories:\n", style="bold")
        for d in project.active_directories:
            dir_text.append(f"  - {d}\n", style="reset")
        ctx.console.print(dir_text)

    # User count / listing
    if list_users:
        display_project_users(ctx, project)
    else:
        ctx.console.print(f"\nActive Users: [bold]{project.get_user_count()}[/]")
        if not ctx.verbose and not ctx.very_verbose:
            ctx.console.print(" (Use --list-users to see user details, --verbose/-vv for more project information.)", style="dim italic")

    if ctx.verbose or ctx.very_verbose:
        # Abstract - truncated for verbose, full for very verbose
        if project.abstract:
            abstract = project.abstract
            if ctx.verbose and not ctx.very_verbose and len(abstract) > 500:
                abstract = abstract[:500] + "..."

            ctx.console.print(Panel(abstract, title="Abstract", border_style="dim", expand=False))

        # Tree info - show entire tree from root
        if project.parent or project.get_children():
            # Get the root of the project tree
            root = project.get_root() if hasattr(project, 'get_root') else project
            current_projcode = project.projcode

            def build_tree_node(node, parent_tree, current_projcode):
                """Recursively build tree with sorted children, inactive status, and current highlight."""
                # Sort children alphabetically by projcode
                sorted_children = sorted(node.get_children(), key=lambda c: c.projcode)

                for child in sorted_children:
                    is_current = child.projcode == current_projcode

                    # Format child node with inactive status and current highlight
                    if is_current:
                        # Current project - highlighted in yellow
                        if hasattr(child, 'active') and not child.active:
                            label = f"[bold yellow]→ {child.projcode} - {child.title}[/bold yellow] [dim italic](Inactive)[/dim italic]"
                        else:
                            label = f"[bold yellow]→ {child.projcode} - {child.title}[/bold yellow]"
                    elif hasattr(child, 'active') and not child.active:
                        # Inactive project - muted gray
                        label = f"[dim]{child.projcode} - {child.title} [italic](Inactive)[/italic][/dim]"
                    else:
                        # Active project - normal display
                        label = f"{child.projcode} - {child.title}"

                    child_node = parent_tree.add(label)

                    # Recursively add grandchildren
                    if child.get_children():
                        build_tree_node(child, child_node, current_projcode)

            # Build tree starting from root
            is_current_root = root.projcode == current_projcode
            if is_current_root:
                if hasattr(root, 'active') and not root.active:
                    root_label = f"[bold yellow]→ {root.projcode} - {root.title}[/bold yellow] [dim italic](Inactive)[/dim italic]"
                else:
                    root_label = f"[bold yellow]→ {root.projcode} - {root.title}[/bold yellow]"
            else:
                if hasattr(root, 'active') and not root.active:
                    root_label = f"[dim]{root.projcode} - {root.title} [italic](Inactive)[/italic][/dim]"
                else:
                    root_label = f"{root.projcode} - {root.title}"

            tree = Tree(root_label)
            build_tree_node(root, tree, current_projcode)
            ctx.console.print(Panel(tree, title="Project Hierarchy", border_style="blue", expand=False))


def display_project_users(ctx: Context, project: Project):
    """Display users for a project with resource access information."""
    users = project.users

    if not users:
        ctx.console.print("No active users found.", style="yellow")
        return

    count = len(users)
    plural = "s" if count > 1 else ""

    ctx.console.print(f"\n[bold]{count} Active user{plural} for {project.projcode}:[/]")

    table = Table(box=box.SIMPLE)
    table.add_column("#", style="dim", width=4)
    table.add_column("Username", style="green")
    table.add_column("Name")
    table.add_column("Access Notes", style="yellow italic")

    if ctx.verbose:
        table.add_column("Email")
        table.add_column("UID")

    for i, user in enumerate(sorted(users, key=lambda u: u.username), 1):
        # Check for restricted resource access
        inaccessible = project.get_user_inaccessible_resources(user)
        access_notes = ""
        if inaccessible:
            sorted_resources = sorted(inaccessible)
            access_notes = f"no access to {', '.join(sorted_resources)}"

        row = [str(i), user.username, user.display_name, access_notes]
        if ctx.verbose:
            row.append(user.primary_email or 'N/A')
            row.append(str(user.unix_uid))
        table.add_row(*row)

    ctx.console.print(table)


def display_project_search_results(ctx: Context, projects: list, pattern: str):
    """Display project pattern search results."""
    ctx.console.print(f"✅ Found {len(projects)} project(s):\n", style="green bold")

    for i, project in enumerate(projects, 1):
        ctx.console.print(f"{i}. {project.projcode}", style="cyan bold")
        ctx.console.print(f"   {project.title}")

        if ctx.verbose:
            lead_name = project.lead.display_name if project.lead else 'N/A'
            ctx.console.print(f"   ID: {project.project_id}")
            ctx.console.print(f"   Lead: {lead_name}")
            ctx.console.print(f"   Users: {project.get_user_count()}")

        ctx.console.print("")


def display_expiring_projects(ctx: Context, expiring_data: list, list_users: bool = False, upcoming: bool = True):
    """Display upcoming or recently expired projects.

    Args:
        ctx: Context object
        expiring_data: List of tuples (project, allocation, resource_name, days)
        list_users: Whether to list users
        upcoming: True for upcoming expirations, False for recent expirations
    """
    if upcoming:
        ctx.console.print(f"Found {len(expiring_data)} allocations expiring", style="yellow")
        for proj, alloc, res_name, days in expiring_data:
            if ctx.verbose:
                display_project(ctx, proj, f" - {days} days remaining", list_users=list_users)
            else:
                ctx.console.print(f"  {proj.projcode} - {days} days remaining")
    else:
        ctx.console.print(f"Found {len(expiring_data)} recently expired projects:", style="yellow")
        for proj, alloc, res_name, days_expired in expiring_data:
            if ctx.verbose:
                display_project(ctx, proj, f" - {days_expired} days since expiration", list_users=list_users)
            else:
                ctx.console.print(f"  {proj.projcode} - {days_expired} days since expiration")

    if not ctx.verbose:
        ctx.console.print("\n (Use --verbose for more project information.)", style="dim italic")


def display_abandoned_users_from_expired_projects(ctx: Context, abandoned_users: set):
    """Display users whose only active projects have expired."""
    ctx.console.print(f"Found {len(abandoned_users)} expiring users:", style="bold red")
    table = Table(show_header=False, box=None)
    table.add_column("User")
    for user in sorted(abandoned_users, key=lambda u: u.username):
        table.add_row(f"{user.username:12} {user.display_name:30} <{user.primary_email}>")
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
    total_sent = success_count + failed_count

    # Summary panel
    grid = Table(show_header=False, box=None, padding=(0, 2))
    grid.add_column("Field", style="cyan bold")
    grid.add_column("Value")

    grid.add_row("Expiring Projects", str(total_projects))
    grid.add_row("Emails Sent", f"[green]{success_count}[/]" if success_count > 0 else "0")
    grid.add_row("Failed", f"[red]{failed_count}[/]" if failed_count > 0 else "0")

    panel = Panel(grid, title="Notification Results", expand=False, border_style="blue")
    ctx.console.print(panel)

    # Show failures if any
    if failed_count > 0:
        ctx.console.print("\n[red bold]Failed Notifications:[/]")
        for notification in results['failed']:
            ctx.console.print(f"  {notification['recipient']}: {notification.get('error', 'Unknown error')}", style="red")

    # Show success details in verbose mode
    if ctx.verbose and success_count > 0:
        ctx.console.print("\n[green]Successful Notifications:[/]")
        for notification in results['success']:
            ctx.console.print(f"  {notification['recipient']} ({notification['project_code']})", style="green")
