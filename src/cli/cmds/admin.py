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
from cli.project.commands import ProjectAdminCommand


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
        ctx.console.print(f"Error connecting to database: {e}", style="bold red", err=True)
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
@click.argument('projcode')
@click.option('--validate', is_flag=True, help='Validate project data')
@click.option('--reconcile', is_flag=True, help='Reconcile allocations')
@click.option('--list-users', is_flag=True, help='List all users')
@click.option('--verbose', '-v', is_flag=True, help='Show detailed information')
@pass_context
def project(ctx: Context, projcode, validate, reconcile, list_users, verbose):
    """Administrative project commands."""
    if verbose:
        ctx.verbose = True

    command = ProjectAdminCommand(ctx)
    exit_code = command.execute(projcode, validate=validate, reconcile=reconcile,
                                list_users=list_users)
    sys.exit(exit_code)


if __name__ == '__main__':
    cli()
