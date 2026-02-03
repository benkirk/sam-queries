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
    display_notification_results
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

            # Get recipients: lead, admin, all roster members
            recipients = set()
            if project.lead and project.lead.primary_email:
                recipients.add((project.lead.primary_email, project.lead.display_name))
            if project.admin and project.admin.primary_email:
                recipients.add((project.admin.primary_email, project.admin.display_name))
            for user in project.roster:
                if user.primary_email:
                    recipients.add((user.primary_email, user.display_name))

            # Add additional recipients if provided
            if additional_recipients:
                for email in additional_recipients.split(','):
                    email = email.strip()
                    if email:
                        recipients.add((email, email))

            # Create notification for each recipient
            for recipient_email, recipient_name in recipients:
                notifications.append({
                    'recipient': recipient_email,
                    'project_code': projcode,
                    'project_title': project.title,
                    'resources': resources,
                    'user_name': recipient_name
                })

        # Send notifications or preview in dry-run mode
        total_projects = len(projects_map)

        if dry_run:
            # Dry-run mode: preview without sending
            self._preview_notifications(notifications, total_projects)
            return EXIT_SUCCESS
        else:
            # Actually send emails
            email_service = EmailNotificationService(self.ctx)
            results = email_service.send_batch_notifications(notifications)

            # Display results
            display_notification_results(self.ctx, results, total_projects)

            # Return error if any failed
            if results['failed']:
                return EXIT_ERROR

            return EXIT_SUCCESS

    def _preview_notifications(self, notifications: list, total_projects: int):
        """Preview notifications in dry-run mode without sending.

        Args:
            notifications: List of notification dicts
            total_projects: Total number of unique projects
        """
        from rich.panel import Panel
        from rich.table import Table

        self.console.print(f"\n[bold yellow]DRY-RUN MODE: Preview only, no emails will be sent[/]\n")

        # Summary
        grid = Table(show_header=False, box=None, padding=(0, 2))
        grid.add_column("Field", style="cyan bold")
        grid.add_column("Value")

        grid.add_row("Projects with Expiring Allocations", str(total_projects))
        grid.add_row("Emails That Would Be Sent", str(len(notifications)))

        # Count unique recipients
        unique_recipients = set(n['recipient'] for n in notifications)
        grid.add_row("Unique Recipients", str(len(unique_recipients)))

        panel = Panel(grid, title="Dry-Run Summary", expand=False, border_style="yellow")
        self.console.print(panel)

        # Group by project for display
        from collections import defaultdict
        by_project = defaultdict(list)
        for notification in notifications:
            by_project[notification['project_code']].append(notification)

        # Show details
        self.console.print("\n[bold]Email Preview:[/]\n")

        for projcode, project_notifications in sorted(by_project.items()):
            # Use first notification to get project details
            first = project_notifications[0]
            recipients = [n['recipient'] for n in project_notifications]

            self.console.print(f"[cyan bold]{projcode}[/] - {first['project_title']}")
            self.console.print(f"  Recipients ({len(recipients)}): {', '.join(sorted(recipients))}")

            # Show resources that would be included
            resources = first['resources']
            for resource in resources:
                urgency = "🔴 URGENT" if resource['days_remaining'] <= 7 else "🟠 WARNING" if resource['days_remaining'] <= 14 else "🔵 NOTICE"
                self.console.print(f"    {urgency} {resource['resource_name']}: {resource['days_remaining']} days remaining (expires {resource['expiration_date']})")

            self.console.print()

        # Option to show full email preview
        if self.ctx.verbose:
            self.console.print("\n[bold]Sample Email Content:[/]\n")
            if notifications:
                # Render first email as example
                from cli.notifications import EmailNotificationService
                email_service = EmailNotificationService(self.ctx)

                first_notification = notifications[0]
                text_template = email_service.jinja_env.get_template('expiration.txt')
                sample_text = text_template.render(
                    user_name=first_notification.get('user_name', 'User'),
                    project_code=first_notification['project_code'],
                    project_title=first_notification['project_title'],
                    resources=first_notification['resources']
                )

                self.console.print(Panel(sample_text, title=f"Sample Email to {first_notification['recipient']}", border_style="dim"))

        self.console.print("[dim]Use --verbose to see sample email content[/]" if not self.ctx.verbose else "")


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
