"""Display functions for user commands. Operate on plain dicts produced
by `cli.user.builders`; never touch ORM objects directly."""


from cli.core.context import Context
from cli.core.display_utils import date_cell, issues_table, stamp
from sam import fmt
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box


def _user_line(u: dict) -> str:
    return f"{u['username']:12} {u['display_name']:30} <{u['primary_email']}>"


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

    if 'last_seen' in data:
        grid.add_row("Last seen", _last_seen_summary(data['last_seen']))

    grid.add_row("Active Projects", str(data['active_project_count']))

    panel = Panel(grid, title=f"User Information: [bold]{data['username']}[/]",
                  expand=False, border_style="blue")
    ctx.console.print(panel)

    if not ctx.verbose and not list_projects:
        ctx.console.print(
            " (Use --list-projects to see project details, --verbose for more user information.)",
            style="dim italic"
        )

    if ctx.verbose and data.get('last_seen'):
        from cli.last_seen.display import display_last_seen
        display_last_seen(ctx, {'username': data['username'], 'sources': data['last_seen']})

    if list_projects and 'projects' in data:
        display_user_projects(ctx, data['projects'], data['username'])

    if data.get('provisioning') is not None:
        display_user_provisioning(ctx, data['provisioning'], data['username'])


def _via(kind, system) -> str:
    if not kind:
        return '—'
    return kind if system == kind else f"{kind} · {system}"


def _last_seen_summary(sources):
    """Newest sighting for the panel row: ``2026-09-27 23:40 UTC  webapp · samuel  (3 hours ago)``."""
    if sources is None:
        return Text("unavailable", style="dim")
    if not sources:
        return Text("never", style="yellow")
    from system_status.timeutil import utcnow_naive
    newest = sources[0]
    when = 'now' if newest.get('current') else f"{fmt.ago(utcnow_naive() - newest['last_seen'])} ago"
    return (f"{stamp(newest['last_seen'], seconds=False)} UTC  "
            f"{_via(newest['kind'], newest['system'])}  ({when})")


def display_user_provisioning(ctx: Context, prov: dict, username: str):
    """Render the host provisioning cross-check for a user.

    `prov` is the dict from `sam.provisioning.check_user_provisioning`. Emits a
    single green line when everything is consistent, otherwise a short table of
    the specific gaps.
    """
    if not prov['recognized']:
        ctx.console.print(
            f"⚠️  Host provisioning: user [bold]{username}[/] is not recognized "
            "on this host.",
            style="red",
        )
        return

    issues = []
    if prov['uid_matches'] is False:
        issues.append(("UID mismatch",
                       f"host reports {prov['uid']} (SAM unix_uid differs)"))
    if prov['shell_ok'] is False:
        issues.append(("Login shell", f"{prov['shell']} (no-login)"))
    if prov['home_exists'] is False:
        issues.append(("Home directory", f"{prov['home']} (missing)"))
    for m in prov['missing_project_groups']:
        issues.append(("Missing group",
                       f"{m['projcode']} (gid {m['unix_gid']}) — not a member"))

    if not issues:
        ctx.console.print(
            "[green]✓[/] Host provisioning consistent "
            "(recognized, uid matches, all project groups present).",
            style="dim",
        )
        return

    issues_table(ctx, username, issues)


def display_user_projects(ctx: Context, projects: list, username: str):
    """Display projects for a user."""
    label = "All" if ctx.inactive_projects else "Active"

    if not projects:
        ctx.console.print("No projects found.", style="yellow")
        return

    ctx.console.print(f"\n{label} projects for {username}:", style="bold underline")

    # In the "All" view a project can be Active while the user's membership
    # in it has ended; surface that with a Membership column so the listing
    # doesn't contradict the "Active Projects" count / the web UI.
    show_membership = ctx.inactive_projects

    table = Table(box=box.SIMPLE_HEAD)
    table.add_column("#", style="dim", width=4)
    table.add_column("Code", style="cyan bold")
    table.add_column("Title")
    table.add_column("Role", style="magenta")
    if show_membership:
        table.add_column("Membership")
    table.add_column("Status")
    if ctx.very_verbose:
        table.add_column("Alloc End", style="yellow")

    for i, p in enumerate(projects, 1):
        status_style = "green" if p['active'] else "red"
        status_str = "Active" if p['active'] else "Inactive"

        row = [str(i), p['projcode'], p['title'], p['role']]

        if show_membership:
            m_active = p.get('membership_active', True)
            m_style = "green" if m_active else "red"
            m_str = "Active" if m_active else "Ended"
            row.append(f"[{m_style}]{m_str}[/]")

        row.append(f"[{status_style}]{status_str}[/]")

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
            table.add_row(_user_line(u))
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
        table.add_row(_user_line(u))
        if list_projects and 'projects' in u:
            for p in u['projects']:
                table.add_row(f"    - {p['projcode']:12} {p['title']}")
    ctx.console.print(table)


def display_not_seen_users(ctx: Context, data: dict):
    """Render ``build_not_seen_users``; email shows only under ``--verbose``."""
    who = 'active users' if data['active_only'] else 'users'
    scope = f" on {data['source']}" if data['source'] else ''
    tail = ', with no active projects' if data['abandoned'] else ''
    ctx.console.print(
        f"{fmt.number(data['count'])} of {fmt.number(data['total_considered'])} {who} "
        f"not seen{scope} since {fmt.date_str(data['cutoff'])} ({data['since']}){tail}",
        style="bold yellow" if data['count'] else "green")
    if not data['users']:
        return

    table = Table(box=box.SIMPLE_HEAD, caption="Dates are UTC.", caption_justify="left")
    table.add_column("Username", style="cyan", no_wrap=True)
    table.add_column("Name", overflow="ellipsis", max_width=22)
    table.add_column("Last seen", no_wrap=True, min_width=10)
    table.add_column("Via", style="dim", overflow="ellipsis")
    table.add_column("Projects", justify="right", no_wrap=True, min_width=8)
    if ctx.verbose:
        table.add_column("Email", no_wrap=True)
    for u in data['users']:
        seen = u['last_seen']
        # Active is the default and unmarked; only the exceptions carry a tag.
        name = u['username'] if u['status'] == 'active' else f"{u['username']} [dim]({u['status']})[/]"
        row = [name, u['display_name']]
        row += [fmt.date_str(seen) if seen else '[yellow]never[/]',
                _via(u['source'], u['system']),
                str(u['active_project_count'])]
        if ctx.verbose:
            row.append(u['primary_email'] or '—')
        table.add_row(*row)
    ctx.console.print(table)


def display_deactivation_restore(ctx: Context, data: dict):
    """The memberships a deactivation closed, and what a restore does to each."""
    if data['closed_at'] is None:
        ctx.console.print(f"No deactivation closure found for {data['username']}.",
                          style='yellow')
        return
    verb = 'would restore' if data['dry_run'] else 'restored'
    closed_at = stamp(data['closed_at'])
    table = Table(title=f"{data['username']}: memberships closed at {closed_at}", box=box.SIMPLE)
    for col in ('Project', 'Resource', 'Started', 'Outcome'):
        table.add_column(col)
    for row in data['rows']:
        outcome = row['outcome']
        style = 'green' if outcome == 'restored' else 'yellow'
        shown = verb if outcome == 'restored' else outcome
        table.add_row(row['projcode'], row['resource'], date_cell(row['start_date']),
                      Text(shown, style=style))
    ctx.console.print(table)
