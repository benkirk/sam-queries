#!/usr/bin/env python3
"""
SAM Admin CLI - Administrative commands.

Administrative commands for SAM database management and validation.
"""

import sys
import click
from datetime import date as _date
from sqlalchemy.orm import Session

from config import SAMConfig
from cli.core.context import Context
from cli.user.commands import UserAdminCommand
from cli.project.commands import ProjectAdminCommand, ProjectExpirationCommand
from cli.accounting.commands import AccountingAdminCommand
from cli.accounting.dates import _validate_accounting_dates, _resolve_accounting_dates


pass_context = click.make_pass_decorator(Context, ensure=True)
CONTEXT_SETTINGS = dict(help_option_names=['-h', '--help'])


@click.group(context_settings=CONTEXT_SETTINGS)
@click.option('--verbose', '-v', is_flag=True, help='Show detailed information')
@click.option('--format', 'output_format',
              type=click.Choice(['rich', 'json']), default='rich',
              help='Output format (default: rich)')
@pass_context
def cli(ctx: Context, verbose: bool, output_format: str):
    """Administrative commands for SAM database"""
    try:
        SAMConfig.validate()
    except EnvironmentError as e:
        ctx.stderr_console.print(str(e), style="bold red")
        sys.exit(2)

    ctx.verbose = verbose
    ctx.output_format = output_format

    # Initialize database connection
    try:
        from sam.session import create_sam_engine
        engine, _ = create_sam_engine()
        ctx.session = Session(engine)
    except Exception as e:
        ctx.stderr_console.print(f"Error connecting to database: {e}", style="bold red")
        sys.exit(1)


@cli.command()
@click.argument('username')
@click.option('--validate', is_flag=True, help='Validate user data integrity')
@click.option('--list-projects', is_flag=True, help='List all projects')
@click.option('--verbose', '-v', is_flag=True, help='Show detailed information')
@pass_context
def user(ctx: Context, username, validate, list_projects, verbose):
    """Administrative user commands."""
    if verbose:
        ctx.verbose = True

    command = UserAdminCommand(ctx)
    exit_code = command.execute(username, validate=validate, list_projects=list_projects)
    sys.exit(exit_code)


@cli.command()
@click.argument('projcode', required=False)
@click.option('--validate', is_flag=True, help='Validate project data')
@click.option('--reconcile', is_flag=True, help='Reconcile allocations')
@click.option('--upcoming-expirations', is_flag=True, help='Search for upcoming project expirations')
@click.option('--recent-expirations', is_flag=True, help='Show recently expired projects')
@click.option('--notify', is_flag=True, help='Send email notifications (requires --upcoming-expirations)')
@click.option('--dry-run', is_flag=True, help='Preview emails without sending (requires --notify)')
@click.option('--email-list', type=str, help='Comma-separated list of additional email recipients')
@click.option('--deactivate', is_flag=True, help='Deactivate expired projects (requires --recent-expirations)')
@click.option('--force', is_flag=True, help='Skip confirmation prompt (requires --deactivate)')
@click.option('--since', type=click.DateTime(formats=['%Y-%m-%d']), default=None,
              help='Look back to this date for --recent-expirations (e.g., 2024-01-01)')
@click.option('--list-users', is_flag=True, help='List all users')
@click.option('--facilities', '-F', multiple=True, default=['UNIV', 'WNA'], help='Facilities to include (default: UNIV, WNA). Use * for all facilities.')
@click.option('--verbose', '-v', is_flag=True, help='Show detailed information')
@pass_context
def project(ctx: Context, projcode, validate, reconcile, upcoming_expirations, recent_expirations,
            notify, dry_run, email_list, deactivate, force, since, list_users, facilities, verbose):
    """Administrative project commands."""
    if verbose:
        ctx.verbose = True

    # Validate that --notify requires --upcoming-expirations
    if notify and not upcoming_expirations:
        ctx.console.print("Error: --notify requires --upcoming-expirations", style="bold red")
        sys.exit(1)

    # Validate that --dry-run requires --notify
    if dry_run and not notify:
        ctx.console.print("Error: --dry-run requires --notify", style="bold red")
        sys.exit(1)

    # Validate that --deactivate requires --recent-expirations
    if deactivate and not recent_expirations:
        ctx.console.print("Error: --deactivate requires --recent-expirations", style="bold red")
        sys.exit(1)

    # Validate that --force requires --deactivate
    if force and not deactivate:
        ctx.console.print("Error: --force requires --deactivate", style="bold red")
        sys.exit(1)

    # Handle facility filtering - '*' means all facilities
    facility_filter = None if '*' in facilities else list(facilities)

    # Handle upcoming expirations with optional notification
    if upcoming_expirations:
        command = ProjectExpirationCommand(ctx)
        exit_code = command.execute(
            upcoming=True,
            list_users=list_users,
            facility_filter=facility_filter,
            notify=notify,
            dry_run=dry_run,
            email_list=email_list
        )
        sys.exit(exit_code)

    # Handle recent expirations with optional deactivation
    if recent_expirations:
        command = ProjectExpirationCommand(ctx)
        exit_code = command.execute(
            upcoming=False,
            since=since,
            list_users=list_users,
            facility_filter=facility_filter,
            deactivate=deactivate,
            force=force
        )
        sys.exit(exit_code)

    # Require projcode for other operations
    if not projcode:
        ctx.console.print(
            "Error: projcode argument is required unless using --upcoming-expirations or --recent-expirations",
            style="bold red"
        )
        click.echo(click.get_current_context().get_help())
        sys.exit(1)

    command = ProjectAdminCommand(ctx)
    exit_code = command.execute(projcode, validate=validate, reconcile=reconcile,
                                list_users=list_users)
    sys.exit(exit_code)


@cli.command()
@click.option('--comp', is_flag=True, help='Post computational charge summaries')
@click.option('--disk', is_flag=True, help='Post disk charge summaries (not yet implemented)')
@click.option('--archive', is_flag=True, help='Post archive charge summaries (not yet implemented)')
@click.option('--reconcile-quotas', 'reconcile_quotas', type=click.Path(exists=True, dir_okay=False),
              default=None, metavar='PATH',
              help='Reconcile SAM allocations against a storage quota file (requires --resource)')
@click.option('--resource', type=str, default=None,
              help='Resource name (required with --reconcile-quotas, e.g. Campaign_Store)')
@click.option('--machine', '-m', type=click.Choice(['derecho', 'casper']), default=None,
              help='HPC machine (required with --comp/--disk/--archive)')
@click.option('--start', type=str, default=None, help='Start date (YYYY-MM-DD, inclusive; default: 2024-01-01)')
@click.option('--end', type=str, default=None, help='End date (YYYY-MM-DD, inclusive; default: yesterday)')
@click.option('-d', '--date', 'date_str', type=str, default=None, help='Specific date (YYYY-MM-DD)')
@click.option('--today', 'today_flag', is_flag=True, help='Use today as the date')
@click.option('--last', type=str, default=None, metavar='N[d]',
              help='Last N days including today (e.g. --last 3d)')
@click.option('--dry-run', is_flag=True, help='Preview without writing (charge-posting modes only; --reconcile-quotas is report-only by default)')
@click.option('--update-accounting-system', 'update_accounting_system', is_flag=True,
              help='Apply mismatched amount updates (requires --reconcile-quotas; default is report-only)')
@click.option('--deactivate-orphaned', 'deactivate_orphaned', is_flag=True,
              help='Deactivate orphaned allocations (independent of --update-accounting-system)')
@click.option('--force', is_flag=True,
              help='Override the live-path safety gate when deactivating orphans whose ProjectDirectory paths still exist on disk (requires --deactivate-orphaned)')
@click.option('--verify-paths', 'verify_paths', is_flag=True,
              help='Check fileset/ProjectDirectory paths on disk (requires --reconcile-quotas)')
@click.option('--verify-host', 'verify_host', type=str, default=None, metavar='HOST',
              help='SSH host to use for --verify-paths (default: auto-detect from the reader)')
@click.option('--skip-errors', is_flag=True, help='Skip rows that fail entity resolution')
@click.option('--create-queues', is_flag=True, help='Auto-create unknown queues in SAM')
@click.option('--chunk-size', type=int, default=500, show_default=True,
              help='Rows per database transaction')
@click.option('--include-deleted-accounts', is_flag=True,
              help='Allow posting to accounts marked deleted (for backfill)')
@click.option('--verbose', '-v', is_flag=True, help='Show per-row warnings and details (charge-posting modes only)')
@pass_context
def accounting(ctx: Context, comp, disk, archive, reconcile_quotas, resource,
               machine, start, end, date_str, today_flag, last,
               dry_run, update_accounting_system, deactivate_orphaned,
               force, verify_paths, verify_host,
               skip_errors, create_queues, chunk_size,
               include_deleted_accounts, verbose):
    """Post charge summaries into SAM, or reconcile allocations against quota truth.

    \b
    Two modes:
      1. Post charge summaries  (--comp / --disk / --archive)
         Required: --machine and a date selection.
         Date Selection:
           --date YYYY-MM-DD   Single specific date
           --today             Today's date
           --last N[d]         Last N days including today
           --start / --end     Date range (defaults: 2024-01-01 to yesterday)

      2. Reconcile storage quotas  (--reconcile-quotas PATH)
         Required: --resource <name>
         Report-only by default — full tables, no writes.  Each write
         flag is independent; combine them as needed:
           --update-accounting-system   Apply mismatched amount updates
           --deactivate-orphaned        Deactivate orphaned allocations
           --force                      Override the live-path safety gate
                                        (requires --deactivate-orphaned)
    """
    if verbose:
        ctx.verbose = True

    # --- Mode validation ----------------------------------------------------
    charge_mode = bool(comp or disk or archive)
    reconcile_mode = reconcile_quotas is not None

    if reconcile_mode and charge_mode:
        ctx.console.print(
            "Error: --reconcile-quotas is mutually exclusive with --comp/--disk/--archive",
            style="bold red",
        )
        sys.exit(1)

    if (verify_paths or verify_host) and not reconcile_mode:
        ctx.console.print(
            "Error: --verify-paths/--verify-host require --reconcile-quotas",
            style="bold red",
        )
        sys.exit(1)
    if verify_host and not verify_paths:
        ctx.console.print(
            "Error: --verify-host requires --verify-paths",
            style="bold red",
        )
        sys.exit(1)

    # Reconcile-mode write flags. The two write flags are independent so
    # admins can act on either bucket alone (e.g. deactivate orphans
    # without touching mismatch updates, or vice versa). --force is
    # specifically the live-path safety override, so it only makes
    # sense alongside --deactivate-orphaned. Charge-posting modes
    # (--comp/--disk/--archive) ignore these flags.
    if (update_accounting_system or deactivate_orphaned) and not reconcile_mode:
        ctx.console.print(
            "Error: --update-accounting-system / --deactivate-orphaned "
            "require --reconcile-quotas",
            style="bold red",
        )
        sys.exit(1)
    if force and reconcile_mode and not deactivate_orphaned:
        ctx.console.print(
            "Error: --force requires --deactivate-orphaned (overrides the "
            "live-path safety gate when deactivating orphans)",
            style="bold red",
        )
        sys.exit(1)

    if reconcile_mode:
        if not resource:
            ctx.console.print(
                "Error: --reconcile-quotas requires --resource",
                style="bold red",
            )
            sys.exit(1)
        command = AccountingAdminCommand(ctx)
        exit_code = command.execute(
            reconcile_quotas=reconcile_quotas,
            resource=resource,
            update_accounting_system=update_accounting_system,
            deactivate_orphaned=deactivate_orphaned,
            force=force,
            verify_paths=verify_paths,
            verify_host=verify_host,
        )
        sys.exit(exit_code)

    # Charge-posting mode: machine + dates required
    if not machine:
        ctx.console.print(
            "Error: --machine is required with --comp/--disk/--archive",
            style="bold red",
        )
        sys.exit(1)

    _validate_accounting_dates(date_str, start, end, today_flag, last)
    start_date, end_date = _resolve_accounting_dates(date_str, start, end, today_flag, last)
    command = AccountingAdminCommand(ctx)
    exit_code = command.execute(
        comp=comp,
        disk=disk,
        archive=archive,
        machine=machine,
        start_date=start_date,
        end_date=end_date,
        dry_run=dry_run,
        skip_errors=skip_errors,
        create_queues=create_queues,
        chunk_size=chunk_size,
        include_deleted_accounts=include_deleted_accounts,
    )
    sys.exit(exit_code)


if __name__ == '__main__':
    cli()
