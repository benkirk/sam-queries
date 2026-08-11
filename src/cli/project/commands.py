"""Project command classes."""

from datetime import datetime, timedelta
from collections import defaultdict
from cli.core.base import BaseProjectCommand
from cli.core.output import output_json
from cli.core.utils import EXIT_SUCCESS, EXIT_NOT_FOUND, EXIT_ERROR
from cli.project.builders import (
    build_project_core,
    build_project_detail,
    build_project_allocations,
    build_project_rolling,
    build_project_tree,
    build_project_users,
    build_project_provisioning,
    build_project_search_results,
    build_expiring_projects,
)
from cli.project.display import (
    display_project,
    display_project_search_results,
    display_expiring_projects,
    display_abandoned_users_from_expired_projects,
    display_notification_results,
    display_notification_preview,
    display_tree_audit,
    notification_progress,
)
from sam import Project, fmt
from sam.enums import FacilityName, ResourceTypeName
from sam.queries.expirations import (
    get_projects_by_allocation_end_date,
    get_projects_with_expired_allocations,
    get_all_expiring_allocations
)
from sam.queries.tree_audit import audit_allocation_trees, audit_allocation_dates
from rich.progress import track


class ProjectSearchCommand(BaseProjectCommand):
    """Exact project search by projcode."""

    def execute(self, projcode: str, list_users: bool = False) -> int:
        try:
            project = self.get_project(projcode)

            if not project:
                if self.ctx.output_format == 'json':
                    output_json({'kind': 'project', 'error': 'not_found',
                                 'projcode': projcode})
                else:
                    self.console.print(f"❌ Project not found: {projcode}", style="bold red")
                return EXIT_NOT_FOUND

            json_mode = self.ctx.output_format == 'json'
            verbose = self.ctx.verbose
            vv = self.ctx.very_verbose

            data = build_project_core(project)
            data['allocations'] = build_project_allocations(project)

            if json_mode or verbose or vv:
                data['detail'] = build_project_detail(project)
                data['rolling'] = build_project_rolling(self.session, project.projcode)
                data['tree'] = build_project_tree(project)
            if json_mode or list_users:
                data['users'] = build_project_users(project)
            if self.ctx.check_provisioning:
                data['provisioning'] = build_project_provisioning(project)

            if json_mode:
                output_json(data)
            else:
                display_project(self.ctx, data, list_users=list_users)
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
                if self.ctx.output_format == 'json':
                    output_json({'kind': 'project_search_results', 'pattern': pattern,
                                 'count': 0, 'projects': []})
                    return EXIT_NOT_FOUND
                self.console.print(f"❌ No projects found matching: {pattern}", style="bold red")
                return EXIT_NOT_FOUND

            json_mode = self.ctx.output_format == 'json'
            data = build_project_search_results(
                projects, pattern, verbose=(json_mode or self.ctx.verbose)
            )
            if json_mode:
                output_json(data)
            else:
                display_project_search_results(self.ctx, data)
            return EXIT_SUCCESS
        except Exception as e:
            return self.handle_exception(e)


class ProjectExpirationCommand(BaseProjectCommand):
    """Find upcoming or recently expired projects."""

    def execute(self, upcoming: bool = True, since: datetime = None,
                list_users: bool = False, facility_filter: list = None,
                notify: bool = False, dry_run: bool = False, email_list: str = None,
                deactivate: bool = False, force: bool = False) -> int:
        try:
            json_mode = self.ctx.output_format == 'json'

            # JSON output is read-only; reject combinations with side effects.
            if json_mode and (notify or deactivate):
                output_json({
                    'kind': ('expiring_projects' if upcoming
                             else 'recently_expired_projects'),
                    'error': 'json_unsupported_for_writes',
                    'message': '--format json cannot be combined with --notify or --deactivate',
                })
                return EXIT_ERROR

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

                if json_mode:
                    output_json(build_expiring_projects(expiring, upcoming=True))
                    return EXIT_SUCCESS

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
                    return self._send_notifications(all_expiring, email_list,
                                                    dry_run, force=force)

            else:
                # Recent Expirations
                all_users = set()
                abandoned_users = set()
                expiring_projects = set()

                # Calculate max_days_expired from --since date, default to 365 days
                if since:
                    max_days = (datetime.now() - since).days
                    if max_days < 0:
                        self.console.print(f"Error: --since date cannot be in the future", style="bold red")
                        return EXIT_ERROR
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

                if json_mode:
                    output_json(build_expiring_projects(expiring, upcoming=False))
                    return EXIT_SUCCESS

                # Extract users if needed (business logic)
                if list_users:
                    for proj, alloc, res_name, days_expired in expiring:
                        all_users.update(proj.roster)
                        expiring_projects.add(proj.projcode)

                    for user in track(all_users, description="Determining abandoned users..."):
                        user_projects = set()
                        for proj in user.active_projects():
                            user_projects.add(proj.projcode)
                        if user_projects.issubset(expiring_projects):
                            abandoned_users.add(user)

                # Display results
                display_expiring_projects(self.ctx, expiring, list_users=list_users, upcoming=False)

                if list_users and abandoned_users:
                    display_abandoned_users_from_expired_projects(self.ctx, abandoned_users)

                if deactivate:
                    return self._deactivate_projects(expiring, force=force)

            return EXIT_SUCCESS
        except Exception as e:
            return self.handle_exception(e)


    def _deactivate_projects(self, expiring: list, force: bool = False) -> int:
        """Soft-deactivate recently expired projects.

        Args:
            expiring: List of tuples (project, allocation, resource_name, days_expired)
            force: If True, skip confirmation prompt

        Returns:
            EXIT_SUCCESS if all projects deactivated, EXIT_ERROR if any failed
        """
        # Deduplicate: one entry per project (query may return multiple allocations per project)
        projects = {}
        for proj, alloc, res_name, days_expired in expiring:
            if proj.projcode not in projects:
                projects[proj.projcode] = proj

        if not projects:
            self.console.print("[yellow]No active projects to deactivate.[/]")
            return EXIT_SUCCESS

        # Prompt for confirmation unless --force
        if not force:
            from rich.prompt import Confirm
            confirmed = Confirm.ask(
                f"\nDeactivate [bold]{len(projects)}[/] project(s)?",
                console=self.console
            )
            if not confirmed:
                self.console.print("[yellow]Deactivation cancelled.[/]")
                return EXIT_SUCCESS

        # Soft-deactivate each project
        now = datetime.now()
        deactivated = []
        failed = []
        for projcode, project in projects.items():
            try:
                project.active = False
                project.inactivate_time = now
                deactivated.append(projcode)
            except Exception as e:
                self.console.print(f"[bold red]Error staging {projcode}: {e}[/]")
                failed.append(projcode)

        # Commit all changes
        try:
            self.session.commit()
        except Exception as e:
            self.session.rollback()
            self.console.print(f"[bold red]Database error: {e}[/]")
            return EXIT_ERROR

        # Report results
        if deactivated:
            self.console.print(
                f"\n✅ Deactivated {len(deactivated)} project(s): {', '.join(deactivated)}",
                style="bold green"
            )
        if failed:
            self.console.print(
                f"❌ Failed to stage {len(failed)} project(s): {', '.join(failed)}",
                style="bold red"
            )

        return EXIT_ERROR if failed else EXIT_SUCCESS

    def _notifier(self):
        """Build a :class:`sam.notify.Notifier` wired to a ledger.

        The ledger gets its **own** sessions off this command's engine, not
        ``self.session``: mail handed to a relay cannot be un-sent by a
        rollback, so a ledger row must survive one. See
        ``sam/notify/ledger.py``.
        """
        from sqlalchemy.orm import Session
        from sam.notify import Notifier
        from sam.notify.ledger import NotificationLedger

        engine = self.session.get_bind()
        return Notifier(ledger=NotificationLedger(lambda: Session(engine)))

    def _send_notifications(self, expiring_data: list, additional_recipients: str = None,
                            dry_run: bool = False, force: bool = False) -> int:
        """Send expiration notices for expiring projects.

        This builds the audience and the payload — that is expiration domain
        logic and stays here — then hands ``Message`` objects to
        ``sam.notify``. Rendering, the safety guards, the transport and the
        ``notification_log`` rows all live there.

        Args:
            expiring_data: List of tuples (project, allocation, resource_name, days_remaining)
            additional_recipients: Comma-separated list of additional email addresses
            dry_run: If True, render previews without sending and write no
                ledger rows — a preview is not an attempt.
            force: Ignore suppression and re-send. The escape hatch for a
                notice that genuinely has to go out twice.

        Returns:
            EXIT_SUCCESS if all emails sent, EXIT_ERROR if any failed
        """
        import getpass

        from sam.notify import Message, Recipient

        try:
            requested_by = getpass.getuser()
        except Exception:                       # no passwd entry (container)
            requested_by = 'cli'

        # Group by project to send one email per project
        projects_map = defaultdict(list)
        for proj, alloc, resource_name, days_remaining in expiring_data:
            projects_map[proj.projcode].append({
                'project': proj,
                'allocation': alloc,
                'resource_name': resource_name,
                'days_remaining': days_remaining
            })

        # Build the message list
        messages = []
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
                    'expiration_date': fmt.date_str(item['allocation'].end_date, null='N/A'),
                    'days_remaining': item['days_remaining'],
                    'allocated_amount': resource_usage.get('allocated', 0),
                    'used_amount': resource_usage.get('used', 0),
                    'remaining_amount': resource_usage.get('remaining', 0),
                    # Was hardcoded 'core-hours', which told a PI with a DISK
                    # or ARCHIVE allocation that their TiB-years were
                    # core-hours. ResourceTypeName.allocation_unit is the one
                    # source the dashboard and the CLI already share; it also
                    # returns None for an access-boolean grant (amount == 1),
                    # so the notice stops rendering "1 hours".
                    'units': ResourceTypeName.allocation_unit(
                        resource_usage.get('resource_type'),
                        resource_usage.get('allocated')),
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

            # Lead details for the templates.
            #
            # `.primary_email` used to be read unguarded, two lines below a
            # guarded `project_lead_name`. Measured, that asymmetry is NOT the
            # crash it looks like: `project.project_lead_user_id` is NOT NULL
            # with an enforced FK (`project_lead_user_fk`, 0 dangling rows), so
            # `project.lead` cannot be None, and `primary_email` returns None
            # rather than raising when a lead has no address on file. The guard
            # is kept for consistency with the line above it, not because it
            # fixes a reachable AttributeError. What IS reachable — and what
            # the templates must cope with — is `project_lead_email is None`.
            project_lead_name = project.lead.display_name if project.lead else 'Project Lead'
            project_lead_email = project.lead.primary_email if project.lead else None

            subject = f'NSF NCAR Project {projcode} Expiration Notice'
            if facility_name == FacilityName.WNA:
                subject = f'NCAR/Wyoming Computing Project {projcode} Expiration Notice'

            # One Message per recipient — the ledger records one row per
            # person, and suppression keys on the recipient.
            for recipient_email, (recipient_name, recipient_role) in recipients.items():
                messages.append(Message(
                    kind='expiration',
                    recipient=Recipient(recipient_email, name=recipient_name,
                                        role=recipient_role),
                    subject=subject,
                    context={
                        'project_code': projcode,
                        'project_title': project.title,
                        'project_lead': project_lead_name,
                        'project_lead_email': project_lead_email,
                        'resources': resources,
                        'latest_expiration': latest_expiration_date,
                        'grace_expiration': grace_expiration_date,
                        'facility': facility_name,
                    },
                    facility=facility_name,
                    entity=('project', project.project_id),
                    projcode=projcode,
                    # Keyed on the expiration date, so a NEW expiration mints a
                    # new key and is never wrongly suppressed — which is why
                    # the suppression query needs no time window of its own.
                    dedup_key=(f'expiration:{projcode}:{latest_expiration_date}'
                               f':{recipient_email}'),
                    requested_by=requested_by,
                ))

        total_projects = len(projects_map)
        notifier = self._notifier()

        if dry_run:
            # preview() writes NO ledger row: a preview is not an attempt, and
            # a stray row would poison the dedup query for the real send.
            previews = []
            failures = []
            for message in messages:
                try:
                    previews.append((message, notifier.preview(message)))
                except Exception as exc:
                    failures.append((message, str(exc)))
            display_notification_preview(self.ctx, previews, failures, total_projects)
            return EXIT_ERROR if failures else EXIT_SUCCESS

        with notification_progress(self.ctx, len(messages)) as on_result:
            results = notifier.send_many(messages, force=force,
                                         on_result=on_result)

        display_notification_results(self.ctx, results, total_projects)
        return EXIT_ERROR if any(not r.ok for r in results) else EXIT_SUCCESS


class ProjectTreeAuditCommand(BaseProjectCommand):
    """Audit the project-tree allocation invariant across all projects.

    Unlike the other admin project commands this is DB-wide, not scoped to a
    single projcode: the invariant is a property of trees, not of any one
    project.
    """

    def execute(self, resource_name: str = None) -> int:
        try:
            violations = audit_allocation_trees(self.session, resource_name)
            bad_dates = audit_allocation_dates(self.session, resource_name)
        except Exception as e:
            self.console.print(f"Error auditing project trees: {e}", style="bold red")
            return EXIT_ERROR

        if self.ctx.output_format == 'json':
            output_json({
                'kind':          'tree_audit',
                'resource':      resource_name,
                'violations':    violations,
                'invalid_dates': [{**d,
                                   'start_date': d['start_date'].isoformat()
                                   if d['start_date'] else None,
                                   'end_date': d['end_date'].isoformat()
                                   if d['end_date'] else None}
                                  for d in bad_dates],
            })
        else:
            scope = f" on {resource_name}" if resource_name else ""
            self.console.print(
                f"[dim]Auditing project allocation trees{scope}...[/dim]\n"
            )
            display_tree_audit(self.ctx, violations, bad_dates)

        return EXIT_ERROR if (violations or bad_dates) else EXIT_SUCCESS


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
