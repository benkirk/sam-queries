#!/usr/bin/env python3
"""
SAM Search CLI Utility

A command-line tool for searching users and projects in the SAM database.

Usage:
    sam_search user <username> [--list-projects] [--verbose]
    sam_search project <projcode> [--list-users] [--verbose]
    sam_search user --search <pattern> [--limit N]
    sam_search project --search <pattern> [--limit N]

Examples:
    # Find a specific user
    sam_search user jsmith

    # Find a user and list their projects
    sam_search user jsmith --list-projects

    # Search for users matching a pattern
    sam_search user --search "john%"

    # Find a specific project
    sam_search project UCSD0001

    # Find a project and list its users
    sam_search project UCSD0001 --list-users

    # Find upcoming project expirations:
    sam_search project --upcoming-expirations

    # Find recently expired projects, including 'abandoned' users:
    sam_search project --recent-expirations --list-users
"""

import argparse
import sys
from typing import Optional, List
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from tqdm import tqdm

from sam import *


class SamSearchCLI:
    """Main CLI application class."""

    def __init__(self):
        """Initialize the CLI."""
        from sam.session import create_sam_engine
        self.engine, _ = create_sam_engine()
        self.session = Session(self.engine)
        self.parser = self._create_parser()
        self.args = None

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - cleanup session."""
        self.session.close()

    def _create_parser(self) -> argparse.ArgumentParser:
        """
        Create and configure the argument parser.

        Returns:
            Configured ArgumentParser
        """
        parser = argparse.ArgumentParser(
            description='Search and query the SAM database',
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="""
Examples:
  # Find a specific user
  %(prog)s user jsmith

  # Find a user and list their projects
  %(prog)s user jsmith --list-projects

  # Search for users matching a pattern
  %(prog)s user --search "john%%"

  # Find a specific project
  %(prog)s project SCSG0001

  # Find a project and list its users
  %(prog)s project SCSG0001 --list-users

  # Search with verbose output
  %(prog)s user jsmith --verbose --list-projects
        """
        )

        # Global options
        parser.add_argument(
            '--inactive-projects',
            action='store_true',
            help='Consider inactive projects'
        )
        parser.add_argument(
            '--inactive-users',
            action='store_true',
            help='Consider inactive users'
        )

        # Create subparsers
        subparsers = parser.add_subparsers(dest='command', help='Command to execute')
        subparsers.required = True

        # ========================================================================
        # User command
        # ========================================================================
        user_parser = subparsers.add_parser(
            'user',
            help='Search for users',
            description='Search for users by username or pattern'
        )

        # Mutually exclusive group for user search type
        user_search = user_parser.add_mutually_exclusive_group(required=True)
        user_search.add_argument(
            'username',
            nargs='?',
            help='Exact username to search for'
        )
        user_search.add_argument(
            '--search',
            metavar='PATTERN',
            help='Search pattern (use %% for wildcard, _ for single char)'
        )

        user_search.add_argument(
            '--abandoned',
            action='store_true',
            help='Find \'active\' users with no active projects'
        )

        user_search.add_argument(
            '--has-active-project',
            action='store_true',
            help='Find \'active\' users with at least one active projects'
        )

        # User options
        user_parser.add_argument(
            '--list-projects',
            action='store_true',
            help='List all projects for the user'
        )
        user_parser.add_argument(
            '--verbose', '-v',
            action='store_true',
            help='Show detailed information'
        )
        user_parser.add_argument(
            '--limit',
            type=int,
            default=50,
            help='Maximum number of results for pattern search (default: 50)'
        )

        #
        parser.list_users = False

        # ========================================================================
        # Project command
        # ========================================================================
        project_parser = subparsers.add_parser(
            'project',
            help='Search for projects',
            description='Search for projects by project code or pattern'
        )

        # Mutually exclusive group for project search type
        project_search = project_parser.add_mutually_exclusive_group(required=False)
        project_search.add_argument(
            'projcode',
            nargs='?',
            help='Exact project code to search for'
        )
        project_search.add_argument(
            '--search',
            metavar='PATTERN',
            help='Search pattern (use %% for wildcard, _ for single char)'
        )

        # Mutually exclusive group for expiration; past or future
        expiration_action = project_parser.add_mutually_exclusive_group(required=False)
        expiration_action.add_argument(
            '--upcoming-expirations', '-f',
            action='store_true',
            help='Search for upcoming project expirations.'
        )
        expiration_action.add_argument(
            '--recent-expirations', '-p',
            action='store_true',
            help='Search for recently expired projects.'
        )

        # Project options
        project_parser.add_argument(
            '--list-users',
            action='store_true',
            help='List all users on the project'
        )
        project_parser.add_argument(
            '--verbose', '-v',
            action='store_true',
            help='Show detailed information'
        )
        project_parser.add_argument(
            '--limit',
            type=int,
            default=50,
            help='Maximum number of results for pattern search (default: 50)'
        )

        return parser

    def run(self, argv: Optional[List[str]] = None) -> int:
        """
        Run the CLI application.

        Args:
            argv: Command line arguments (defaults to sys.argv[1:])

        Returns:
            Exit code (0 for success, non-zero for errors)
        """
        try:
            self.args = self.parser.parse_args(argv)

            # Route to appropriate command
            if self.args.command == 'user':
                if self.args.username:
                    return self._search_user_exact()
                elif self.args.abandoned:
                    return self._abandoned_users()
                elif self.args.has_active_project:
                    return self._users_with_active_project()
                else:
                    return self._search_user_pattern()

            elif self.args.command == 'project':
                if self.args.recent_expirations:
                    return self._recently_expired_projects()
                elif self.args.upcoming_expirations:
                    return self._upcoming_project_expirations()
                elif self.args.projcode:
                    return self._search_project_exact()
                else:
                    return self._search_project_pattern()


            else:
                print(f"Unknown command: {self.args.command}", file=sys.stderr)
                return 2

        except KeyboardInterrupt:
            print("\n\n⚠️  Interrupted by user")
            return 130
        except Exception as e:
            print(f"❌ Fatal error: {e}", file=sys.stderr)
            if self.args.verbose:
                import traceback
                traceback.print_exc()
            return 2

    # ========================================================================
    # User Search Commands
    # ========================================================================
    def _search_user_exact(self) -> int:
        """
        Search for a specific user by exact username.

        Returns:
            Exit code (0 for success, 1 for not found, 2 for error)
        """
        try:
            user = User.get_by_username(self.session, self.args.username)

            if not user:
                print(f"❌ User not found: {self.args.username}")
                return 1

            self._display_user(user)

            if self.args.list_projects:
                self._display_user_projects(user)

            return 0

        except Exception as e:
            print(f"❌ Error searching for user: {e}", file=sys.stderr)
            return 2

    def _search_user_pattern(self) -> int:
        """
        Search for users matching a pattern.

        Returns:
            Exit code (0 for success, 1 for no results, 2 for error)
        """
        try:
            # Clean pattern for search_users method
            clean_pattern = self.args.search.replace('%', '').replace('_', '')
            users = User.search_users(
                self.session,
                clean_pattern,
                active_only=not self.args.inactive_users,
                limit=self.args.limit
            )

            if not users:
                print(f"❌ No users found matching: {self.args.search}")
                return 1

            print(f"✅ Found {len(users)} user(s):\n")

            for i, user in enumerate(users, 1):
                print(f"{i}. {user.username} ({user.display_name})")
                if self.args.verbose:
                    print(f"   ID: {user.user_id}")
                    print(f"   Email: {user.primary_email or 'N/A'}")
                    print(f"   Active: {'✓' if user.is_accessible else '✗'}")
                    print()

            return 0

        except Exception as e:
            print(f"❌ Error searching for users: {e}", file=sys.stderr)
            return 2

    def _display_user(self, user: User):
        """
        Display user information.

        Args:
            user: User object to display
        """
        print("="*80)
        print("USER INFORMATION")
        print("="*80)
        print(f"Username: {user.username}")
        print(f"Name:     {user.display_name}")
        print(f"User ID:  {user.user_id}")
        print(f"UPID:     {user.upid or 'N/A'}")
        print(f"Unix UID: {user.unix_uid}")

        # Email addresses
        if user.email_addresses:
            print(f"Email(s):")
            for email in user.email_addresses:
                primary_marker = " (PRIMARY)" if email.is_primary else ""
                print(f"  - <{email.email_address}>{primary_marker}")

        # Status
        print(f"Status:     {'✅ Active' if user.active else '❌ Inactive'}")
        print(f"Locked:     {'🔒 Yes' if user.locked else '✓ No'}")
        print(f"Accessible: {'✓ Yes' if user.is_accessible else '✗ No'}")

        if self.args.verbose:
            # Academic status
            if user.academic_status:
                print(f"\nAcademic Status: {user.academic_status.description}")

            # Institutions
            if user.institutions:
                print(f"\nInstitution(s):")
                for ui in user.institutions:
                    if ui.is_currently_active:
                        inst = ui.institution
                        print(f"  - {inst.name} ({inst.acronym})")

            # Organizations
            if user.organizations:
                print(f"\nOrganization(s):")
                for uo in user.organizations:
                    if uo.is_currently_active:
                        org = uo.organization
                        print(f"  - {org.name} ({org.acronym})")

            # Project count
            num_projects = len(user.active_projects)
            print(f"\nActive Projects: {num_projects}")
        else:
            # Just show counts
            num_projects = len(user.active_projects)
            print(f"\nActive Projects: {num_projects}")
            print("\n (Use --list-projects to see project details, --verbose for more user information.)")

    def _display_user_projects(self, user: User):
        """
        Display projects for a user.

        Args:
            user: User object
        """
        show_inactive = self.args.inactive_projects
        projects = user.all_projects if show_inactive else user.active_projects
        label = "All" if show_inactive else "Active"

        if not projects:
            print("No projects found.")
            return

        print(f"\n{label} projects for {user.username}:\n")

        for i, project in enumerate(projects, 1):
            print(f"{i}. {project.projcode}")
            self._display_project(project)
            print()

    def _abandoned_users(self):
        active_users = User.get_active_users(self.session)
        print(f"Examining {len(active_users):,} 'active' users listed in SAM")
        abandoned_users = set()
        for user in tqdm(active_users, desc=" --> determining abandoned users..."):
            if len(user.active_projects) == 0:
                abandoned_users.add(user)
        if abandoned_users:
            print(f"Found {len(abandoned_users):,} abandoned_users")
            for user in sorted(abandoned_users, key=lambda u: u.username):
                print(f" {user.username:12} {user.display_name:30} <{user.primary_email}>")
        return

    def _users_with_active_project(self):
        active_users = User.get_active_users(self.session)
        users_with_projects = set()
        for user in tqdm(active_users, desc="Determining users with at least one active project..."):
            if len(user.active_projects) > 0:
                users_with_projects.add(user)
        if users_with_projects:
            print(f"Found {len(users_with_projects)} users with at least one active project.")
            for user in sorted(users_with_projects, key=lambda u: u.username):
                if self.args.verbose:
                    self._display_user(user)
                    if self.args.list_projects:
                        self._display_user_projects(user)
                else:
                    print(f" {user.username:12} {user.display_name:30} <{user.primary_email}>")

        return

    # ========================================================================
    # Project Search Commands
    # ========================================================================
    def _search_project_exact(self) -> int:
        """
        Search for a specific project by exact project code.

        Returns:
            Exit code (0 for success, 1 for not found, 2 for error)
        """
        try:
            project = Project.get_by_projcode(self.session, self.args.projcode)

            if not project:
                print(f"❌ Project not found: {self.args.projcode}")
                return 1

            self._display_project(project)

            return 0

        except Exception as e:
            print(f"❌ Error searching for project: {e}", file=sys.stderr)
            return 2

    def _search_project_pattern(self) -> int:
        """
        Search for projects matching a pattern.

        Returns:
            Exit code (0 for success, 1 for no results, 2 for error)
        """
        try:
            projects = Project.search_by_pattern(
                self.session,
                self.args.search,
                search_title=True,
                active_only=not self.args.inactive_projects,
                limit=self.args.limit
            )

            if not projects:
                print(f"❌ No projects found matching: {self.args.search}")
                return 1

            print(f"✅ Found {len(projects)} project(s):\n")

            for i, project in enumerate(projects, 1):
                print(f"{i}. {project.projcode}")
                print(f"   {project.title}")

                if self.args.verbose:
                    print(f"   ID: {project.project_id}")
                    print(f"   Lead: {project.lead.display_name if project.lead else 'N/A'}")
                    print(f"   Users: {project.get_user_count()}")

                print()

            return 0

        except Exception as e:
            print(f"❌ Error searching for projects: {e}", file=sys.stderr)
            return 2

    def _display_project(self, project: Project, extra_title_info: str = ""):
        """
        Display project information.

        Args:
            project: Project object to display
        """
        print("="*80)
        print(f"PROJECT INFORMATION - {project.projcode}{extra_title_info}")
        print("="*80)
        print(f"Title:  {project.title}")
        print(f"Code:   {project.projcode}")
        print(f"GID:    {project.unix_gid}")
        print(f"Status: {'Active ✅' if project.active else 'Inactive ❌'}")

        if project.lead:
            print(f"Lead:   {project.lead.display_name} ({project.lead.username}) <{project.lead.primary_email or 'N/A'}>")
        if project.admin and project.admin != project.lead:
            print(f"Admin:  {project.admin.display_name} ({project.admin.username}) <{project.admin.primary_email or 'N/A'}>")

        print(f"Type:   {project.allocation_type.allocation_type}")
        print(f"Panel:  {project.allocation_type.panel.panel_name}")

        if project.area_of_interest:
            print(f"Area:   {project.area_of_interest.area_of_interest}")

        if project.contracts:
            print(f"Contracts:")
            for pc in project.contracts:
                print(f"  - {pc.contract.contract_source.contract_source} {str(pc.contract.contract_number):<20} {pc.contract.title}")

        if project.charging_exempt:
            print(f"** Charging Exempt **")

        # Allocations & Usage by resource
        usage = project.get_detailed_allocation_usage()
        allocations = project.get_all_allocations_by_resource()
        if usage:
            print(f"Allocations:")
            for resource_name, alloc in allocations.items():
                resource_usage = usage[resource_name]
                print(f"  - {resource_name} ({resource_usage['resource_type']}) [{alloc.start_date.date()} - {alloc.end_date.date() if alloc.end_date else 'N/A'}]:")
                print(f"     Allocation: {alloc.amount:,.0f} ({resource_usage['remaining']:,.0f} Remaining)")
                print(f"     Used:       {resource_usage['used']:,.0f} / ({resource_usage['percent_used']:,.0f}%)")

        # Active project directories
        if project.active_directories:
            print("Directories:")
            for d in project.active_directories:
                print(f"  - {d}")

        # User count - handle circularity.
        # When calling this method through the project path, we may want to list users.
        # however, when calling through the user path, we won't have a list_users attribute.
        # (if the user list is desired, we can simply rerun querying the project)
        if hasattr(self.args, 'list_users'):
            self._display_project_users(project)
        else:
            print(f"Active Users: {project.get_user_count()}")


        if self.args.verbose:
            # Show abstract if available
            if project.abstract:
                print(f"Abstract:")
                # Truncate long abstracts
                abstract = project.abstract
                if len(abstract) > 500:
                    abstract = abstract[:500] + "..."
                print(f"  {abstract}")

            # Show organizations
            if project.organizations:
                print(f"Organizations:")
                for po in project.organizations:
                    if po.is_currently_active:
                        org = po.organization
                        print(f"  - {org.name} ({org.acronym})")

            # Show tree information
            if project.parent:
                print(f"Parent Project: {project.parent.projcode}")

            children = project.get_children()
            if children:
                print(f"Child Projects: {len(children)}")
                for child in children[:5]:  # Show first 5
                    print(f"  - {child.projcode}")
                if len(children) > 5:
                    print(f"  ... and {len(children) - 5} more")
        else:
            print("\n (Use --list-users to see user details, --verbose for more project information.)")

    def _display_project_users(self, project: Project):
        """
        Display users for a project.

        Args:
            project: Project object
        """
        users = project.users

        if not users:
            print("No active users found.")
            return

        count = len(users)
        plural = "s" if count > 1 else ""

        print(f"{count} Active user{plural} for {project.projcode}:\n")

        for i, user in enumerate(sorted(users, key=lambda u: u.username), 1):
            print(f"{i}. {user.username} - {user.display_name}")

            if self.args.verbose:
                print(f"   Email: {user.primary_email or 'N/A'}")
                print(f"   UID:   {user.unix_uid}")
        return

    # ========================================================================
    # Expiration Search Commands
    # ========================================================================
    def _upcoming_project_expirations(self):
        from sam.queries import get_projects_by_allocation_end_date
        from datetime import timedelta
        expiring = get_projects_by_allocation_end_date(self.session,
                                                       start_date=datetime.now(),
                                                       end_date=datetime.now() + timedelta(days=32),
                                                       facility_names=['UNIV', 'WNA'])

        print(f"Found {len(expiring)} allocations expiring")
        for proj, alloc, res_name, days in expiring:
            if self.args.verbose:
                self._display_project(proj, f" - {days} days remaining" )
            else:
                print(f"  {proj.projcode} - {days} days remaining")
        if not self.args.verbose:
            print("\n (Use --verbose for more project information.)")
        return

    def _recently_expired_projects(self):
        from sam.queries import get_projects_with_expired_allocations
        from datetime import timedelta

        all_users = set()
        abandoned_users = set()
        expiring_projects = set()
        expiring = get_projects_with_expired_allocations(self.session,
                                                         max_days_expired=90,
                                                         min_days_expired=365,
                                                         facility_names=['UNIV', 'WNA'])

        print(f"Found {len(expiring)} recently expired projects:")
        for proj, alloc, res_name, days in expiring:
            all_users.update(proj.roster)
            expiring_projects.add(proj.projcode)
            if self.args.verbose:
                self._display_project(proj, f" - {days} since expiration")
            else:
                print(f"  {proj.projcode} - {days} days since expiration")
        if self.args.list_users:
            for user in tqdm(all_users, desc="Determining abandoned users..."):
                user_projects = set()
                for proj in user.active_projects:
                    user_projects.add(proj.projcode)
                if user_projects.issubset(expiring_projects): abandoned_users.add(user)

            print(f"Found {len(abandoned_users)} expiring users:")
            for user in sorted(abandoned_users, key=lambda u: u.username):
                print(f" {user.username:12} {user.display_name:30} <{user.primary_email}>")

        return


# ========================================================================
# Main Program Follows
# ========================================================================
def main():
    """Main entry point for the CLI."""
    try:
        with SamSearchCLI() as cli:
            exit_code = cli.run()
            sys.exit(exit_code)
    except Exception as e:
        print(f"❌ Fatal error: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == '__main__':
    main()
