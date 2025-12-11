#!/usr/bin/env python3
"""
SAM Search CLI Utility

A command-line tool for searching users and projects in the SAM database.
Reimplemented using Click and Rich.
"""

import sys
import click
from typing import Optional, List, Union, Dict
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.progress import track
from rich import box
from rich.tree import Tree

from sam import User, Project
# Import specific queries used in the original script
from sam.queries.expirations import (
    get_projects_by_allocation_end_date,
    get_projects_with_expired_allocations
)
from sam.queries.allocations import get_allocation_summary, get_allocation_summary_with_usage


class Context:
    def __init__(self):
        self.session: Optional[Session] = None
        self.verbose: bool = False
        self.inactive_projects: bool = False
        self.inactive_users: bool = False
        self.console = Console()


pass_context = click.make_pass_decorator(Context, ensure=True)


@click.group()
@click.option('--verbose', '-v', is_flag=True, help='Show detailed information')
@click.option('--inactive-projects', is_flag=True, help='Consider inactive projects')
@click.option('--inactive-users', is_flag=True, help='Consider inactive users')
@pass_context
def cli(ctx: Context, verbose: bool, inactive_projects: bool, inactive_users: bool):
    """Search and query the SAM database"""
    ctx.verbose = verbose
    ctx.inactive_projects = inactive_projects
    ctx.inactive_users = inactive_users

    # Initialize database connection
    try:
        from sam.session import create_sam_engine
        engine, _ = create_sam_engine()
        ctx.session = Session(engine)
    except Exception as e:
        ctx.console.print(f"Error connecting to database: {e}", style="bold red", err=True)
        sys.exit(1)


@cli.result_callback()
def process_result(result, **kwargs):
    """Cleanup session after command execution"""
    # This might not run if the command fails with an exception,
    # but the OS will clean up the socket/connection anyway.
    pass


# ========================================================================
# Helper Functions (Display Logic)
# ========================================================================

def _display_user(ctx: Context, user: User, list_projects: bool = False):
    """Display user information."""

    # Create a grid table for key-value pairs
    grid = Table(show_header=False, box=None, padding=(0, 2))
    grid.add_column("Field", style="cyan bold")
    grid.add_column("Value")

    grid.add_row("Username", user.username)
    grid.add_row("Name", user.display_name)
    grid.add_row("User ID", str(user.user_id))
    grid.add_row("UPID", str(user.upid or 'N/A'))
    grid.add_row("Unix UID", str(user.unix_uid))

    # Email addresses
    if user.email_addresses:
        emails = []
        for email in user.email_addresses:
            primary_marker = " (PRIMARY)" if email.is_primary else ""
            emails.append(f"<{email.email_address}>{primary_marker}")
        grid.add_row("Email(s)", "\n".join(emails))

    # Status
    status_text = Text()
    status_text.append("Active" if user.active else "Inactive", style="green" if user.active else "red")
    status_text.append("  ")
    status_text.append("Locked: ", style="bold")
    status_text.append("Yes", style="red") if user.locked else status_text.append("No", style="green")
    status_text.append("  ")
    status_text.append("Accessible: ", style="bold")
    status_text.append("Yes", style="green") if user.is_accessible else status_text.append("No", style="red")

    grid.add_row("Status", status_text)

    if ctx.verbose:
        # Academic status
        if user.academic_status:
            grid.add_row("Academic Status", user.academic_status.description)

        # Institutions
        if user.institutions:
            insts = []
            for ui in user.institutions:
                if ui.is_currently_active:
                    inst = ui.institution
                    insts.append(f"{inst.name} ({inst.acronym})")
            if insts:
                grid.add_row("Institution(s)", "\n".join(insts))

        # Organizations
        if user.organizations:
            orgs = []
            for uo in user.organizations:
                if uo.is_currently_active:
                    org = uo.organization
                    orgs.append(f"{org.name} ({org.acronym})")
            if orgs:
                grid.add_row("Organization(s)", "\n".join(orgs))

        # Project count
        num_projects = len(user.active_projects)
        grid.add_row("Active Projects", str(num_projects))
    else:
        # Just show counts
        num_projects = len(user.active_projects)
        grid.add_row("Active Projects", str(num_projects))

    panel = Panel(grid, title=f"User Information: [bold]{user.username}[/]", expand=False, border_style="blue")
    ctx.console.print(panel)

    if not ctx.verbose and not list_projects:
         ctx.console.print(" (Use --list-projects to see project details, --verbose for more user information.)", style="dim italic")

    if list_projects:
        _display_user_projects(ctx, user)


def _display_user_projects(ctx: Context, user: User):
    """Display projects for a user."""
    show_inactive = ctx.inactive_projects
    projects = user.all_projects if show_inactive else user.active_projects
    label = "All" if show_inactive else "Active"

    if not projects:
        ctx.console.print("No projects found.", style="yellow")
        return

    ctx.console.print(f"\n{label} projects for {user.username}:", style="bold underline")

    table = Table(box=box.SIMPLE_HEAD)
    table.add_column("#", style="dim", width=4)
    table.add_column("Code", style="cyan bold")
    table.add_column("Title")
    table.add_column("Role", style="magenta")
    table.add_column("Status")

    for i, project in enumerate(projects, 1):
        # Determine role logic (simple approximation if exact role object isn't easily grabbed without more queries,
        # but normally we'd check UserProject association.
        # Here we check if lead/admin match for simplicity as per original script logic which didn't show role explicitly in list).
        # We'll just show status and standard info.

        status_style = "green" if project.active else "red"
        status_str = "Active" if project.active else "Inactive"

        role = "Member"
        if project.lead == user:
            role = "Lead"
        elif project.admin == user:
            role = "Admin"

        table.add_row(str(i), project.projcode, project.title, role, f"[{status_style}]{status_str}[/]")

    ctx.console.print(table)
    return


def _display_project(ctx: Context, project: Project, extra_title_info: str = "", list_users: bool = False):
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

                    alloc_table.add_row(
                        resource_name,
                        resource_usage['resource_type'],
                        date_range,
                        f"{alloc.amount:,.0f}",
                        f"{resource_usage['remaining']:,.0f}",
                        f"{resource_usage['used']:,.0f}",
                        f"[{pct_style}]{pct:,.1f}%[/]"
                    )
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
        _display_project_users(ctx, project)
    else:
        ctx.console.print(f"\nActive Users: [bold]{project.get_user_count()}[/]")
        if not ctx.verbose:
            ctx.console.print(" (Use --list-users to see user details, --verbose for more project information.)", style="dim italic")

    if ctx.verbose:
        # Abstract
        if project.abstract:
            abstract = project.abstract
            if len(abstract) > 500:
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


def _display_project_users(ctx: Context, project: Project):
    """Display users for a project."""
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

    if ctx.verbose:
        table.add_column("Email")
        table.add_column("UID")

    for i, user in enumerate(sorted(users, key=lambda u: u.username), 1):
        row = [str(i), user.username, user.display_name]
        if ctx.verbose:
            row.append(user.primary_email or 'N/A')
            row.append(str(user.unix_uid))
        table.add_row(*row)

    ctx.console.print(table)


# ========================================================================
# User Commands
# ========================================================================

@cli.command()
@click.argument('username', required=False)
@click.option('--search', metavar='PATTERN', help='Search pattern (use % for wildcard, _ for single char)')
@click.option('--abandoned', is_flag=True, help="Find 'active' users with no active projects")
@click.option('--has-active-project', is_flag=True, help="Find 'active' users with at least one active projects")
@click.option('--list-projects', is_flag=True, help='List all projects for the user')
@click.option('--limit', type=int, default=50, help='Maximum number of results for pattern search (default: 50)')
@click.option('--verbose', '-v', is_flag=True, help='Show detailed information')
@pass_context
def user(ctx: Context, username, search, abandoned, has_active_project, list_projects, limit, verbose):
    """
    Search for users.

    You must provide either a username, --search PATTERN, --abandoned, or --has-active-project.
    """
    # Enforce mutual exclusivity
    inputs = [bool(username), bool(search), abandoned, has_active_project]
    if sum(inputs) != 1:
        ctx.console.print("Error: Please provide exactly one of: username, --search, --abandoned, or --has-active-project", style="bold red")
        click.echo(ctx.get_help())
        sys.exit(1)

    if verbose:
        ctx.verbose = True

    if username:
        # Exact Search
        try:
            user = User.get_by_username(ctx.session, username)
            if not user:
                ctx.console.print(f"❌ User not found: {username}", style="bold red")
                sys.exit(1)

            _display_user(ctx, user, list_projects)
        except Exception as e:
            ctx.console.print(f"❌ Error searching for user: {e}", style="bold red", err=True)
            sys.exit(2)

    elif abandoned:
        # Abandoned Users
        active_users = User.get_active_users(ctx.session)
        ctx.console.print(f"Examining {len(active_users):,} 'active' users listed in SAM")
        abandoned_users = set()

        for user in track(active_users, description=" --> determining abandoned users..."):
            if len(user.active_projects) == 0:
                abandoned_users.add(user)

        if abandoned_users:
            ctx.console.print(f"Found {len(abandoned_users):,} abandoned_users", style="bold yellow")

            table = Table(show_header=False, box=None)
            table.add_column("User")
            for user in sorted(abandoned_users, key=lambda u: u.username):
                table.add_row(f"{user.username:12} {user.display_name:30} <{user.primary_email}>")
            ctx.console.print(table)

    elif has_active_project:
        # Users with active projects
        active_users = User.get_active_users(ctx.session)
        users_with_projects = set()

        for user in track(active_users, description="Determining users with at least one active project..."):
            if len(user.active_projects) > 0:
                users_with_projects.add(user)

        if users_with_projects:
            ctx.console.print(f"Found {len(users_with_projects)} users with at least one active project.", style="green")

            if ctx.verbose:
                 for user in sorted(users_with_projects, key=lambda u: u.username):
                    _display_user(ctx, user)
                    if list_projects:
                        _display_user_projects(ctx, user)
            else:
                table = Table(show_header=False, box=None)
                table.add_column("User")
                for user in sorted(users_with_projects, key=lambda u: u.username):
                     table.add_row(f"{user.username:12} {user.display_name:30} <{user.primary_email}>")
                ctx.console.print(table)

    else:
        # Pattern Search
        try:
            clean_pattern = search.replace('%', '').replace('_', '')
            users = User.search_users(
                ctx.session,
                clean_pattern,
                active_only=not ctx.inactive_users,
                limit=limit
            )

            if not users:
                ctx.console.print(f"❌ No users found matching: {search}", style="red")
                sys.exit(1)

            ctx.console.print(f"✅ Found {len(users)} user(s):\n", style="green bold")

            table = Table(box=box.SIMPLE)
            table.add_column("#", style="dim")
            table.add_column("Username", style="green")
            table.add_column("Name")

            if ctx.verbose:
                table.add_column("ID")
                table.add_column("Email")
                table.add_column("Active")

            for i, user in enumerate(users, 1):
                row = [str(i), user.username, user.display_name]
                if ctx.verbose:
                    row.append(str(user.user_id))
                    row.append(user.primary_email or 'N/A')
                    row.append("✓" if user.is_accessible else "✗")
                table.add_row(*row)

            ctx.console.print(table)

        except Exception as e:
            ctx.console.print(f"❌ Error searching for users: {e}", style="bold red", err=True)
            sys.exit(2)


# ========================================================================
# Project Commands
# ========================================================================

@cli.command()
@click.argument('projcode', required=False)
@click.option('--search', metavar='PATTERN', help='Search pattern (use % for wildcard, _ for single char)')
@click.option('--upcoming-expirations', '-f', is_flag=True, help='Search for upcoming project expirations.')
@click.option('--recent-expirations', '-p', is_flag=True, help='Search for recently expired projects.')
@click.option('--list-users', is_flag=True, help='List all users on the project')
@click.option('--limit', type=int, default=50, help='Maximum number of results for pattern search (default: 50)')
@click.option('--verbose', '-v', is_flag=True, help='Show detailed information')
@pass_context
def project(ctx: Context, projcode, search, upcoming_expirations, recent_expirations, list_users, limit, verbose):
    """
    Search for projects.

    You must provide either a project code, --search PATTERN, --upcoming-expirations, or --recent-expirations.
    """
    inputs = [bool(projcode), bool(search), upcoming_expirations, recent_expirations]
    if sum(inputs) != 1:
        ctx.console.print("Error: Please provide exactly one of: projcode, --search, --upcoming-expirations, or --recent-expirations", style="bold red")
        click.echo(ctx.get_help())
        sys.exit(1)

    if verbose:
        ctx.verbose = True

    if upcoming_expirations:
        # Upcoming Expirations
        expiring = get_projects_by_allocation_end_date(ctx.session,
                                                       start_date=datetime.now(),
                                                       end_date=datetime.now() + timedelta(days=32),
                                                       facility_names=['UNIV', 'WNA'])

        ctx.console.print(f"Found {len(expiring)} allocations expiring", style="yellow")
        for proj, alloc, res_name, days in expiring:
            if ctx.verbose:
                _display_project(ctx, proj, f" - {days} days remaining", list_users=list_users)
            else:
                 ctx.console.print(f"  {proj.projcode} - {days} days remaining")
        if not ctx.verbose:
            ctx.console.print("\n (Use --verbose for more project information.)", style="dim italic")

    elif recent_expirations:
        # Recent Expirations
        all_users = set()
        abandoned_users = set()
        expiring_projects = set()
        expiring = get_projects_with_expired_allocations(ctx.session,
                                                         max_days_expired=90,
                                                         min_days_expired=365,
                                                         facility_names=['UNIV', 'WNA'])

        ctx.console.print(f"Found {len(expiring)} recently expired projects:", style="yellow")
        for proj, alloc, res_name, days in expiring:
            if list_users:
                all_users.update(proj.roster)
            expiring_projects.add(proj.projcode)

            if ctx.verbose:
                _display_project(ctx, proj, f" - {days} since expiration", list_users=list_users)
            else:
                ctx.console.print(f"  {proj.projcode} - {days} days since expiration")

        if list_users:
            for user in track(all_users, description="Determining abandoned users..."):
                user_projects = set()
                for proj in user.active_projects:
                    user_projects.add(proj.projcode)
                if user_projects.issubset(expiring_projects):
                    abandoned_users.add(user)

            ctx.console.print(f"Found {len(abandoned_users)} expiring users:", style="bold red")
            table = Table(show_header=False, box=None)
            table.add_column("User")
            for user in sorted(abandoned_users, key=lambda u: u.username):
                table.add_row(f"{user.username:12} {user.display_name:30} <{user.primary_email}>")
            ctx.console.print(table)

    elif projcode:
        # Exact Search
        try:
            project = Project.get_by_projcode(ctx.session, projcode)

            if not project:
                ctx.console.print(f"❌ Project not found: {projcode}", style="bold red")
                sys.exit(1)

            _display_project(ctx, project, list_users=list_users)

        except Exception as e:
            ctx.console.print(f"❌ Error searching for project: {e}", style="bold red", err=True)
            sys.exit(2)

    else:
        # Pattern Search
        try:
            projects = Project.search_by_pattern(
                ctx.session,
                search,
                search_title=True,
                active_only=not ctx.inactive_projects,
                limit=limit
            )

            if not projects:
                ctx.console.print(f"❌ No projects found matching: {search}", style="bold red")
                sys.exit(1)

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
        except Exception as e:
            ctx.console.print(f"❌ Error searching for projects: {e}", style="bold red", err=True)
            sys.exit(2)


# ========================================================================
# Allocation Commands
# ========================================================================

def _parse_comma_list(value: Optional[str]) -> Optional[Union[str, List[str]]]:
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


@cli.command()
@click.option('--resource', metavar='NAME', help='Resource name(s) to filter/group (comma-separated for multiple, or TOTAL to sum across)')
@click.option('--facility', metavar='NAME', help='Facility name(s) to filter/group (comma-separated for multiple, or TOTAL to sum across)')
@click.option('--allocation-type', metavar='TYPE', help='Allocation type(s) to filter/group (comma-separated for multiple, or TOTAL to sum across)')
@click.option('--project', metavar='CODE', help='Project code(s) to filter/group (comma-separated for multiple, or TOTAL to sum across)')
@click.option('--total-resources', is_flag=True, help='Sum across all resources (equivalent to --resource TOTAL)')
@click.option('--total-facilities', is_flag=True, help='Sum across all facilities (equivalent to --facility TOTAL)')
@click.option('--total-types', is_flag=True, help='Sum across all allocation types (equivalent to --allocation-type TOTAL)')
@click.option('--total-projects', is_flag=True, help='Sum across all projects (equivalent to --project TOTAL)')
@click.option('--active-at', metavar='DATE', help='Check allocations active at this date (YYYY-MM-DD). Default: today')
@click.option('--inactive', is_flag=True, help='Include inactive allocations (ignore dates)')
@click.option('--show-usage', is_flag=True, help='Include usage information (total used, percent used)')
@click.option('--verbose', '-v', is_flag=True, help='Show detailed information including averages')
@pass_context
def allocations(ctx: Context, resource, facility, allocation_type, project,
                total_resources, total_facilities, total_types, total_projects,
                active_at, inactive, show_usage, verbose):
    """
    Query allocation summaries with flexible grouping and filtering.

    By default, results are grouped by all dimensions (resource, facility, type, project).
    Use specific values to filter to one item, or use TOTAL/--total-* to sum across a dimension.
    You can specify multiple values as comma-separated lists (e.g., --resource Derecho,Casper).

    Examples:
        # All active allocations grouped by everything
        sam-search allocations

        # All Derecho allocations grouped by facility and type
        sam-search allocations --resource Derecho

        # Multiple resources
        sam-search allocations --resource Derecho,Casper --allocation-type Small,Classroom --total-projects

        # Total allocation amount for Exploratory projects on Casper GPU
        sam-search allocations --resource "Casper GPU" --allocation-type Exploratory --total-projects

        # Allocations that were active 6 months ago
        sam-search allocations --active-at 2024-06-15

        # All allocations for a specific project
        sam-search allocations --project SCSG0001
    """
    if verbose:
        ctx.verbose = True

    # Handle --total-* flags by converting to "TOTAL" string
    if total_resources:
        resource = "TOTAL"
    if total_facilities:
        facility = "TOTAL"
    if total_types:
        allocation_type = "TOTAL"
    if total_projects:
        project = "TOTAL"

    # Parse comma-separated values
    resource = _parse_comma_list(resource)
    facility = _parse_comma_list(facility)
    allocation_type = _parse_comma_list(allocation_type)
    project = _parse_comma_list(project)

    # Parse active_at date if provided
    active_at_date = None
    if active_at:
        try:
            active_at_date = datetime.strptime(active_at, "%Y-%m-%d")
        except ValueError:
            ctx.console.print(f"❌ Invalid date format: {active_at}. Use YYYY-MM-DD", style="bold red")
            sys.exit(1)

    # Query allocations
    try:
        if show_usage:
            results = get_allocation_summary_with_usage(
                ctx.session,
                resource_name=resource,
                facility_name=facility,
                allocation_type=allocation_type,
                projcode=project,
                active_only=not inactive,
                active_at=active_at_date
            )
        else:
            results = get_allocation_summary(
                ctx.session,
                resource_name=resource,
                facility_name=facility,
                allocation_type=allocation_type,
                projcode=project,
                active_only=not inactive,
                active_at=active_at_date
            )

        if not results:
            ctx.console.print("No allocations found matching criteria.", style="yellow")
            return

        # Display results
        _display_allocation_summary(ctx, results, show_usage=show_usage)

    except Exception as e:
        ctx.console.print(f"❌ Error querying allocations: {e}", style="bold red", err=True)
        if ctx.verbose:
            import traceback
            ctx.console.print(traceback.format_exc(), style="dim")
        sys.exit(2)


def _display_allocation_summary(ctx: Context, results: List[Dict], show_usage: bool = False):
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
    show_dates = ctx.verbose and all_single_allocations
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
            table_row.extend([start_str, end_str])

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

        # Count column always last (only when aggregating)
        if show_count:
            table_row.append(str(count))

        table.add_row(*table_row)

    ctx.console.print(table)

    # Print totals
    if show_usage:
        total_remaining = total_amount - total_used
        total_pct = (total_used / total_amount * 100) if total_amount > 0 else 0
        ctx.console.print(f"\n[bold]Grand Total:[/] {total_count:,} allocations")
        ctx.console.print(f"  Allocated: {total_amount:,.0f}")
        ctx.console.print(f"  Used: {total_used:,.0f}")
        ctx.console.print(f"  Remaining: {total_remaining:,.0f}")
        ctx.console.print(f"  Percent Used: {total_pct:.1f}%")
    else:
        ctx.console.print(f"\n[bold]Grand Total:[/] {total_count:,} allocations, {total_amount:,.0f} total allocation units")


if __name__ == '__main__':
    cli()
