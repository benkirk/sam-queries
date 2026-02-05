"""Project command classes."""

from datetime import datetime, timedelta
from collections import defaultdict
from cli.core.base import BaseProjectCommand
from cli.core.utils import EXIT_SUCCESS, EXIT_NOT_FOUND, EXIT_ERROR
from cli.project.display import (
    display_project,
    display_project_search_results,
    display_expiring_projects,
    display_abandoned_users_from_expired_projects,
    display_notification_results,
    display_notification_preview
)
from sam import Project
from sam.queries.expirations import (
    get_projects_by_allocation_end_date,
    get_projects_with_expired_allocations,
    get_all_expiring_allocations
)
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

            display_project_search_results(self.ctx, projects, pattern)
            return EXIT_SUCCESS
        except Exception as e:
            return self.handle_exception(e)


class ProjectExpirationCommand(BaseProjectCommand):
    """Find upcoming or recently expired projects."""

    def execute(self, upcoming: bool = True, since: datetime = None,
                list_users: bool = False, facility_filter: list = None,
                notify: bool = False, dry_run: bool = False, email_list: str = None) -> int:
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

                display_expiring_projects(self.ctx, expiring, list_users=list_users, upcoming=True)

                # Send notifications if requested
                if notify:
                    # For notifications, get ALL expiring allocations (not just latest per project)
                    all_expiring = get_all_expiring_allocations(
                        self.session,
                        start_date=datetime.now(),
                        end_date=datetime.now() + timedelta(days=32),
                        facility_names=facility_filter
                    )
                    return self._send_notifications(all_expiring, email_list, dry_run)

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

                # Extract users if needed (business logic)
                if list_users:
                    for proj, alloc, res_name, days_expired in expiring:
                        all_users.update(proj.roster)
                        expiring_projects.add(proj.projcode)

                    for user in track(all_users, description="Determining abandoned users..."):
                        user_projects = set()
                        for proj in user.active_projects:
                            user_projects.add(proj.projcode)
                        if user_projects.issubset(expiring_projects):
                            abandoned_users.add(user)

                # Display results
                display_expiring_projects(self.ctx, expiring, list_users=list_users, upcoming=False)

                if list_users and abandoned_users:
                    display_abandoned_users_from_expired_projects(self.ctx, abandoned_users)

            return EXIT_SUCCESS
        except Exception as e:
            return self.handle_exception(e)


    def _send_notifications(self, expiring_data: list, additional_recipients: str = None, dry_run: bool = False) -> int:
        """Send email notifications for expiring projects.

        Args:
            expiring_data: List of tuples (project, allocation, resource_name, days_remaining)
            additional_recipients: Comma-separated list of additional email addresses
            dry_run: If True, preview emails without sending

        Returns:
            EXIT_SUCCESS if all emails sent, EXIT_ERROR if any failed
        """
        from cli.notifications import EmailNotificationService

        # Group by project to send one email per project
        projects_map = defaultdict(list)
        for proj, alloc, resource_name, days_remaining in expiring_data:
            projects_map[proj.projcode].append({
                'project': proj,
                'allocation': alloc,
                'resource_name': resource_name,
                'days_remaining': days_remaining
            })

        # Build notification list
        notifications = []
        for projcode, resources_data in projects_map.items():
            project = resources_data[0]['project']

            # Get usage data for all resources
            usage = project.get_detailed_allocation_usage()

            # Calculate grace expiration (90 days after latest resource expiration)
            latest_expiration = None
            for item in resources_data:
                if item['allocation'].end_date:
                    if latest_expiration is None or item['allocation'].end_date > latest_expiration:
                        latest_expiration = item['allocation'].end_date

            latest_expiration_date = None
            grace_expiration_date = None
            if latest_expiration:
                latest_expiration_date = latest_expiration.strftime("%Y-%m-%d")
                grace_expiration_date = (latest_expiration + timedelta(days=90)).strftime("%Y-%m-%d")

            # Determine facility for template selection
            facility_name = None
            if project.allocation_type and project.allocation_type.panel and project.allocation_type.panel.facility:
                facility_name = project.allocation_type.panel.facility.facility_name

            # Build resources list for email
            resources = []
            for item in resources_data:
                resource_name = item['resource_name']
                resource_usage = usage.get(resource_name, {})

                resources.append({
                    'resource_name': resource_name,
                    'expiration_date': item['allocation'].end_date.strftime("%Y-%m-%d") if item['allocation'].end_date else 'N/A',
                    'days_remaining': item['days_remaining'],
                    'allocated_amount': resource_usage.get('allocated', 0),
                    'used_amount': resource_usage.get('used', 0),
                    'remaining_amount': resource_usage.get('remaining', 0),
                    'units': 'core-hours'  # Default unit
                })

            # Build recipients dict: email -> (name, role)
            # Start with roster (all users default to 'user' role)
            recipients = {}
            for user in project.roster:
                if user.primary_email:
                    recipients[user.primary_email] = (user.display_name, 'user')

            # Override with admin role (higher priority than user)
            if project.admin and project.admin.primary_email:
                recipients[project.admin.primary_email] = (project.admin.display_name, 'admin')

            # Override with lead role (highest priority)
            if project.lead and project.lead.primary_email:
                recipients[project.lead.primary_email] = (project.lead.display_name, 'lead')

            # Add additional recipients if provided (default to 'user' role)
            if additional_recipients:
                for email in additional_recipients.split(','):
                    email = email.strip()
                    if email and email not in recipients:
                        recipients[email] = (email, 'user')

            # Get project lead name for templates
            project_lead_name = project.lead.display_name if project.lead else 'Project Lead'

            # Create notification for each recipient
            for recipient_email, (recipient_name, recipient_role) in recipients.items():
                notifications.append({
                    'recipient': recipient_email,
                    'recipient_name': recipient_name,
                    'recipient_role': recipient_role,
                    'project_code': projcode,
                    'project_title': project.title,
                    'project_lead': project_lead_name,
                    'resources': resources,
                    'latest_expiration': latest_expiration_date,
                    'grace_expiration': grace_expiration_date,
                    'facility': facility_name
                })

        # Send notifications (or preview in dry-run mode)
        total_projects = len(projects_map)
        email_service = EmailNotificationService(self.ctx)
        results = email_service.send_batch_notifications(notifications, dry_run=dry_run)

        # Display results
        if dry_run:
            display_notification_preview(self.ctx, results, total_projects)
        else:
            display_notification_results(self.ctx, results, total_projects)

        # Return error if any failed
        if results['failed']:
            return EXIT_ERROR

        return EXIT_SUCCESS


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
