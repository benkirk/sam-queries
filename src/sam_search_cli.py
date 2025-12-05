#!/usr/bin/env python3
"""
SAM Search CLI Utility

A command-line tool for searching users and projects in the SAM database.
Reimplemented using Click.
"""

import sys
import click
from typing import Optional, List
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from tqdm import tqdm

from sam import User, Project
# Import specific queries used in the original script
from sam.queries import (
    get_projects_by_allocation_end_date,
    get_projects_with_expired_allocations
)


class Context:
    def __init__(self):
        self.session: Optional[Session] = None
        self.verbose: bool = False
        self.inactive_projects: bool = False
        self.inactive_users: bool = False


pass_context = click.make_pass_decorator(Context, ensure=True)


@click.group()
@click.option('--verbose', '-v', is_flag=True, help='Show detailed information')
@click.option('--inactive-projects', is_flag=True, help='Consider inactive projects')
@click.option('--inactive-users', is_flag=True, help='Consider inactive users')
@pass_context
def cli(ctx: Context, verbose: bool, inactive_projects: bool, inactive_users: bool):
    """Search and query the SAM database"""
    ctx.verbose = verbose
    ctx.inactive_projects = inactive_projects
    ctx.inactive_users = inactive_users
    
    # Initialize database connection
    try:
        from sam.session import create_sam_engine
        engine, _ = create_sam_engine()
        ctx.session = Session(engine)
    except Exception as e:
        click.secho(f"Error connecting to database: {e}", fg="red", err=True)
        sys.exit(1)


@cli.result_callback()
def process_result(result, **kwargs):
    """Cleanup session after command execution"""
    # This might not run if the command fails with an exception, 
    # but the OS will clean up the socket/connection anyway.
    pass


# ========================================================================
# Helper Functions (Display Logic)
# ========================================================================

def _display_user(ctx: Context, user: User, list_projects: bool = False):
    """Display user information."""
    click.echo("="*80)
    click.echo("USER INFORMATION")
    click.echo("="*80)
    click.echo(f"Username: {user.username}")
    click.echo(f"Name:     {user.display_name}")
    click.echo(f"User ID:  {user.user_id}")
    click.echo(f"UPID:     {user.upid or 'N/A'}")
    click.echo(f"Unix UID: {user.unix_uid}")

    # Email addresses
    if user.email_addresses:
        click.echo("Email(s):")
        for email in user.email_addresses:
            primary_marker = " (PRIMARY)" if email.is_primary else ""
            click.echo(f"  - <{email.email_address}>{primary_marker}")

    # Status
    click.echo(f"Status:     {'✅ Active' if user.active else '❌ Inactive'}")
    click.echo(f"Locked:     {'🔒 Yes' if user.locked else '✓ No'}")
    click.echo(f"Accessible: {'✓ Yes' if user.is_accessible else '✗ No'}")

    if ctx.verbose:
        # Academic status
        if user.academic_status:
            click.echo(f"\nAcademic Status: {user.academic_status.description}")

        # Institutions
        if user.institutions:
            click.echo("\nInstitution(s):")
            for ui in user.institutions:
                if ui.is_currently_active:
                    inst = ui.institution
                    click.echo(f"  - {inst.name} ({inst.acronym})")

        # Organizations
        if user.organizations:
            click.echo("\nOrganization(s):")
            for uo in user.organizations:
                if uo.is_currently_active:
                    org = uo.organization
                    click.echo(f"  - {org.name} ({org.acronym})")

        # Project count
        num_projects = len(user.active_projects)
        click.echo(f"\nActive Projects: {num_projects}")
    else:
        # Just show counts
        num_projects = len(user.active_projects)
        click.echo(f"\nActive Projects: {num_projects}")
        click.echo("\n (Use --list-projects to see project details, --verbose for more user information.)")

    if list_projects:
        _display_user_projects(ctx, user)


def _display_user_projects(ctx: Context, user: User):
    """Display projects for a user."""
    show_inactive = ctx.inactive_projects
    projects = user.all_projects if show_inactive else user.active_projects
    label = "All" if show_inactive else "Active"

    if not projects:
        click.echo("No projects found.")
        return

    click.echo(f"\n{label} projects for {user.username}:\n")

    for i, project in enumerate(projects, 1):
        click.echo(f"{i}. {project.projcode}")
        _display_project(ctx, project)
        click.echo()


def _display_project(ctx: Context, project: Project, extra_title_info: str = "", list_users: bool = False):
    """Display project information."""
    click.echo("="*80)
    click.echo(f"PROJECT INFORMATION - {project.projcode}{extra_title_info}")
    click.echo("="*80)
    click.echo(f"Title:  {project.title}")
    click.echo(f"Code:   {project.projcode}")
    click.echo(f"GID:    {project.unix_gid}")
    click.echo(f"Status: {'Active ✅' if project.active else 'Inactive ❌'}")

    if project.lead:
        click.echo(f"Lead:   {project.lead.display_name} ({project.lead.username}) <{project.lead.primary_email or 'N/A'}>")
    if project.admin and project.admin != project.lead:
        click.echo(f"Admin:  {project.admin.display_name} ({project.admin.username}) <{project.admin.primary_email or 'N/A'}>")

    click.echo(f"Type:   {project.allocation_type.allocation_type}")
    if project.allocation_type.panel:
        click.echo(f"Panel:  {project.allocation_type.panel.panel_name}")

    if project.area_of_interest:
        click.echo(f"Area:   {project.area_of_interest.area_of_interest}")

    if project.contracts:
        click.echo("Contracts:")
        for pc in project.contracts:
            click.echo(f"  - {pc.contract.contract_source.contract_source} {str(pc.contract.contract_number):<20} {pc.contract.title}")

    if project.charging_exempt:
        click.echo("** Charging Exempt **")

    # Allocations & Usage by resource
    try:
        usage = project.get_detailed_allocation_usage()
        allocations = project.get_all_allocations_by_resource()
        if usage:
            click.echo("Allocations:")
            for resource_name, alloc in allocations.items():
                if resource_name in usage:
                    resource_usage = usage[resource_name]
                    click.echo(f"  - {resource_name} ({resource_usage['resource_type']}) [{alloc.start_date.date()} - {alloc.end_date.date() if alloc.end_date else 'N/A'}]:")
                    click.echo(f"     Allocation: {alloc.amount:,.0f} ({resource_usage['remaining']:,.0f} Remaining)")
                    click.echo(f"     Used:       {resource_usage['used']:,.0f} / ({resource_usage['percent_used']:,.0f}%)")
    except Exception as e:
         click.secho(f"Warning: Could not fetch allocations: {e}", fg="yellow")

    # Active project directories
    if project.active_directories:
        click.echo("Directories:")
        for d in project.active_directories:
            click.echo(f"  - {d}")

    # User count logic matching original script
    # If list_users is explicitly passed, show them.
    if list_users:
        _display_project_users(ctx, project)
    else:
        click.echo(f"Active Users: {project.get_user_count()}")

    if ctx.verbose:
        # Show abstract if available
        if project.abstract:
            click.echo("Abstract:")
            # Truncate long abstracts
            abstract = project.abstract
            if len(abstract) > 500:
                abstract = abstract[:500] + "..."
            click.echo(f"  {abstract}")

        # Show organizations
        if project.organizations:
            click.echo("Organizations:")
            for po in project.organizations:
                if po.is_currently_active:
                    org = po.organization
                    click.echo(f"  - {org.name} ({org.acronym})")

        # Show tree information
        if project.parent:
            click.echo(f"Parent Project: {project.parent.projcode}")

        children = project.get_children()
        if children:
            click.echo(f"Child Projects: {len(children)}")
            for child in children[:5]:  # Show first 5
                click.echo(f"  - {child.projcode}")
            if len(children) > 5:
                click.echo(f"  ... and {len(children) - 5} more")
    else:
        click.echo("\n (Use --list-users to see user details, --verbose for more project information.)")


def _display_project_users(ctx: Context, project: Project):
    """Display users for a project."""
    users = project.users

    if not users:
        click.echo("No active users found.")
        return

    count = len(users)
    plural = "s" if count > 1 else ""

    click.echo(f"{count} Active user{plural} for {project.projcode}:\n")

    for i, user in enumerate(sorted(users, key=lambda u: u.username), 1):
        click.echo(f"{i}. {user.username} - {user.display_name}")

        if ctx.verbose:
            click.echo(f"   Email: {user.primary_email or 'N/A'}")
            click.echo(f"   UID:   {user.unix_uid}")


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
@pass_context
def user(ctx: Context, username, search, abandoned, has_active_project, list_projects, limit, verbose):
    """
    Search for users. 
    
    You must provide either a username, --search PATTERN, --abandoned, or --has-active-project.
    """
    # Enforce mutual exclusivity manually since Click doesn't support mutually exclusive groups natively/cleanly yet
    inputs = [bool(username), bool(search), abandoned, has_active_project]
    if sum(inputs) != 1:
        click.secho("Error: Please provide exactly one of: username, --search, --abandoned, or --has-active-project", fg="red")
        click.echo(ctx.get_help())
        sys.exit(1)

    if verbose:
        ctx.verbose = True

    if username:
        # Exact Search
        try:
            user = User.get_by_username(ctx.session, username)
            if not user:
                click.secho(f"❌ User not found: {username}", fg="red")
                sys.exit(1)
            
            _display_user(ctx, user, list_projects)
        except Exception as e:
            click.secho(f"❌ Error searching for user: {e}", fg="red", err=True)
            sys.exit(2)

    elif abandoned:
        # Abandoned Users
        active_users = User.get_active_users(ctx.session)
        click.echo(f"Examining {len(active_users):,} 'active' users listed in SAM")
        abandoned_users = set()
        for user in tqdm(active_users, desc=" --> determining abandoned users..."):
            if len(user.active_projects) == 0:
                abandoned_users.add(user)
        if abandoned_users:
            click.echo(f"Found {len(abandoned_users):,} abandoned_users")
            for user in sorted(abandoned_users, key=lambda u: u.username):
                click.echo(f" {user.username:12} {user.display_name:30} <{user.primary_email}>")

    elif has_active_project:
        # Users with active projects
        active_users = User.get_active_users(ctx.session)
        users_with_projects = set()
        for user in tqdm(active_users, desc="Determining users with at least one active project..."):
            if len(user.active_projects) > 0:
                users_with_projects.add(user)
        if users_with_projects:
            click.echo(f"Found {len(users_with_projects)} users with at least one active project.")
            for user in sorted(users_with_projects, key=lambda u: u.username):
                if ctx.verbose:
                    _display_user(ctx, user)
                    if list_projects:
                        _display_user_projects(ctx, user)
                else:
                    click.echo(f" {user.username:12} {user.display_name:30} <{user.primary_email}>")

    else:
        # Pattern Search
        try:
            clean_pattern = search.replace('%', '').replace('_', '')
            users = User.search_users(
                ctx.session,
                clean_pattern,
                active_only=not ctx.inactive_users,
                limit=limit
            )

            if not users:
                click.secho(f"❌ No users found matching: {search}", fg="red")
                sys.exit(1)

            click.echo(f"✅ Found {len(users)} user(s):\n")

            for i, user in enumerate(users, 1):
                click.echo(f"{i}. {user.username} ({user.display_name})")
                if ctx.verbose:
                    click.echo(f"   ID: {user.user_id}")
                    click.echo(f"   Email: {user.primary_email or 'N/A'}")
                    click.echo(f"   Active: {'✓' if user.is_accessible else '✗'}")
                    click.echo()
        except Exception as e:
            click.secho(f"❌ Error searching for users: {e}", fg="red", err=True)
            sys.exit(2)


# ========================================================================
# Project Commands
# ========================================================================

@cli.command()
@click.argument('projcode', required=False)
@click.option('--search', metavar='PATTERN', help='Search pattern (use % for wildcard, _ for single char)')
@click.option('--upcoming-expirations', '-f', is_flag=True, help='Search for upcoming project expirations.')
@click.option('--recent-expirations', '-p', is_flag=True, help='Search for recently expired projects.')
@click.option('--list-users', is_flag=True, help='List all users on the project')
@click.option('--limit', type=int, default=50, help='Maximum number of results for pattern search (default: 50)')
@click.option('--verbose', '-v', is_flag=True, help='Show detailed information')
@pass_context
def project(ctx: Context, projcode, search, upcoming_expirations, recent_expirations, list_users, limit, verbose):
    """
    Search for projects. 
    
    You must provide either a project code, --search PATTERN, --upcoming-expirations, or --recent-expirations.
    """
    inputs = [bool(projcode), bool(search), upcoming_expirations, recent_expirations]
    if sum(inputs) != 1:
        click.secho("Error: Please provide exactly one of: projcode, --search, --upcoming-expirations, or --recent-expirations", fg="red")
        click.echo(ctx.get_help())
        sys.exit(1)

    if verbose:
        ctx.verbose = True

    if upcoming_expirations:
        # Upcoming Expirations
        expiring = get_projects_by_allocation_end_date(ctx.session,
                                                       start_date=datetime.now(),
                                                       end_date=datetime.now() + timedelta(days=32),
                                                       facility_names=['UNIV', 'WNA'])

        click.echo(f"Found {len(expiring)} allocations expiring")
        for proj, alloc, res_name, days in expiring:
            if ctx.verbose:
                _display_project(ctx, proj, f" - {days} days remaining", list_users=list_users)
            else:
                click.echo(f"  {proj.projcode} - {days} days remaining")
        if not ctx.verbose:
            click.echo("\n (Use --verbose for more project information.)")

    elif recent_expirations:
        # Recent Expirations
        all_users = set()
        abandoned_users = set()
        expiring_projects = set()
        expiring = get_projects_with_expired_allocations(ctx.session,
                                                         max_days_expired=90,
                                                         min_days_expired=365,
                                                         facility_names=['UNIV', 'WNA'])

        click.echo(f"Found {len(expiring)} recently expired projects:")
        for proj, alloc, res_name, days in expiring:
            if list_users:
                all_users.update(proj.roster)
            expiring_projects.add(proj.projcode)
            
            if ctx.verbose:
                _display_project(ctx, proj, f" - {days} since expiration", list_users=list_users)
            else:
                click.echo(f"  {proj.projcode} - {days} days since expiration")
        
        if list_users:
            for user in tqdm(all_users, desc="Determining abandoned users..."):
                user_projects = set()
                for proj in user.active_projects:
                    user_projects.add(proj.projcode)
                if user_projects.issubset(expiring_projects):
                    abandoned_users.add(user)

            click.echo(f"Found {len(abandoned_users)} expiring users:")
            for user in sorted(abandoned_users, key=lambda u: u.username):
                click.echo(f" {user.username:12} {user.display_name:30} <{user.primary_email}>")

    elif projcode:
        # Exact Search
        try:
            project = Project.get_by_projcode(ctx.session, projcode)

            if not project:
                click.secho(f"❌ Project not found: {projcode}", fg="red")
                sys.exit(1)

            _display_project(ctx, project, list_users=list_users)

        except Exception as e:
            click.secho(f"❌ Error searching for project: {e}", fg="red", err=True)
            sys.exit(2)

    else:
        # Pattern Search
        try:
            projects = Project.search_by_pattern(
                ctx.session,
                search,
                search_title=True,
                active_only=not ctx.inactive_projects,
                limit=limit
            )

            if not projects:
                click.secho(f"❌ No projects found matching: {search}", fg="red")
                sys.exit(1)

            click.echo(f"✅ Found {len(projects)} project(s):\n")

            for i, project in enumerate(projects, 1):
                click.echo(f"{i}. {project.projcode}")
                click.echo(f"   {project.title}")

                if ctx.verbose:
                    click.echo(f"   ID: {project.project_id}")
                    lead_name = project.lead.display_name if project.lead else 'N/A'
                    click.echo(f"   Lead: {lead_name}")
                    click.echo(f"   Users: {project.get_user_count()}")

                click.echo()
        except Exception as e:
            click.secho(f"❌ Error searching for projects: {e}", fg="red", err=True)
            sys.exit(2)


if __name__ == '__main__':
    cli()
