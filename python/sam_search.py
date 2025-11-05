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
"""

import argparse
import sys

from sam import *
from sqlalchemy.orm import Session


class SamSearchCLI:
    """Main CLI application class."""

    def __init__(self, db_url: str):
        """
        Initialize the CLI.
        """
        from sam.session import create_sam_engine

        self.engine, _ = create_sam_engine()
        self.session = Session(self.engine)

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - cleanup session."""
        self.session.close()

    # ========================================================================
    # User Search Commands
    # ========================================================================

    def search_user_exact(
        self, username: str, list_projects: bool = False, verbose: bool = False
    ) -> int:
        """
        Search for a specific user by exact username.

        Args:
            username: Exact username to search for
            list_projects: If True, also list user's projects
            verbose: If True, show detailed information

        Returns:
            Exit code (0 for success, 1 for not found, 2 for error)
        """
        try:
            user = User.get_by_username(self.session, username)

            if not user:
                print(f"❌ User not found: {username}")
                return 1

            self._display_user(user, verbose=verbose)

            if list_projects:
                print("\n" + "=" * 80)
                print("PROJECTS")
                print("=" * 80)
                self._display_user_projects(user, verbose=verbose)

            return 0

        except Exception as e:
            print(f"❌ Error searching for user: {e}", file=sys.stderr)
            return 2

    def search_user_pattern(
        self, pattern: str, limit: int = 50, verbose: bool = False
    ) -> int:
        """
        Search for users matching a pattern.

        Args:
            pattern: Search pattern (SQL LIKE syntax with % and _)
            limit: Maximum number of results
            verbose: If True, show detailed information

        Returns:
            Exit code (0 for success, 1 for no results, 2 for error)
        """
        try:
            users = User.search_users(
                self.session,
                pattern.replace("%", "").replace("_", ""),  # Clean for search_users
                active_only=False,
                limit=limit,
            )

            if not users:
                print(f"❌ No users found matching: {pattern}")
                return 1

            print(f"✅ Found {len(users)} user(s):\n")

            for i, user in enumerate(users, 1):
                print(f"{i}. {user.username} ({user.full_name})")
                if verbose:
                    print(f"   ID: {user.user_id}")
                    print(f"   Email: {user.primary_email or 'N/A'}")
                    print(f"   Active: {'✓' if user.is_accessible else '✗'}")
                    print()

            return 0

        except Exception as e:
            print(f"❌ Error searching for users: {e}", file=sys.stderr)
            return 2

    def _display_user(self, user: User, verbose: bool = False):
        """
        Display user information.

        Args:
            user: User object to display
            verbose: If True, show detailed information
        """
        print("=" * 80)
        print("USER INFORMATION")
        print("=" * 80)
        print(f"Username:     {user.username}")
        print(f"Full Name:    {user.full_name}")
        print(f"User ID:      {user.user_id}")
        print(f"UPID:         {user.upid or 'N/A'}")
        print(f"Unix UID:     {user.unix_uid}")

        # Email addresses
        if user.email_addresses:
            print("\nEmail(s):")
            for email in user.email_addresses:
                primary_marker = " (PRIMARY)" if email.is_primary else ""
                active_marker = "" if email.active else " [INACTIVE]"
                print(f"  • {email.email_address}{primary_marker}{active_marker}")

        # Status
        print(f"\nStatus:       {'✅ Active' if user.active else '❌ Inactive'}")
        print(f"Locked:       {'🔒 Yes' if user.locked else '✓ No'}")
        print(f"Accessible:   {'✓ Yes' if user.is_accessible else '✗ No'}")

        if verbose:
            # Academic status
            if user.academic_status:
                print(f"\nAcademic Status: {user.academic_status.description}")

            # Institutions
            if user.institutions:
                print("\nInstitution(s):")
                for ui in user.institutions:
                    if ui.is_currently_active:
                        inst = ui.institution
                        print(f"  • {inst.name} ({inst.acronym})")

            # Organizations
            if user.organizations:
                print("\nOrganization(s):")
                for uo in user.organizations:
                    if uo.is_currently_active:
                        org = uo.organization
                        print(f"  • {org.name} ({org.acronym})")

            # Project count
            num_projects = len(user.active_projects)
            print(f"\nActive Projects: {num_projects}")
        else:
            # Just show counts
            num_projects = len(user.active_projects)
            print(f"\nActive Projects: {num_projects}")
            print("\n💡 Use --list-projects to see project details")
            print("💡 Use --verbose for more user information")

    def _display_user_projects(self, user: User, verbose: bool = False):
        """
        Display projects for a user.

        Args:
            user: User object
            verbose: If True, show detailed information
        """
        projects = user.active_projects

        if not projects:
            print("No active projects found.")
            return

        print(f"Active projects for {user.username}:\n")

        for i, project in enumerate(projects, 1):
            print(f"{i}. {project.projcode}")
            print(f"   Title: {project.title}")

            if verbose:
                # Get allocations by resource
                allocations = project.get_all_allocations_by_resource()

                if allocations:
                    print("   Allocations:")
                    for resource_name, alloc in allocations.items():
                        print(f"     • {resource_name}: {alloc.amount:,.2f}")
                        if alloc.end_date:
                            print(f"       Valid until: {alloc.end_date.date()}")

                # Show lead
                print(f"   Lead: {project.lead.full_name}")

                # Show user count
                num_users = project.get_user_count()
                print(f"   Users: {num_users}")

            print()

    # ========================================================================
    # Project Search Commands
    # ========================================================================

    def search_project_exact(
        self, projcode: str, list_users: bool = False, verbose: bool = False
    ) -> int:
        """
        Search for a specific project by exact project code.

        Args:
            projcode: Exact project code to search for
            list_users: If True, also list project's users
            verbose: If True, show detailed information

        Returns:
            Exit code (0 for success, 1 for not found, 2 for error)
        """
        try:
            # Query for project
            project = Project.get_by_projcode(self.session, projcode)

            if not project:
                print(f"❌ Project not found: {projcode}")
                return 1

            self._display_project(project, verbose=verbose)

            if list_users:
                print("\n" + "=" * 80)
                print("USERS")
                print("=" * 80)
                self._display_project_users(project, verbose=verbose)

            return 0

        except Exception as e:
            print(f"❌ Error searching for project: {e}", file=sys.stderr)
            return 2

    def search_project_pattern(
        self, pattern: str, limit: int = 50, verbose: bool = False
    ) -> int:
        """
        Search for projects matching a pattern.

        Args:
            pattern: Search pattern (SQL LIKE syntax with % and _)
            limit: Maximum number of results
            verbose: If True, show detailed information

        Returns:
            Exit code (0 for success, 1 for no results, 2 for error)
        """
        try:
            # Search in both projcode and title
            projects = Project.search_by_pattern(
                self.session, pattern, search_title=True, active_only=False, limit=limit
            )

            if not projects:
                print(f"❌ No projects found matching: {pattern}")
                return 1

            print(f"✅ Found {len(projects)} project(s):\n")

            for i, project in enumerate(projects, 1):
                print(f"{i}. {project.projcode}")
                print(f"   {project.title}")

                if verbose:
                    print(f"   ID: {project.project_id}")
                    print(
                        f"   Lead: {project.lead.full_name if project.lead else 'N/A'}"
                    )
                    print(f"   Users: {project.get_user_count()}")

                print()

            return 0

        except Exception as e:
            print(f"❌ Error searching for projects: {e}", file=sys.stderr)
            return 2

    def _display_project(self, project: Project, verbose: bool = False):
        """
        Display project information.

        Args:
            project: Project object to display
            verbose: If True, show detailed information
        """
        print("=" * 80)
        print("PROJECT INFORMATION")
        print("=" * 80)
        print(f"Project Code: {project.projcode}")
        print(f"Title:        {project.title}")
        print(f"Project ID:   {project.project_id}")

        # Leadership
        if project.lead:
            print(f"\nProject Lead: {project.lead.full_name} ({project.lead.username})")
            print(f"Lead Email:   {project.lead.primary_email or 'N/A'}")

        if project.admin:
            print(
                f"Project Admin: {project.admin.full_name} ({project.admin.username})"
            )

        # Status
        print(f"\nStatus:       {'✅ Active' if project.active else '❌ Inactive'}")

        # Area of interest
        if project.area_of_interest:
            print(f"Research Area: {project.area_of_interest.area_of_interest}")

        # Allocations by resource
        allocations = project.get_all_allocations_by_resource()
        if allocations:
            print("\nAllocations:")
            for resource_name, alloc in allocations.items():
                status = "✅" if alloc.is_active else "❌"
                print(f"  {status} {resource_name}:")
                print(f"     Amount: {alloc.amount:,.2f}")
                print(f"     Start:  {alloc.start_date.date()}")
                if alloc.end_date:
                    print(f"     End:    {alloc.end_date.date()}")

        # User count
        num_users = project.get_user_count()
        print(f"\nActive Users: {num_users}")

        if verbose:
            # Show abstract if available
            if project.abstract:
                print("\nAbstract:")
                # Truncate long abstracts
                abstract = project.abstract
                if len(abstract) > 500:
                    abstract = abstract[:500] + "..."
                print(f"  {abstract}")

            # Show organizations
            if project.organizations:
                print("\nOrganizations:")
                for po in project.organizations:
                    if po.is_currently_active:
                        org = po.organization
                        print(f"  • {org.name} ({org.acronym})")

            # Show tree information
            if project.parent:
                print(f"\nParent Project: {project.parent.projcode}")

            children = project.get_children()
            if children:
                print(f"Child Projects: {len(children)}")
                for child in children[:5]:  # Show first 5
                    print(f"  • {child.projcode}")
                if len(children) > 5:
                    print(f"  ... and {len(children) - 5} more")
        else:
            print("\n💡 Use --list-users to see user details")
            print("💡 Use --verbose for more project information")

    def _display_project_users(self, project: Project, verbose: bool = False):
        """
        Display users for a project.

        Args:
            project: Project object
            verbose: If True, show detailed information
        """
        users = project.users

        if not users:
            print("No active users found.")
            return

        print(f"Active users for {project.projcode}:\n")

        for i, user in enumerate(sorted(users, key=lambda u: u.username), 1):
            print(f"{i}. {user.username} - {user.full_name}")

            if verbose:
                print(f"   Email: {user.primary_email or 'N/A'}")
                print(f"   UID:   {user.unix_uid}")

            print()


def create_parser() -> argparse.ArgumentParser:
    """
    Create and configure the argument parser.

    Returns:
        Configured ArgumentParser
    """
    parser = argparse.ArgumentParser(
        description="Search and query the SAM database",
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
  %(prog)s project UCSD0001

  # Find a project and list its users
  %(prog)s project UCSD0001 --list-users

  # Search with verbose output
  %(prog)s user jsmith --verbose --list-projects
        """,
    )

    # Database connection
    parser.add_argument(
        "--db-url",
        default="mysql+pymysql://user:pass@localhost/sam",
        help="Database connection URL (default: from environment or config)",
    )

    # Create subparsers
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")
    subparsers.required = True

    # ========================================================================
    # User command
    # ========================================================================
    user_parser = subparsers.add_parser(
        "user",
        help="Search for users",
        description="Search for users by username or pattern",
    )

    # Mutually exclusive group for user search type
    user_search = user_parser.add_mutually_exclusive_group(required=True)
    user_search.add_argument("username", nargs="?", help="Exact username to search for")
    user_search.add_argument(
        "--search",
        metavar="PATTERN",
        help="Search pattern (use %% for wildcard, _ for single char)",
    )

    # User options
    user_parser.add_argument(
        "--list-projects", action="store_true", help="List all projects for the user"
    )
    user_parser.add_argument(
        "--verbose", "-v", action="store_true", help="Show detailed information"
    )
    user_parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum number of results for pattern search (default: 50)",
    )

    # ========================================================================
    # Project command
    # ========================================================================
    project_parser = subparsers.add_parser(
        "project",
        help="Search for projects",
        description="Search for projects by project code or pattern",
    )

    # Mutually exclusive group for project search type
    project_search = project_parser.add_mutually_exclusive_group(required=True)
    project_search.add_argument(
        "projcode", nargs="?", help="Exact project code to search for"
    )
    project_search.add_argument(
        "--search",
        metavar="PATTERN",
        help="Search pattern (use %% for wildcard, _ for single char)",
    )

    # Project options
    project_parser.add_argument(
        "--list-users", action="store_true", help="List all users on the project"
    )
    project_parser.add_argument(
        "--verbose", "-v", action="store_true", help="Show detailed information"
    )
    project_parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum number of results for pattern search (default: 50)",
    )

    return parser


def main():
    """Main entry point for the CLI."""
    parser = create_parser()
    args = parser.parse_args()

    # Create CLI instance with database connection
    try:
        with SamSearchCLI(args.db_url) as cli:
            # Route to appropriate command
            if args.command == "user":
                if args.username:
                    # Exact username search
                    exit_code = cli.search_user_exact(
                        args.username,
                        list_projects=args.list_projects,
                        verbose=args.verbose,
                    )
                else:
                    # Pattern search
                    exit_code = cli.search_user_pattern(
                        args.search, limit=args.limit, verbose=args.verbose
                    )

            elif args.command == "project":
                if args.projcode:
                    # Exact project code search
                    exit_code = cli.search_project_exact(
                        args.projcode, list_users=args.list_users, verbose=args.verbose
                    )
                else:
                    # Pattern search
                    exit_code = cli.search_project_pattern(
                        args.search, limit=args.limit, verbose=args.verbose
                    )

            else:
                print(f"Unknown command: {args.command}", file=sys.stderr)
                exit_code = 2

            sys.exit(exit_code)

    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"❌ Fatal error: {e}", file=sys.stderr)
        if "--verbose" in sys.argv or "-v" in sys.argv:
            import traceback

            traceback.print_exc()
        sys.exit(2)


if __name__ == "__main__":
    main()
