#!/usr/bin/env python3
"""
SAM Search CLI - User-facing search commands.

A command-line tool for searching users and projects in the SAM database.
"""

import sys
import click
from sqlalchemy.orm import Session

from config import SAMConfig
from cli.core.context import Context
from cli.core.utils import EXIT_ERROR
from cli.user.commands import (
    UserSearchCommand,
    UserPatternSearchCommand,
    UserAbandonedCommand,
    UserWithProjectsCommand
)
from cli.project.commands import (
    ProjectSearchCommand,
    ProjectPatternSearchCommand,
    ProjectExpirationCommand
)
from cli.allocations.commands import AllocationSearchCommand
from cli.accounting.commands import AccountingSearchCommand
from cli.accounting.dates import _validate_accounting_dates, _resolve_accounting_dates


pass_context = click.make_pass_decorator(Context, ensure=True)
CONTEXT_SETTINGS = dict(help_option_names=['-h', '--help'])


@click.group(context_settings=CONTEXT_SETTINGS)
@click.option('--verbose', '-v', is_flag=True, help='Show detailed information')
@click.option('--inactive-projects', is_flag=True, help='Consider inactive projects')
@click.option('--inactive-users', is_flag=True, help='Consider inactive users')
@pass_context
def cli(ctx: Context, verbose: bool, inactive_projects: bool, inactive_users: bool):
    """Search and query the SAM database"""
    try:
        SAMConfig.validate()
    except EnvironmentError as e:
        ctx.stderr_console.print(str(e), style="bold red")
        sys.exit(2)

    ctx.verbose = verbose
    ctx.inactive_projects = inactive_projects
    ctx.inactive_users = inactive_users

    # Initialize database connection
    try:
        from sam.session import create_sam_engine
        engine, _ = create_sam_engine()
        ctx.session = Session(engine)
    except Exception as e:
        ctx.stderr_console.print(f"Error connecting to database: {e}", style="bold red")
        sys.exit(1)


@cli.result_callback()
def process_result(result, **kwargs):
    """Cleanup session after command execution"""
    # This might not run if the command fails with an exception,
    # but the OS will clean up the socket/connection anyway.
    pass


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
@click.option('--very-verbose', '-vv', is_flag=True, help='Show full information (allocation end dates, timestamps)')
@pass_context
def user(ctx: Context, username, search, abandoned, has_active_project, list_projects, limit, verbose, very_verbose):
    """
    Search for users.

    You must provide either a username, --search PATTERN, --abandoned, or --has-active-project.
    """
    # Enforce mutual exclusivity
    inputs = [bool(username), bool(search), abandoned, has_active_project]
    if sum(inputs) != 1:
        ctx.console.print("Error: Please provide exactly one of: username, --search, --abandoned, or --has-active-project", style="bold red")
        click.echo(click.get_current_context().get_help())
        sys.exit(1)

    if very_verbose:
        ctx.very_verbose = True
        ctx.verbose = True  # very_verbose implies verbose
    elif verbose:
        ctx.verbose = True

    if username:
        # Exact Search
        command = UserSearchCommand(ctx)
        exit_code = command.execute(username, list_projects)
        sys.exit(exit_code)

    elif search:
        # Pattern Search
        command = UserPatternSearchCommand(ctx)
        exit_code = command.execute(search, limit)
        sys.exit(exit_code)

    elif abandoned:
        # Abandoned Users
        command = UserAbandonedCommand(ctx)
        exit_code = command.execute()
        sys.exit(exit_code)

    elif has_active_project:
        # Users with active projects
        command = UserWithProjectsCommand(ctx)
        exit_code = command.execute(list_projects)
        sys.exit(exit_code)


# ========================================================================
# Project Commands
# ========================================================================

@cli.command()
@click.argument('projcode', required=False)
@click.option('--search', metavar='PATTERN', help='Search pattern (use % for wildcard, _ for single char)')
@click.option('--upcoming-expirations', '-f', is_flag=True, help='Search for upcoming project expirations.')
@click.option('--recent-expirations', '-p', is_flag=True, help='Search for recently expired projects.')
@click.option('--since', type=click.DateTime(formats=['%Y-%m-%d']), default=None, help='Look back to this date for --recent-expirations (e.g., 2024-01-01)')
@click.option('--list-users', is_flag=True, help='List all users on the project')
@click.option('--limit', type=int, default=50, help='Maximum number of results for pattern search (default: 50)')
@click.option('--facilities', '-F', multiple=True, default=['UNIV', 'WNA'], help='Facilities to include (default: UNIV, WNA). Use * for all facilities.')
@click.option('--verbose', '-v', is_flag=True, help='Show detailed information (truncated abstract, hierarchy)')
@click.option('--very-verbose', '-vv', is_flag=True, help='Show full information (full abstract, timestamps, IDs, charge breakdown)')
@pass_context
def project(ctx: Context, projcode, search, upcoming_expirations, recent_expirations, since, list_users, limit, facilities, verbose, very_verbose):
    """
    Search for projects.

    You must provide either a project code, --search PATTERN, --upcoming-expirations, or --recent-expirations.
    """
    inputs = [bool(projcode), bool(search), upcoming_expirations, recent_expirations]
    if sum(inputs) != 1:
        ctx.console.print("Error: Please provide exactly one of: projcode, --search, --upcoming-expirations, or --recent-expirations", style="bold red")
        click.echo(click.get_current_context().get_help())
        sys.exit(1)

    if very_verbose:
        ctx.very_verbose = True
        ctx.verbose = True  # very_verbose implies verbose
    elif verbose:
        ctx.verbose = True

    # Handle facility filtering - '*' means all facilities
    facility_filter = None if '*' in facilities else list(facilities)

    if upcoming_expirations:
        # Upcoming Expirations
        command = ProjectExpirationCommand(ctx)
        exit_code = command.execute(upcoming=True, list_users=list_users, facility_filter=facility_filter)
        sys.exit(exit_code)

    elif recent_expirations:
        # Recent Expirations
        command = ProjectExpirationCommand(ctx)
        exit_code = command.execute(upcoming=False, since=since, list_users=list_users, facility_filter=facility_filter)
        sys.exit(exit_code)

    elif projcode:
        # Exact Search
        command = ProjectSearchCommand(ctx)
        exit_code = command.execute(projcode, list_users=list_users)
        sys.exit(exit_code)

    else:
        # Pattern Search
        command = ProjectPatternSearchCommand(ctx)
        exit_code = command.execute(search, limit)
        sys.exit(exit_code)


# ========================================================================
# Allocation Commands
# ========================================================================

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
@click.option('--exclude-adjustments', is_flag=True, default=False,
              help='Exclude manual charge adjustments from usage totals.')
@click.option('--verbose', '-v', is_flag=True, help='Show detailed information including averages')
@pass_context
def allocations(ctx: Context, resource, facility, allocation_type, project,
                total_resources, total_facilities, total_types, total_projects,
                active_at, inactive, show_usage, exclude_adjustments, verbose):
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

    command = AllocationSearchCommand(ctx)
    exit_code = command.execute(
        resource=resource,
        facility=facility,
        allocation_type=allocation_type,
        project=project,
        total_resources=total_resources,
        total_facilities=total_facilities,
        total_types=total_types,
        total_projects=total_projects,
        active_at=active_at,
        inactive=inactive,
        show_usage=show_usage,
        include_adjustments=not exclude_adjustments
    )
    sys.exit(exit_code)


# ========================================================================
# Accounting Commands
# ========================================================================

@cli.command()
@click.option('--user',     metavar='USERNAME', default=None, help='Filter by username (% wildcard ok)')
@click.option('--project',  metavar='CODE',     default=None, help='Filter by project code (% wildcard ok)')
@click.option('--resource', metavar='NAME',     default=None, help='Filter by resource name (% wildcard ok)')
@click.option('--queue',    metavar='NAME',     default=None, help='Filter by queue name (exact)')
@click.option('--machine',  metavar='NAME',     default=None, help='Filter by machine name (% wildcard ok, e.g. derecho)')
@click.option('-d', '--date', 'date_str',  type=str, default=None, metavar='YYYY-MM-DD', help='Single date')
@click.option('--today', 'today_flag', is_flag=True, help='Use today as the date')
@click.option('--last',  type=str, default=None, metavar='N[d]', help='Last N days including today (e.g. --last 14d)')
@click.option('--start', type=str, default=None, metavar='YYYY-MM-DD', help='Start date')
@click.option('--end',   type=str, default=None, metavar='YYYY-MM-DD', help='End date')
@click.option('--verbose', '-v', is_flag=True, help='Show per-day row breakdown')
@pass_context
def accounting(ctx: Context, user, project, resource, queue, machine,
               date_str, today_flag, last, start, end, verbose):
    """
    Query comp charge summaries from SAM summary tables.

    Reads directly from comp_charge_summary (no HPC job-history plugin required).
    Results are aggregated by user/project/resource/machine/queue over the date range.
    Use --verbose for a per-day breakdown.

    \b
    Date Selection (one required):
      --date YYYY-MM-DD   Single specific date
      --today             Today's date
      --last N[d]         Last N days including today (e.g. --last 14d)
      --start / --end     Date range

    Examples:
      sam-search accounting --last 14d --resource Derecho
      sam-search accounting --last 7d --user benkirk
      sam-search accounting --start 2025-01-01 --end 2025-03-01 --project SCSG%
      sam-search accounting --last 7d --resource Derecho --verbose
    """
    _validate_accounting_dates(date_str, start, end, today_flag, last)
    start_date, end_date = _resolve_accounting_dates(date_str, start, end, today_flag, last)
    if verbose:
        ctx.verbose = True
    command = AccountingSearchCommand(ctx)
    sys.exit(command.execute(
        start_date=start_date,
        end_date=end_date,
        username=user,
        projcode=project,
        resource=resource,
        queue=queue,
        machine=machine,
    ))


if __name__ == '__main__':
    cli()
