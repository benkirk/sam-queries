"""Display functions for user commands. Operate on plain dicts produced
by `cli.user.builders`; never touch ORM objects directly."""

from cli.core.context import Context
from sam import fmt
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box


def display_user(ctx: Context, data: dict, list_projects: bool = False):
    """Display user information.

    `data` is the dict returned by `build_user_core`, optionally with
    `data['detail']` (from `build_user_detail`) and `data['projects']`
    (from `build_user_projects`) filled in by the caller.
    """
    grid = Table(show_header=False, box=None, padding=(0, 2))
    grid.add_column("Field", style="cyan bold")
    grid.add_column("Value")

    grid.add_row("Username", data['username'])
    grid.add_row("Name", data['display_name'])
    grid.add_row("User ID", str(data['user_id']))
    grid.add_row("UPID", str(data['upid'] or 'N/A'))
    grid.add_row("Unix UID", str(data['unix_uid']))

    if data['emails']:
        emails = []
        for email in data['emails']:
            primary_marker = " (PRIMARY)" if email['is_primary'] else ""
            emails.append(f"<{email['address']}>{primary_marker}")
        grid.add_row("Email(s)", "\n".join(emails))

    status_text = Text()
    status_text.append("Active" if data['active'] else "Inactive",
                       style="green" if data['active'] else "red")
    status_text.append("  ")
    status_text.append("Locked: ", style="bold")
    status_text.append("Yes", style="red") if data['locked'] else status_text.append("No", style="green")
    status_text.append("  ")
    status_text.append("Accessible: ", style="bold")
    status_text.append("Yes", style="green") if data['is_accessible'] else status_text.append("No", style="red")
    grid.add_row("Status", status_text)

    if ctx.verbose and 'detail' in data:
        detail = data['detail']
        if detail['academic_status']:
            grid.add_row("Academic Status", detail['academic_status'])
        if detail['institutions']:
            grid.add_row(
                "Institution(s)",
                "\n".join(f"{i['name']} ({i['acronym']})" for i in detail['institutions'])
            )
        if detail['organizations']:
            grid.add_row(
                "Organization(s)",
                "\n".join(f"{o['name']} ({o['acronym']})" for o in detail['organizations'])
            )

    grid.add_row("Active Projects", str(data['active_project_count']))

    panel = Panel(grid, title=f"User Information: [bold]{data['username']}[/]",
                  expand=False, border_style="blue")
    ctx.console.print(panel)

    if not ctx.verbose and not list_projects:
        ctx.console.print(
            " (Use --list-projects to see project details, --verbose for more user information.)",
            style="dim italic"
        )

    if list_projects and 'projects' in data:
        display_user_projects(ctx, data['projects'], data['username'])


def display_user_projects(ctx: Context, projects: list, username: str):
    """Display projects for a user."""
    label = "All" if ctx.inactive_projects else "Active"

    if not projects:
        ctx.console.print("No projects found.", style="yellow")
        return

    ctx.console.print(f"\n{label} projects for {username}:", style="bold underline")

    table = Table(box=box.SIMPLE_HEAD)
    table.add_column("#", style="dim", width=4)
    table.add_column("Code", style="cyan bold")
    table.add_column("Title")
    table.add_column("Role", style="magenta")
    table.add_column("Status")
    if ctx.very_verbose:
        table.add_column("Alloc End", style="yellow")

    for i, p in enumerate(projects, 1):
        status_style = "green" if p['active'] else "red"
        status_str = "Active" if p['active'] else "Inactive"

        row = [str(i), p['projcode'], p['title'], p['role'],
               f"[{status_style}]{status_str}[/]"]

        if ctx.very_verbose:
            row.append(fmt.date_str(p['latest_allocation_end'], null='—'))

        table.add_row(*row)

    ctx.console.print(table)


def display_user_search_results(ctx: Context, data: dict):
    """Display user pattern search results from `build_user_search_results`."""
    ctx.console.print(f"✅ Found {data['count']} user(s):\n", style="green bold")

    table = Table(box=box.SIMPLE)
    table.add_column("#", style="dim")
    table.add_column("Username", style="green")
    table.add_column("Name")

    if ctx.verbose:
        table.add_column("ID")
        table.add_column("Email")
        table.add_column("Active")

    for i, u in enumerate(data['users'], 1):
        row = [str(i), u['username'], u['display_name']]
        if ctx.verbose:
            row.extend([
                str(u['user_id']),
                u['primary_email'] or 'N/A',
                "✓" if u['is_accessible'] else "✗"
            ])
        table.add_row(*row)

    ctx.console.print(table)


def display_abandoned_users(ctx: Context, data: dict):
    """Display abandoned users from `build_abandoned_users`."""
    ctx.console.print(f"Examining {data['total_active_users']:,} 'active' users listed in SAM")

    if data['users']:
        ctx.console.print(f"Found {data['count']:,} abandoned_users", style="bold yellow")

        table = Table(show_header=False, box=None)
        table.add_column("User")
        for u in data['users']:
            table.add_row(f"{u['username']:12} {u['display_name']:30} <{u['primary_email']}>")
        ctx.console.print(table)


def display_users_with_projects(ctx: Context, data: dict, list_projects: bool = False):
    """Display users who have at least one active project from
    `build_users_with_projects`."""
    ctx.console.print(
        f"Found {data['count']} users with at least one active project.",
        style="green"
    )

    if ctx.verbose:
        # Verbose mode renders each user as a full panel.  For that we
        # need core+detail dicts, which build_users_with_projects does
        # not produce — it has only the brief summary.  Fall back to
        # the same flat table layout as non-verbose for now; if a user
        # wants per-user verbose detail, they can run `sam-search user
        # <name> --verbose` directly.
        pass

    table = Table(show_header=False, box=None)
    table.add_column("User")
    for u in data['users']:
        table.add_row(f"{u['username']:12} {u['display_name']:30} <{u['primary_email']}>")
        if list_projects and 'projects' in u:
            for p in u['projects']:
                table.add_row(f"    - {p['projcode']:12} {p['title']}")
    ctx.console.print(table)
