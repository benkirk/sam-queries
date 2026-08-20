#!/usr/bin/env python3
"""
SAM Admin CLI - Administrative commands.

Administrative commands for SAM database management and validation.
"""

import os
import sys
import click
from datetime import date as _date, datetime

from config import SAMConfig
from cli.core.context import Context
from cli.core.utils import EXIT_SUCCESS, EXIT_ERROR
from cli.user.commands import UserAdminCommand
from cli.project.commands import (
    ProjectAdminCommand,
    ProjectExpirationCommand,
    ProjectTreeAuditCommand,
)
from cli.accounting.commands import AccountingAdminCommand
from cli.accounting.dates import _validate_accounting_dates, _resolve_accounting_dates
from cli.contracts.commands import ContractsAuditCommand
from cli.tasks.commands import TasksCommand
from cli.xras.commands import XrasCommand

# Default base URL for the running webapp (matches the systems-integration
# shell client, scripts/apis/systems_integration_apis.sh).
_DEFAULT_API_BASE = 'https://samuel.k8s.ucar.edu'


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

    # NO database connection here. `Context.require_sam()` opens one on first
    # use, so a subcommand that never queries SAM MySQL never needs it to be
    # up. See SCHEDULED_TASKS.md § 3.2 — `tasks --run-due` prunes Postgres and
    # must not die on a SAM outage.


@cli.command()
@click.argument('username')
@click.option('--validate', is_flag=True, help='Validate user data integrity')
@click.option('--list-projects', is_flag=True, help='List all projects')
@click.option('--verbose', '-v', is_flag=True, help='Show detailed information')
@click.option('--provisioning/--no-provisioning', default=None,
              help='Cross-check host provisioning (auto-on on a provisioned host)')
@pass_context
def user(ctx: Context, username, validate, list_projects, verbose, provisioning):
    """Administrative user commands."""
    if verbose:
        ctx.verbose = True

    if provisioning is not None:
        ctx.check_provisioning = provisioning

    command = UserAdminCommand(ctx)
    exit_code = command.execute(username, validate=validate, list_projects=list_projects)
    sys.exit(exit_code)


@cli.command()
@click.argument('projcode', required=False)
@click.option('--validate', is_flag=True, help='Validate project data')
@click.option('--reconcile', is_flag=True, help='Reconcile allocations')
@click.option('--audit-trees', 'audit_trees', is_flag=True,
              help='Audit project allocation trees DB-wide (no projcode needed)')
@click.option('--resource', 'audit_resource', type=str, default=None,
              help='[audit-trees] Limit the audit to one resource (e.g. Derecho)')
@click.option('--upcoming-expirations', is_flag=True, help='Search for upcoming project expirations')
@click.option('--recent-expirations', is_flag=True, help='Show recently expired projects')
@click.option('--notify', is_flag=True, help='Send email notifications (requires --upcoming-expirations)')
@click.option('--dry-run', is_flag=True, help='Preview emails without sending (requires --notify)')
@click.option('--email-list', type=str, help='Comma-separated list of additional email recipients')
@click.option('--deactivate', is_flag=True, help='Deactivate expired projects (requires --recent-expirations)')
@click.option('--force', is_flag=True,
              help='With --deactivate: skip the confirmation prompt. '
                   'With --notify: re-send to recipients already notified '
                   'about this expiration (overrides suppression).')
@click.option('--since', type=click.DateTime(formats=['%Y-%m-%d']), default=None,
              help='Look back to this date for --recent-expirations (e.g., 2024-01-01)')
@click.option('--list-users', is_flag=True, help='List all users')
@click.option('--facilities', '-F', multiple=True, default=['UNIV', 'WNA'], help='Facilities to include (default: UNIV, WNA). Use * for all facilities.')
@click.option('--verbose', '-v', is_flag=True, help='Show detailed information')
@click.option('--provisioning/--no-provisioning', default=None,
              help='Cross-check host provisioning (auto-on on a provisioned host)')
@pass_context
def project(ctx: Context, projcode, validate, reconcile, audit_trees, audit_resource,
            upcoming_expirations, recent_expirations,
            notify, dry_run, email_list, deactivate, force, since, list_users, facilities, verbose, provisioning):
    """Administrative project commands."""
    if verbose:
        ctx.verbose = True

    if provisioning is not None:
        ctx.check_provisioning = provisioning

    # Validate that --resource requires --audit-trees
    if audit_resource and not audit_trees:
        ctx.console.print("Error: --resource requires --audit-trees", style="bold red")
        sys.exit(1)

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

    # --force means "skip the protection" on both surfaces that have one:
    # the deactivation confirmation prompt, and notification suppression.
    if force and not (deactivate or notify):
        ctx.console.print("Error: --force requires --deactivate or --notify",
                          style="bold red")
        sys.exit(1)

    # DB-wide tree audit — no projcode (the invariant spans trees, not projects)
    if audit_trees:
        command = ProjectTreeAuditCommand(ctx)
        sys.exit(command.execute(resource_name=audit_resource))

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
            email_list=email_list,
            force=force,
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
            "Error: projcode argument is required unless using --upcoming-expirations, "
            "--recent-expirations, or --audit-trees",
            style="bold red"
        )
        click.echo(click.get_current_context().get_help())
        sys.exit(1)

    command = ProjectAdminCommand(ctx)
    exit_code = command.execute(projcode, validate=validate, reconcile=reconcile,
                                list_users=list_users)
    sys.exit(exit_code)


@cli.command()
@click.option('--validate', is_flag=True,
              help='Audit contract data hygiene (read-only)')
@click.option('--all', 'audit_all', is_flag=True,
              help='[validate] Audit all contracts, not just open ones')
@click.option('--check-sources', 'check_sources', is_flag=True,
              help='[validate] Also compare each contract against its funding '
                   'source (slow — hits the network)')
@click.option('--limit', type=int, default=None,
              help='[check-sources] Stop after N contracts')
@click.option('--sleep', 'sleep_between', type=float, default=0.3,
              show_default=True,
              help='[check-sources] Seconds to wait between provider requests')
@click.option('--verbose', '-v', is_flag=True, help='Show detailed information')
@pass_context
def contracts(ctx: Context, validate, audit_all, check_sources, limit,
              sleep_between, verbose):
    """Administrative contract commands.

    \b
    Read-only data-hygiene audit over the contract table.  Reports what is
    wrong; corrections are made through the webapp's edit form.

    \b
    Defaults to open contracts (inside their date window).  --all widens to
    every contract, which surfaces long-expired rows nobody will fix but
    makes the otherwise-vacuous checks meaningful.

    \b
    --check-sources compares title, dates, number, program and — for NSF —
    PI and Monitor against what the funding agency reports.  It is the only
    check that finds *stale* values rather than missing ones.  Set
    CACHE_REDIS_URL to share the webapp's warm award cache; without it the
    per-process fallback holds AWARD_LOOKUP_CACHE_SIZE (256) entries, fewer
    than the open contract count, so a full run partially evicts itself.
    """
    if verbose:
        ctx.verbose = True

    if audit_all and not validate:
        ctx.console.print("Error: --all requires --validate", style="bold red")
        sys.exit(1)

    if check_sources and not validate:
        ctx.console.print("Error: --check-sources requires --validate",
                          style="bold red")
        sys.exit(1)

    if limit is not None and not check_sources:
        ctx.console.print("Error: --limit requires --check-sources",
                          style="bold red")
        sys.exit(1)

    if not validate:
        ctx.console.print("Error: no action specified (use --validate)",
                          style="bold red")
        click.echo(click.get_current_context().get_help())
        sys.exit(EXIT_ERROR)

    command = ContractsAuditCommand(ctx)
    sys.exit(command.execute(active_only=not audit_all,
                             check_sources=check_sources,
                             limit=limit,
                             sleep_between=sleep_between))


@cli.command()
# --- Mode selectors (mutually exclusive — pick exactly one) ----------------
@click.option('--comp', is_flag=True,
              help='Mode: post computational charge summaries')
@click.option('--disk', is_flag=True,
              help='Mode: post disk charge summaries')
@click.option('--archive', is_flag=True,
              help='Mode: post archive charge summaries (not yet implemented)')
@click.option('--reconcile-quotas', 'reconcile_quotas', type=click.Path(exists=True, dir_okay=False),
              default=None, metavar='PATH',
              help='Mode: reconcile SAM allocations against a storage quota file (requires --resource)')
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
# --- Comp/Disk shared epoch override ---------------------------------------
@click.option('--epoch', 'epoch_str', type=str, default=None, metavar='YYYY-MM-DD',
              help='[comp/disk] Override the hard-coded charging epoch. '
                   'Default: COMP_CHARGING_EPOCH for --comp, '
                   'DISK_CHARGING_TIB_EPOCH for --disk.')
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
               epoch_str,
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

    # --- --epoch parse + scope check --------------------------------------
    # --epoch only applies to --comp / --disk. Reject early elsewhere so
    # operators don't get a silent no-op under --reconcile-quotas/--archive.
    epoch_date = None
    if epoch_str:
        if not (comp or disk):
            ctx.console.print(
                "Error: --epoch only applies to --comp or --disk",
                style="bold red",
            )
            sys.exit(1)
        try:
            epoch_date = datetime.strptime(epoch_str, '%Y-%m-%d').date()
        except ValueError:
            ctx.console.print(
                "Error: --epoch must be in YYYY-MM-DD format",
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
            epoch=epoch_date,
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
        epoch=epoch_date,
    )
    sys.exit(exit_code)


@cli.command()
@click.option('--refresh', is_flag=True,
              help='Invalidate the running webapp\'s caches')
@click.option('--category',
              type=click.Choice(['flask', 'chart', 'usage', 'scans', 'jobs',
                                 'awards', 'xras_api']),
              default=None,
              help='Scope the refresh to one cache category (default: all)')
@click.option('--base', 'base_url', type=str, default=None,
              help=f'Webapp base URL (default: $SAM_API_BASE or {_DEFAULT_API_BASE})')
@pass_context
def cache(ctx: Context, refresh: bool, category, base_url):
    """Manage the running webapp's caches.

    \b
    Thin HTTP client for POST /api/v1/admin/cache/refresh — the caches live
    inside the webapp worker process (and shared Redis), not the DB, so this
    hits the live endpoint rather than clearing anything locally.

    Credentials (HTTP Basic Auth, same as the systems-integration client):
      SAM_API_USER   API-key username (required)
      SAM_API_PASS   API-key password (required)
      SAM_API_BASE   Base URL (optional; --base overrides)

    \b
    Examples:
      sam-admin cache --refresh
      sam-admin cache --refresh --category chart
    """
    if not refresh:
        ctx.console.print(
            "Error: specify an action (currently only --refresh)",
            style="bold red",
        )
        sys.exit(EXIT_ERROR)

    base = (base_url or os.getenv('SAM_API_BASE') or _DEFAULT_API_BASE).rstrip('/')
    user = os.getenv('SAM_API_USER')
    password = os.getenv('SAM_API_PASS')
    if not user or not password:
        ctx.console.print(
            "Error: SAM_API_USER and SAM_API_PASS must be set (API-key credentials).",
            style="bold red",
        )
        sys.exit(EXIT_ERROR)

    import requests

    url = f"{base}/api/v1/admin/cache/refresh"
    params = {'category': category} if category else {}
    try:
        resp = requests.post(url, auth=(user, password), params=params, timeout=60)
    except requests.RequestException as e:
        ctx.console.print(f"Error: could not reach {url}: {e}", style="bold red")
        sys.exit(EXIT_ERROR)

    if resp.status_code != 200:
        body = resp.text.strip()
        ctx.console.print(
            f"Error: cache refresh failed (HTTP {resp.status_code}): {body}",
            style="bold red",
        )
        sys.exit(EXIT_ERROR)

    payload = resp.json()
    cleared = payload.get('cleared', {})

    if ctx.output_format == 'json':
        import json
        click.echo(json.dumps(payload, indent=2, sort_keys=False))
        sys.exit(EXIT_SUCCESS)

    from rich.table import Table
    table = Table(title=f"Cache refreshed via {base}", title_style="bold")
    table.add_column("Category", style="cyan")
    table.add_column("Entries cleared", justify="right", style="green")
    for cat, count in cleared.items():
        table.add_row(cat, str(count))
    ctx.console.print(table)
    sys.exit(EXIT_SUCCESS)


@cli.command()
@click.option('--show', 'action_id', type=int, default=None,
              help='[detail] Show one action by id')
@click.option('--payload', 'show_payload', is_flag=True,
              help='[detail] Include the raw payload (requires --show; contains PII)')
@click.option('--recheck', type=int, default=None,
              help='[check] Would this action succeed if posted now? Applies nothing')
@click.option('--summary', is_flag=True,
              help='[rollup] Counts by status and action type')
@click.option('--validate-mapping', is_flag=True,
              help='[check] Report SAM resources XRAS cannot name (pre-cutover gate)')
@click.option('--accounts', is_flag=True,
              help='[worklist] Accounts to create or reactivate before a handoff')
@click.option('--enrich', is_flag=True,
              help='[worklist] Add XRAS person detail (requires --accounts and the API)')
@click.option('--person', type=str, default=None,
              help='[detail] Look one username up in the XRAS directory')
@click.option('--status', multiple=True,
              type=click.Choice(['received', 'processed', 'manual',
                                 'failed', 'rechecked']),
              help='[list/rollup] Filter by status (repeatable)')
@click.option('--type', 'action_type', multiple=True,
              help='[list/rollup] Filter by action type, e.g. New (repeatable)')
@click.option('--request', 'request_number', type=str, default=None,
              help='[list] Filter by XRAS request number / projcode')
@click.option('--last', type=str, default=None,
              help='[list/rollup] Time window, e.g. 7d, 24h, 2w (default: all time)')
@click.option('--limit', type=int, default=50, show_default=True,
              help='[list] Maximum rows to return')
@click.option('--verbose', '-v', is_flag=True, help='Show detailed information')
@pass_context
def xras(ctx: Context, action_id, show_payload, recheck, summary, validate_mapping,
         accounts, enrich, person,
         status, action_type, request_number, last, limit, verbose):
    """Inspect and re-check the XRAS action log.

    \b
    Reads xras_action_log — the audit trail written by POST /api/xras/v1/actions.
    Every post lands there before dispatch, so a request that fails is still
    recorded and still re-checkable.

    \b
    Modes:
      (default)    list recent actions
      --show ID    one action in full, with its re-check lineage
      --summary    counts by status, and by status x action type
      --recheck ID would this action succeed now? (applies nothing)
      --accounts   who must be created or reactivated before a handoff works
      --person U   one username in the XRAS directory
      --validate-mapping  which resources XRAS and SAM can name each other's

    \b
    --recheck answers "would this succeed if XRAS posted it now?" It re-parses the
    stored payload and runs the handler's validation half against today's code and
    data, then records the verdict as a new linked row. It APPLIES NOTHING, in any
    configuration — the write path is never reached, rather than being suppressed by
    a flag. Use it after fixing data a post failed on, to learn whether asking XRAS
    to resend will land.

    \b
    --accounts is the account-creation worklist: unreconciled ARC placeholder
    identities are 55% of production handoff failures, and account creation is
    manual. Rows are usernames XRAS names that SAM cannot use — either absent
    (create) or inactive (reactivate). An EMPTY worklist exits 0; nobody being
    blocked is a successful report, not a miss. --enrich adds names, emails and
    the isReconciled closure signal, one XRAS round trip per username.

    \b
    --validate-mapping is two-sided when the XRAS API is configured: it also
    reports keys XRAS sends that SAM has no mapping row for, which is the
    failure that breaks an award. Unconfigured, it degrades to the local-only
    report and says so.

    \b
    Examples:
      sam-admin xras --last 7d
      sam-admin xras --status failed --type Extension
      sam-admin xras --show 42 --payload
      sam-admin xras --summary --last 30d
      sam-admin xras --validate-mapping
      sam-admin xras --accounts
      sam-admin xras --accounts --enrich --last 30d
      sam-admin xras --person somebody-user-00042
      sam-admin xras --recheck 42
      sam-admin --format json xras --summary | jq .by_status
    """
    if verbose:
        ctx.verbose = True

    if show_payload and action_id is None:
        ctx.console.print('Error: --payload requires --show', style='bold red')
        sys.exit(EXIT_ERROR)

    if enrich and not accounts:
        ctx.console.print('Error: --enrich requires --accounts', style='bold red')
        sys.exit(EXIT_ERROR)

    # Writes have no JSON contract: the envelope is for consumers reading state,
    # and a machine-readable success receipt for a side-effecting command invites
    # scripting a write loop that no one reviewed. Same rule as src/cli/README.md.
    if recheck is not None and ctx.output_format == 'json':
        import json
        click.echo(json.dumps({'error': 'json_unsupported_for_writes'},
                              indent=2, sort_keys=False))
        sys.exit(EXIT_ERROR)

    sys.exit(XrasCommand(ctx).execute(
        action_id=action_id,
        show_payload=show_payload,
        recheck=recheck,
        summary=summary,
        validate_mapping=validate_mapping,
        accounts=accounts,
        enrich=enrich,
        person=person,
        status=status,
        action_type=action_type,
        request_number=request_number,
        last=last,
        limit=limit,
    ))


@cli.command()
@click.option('--list', 'list_tasks', is_flag=True,
              help='List registered tasks and their latest run (default)')
@click.option('--run-due', 'run_due', is_flag=True,
              help='Dispatch every task whose slot is open (the CronJob entry point)')
@click.option('--run', metavar='NAME',
              help='Run one task now, ignoring dueness')
@click.option('--history', is_flag=True, help='Show recent task runs')
@click.option('--task', metavar='NAME', help='[history] Limit to one task')
@click.option('--limit', type=int, default=20,
              help='[history] Maximum rows (default: 20)')
@click.option('--dry-run', is_flag=True,
              help='Report what would run; writes NO ledger rows and executes nothing')
@click.option('--force', is_flag=True,
              help='With --run: claim a manual occurrence key. A forced run does '
                   'NOT satisfy the scheduled slot — tonight\'s run still happens.')
@click.option('--occurrence', metavar='ISO8601',
              help='With --run --force: replay this slot instead of now, e.g. '
                   '2026-11-23T09:00 (naive UTC). A task computes everything '
                   'from its occurrence, so this asks "what would that run '
                   'have done?" without waiting for it.')
@click.option('--verbose', '-v', is_flag=True, help='Show detailed information')
@pass_context
def tasks(ctx: Context, list_tasks, run_due, run, history, task, limit,
          dry_run, force, occurrence, verbose):
    """Scheduled task dispatcher.

    The ledger lives in system_status, so these commands do not need SAM MySQL.
    """
    if verbose:
        ctx.verbose = True

    modes = [bool(list_tasks), bool(run_due), bool(run), bool(history)]
    if sum(modes) > 1:
        ctx.console.print(
            'Error: --list, --run-due, --run and --history are mutually exclusive',
            style='bold red')
        sys.exit(EXIT_ERROR)

    if dry_run and not (run_due or run):
        ctx.console.print('Error: --dry-run requires --run-due or --run',
                          style='bold red')
        sys.exit(EXIT_ERROR)

    if force and not run:
        ctx.console.print('Error: --force requires --run', style='bold red')
        sys.exit(EXIT_ERROR)

    # --occurrence is honored only on the forced path, where the ledger key is
    # already `M`-prefixed and so cannot collide with — or satisfy — a real
    # scheduled slot. Accepting it without --force would let a replay claim a
    # scheduled occurrence and suppress the run that slot was for.
    if occurrence and not (run and force):
        ctx.console.print('Error: --occurrence requires --run and --force',
                          style='bold red')
        sys.exit(EXIT_ERROR)

    if (task or limit != 20) and not history:
        ctx.console.print('Error: --task and --limit require --history',
                          style='bold red')
        sys.exit(EXIT_ERROR)

    # NOTE: no `json_unsupported_for_writes` guard here, unlike `xras --recheck`
    # above, and that is deliberate — see src/cli/README.md § Exit Codes. The
    # guard exists to stop someone accidentally writing while scripting a
    # *report*; for --run-due the side effect IS the command, and JSON on stdout
    # is exactly what a log-scraped CronJob should emit.
    sys.exit(TasksCommand(ctx).execute(
        list_tasks=list_tasks,
        run_due=run_due,
        run=run,
        history=history,
        task=task,
        limit=limit,
        dry_run=dry_run,
        force=force,
        occurrence=occurrence,
    ))


if __name__ == '__main__':
    cli()
