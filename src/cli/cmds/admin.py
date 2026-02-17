#!/usr/bin/env python3
"""
SAM Admin CLI - Administrative commands.

Administrative commands for SAM database management and validation.
"""

import sys
import click
from sqlalchemy.orm import Session

from cli.core.context import Context
from cli.user.commands import UserAdminCommand
from cli.project.commands import ProjectAdminCommand, ProjectExpirationCommand


pass_context = click.make_pass_decorator(Context, ensure=True)
CONTEXT_SETTINGS = dict(help_option_names=['-h', '--help'])


@click.group(context_settings=CONTEXT_SETTINGS)
@click.option('--verbose', '-v', is_flag=True, help='Show detailed information')
@pass_context
def cli(ctx: Context, verbose: bool):
    """Administrative commands for SAM database"""
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
@click.option('--notify', is_flag=True, help='Send email notifications (requires --upcoming-expirations)')
@click.option('--dry-run', is_flag=True, help='Preview emails without sending (requires --notify)')
@click.option('--email-list', type=str, help='Comma-separated list of additional email recipients')
@click.option('--list-users', is_flag=True, help='List all users')
@click.option('--facilities', '-F', multiple=True, default=['UNIV', 'WNA'], help='Facilities to include (default: UNIV, WNA). Use * for all facilities.')
@click.option('--verbose', '-v', is_flag=True, help='Show detailed information')
@pass_context
def project(ctx: Context, projcode, validate, reconcile, upcoming_expirations, notify, dry_run, email_list, list_users, facilities, verbose):
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

    # Require projcode for other operations
    if not projcode:
        ctx.console.print("Error: projcode argument is required unless using --upcoming-expirations", style="bold red")
        click.echo(click.get_current_context().get_help())
        sys.exit(1)

    command = ProjectAdminCommand(ctx)
    exit_code = command.execute(projcode, validate=validate, reconcile=reconcile,
                                list_users=list_users)
    sys.exit(exit_code)


if __name__ == '__main__':
    cli()
