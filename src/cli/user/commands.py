"""User command classes."""

import logging
from datetime import timedelta

from cli.core.base import BaseUserCommand
from cli.core.output import output_json
from cli.core.utils import EXIT_SUCCESS, EXIT_NOT_FOUND, EXIT_ERROR
from cli.user.builders import (
    build_user_core,
    build_user_detail,
    build_user_projects,
    build_user_provisioning,
    build_user_search_results,
    build_abandoned_users,
    build_users_with_projects,
    build_not_seen_users,
    build_deactivation_restore,
)
from cli.user.display import (
    display_user,
    display_user_search_results,
    display_abandoned_users,
    display_users_with_projects,
    display_not_seen_users,
    display_deactivation_restore,
)
from sam import User
from rich.progress import track

logger = logging.getLogger(__name__)

#: ``data`` key left out entirely when the status DB is not configured on this host.
_UNCONFIGURED = object()


def _last_seen(username: str):
    """Ledger rows for one user, None when unreadable, ``_UNCONFIGURED`` without STATUS_DB_*."""
    from cli.last_seen.commands import status_session
    from sam.queries.last_seen_review import is_current
    from system_status.queries.last_seen import get_last_seen
    from system_status.timeutil import utcnow_naive
    try:
        status = status_session()
    except RuntimeError:
        return _UNCONFIGURED
    try:
        now = utcnow_naive()
        with status:
            return [{**r, 'current': is_current(r['last_seen'], r['kind'], now)}
                    for r in get_last_seen(status, username)]
    except Exception:
        logger.debug('last-seen lookup failed for %s', username, exc_info=True)
        return None


class UserSearchCommand(BaseUserCommand):
    """Exact user search by username."""

    def execute(self, username: str, list_projects: bool = False) -> int:
        try:
            user = self.get_user(username)
            if not user:
                return self.not_found('user', f"❌ User not found: {username}", username=username)

            json_mode = self.ctx.output_format == 'json'

            data = build_user_core(user)

            if json_mode or self.ctx.verbose:
                data['detail'] = build_user_detail(user)
            if json_mode or list_projects:
                data['projects'] = build_user_projects(
                    user, inactive=self.ctx.inactive_projects
                )
            if self.ctx.check_provisioning:
                data['provisioning'] = build_user_provisioning(user)
            last_seen = _last_seen(user.username)
            if last_seen is not _UNCONFIGURED:
                data['last_seen'] = last_seen

            return self.emit(data, display_user, list_projects)
        except Exception as e:
            return self.handle_exception(e)


class UserPatternSearchCommand(BaseUserCommand):
    """Pattern search for users."""

    def execute(self, pattern: str, limit: int = 50) -> int:
        try:
            clean_pattern = pattern.replace('%', '').replace('_', '')
            users = User.search_users(
                self.session,
                clean_pattern,
                active_only=not self.ctx.inactive_users,
                limit=limit
            )

            if not users:
                if self.ctx.output_format == 'json':
                    output_json({'kind': 'user_search_results', 'pattern': pattern,
                                 'count': 0, 'users': []})
                    return EXIT_NOT_FOUND
                self.console.print(f"❌ No users found matching: {pattern}", style="red")
                return EXIT_NOT_FOUND

            data = build_user_search_results(users, pattern)
            return self.emit(data, display_user_search_results)
        except Exception as e:
            return self.handle_exception(e)


class UserAbandonedCommand(BaseUserCommand):
    """Find 'active' users with no active projects."""

    def execute(self) -> int:
        try:
            json_mode = self.ctx.output_format == 'json'
            active_users = User.get_active_users(self.session)
            abandoned_users = set()

            for user in track(
                active_users,
                description=" --> determining abandoned users...",
                disable=json_mode,
            ):
                if len(user.active_projects()) == 0:
                    abandoned_users.add(user)

            data = build_abandoned_users(abandoned_users, len(active_users))
            return self.emit(data, display_abandoned_users)
        except Exception as e:
            return self.handle_exception(e)


class UserWithProjectsCommand(BaseUserCommand):
    """Find 'active' users with at least one active project."""

    def execute(self, list_projects: bool = False) -> int:
        try:
            json_mode = self.ctx.output_format == 'json'
            active_users = User.get_active_users(self.session)
            users_with_projects = set()

            for user in track(
                active_users,
                description="Determining users with at least one active project...",
                disable=json_mode,
            ):
                if len(user.active_projects()) > 0:
                    users_with_projects.add(user)

            # JSON always emits projects sub-builder; Rich gates on flag.
            include_projects = json_mode or list_projects
            data = build_users_with_projects(users_with_projects, include_projects)

            if json_mode:
                output_json(data)
            elif users_with_projects:
                display_users_with_projects(self.ctx, data, list_projects)

            return EXIT_SUCCESS
        except Exception as e:
            return self.handle_exception(e)


class UserNotSeenCommand(BaseUserCommand):
    """Users whose newest sighting is older than a cutoff, or who were never seen."""

    def execute(self, days: int, spec: str, source=None, abandoned: bool = False) -> int:
        from cli.last_seen.commands import status_session
        from sam.queries.last_seen_review import review
        from sam.queries.users import (
            count_active_projects_by_username, get_primary_emails, get_user_directory,
        )
        from system_status.queries.last_seen import get_last_seen_by_user
        from system_status.timeutil import utcnow_naive
        try:
            now = utcnow_naive()
            cutoff = now - timedelta(days=days)
            directory = get_user_directory(self.session, active_only=not self.ctx.inactive_users)
            with status_session() as status:
                ledger = get_last_seen_by_user(status)
            rows, _, _ = review(directory, ledger, now=now, kind=source,
                                sort_by='last_seen', sort_dir='asc')
            rows = [r for r in rows if r.last_seen is None or r.last_seen < cutoff]
            projects = count_active_projects_by_username(self.session, [r.username for r in rows])
            if abandoned:
                rows = [r for r in rows if not projects.get(r.username)]
            emails = get_primary_emails(self.session, [r.username for r in rows])

            data = build_not_seen_users(
                rows, projects, emails, spec=spec, cutoff=cutoff, source=source,
                abandoned=abandoned, total_considered=len(directory),
                active_only=not self.ctx.inactive_users)
            return self.emit(data, display_not_seen_users)
        except Exception as e:
            return self.handle_exception(e)


class UserAdminCommand(UserSearchCommand):
    """Admin command for users - extends search with validation."""

    def execute(self, username: str, validate: bool = False, deactivation: bool = False,
                restore_deactivation: bool = False, dry_run: bool = False, **kwargs) -> int:
        if deactivation or restore_deactivation:
            return self._restore_deactivation(
                username, dry_run=dry_run or not restore_deactivation)
        # First run base search
        exit_code = super().execute(username, **kwargs)
        if exit_code != EXIT_SUCCESS:
            return exit_code

        # Add admin-specific logic
        if validate:
            return self._validate_user(username)

        return EXIT_SUCCESS

    def _restore_deactivation(self, username: str, dry_run: bool) -> int:
        """Show, or undo, the membership closure of the user's last deactivation."""
        from sam.manage import management_transaction
        from sam.manage.lifecycle import restore_user_deactivation
        json_mode = self.ctx.output_format == 'json'
        if json_mode and not dry_run:
            output_json({'kind': 'deactivation_restore', 'error': 'json_unsupported_for_writes',
                         'message': '--format json cannot be combined with --restore-deactivation'
                                    ' (add --dry-run)'})
            return EXIT_ERROR
        user = self.get_user(username)
        if user is None:
            return self.not_found('deactivation_restore', f"User not found: {username}",
                                  username=username)
        if dry_run:
            report = restore_user_deactivation(self.session, user, dry_run=True)
        else:
            with management_transaction(self.session):
                report = restore_user_deactivation(self.session, user)
        data = build_deactivation_restore(report, dry_run)
        self.emit(data, display_deactivation_restore)
        return EXIT_SUCCESS if report.instant is not None else EXIT_NOT_FOUND

    def _validate_user(self, username: str) -> int:
        """Admin-only: validate user data integrity."""
        user = self.get_user(username)
        self.ctx.message_console.print(f"[dim]Validating user {username}...[/dim]")

        # Placeholder validation logic
        issues = []
        if not user.primary_email:
            issues.append("Missing primary email")
        if not user.unix_uid:
            issues.append("Missing unix_uid")

        if issues:
            self.ctx.message_console.print("⚠️  Validation issues:", style="yellow")
            for issue in issues:
                self.ctx.message_console.print(f"  - {issue}", style="yellow")
            return EXIT_ERROR

        self.ctx.message_console.print(f"✅ User {username} validated", style="green")
        return EXIT_SUCCESS
