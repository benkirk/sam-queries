#!/usr/bin/env python3
"""
SAM Admin CLI - Administrative commands.

Administrative commands for SAM database management and validation.
"""

import sys
import click
from datetime import date as _date, datetime, timedelta
from sqlalchemy.orm import Session

from config import SAMConfig
from cli.core.context import Context
from cli.user.commands import UserAdminCommand
from cli.project.commands import ProjectAdminCommand, ProjectExpirationCommand
from cli.accounting.commands import AccountingAdminCommand


pass_context = click.make_pass_decorator(Context, ensure=True)
CONTEXT_SETTINGS = dict(help_option_names=['-h', '--help'])


# ---------------------------------------------------------------------------
# Accounting date helpers (mirrors jobhist-sync / hpc-usage-queries patterns)
# ---------------------------------------------------------------------------

def _parse_last_spec(spec: str) -> int:
    """Parse --last spec: '3d' or '3' → 3."""
    s = spec.strip().lower().rstrip('d')
    try:
        n = int(s)
    except ValueError:
        raise click.BadParameter(f"--last must be Nd or N (e.g. 3d), got: {spec!r}")
    if n < 1:
        raise click.BadParameter("--last N must be >= 1")
    return n


def _validate_accounting_dates(
    date_str: str | None,
    start: str | None,
    end: str | None,
    today_flag: bool,
    last: str | None,
) -> None:
    if today_flag and (date_str or start or end or last):
        raise click.BadParameter("--today cannot be combined with --date, --start, --end, or --last")
    if last and (date_str or start or end or today_flag):
        raise click.BadParameter("--last cannot be combined with --date, --start, --end, or --today")
    if date_str and (start or end):
        raise click.BadParameter("Cannot use --date with --start/--end")
    if not any([date_str, start, end, today_flag, last]):
        raise click.UsageError("Specify a date: --date, --today, --last N[d], or --start/--end")
    for val, name in [(date_str, '--date'), (start, '--start'), (end, '--end')]:
        if val:
            try:
                datetime.strptime(val, '%Y-%m-%d')
            except ValueError:
                raise click.BadParameter(f"{name} must be in YYYY-MM-DD format")


def _resolve_accounting_dates(
    date_str: str | None,
    start: str | None,
    end: str | None,
    today_flag: bool,
    last: str | None,
) -> tuple[_date, _date]:
    today = _date.today()
    if today_flag:
        return today, today
    if last:
        n = _parse_last_spec(last)
        return today - timedelta(days=n - 1), today
    if date_str:
        d = _date.fromisoformat(date_str)
        return d, d
    # --start / --end: match jobhist-sync defaults for missing bound
    yesterday = today - timedelta(days=1)
    s = _date.fromisoformat(start) if start else _date.fromisoformat('2024-01-01')
    e = _date.fromisoformat(end) if end else yesterday
    return s, e


@click.group(context_settings=CONTEXT_SETTINGS)
@click.option('--verbose', '-v', is_flag=True, help='Show detailed information')
@pass_context
def cli(ctx: Context, verbose: bool):
    """Administrative commands for SAM database"""
    try:
        SAMConfig.validate()
    except EnvironmentError as e:
        ctx.stderr_console.print(str(e), style="bold red")
        sys.exit(2)

    ctx.verbose = verbose

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
@click.option('--machine', '-m', type=click.Choice(['derecho', 'casper']), required=True,
              help='HPC machine to pull charges from')
@click.option('--start', type=str, default=None, help='Start date (YYYY-MM-DD, inclusive; default: 2024-01-01)')
@click.option('--end', type=str, default=None, help='End date (YYYY-MM-DD, inclusive; default: yesterday)')
@click.option('-d', '--date', 'date_str', type=str, default=None, help='Specific date (YYYY-MM-DD)')
@click.option('--today', 'today_flag', is_flag=True, help='Use today as the date')
@click.option('--last', type=str, default=None, metavar='N[d]',
              help='Last N days including today (e.g. --last 3d)')
@click.option('--dry-run', is_flag=True, help='Show what would be posted, without writing')
@click.option('--skip-errors', is_flag=True, help='Skip rows that fail entity resolution')
@click.option('--create-queues', is_flag=True, help='Auto-create unknown queues in SAM')
@click.option('--chunk-size', type=int, default=500, show_default=True,
              help='Rows per database transaction')
@click.option('--include-deleted-accounts', is_flag=True,
              help='Allow posting to accounts marked deleted (for backfill)')
@click.option('--verbose', '-v', is_flag=True, help='Show per-row warnings and details')
@pass_context
def accounting(ctx: Context, comp, disk, archive, machine, start, end, date_str, today_flag, last,
               dry_run, skip_errors, create_queues, chunk_size,
               include_deleted_accounts, verbose):
    """Post daily charge summaries from HPC job history into SAM.

    \b
    Date Selection (one required):
      --date YYYY-MM-DD   Single specific date
      --today             Today's date
      --last N[d]         Last N days including today (e.g. --last 3d)
      --start / --end     Date range (defaults: 2024-01-01 to yesterday)
    """
    _validate_accounting_dates(date_str, start, end, today_flag, last)
    start_date, end_date = _resolve_accounting_dates(date_str, start, end, today_flag, last)
    if verbose:
        ctx.verbose = True
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
