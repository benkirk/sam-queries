#!/usr/bin/env python3
"""
SAM Admin CLI - Administrative commands.

Administrative commands for SAM database management and validation.
"""

import sys
import click
from datetime import date as _date, datetime
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
# --- Mode selectors ---------------------------------------------------------
@click.option('--comp', is_flag=True, help='[mode] Post computational charge summaries')
@click.option('--disk', is_flag=True, help='[mode] Post disk charge summaries')
@click.option('--archive', is_flag=True, help='[mode] Post archive charge summaries (not yet implemented)')
@click.option('--reconcile-quotas', 'reconcile_quotas', type=click.Path(exists=True, dir_okay=False),
              default=None, metavar='PATH',
              help='[mode] Reconcile SAM allocations against a storage quota file (requires --resource)')
# --- Common ----------------------------------------------------------------
@click.option('--resource', type=str, default=None,
              help='[disk/reconcile] Resource name (e.g. Campaign_Store)')
@click.option('--dry-run', is_flag=True,
              help='[comp/disk] Preview without writing (--reconcile-quotas is report-only by default)')
@click.option('--skip-errors', is_flag=True,
              help='[comp/disk] Skip rows that fail entity resolution')
@click.option('--chunk-size', type=int, default=500, show_default=True,
              help='[comp/disk] Rows per database transaction')
@click.option('--include-deleted-accounts', is_flag=True,
              help='[comp/disk] Allow posting to accounts marked deleted (for backfill)')
@click.option('--verbose', '-v', is_flag=True,
              help='Show per-row warnings and details')
# --- HPC (--comp) ----------------------------------------------------------
@click.option('--machine', '-m', type=click.Choice(['derecho', 'casper']), default=None,
              help='[comp] HPC machine (required)')
@click.option('--create-queues', is_flag=True,
              help='[comp] Auto-create unknown queues in SAM')
@click.option('--start', type=str, default=None,
              help='[comp] Start date (YYYY-MM-DD, inclusive; default: 2024-01-01)')
@click.option('--end', type=str, default=None,
              help='[comp] End date (YYYY-MM-DD, inclusive; default: yesterday)')
@click.option('--today', 'today_flag', is_flag=True,
              help='[comp] Use today as the date')
@click.option('--last', type=str, default=None, metavar='N[d]',
              help='[comp] Last N days including today (e.g. --last 3d)')
# --date is shared between --comp and --disk (different semantics — see below).
@click.option('-d', '--date', 'date_str', type=str, default=None,
              help='[comp] Specific date to import.  '
                   '[disk] Optional safety check: file snapshot must equal this date.')
# --- Disk (--disk) ---------------------------------------------------------
@click.option('--user-usage', 'user_usage_path',
              type=click.Path(exists=True, dir_okay=False),
              default=None, metavar='PATH',
              help='[disk] Per-user-per-project disk usage file (required; e.g. acct.glade.YYYY-MM-DD)')
@click.option('--quotas', 'quotas_path',
              type=click.Path(exists=True, dir_okay=False),
              default=None, metavar='PATH',
              help='[disk] GPFS cs_usage.json (required with --reconcile-quota-gap)')
@click.option('--reporting-interval', 'reporting_interval', type=int, default=7, show_default=True,
              help='[disk] Snapshot interval in days (used in TiB-year math)')
@click.option('--unidentified-label', 'unidentified_label', type=str, default='<unidentified>',
              show_default=True,
              help='[disk] Audit label for synthetic gap rows '
                   '(written to act_username only; never added to users table)')
@click.option('--reconcile-quota-gap', 'reconcile_quota_gap', is_flag=True,
              help='[disk] Attribute (FILESET total − Σuser_rows) to project lead '
                   'with --unidentified-label (requires --quotas)')
@click.option('--gap-tolerance-bytes', 'gap_tolerance_bytes', type=int, default=1024 ** 3, show_default=True,
              help='[disk] Minimum absolute gap in bytes before emitting a synthetic row (default 1 GiB)')
@click.option('--gap-tolerance-frac', 'gap_tolerance_frac', type=float, default=0.01, show_default=True,
              help='[disk] Minimum gap as a fraction of FILESET usage (default 1%)')
# --- Reconcile (--reconcile-quotas) ----------------------------------------
@click.option('--update-accounting-system', 'update_accounting_system', is_flag=True,
              help='[reconcile] Apply mismatched amount updates (default: report-only)')
@click.option('--deactivate-orphaned', 'deactivate_orphaned', is_flag=True,
              help='[reconcile] Deactivate orphaned allocations '
                   '(independent of --update-accounting-system)')
@click.option('--force', is_flag=True,
              help='[reconcile] Override the live-path safety gate when deactivating orphans '
                   'whose ProjectDirectory paths still exist on disk (requires --deactivate-orphaned)')
@click.option('--verify-paths', 'verify_paths', is_flag=True,
              help='[reconcile] Check fileset/ProjectDirectory paths on disk')
@click.option('--verify-host', 'verify_host', type=str, default=None, metavar='HOST',
              help='[reconcile] SSH host to use for --verify-paths (default: auto-detect)')
@pass_context
def accounting(ctx: Context, comp, disk, archive, reconcile_quotas, resource,
               machine,
               user_usage_path, quotas_path, reporting_interval,
               unidentified_label, reconcile_quota_gap,
               gap_tolerance_bytes, gap_tolerance_frac,
               start, end, date_str, today_flag, last,
               dry_run, update_accounting_system, deactivate_orphaned,
               force, verify_paths, verify_host,
               skip_errors, create_queues, chunk_size,
               include_deleted_accounts, verbose):
    """Post charge summaries into SAM, or reconcile allocations against quota truth.

    \b
    Three modes:
      1. Post HPC charge summaries  (--comp / --archive)
         Required: --machine and a date selection.
         Date Selection:
           --date YYYY-MM-DD   Single specific date
           --today             Today's date
           --last N[d]         Last N days including today
           --start / --end     Date range (defaults: 2024-01-01 to yesterday)

      2. Post disk charge summaries  (--disk)
         Required: --resource <name> and --user-usage <path>
         Optional: --quotas <path> --reconcile-quota-gap
         The snapshot date is read from the user-usage file (rows or
         filename). --date YYYY-MM-DD is accepted as an optional
         safety check: if supplied, the file's snapshot date MUST
         match it exactly. --today / --last / --start / --end and
         --create-queues are HPC-only and rejected here.

      3. Reconcile storage quotas  (--reconcile-quotas PATH)
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

    # --- Disk charge import (separate validation path) ---------------------
    if disk:
        if not resource:
            ctx.console.print(
                "Error: --disk requires --resource",
                style="bold red",
            )
            sys.exit(1)
        if machine:
            ctx.console.print(
                "Error: --machine is HPC-only; do not pass it with --disk",
                style="bold red",
            )
            sys.exit(1)
        if not user_usage_path:
            ctx.console.print(
                "Error: --disk requires --user-usage <path>",
                style="bold red",
            )
            sys.exit(1)
        if reconcile_quota_gap and not quotas_path:
            ctx.console.print(
                "Error: --reconcile-quota-gap requires --quotas <path>",
                style="bold red",
            )
            sys.exit(1)
        # Reject HPC-mode-only flags. The snapshot date comes from the
        # input file, not from a date range — there is no meaningful
        # interpretation of `--today` / `--last 7d` / `--start..--end`
        # for a single-snapshot disk import. `--date` is the only date
        # flag accepted (as a safety check: the file's snapshot date
        # must equal the supplied date, else we abort).
        rejected = []
        if today_flag: rejected.append('--today')
        if last:       rejected.append('--last')
        if start:      rejected.append('--start')
        if end:        rejected.append('--end')
        if rejected:
            ctx.console.print(
                f"Error: {', '.join(rejected)} not valid with --disk; "
                "the snapshot date is read from the user-usage file. "
                "Use --date YYYY-MM-DD if you want to assert the "
                "expected snapshot date as a safety check.",
                style="bold red",
            )
            sys.exit(1)
        if create_queues:
            ctx.console.print(
                "Error: --create-queues is HPC-only; do not pass it with --disk",
                style="bold red",
            )
            sys.exit(1)

        # --date is optional: when supplied, the snapshot in the file
        # must match this date exactly (otherwise abort). When omitted,
        # whatever date the file reports is used.
        expected_date = None
        if date_str:
            try:
                expected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            except ValueError:
                ctx.console.print(
                    "Error: --date must be in YYYY-MM-DD format",
                    style="bold red",
                )
                sys.exit(1)

        command = AccountingAdminCommand(ctx)
        exit_code = command.execute(
            disk=True,
            resource=resource,
            user_usage_path=user_usage_path,
            quotas_path=quotas_path,
            reporting_interval=reporting_interval,
            unidentified_label=unidentified_label,
            reconcile_quota_gap=reconcile_quota_gap,
            gap_tolerance_bytes=gap_tolerance_bytes,
            gap_tolerance_frac=gap_tolerance_frac,
            start_date=expected_date,
            end_date=expected_date,
            dry_run=dry_run,
            skip_errors=skip_errors,
            chunk_size=chunk_size,
            include_deleted_accounts=include_deleted_accounts,
        )
        sys.exit(exit_code)

    # Charge-posting mode (--comp / --archive): machine + dates required
    if not machine:
        ctx.console.print(
            "Error: --machine is required with --comp",
            style="bold red",
        )
        sys.exit(1)

    _validate_accounting_dates(date_str, start, end, today_flag, last)
    start_date, end_date = _resolve_accounting_dates(date_str, start, end, today_flag, last)
    command = AccountingAdminCommand(ctx)
    exit_code = command.execute(
        comp=comp,
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
