"""Display functions for user commands."""

from cli.core.context import Context
from sam import User
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box


def display_user(ctx: Context, user: User, list_projects: bool = False):
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
        display_user_projects(ctx, user)


def display_user_projects(ctx: Context, user: User):
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
    if ctx.very_verbose:
        table.add_column("Alloc End", style="yellow")

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

        row = [str(i), project.projcode, project.title, role, f"[{status_style}]{status_str}[/]"]

        if ctx.very_verbose:
            latest_end = None
            for account in project.accounts:
                for alloc in account.allocations:
                    if alloc.end_date and (latest_end is None or alloc.end_date > latest_end):
                        latest_end = alloc.end_date
            row.append(latest_end.strftime("%Y-%m-%d") if latest_end else "—")

        table.add_row(*row)

    ctx.console.print(table)
    return


def display_user_search_results(ctx: Context, users: list, pattern: str):
    """Display user pattern search results."""
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
            row.extend([
                str(user.user_id),
                user.primary_email or 'N/A',
                "✓" if user.is_accessible else "✗"
            ])
        table.add_row(*row)

    ctx.console.print(table)


def display_abandoned_users(ctx: Context, abandoned_users: set, total_active_users: int):
    """Display abandoned users (active users with no active projects)."""
    ctx.console.print(f"Examining {total_active_users:,} 'active' users listed in SAM")

    if abandoned_users:
        ctx.console.print(f"Found {len(abandoned_users):,} abandoned_users", style="bold yellow")

        table = Table(show_header=False, box=None)
        table.add_column("User")
        for user in sorted(abandoned_users, key=lambda u: u.username):
            table.add_row(f"{user.username:12} {user.display_name:30} <{user.primary_email}>")
        ctx.console.print(table)


def display_users_with_projects(ctx: Context, users_with_projects: set, list_projects: bool = False):
    """Display users who have at least one active project."""
    ctx.console.print(f"Found {len(users_with_projects)} users with at least one active project.", style="green")

    if ctx.verbose:
        for user in sorted(users_with_projects, key=lambda u: u.username):
            display_user(ctx, user)
            if list_projects:
                display_user_projects(ctx, user)
    else:
        table = Table(show_header=False, box=None)
        table.add_column("User")
        for user in sorted(users_with_projects, key=lambda u: u.username):
            table.add_row(f"{user.username:12} {user.display_name:30} <{user.primary_email}>")
        ctx.console.print(table)
