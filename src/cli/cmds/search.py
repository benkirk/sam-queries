#!/usr/bin/env python3
"""
SAM Search CLI - User-facing search commands.

A command-line tool for searching users and projects in the SAM database.
"""

import sys
import click

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
from cli.contracts.commands import (
    ContractPatternSearchCommand,
    ContractSearchCommand,
)
from cli.awards.commands import AwardPatternSearchCommand, AwardSearchCommand
from cli.allocations.commands import AllocationSearchCommand
from cli.accounting.commands import AccountingSearchCommand, AccountingJobsCommand
from cli.accounting.dates import _validate_accounting_dates, _resolve_accounting_dates


pass_context = click.make_pass_decorator(Context, ensure=True)
CONTEXT_SETTINGS = dict(help_option_names=['-h', '--help'])


@click.group(context_settings=CONTEXT_SETTINGS)
@click.option('--verbose', '-v', is_flag=True, help='Show detailed information')
@click.option('--inactive-projects', is_flag=True, help='Consider inactive projects')
@click.option('--inactive-users', is_flag=True, help='Consider inactive users')
@click.option('--format', 'output_format',
              type=click.Choice(['rich', 'json']), default='rich',
              help='Output format (default: rich)')
@pass_context
def cli(ctx: Context, verbose: bool, inactive_projects: bool, inactive_users: bool,
        output_format: str):
    """Search and query the SAM database"""
    try:
        SAMConfig.validate()
    except EnvironmentError as e:
        ctx.stderr_console.print(str(e), style="bold red")
        sys.exit(2)

    ctx.verbose = verbose
    ctx.inactive_projects = inactive_projects
    ctx.inactive_users = inactive_users
    ctx.output_format = output_format

    # NO database connection here — `Context.require_sam()` opens one on first
    # use. Kept in step with sam-admin; see SCHEDULED_TASKS.md § 3.2.


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
@click.option('--provisioning/--no-provisioning', default=None,
              help='Cross-check host provisioning (auto-on on a provisioned host)')
@pass_context
def user(ctx: Context, username, search, abandoned, has_active_project, list_projects, limit, verbose, very_verbose, provisioning):
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

    if provisioning is not None:
        ctx.check_provisioning = provisioning

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
@click.option('--provisioning/--no-provisioning', default=None,
              help='Cross-check host provisioning (auto-on on a provisioned host)')
@pass_context
def project(ctx: Context, projcode, search, upcoming_expirations, recent_expirations, since, list_users, limit, facilities, verbose, very_verbose, provisioning):
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

    if provisioning is not None:
        ctx.check_provisioning = provisioning

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
@click.option('--machine',  metavar='NAME',     default=None, help='Filter by machine name (summary: % wildcard ok; --jobs: derecho|casper)')
@click.option('--jobs', is_flag=True, help='List individual jobs via the hpc-usage-queries plugin')
@click.option('--recent',  type=int, default=None, metavar='M', help='With --jobs: most recent M jobs (default 50)')
@click.option('--largest', type=int, default=None, metavar='N', help='With --jobs: top N jobs by charges')
@click.option('--job-id', 'job_id', type=str, default=None, metavar='ID', help='With --jobs: filter by job id. Digits (6049117) match scalar + every array element; partial array form (6049117[28], 6049117[]) prefix-matches across hosts; full id with host (6049117[28].desched1) is exact.')
@click.option('--qos', type=str, default=None, metavar='NAME', help='With --jobs: filter by QoS name')
@click.option('-d', '--date', 'date_str',  type=str, default=None, metavar='YYYY-MM-DD', help='Single date')
@click.option('--today', 'today_flag', is_flag=True, help='Use today as the date')
@click.option('--last',  type=str, default=None, metavar='N[d]', help='Last N days including today (e.g. --last 14d)')
@click.option('--start', type=str, default=None, metavar='YYYY-MM-DD', help='Start date')
@click.option('--end',   type=str, default=None, metavar='YYYY-MM-DD', help='End date')
@click.option('--verbose', '-v', is_flag=True, help='Summary: per-day breakdown. --jobs: extra detail columns')
@pass_context
def accounting(ctx: Context, user, project, resource, queue, machine,
               jobs, recent, largest, job_id, qos,
               date_str, today_flag, last, start, end, verbose):
    """
    Query computational charges from SAM.

    Default mode reads SAM's pre-aggregated comp_charge_summary table (no
    plugin required), grouped by user/project/resource/machine/queue; use
    --verbose for a per-day breakdown.

    With --jobs it lists individual jobs from the hpc-usage-queries plugin,
    classifying each job's billed charge with the same CPU/GPU rules as the
    daily poster. Select either the most recent M jobs (--recent, default 50),
    the top N by charges (--largest), or a specific id (--job-id); use
    --verbose for extra columns.

    \b
    Date Selection (one required):
      --date YYYY-MM-DD   Single specific date
      --today             Today's date
      --last N[d]         Last N days including today (e.g. --last 14d)
      --start / --end     Date range

    \b
    Examples:
      sam-search accounting --last 14d --resource Derecho
      sam-search accounting --last 7d --user benkirk
      sam-search accounting --start 2025-01-01 --end 2025-03-01 --project SCSG%
      sam-search accounting --jobs --last 7d --user benkirk
      sam-search accounting --jobs --largest 20 --last 30d --project SCSG0001
      sam-search accounting --jobs --last 7d --machine derecho --verbose
      sam-search accounting --jobs --last 365d --job-id 6049117[28]
    """
    _validate_accounting_dates(date_str, start, end, today_flag, last)
    start_date, end_date = _resolve_accounting_dates(date_str, start, end, today_flag, last)
    if verbose:
        ctx.verbose = True

    if jobs:
        if resource:
            raise click.UsageError(
                "--resource is not supported with --jobs (resource is derived "
                "per machine); use --machine derecho|casper instead."
            )
        command = AccountingJobsCommand(ctx)
        sys.exit(command.execute(
            start_date=start_date,
            end_date=end_date,
            username=user,
            projcode=project,
            queue=queue,
            qos=qos,
            machine=machine,
            recent=recent,
            largest=largest,
            job_id=job_id,
        ))

    # Summary mode: reject --jobs-only flags
    for val, name in ((recent, '--recent'), (largest, '--largest'),
                      (job_id, '--job-id'), (qos, '--qos')):
        if val is not None:
            raise click.UsageError(f"{name} requires --jobs.")

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


# ========================================================================
# Contract Commands
# ========================================================================

@cli.command()
@click.argument('contract_number', required=False)
@click.option('--search', metavar='PATTERN',
              help='Search number and title. Treated as a LIKE pattern when '
                   'it contains %% or _, otherwise as a substring '
                   '(so "climate" == "%%climate%%", while "AGS-%%" anchors). '
                   'Matching is case-insensitive.')
@click.option('--all', 'search_all', is_flag=True,
              help='Include expired and not-yet-started contracts '
                   '(default: open only)')
@click.option('--source', metavar='NAME',
              help='Filter by funding source name, e.g. NSF or DOE')
@click.option('--pi', metavar='USERNAME',
              help='Filter by principal investigator username')
@click.option('--monitor', metavar='USERNAME',
              help='Filter by contract monitor username')
@click.option('--program', metavar='PATTERN',
              help='Filter by NSF program name (same pattern rules as --search)')
@click.option('--list-projects', is_flag=True,
              help='List the projects linked to the contract')
@click.option('--limit', type=int, default=50,
              help='Maximum number of results for pattern search (default: 50)')
@click.option('--verbose', '-v', is_flag=True, help='Show detailed information')
@pass_context
def contracts(ctx: Context, contract_number, search, search_all, source, pi,
              monitor, program, list_projects, limit, verbose):
    """
    Search for contracts in SAM.

    You must provide either a contract number or --search PATTERN. Filters
    (--source/--pi/--monitor/--program) apply to --search.

    To ask the funding agency instead of SAM, use `sam-search awards`.
    """
    inputs = [bool(contract_number), bool(search)]
    filters_only = any([source, pi, monitor, program]) and sum(inputs) == 0

    # Filters alone are a legitimate query ("every open NSF contract"), so
    # they stand in for --search rather than requiring an empty one.
    if sum(inputs) != 1 and not filters_only:
        ctx.console.print(
            "Error: Please provide exactly one of: contract number, --search, "
            "or at least one filter (--source/--pi/--monitor/--program)",
            style="bold red")
        click.echo(click.get_current_context().get_help())
        sys.exit(1)

    if verbose:
        ctx.verbose = True

    if contract_number:
        command = ContractSearchCommand(ctx)
        sys.exit(command.execute(contract_number,
                                 list_projects=list_projects))

    command = ContractPatternSearchCommand(ctx)
    sys.exit(command.execute(
        pattern=search,
        active_only=not search_all,
        source=source,
        pi=pi,
        monitor=monitor,
        program=program,
        limit=limit,
    ))


# ========================================================================
# Award Commands
# ========================================================================

@cli.command()
@click.argument('contract_number', required=False)
@click.option('--search', metavar='QUERY',
              help='Free-text search across the award providers')
@click.option('--source', metavar='NAME',
              help='Scope to one funding source, e.g. NSF or DOE. '
                   'NSF searches NSF\'s own API; anything else searches '
                   'USAspending.')
@click.option('--limit', type=int, default=10,
              help='Maximum results per provider (default: 10)')
@click.option('--verbose', '-v', is_flag=True, help='Show detailed information')
@pass_context
def awards(ctx: Context, contract_number, search, source, limit, verbose):
    """
    Search public award APIs (NSF, USAspending).

    You must provide either an award/contract number or --search QUERY.

    Asks the funding agency, not SAM — use `sam-search contracts` for what
    SAM already holds. A number lookup also cross-references SAM and reports
    any divergence.

    Exit codes: 0 found, 1 no such award, 2 the source could not be reached.
    """
    inputs = [bool(contract_number), bool(search)]
    if sum(inputs) != 1:
        ctx.console.print(
            "Error: Please provide exactly one of: award number or --search",
            style="bold red")
        click.echo(click.get_current_context().get_help())
        sys.exit(1)

    if verbose:
        ctx.verbose = True

    if contract_number:
        command = AwardSearchCommand(ctx)
        sys.exit(command.execute(contract_number, source=source))

    command = AwardPatternSearchCommand(ctx)
    sys.exit(command.execute(search, source=source, limit=limit))


if __name__ == '__main__':
    cli()
