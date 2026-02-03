"""Project command classes."""

from datetime import datetime, timedelta
from cli.core.base import BaseProjectCommand
from cli.core.utils import EXIT_SUCCESS, EXIT_NOT_FOUND, EXIT_ERROR
from cli.project.display import display_project
from sam import Project
from sam.queries.expirations import (
    get_projects_by_allocation_end_date,
    get_projects_with_expired_allocations
)
from rich.table import Table
from rich.progress import track


class ProjectSearchCommand(BaseProjectCommand):
    """Exact project search by projcode."""

    def execute(self, projcode: str, list_users: bool = False) -> int:
        try:
            project = self.get_project(projcode)

            if not project:
                self.console.print(f"❌ Project not found: {projcode}", style="bold red")
                return EXIT_NOT_FOUND

            display_project(self.ctx, project, list_users=list_users)
            return EXIT_SUCCESS

        except Exception as e:
            return self.handle_exception(e)


class ProjectPatternSearchCommand(BaseProjectCommand):
    """Pattern search for projects."""

    def execute(self, pattern: str, limit: int = 50) -> int:
        try:
            projects = Project.search_by_pattern(
                self.session,
                pattern,
                search_title=True,
                active_only=not self.ctx.inactive_projects,
                limit=limit
            )

            if not projects:
                self.console.print(f"❌ No projects found matching: {pattern}", style="bold red")
                return EXIT_NOT_FOUND

            self.console.print(f"✅ Found {len(projects)} project(s):\n", style="green bold")

            for i, project in enumerate(projects, 1):
                self.console.print(f"{i}. {project.projcode}", style="cyan bold")
                self.console.print(f"   {project.title}")

                if self.ctx.verbose:
                    lead_name = project.lead.display_name if project.lead else 'N/A'
                    self.console.print(f"   ID: {project.project_id}")
                    self.console.print(f"   Lead: {lead_name}")
                    self.console.print(f"   Users: {project.get_user_count()}")

                self.console.print("")
            return EXIT_SUCCESS
        except Exception as e:
            return self.handle_exception(e)


class ProjectExpirationCommand(BaseProjectCommand):
    """Find upcoming or recently expired projects."""

    def execute(self, upcoming: bool = True, since: datetime = None,
                list_users: bool = False, facility_filter: list = None) -> int:
        try:
            if self.ctx.verbose:
                self.console.print(f"[dim]Facilities: {'ALL' if facility_filter is None else ', '.join(facility_filter)}[/]")

            if upcoming:
                # Upcoming Expirations
                expiring = get_projects_by_allocation_end_date(
                    self.session,
                    start_date=datetime.now(),
                    end_date=datetime.now() + timedelta(days=32),
                    facility_names=facility_filter
                )

                self.console.print(f"Found {len(expiring)} allocations expiring", style="yellow")
                for proj, alloc, res_name, days in expiring:
                    if self.ctx.verbose:
                        display_project(self.ctx, proj, f" - {days} days remaining", list_users=list_users)
                    else:
                         self.console.print(f"  {proj.projcode} - {days} days remaining")
                if not self.ctx.verbose:
                    self.console.print("\n (Use --verbose for more project information.)", style="dim italic")

            else:
                # Recent Expirations
                all_users = set()
                abandoned_users = set()
                expiring_projects = set()

                # Calculate max_days_expired from --since date, default to 365 days
                # When --since is provided, automatically include inactive projects
                if since:
                    max_days = (datetime.now() - since).days
                    if max_days < 0:
                        self.console.print(f"Error: --since date cannot be in the future", style="bold red")
                        return EXIT_ERROR
                    include_inactive = True
                else:
                    max_days = 365
                    include_inactive = self.ctx.inactive_projects

                expiring = get_projects_with_expired_allocations(
                    self.session,
                    min_days_expired=0,
                    max_days_expired=max_days,
                    facility_names=facility_filter,
                    include_inactive_projects=include_inactive
                )

                self.console.print(f"Found {len(expiring)} recently expired projects:", style="yellow")
                for proj, alloc, res_name, days_expired in expiring:
                    if list_users:
                        all_users.update(proj.roster)
                    expiring_projects.add(proj.projcode)

                    if self.ctx.verbose:
                        display_project(self.ctx, proj, f" - {days_expired} days since expiration", list_users=list_users)
                    else:
                        self.console.print(f"  {proj.projcode} - {days_expired} days since expiration")

                if list_users:
                    for user in track(all_users, description="Determining abandoned users..."):
                        user_projects = set()
                        for proj in user.active_projects:
                            user_projects.add(proj.projcode)
                        if user_projects.issubset(expiring_projects):
                            abandoned_users.add(user)

                    self.console.print(f"Found {len(abandoned_users)} expiring users:", style="bold red")
                    table = Table(show_header=False, box=None)
                    table.add_column("User")
                    for user in sorted(abandoned_users, key=lambda u: u.username):
                        table.add_row(f"{user.username:12} {user.display_name:30} <{user.primary_email}>")
                    self.console.print(table)

            return EXIT_SUCCESS
        except Exception as e:
            return self.handle_exception(e)


class ProjectAdminCommand(ProjectSearchCommand):
    """Admin command for projects - extends search with validation."""

    def execute(self, projcode: str, validate: bool = False,
                reconcile: bool = False, **kwargs) -> int:
        # First run base search
        exit_code = super().execute(projcode, **kwargs)
        if exit_code != EXIT_SUCCESS:
            return exit_code

        # Add admin-specific logic
        if validate:
            exit_code = self._validate_project(projcode)
            if exit_code != EXIT_SUCCESS:
                return exit_code

        if reconcile:
            return self._reconcile_project(projcode)

        return EXIT_SUCCESS

    def _validate_project(self, projcode: str) -> int:
        """Admin-only: validate project data integrity."""
        project = self.get_project(projcode)
        self.console.print(f"[dim]Validating project {projcode}...[/dim]")

        # Placeholder validation logic
        issues = []
        if not project.lead:
            issues.append("Missing project lead")
        if not project.allocation_type:
            issues.append("Missing allocation type")

        if issues:
            self.console.print(f"⚠️  Validation issues:", style="yellow")
            for issue in issues:
                self.console.print(f"  - {issue}", style="yellow")
            return EXIT_ERROR

        self.console.print(f"✅ Project {projcode} validated", style="green")
        return EXIT_SUCCESS

    def _reconcile_project(self, projcode: str) -> int:
        """Admin-only: reconcile project allocations."""
        self.console.print(f"[dim]Reconciling allocations for {projcode}...[/dim]")

        # Placeholder reconciliation logic
        self.console.print(f"✅ Project {projcode} reconciled", style="green")
        return EXIT_SUCCESS
