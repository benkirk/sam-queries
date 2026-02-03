"""User command classes."""

from cli.core.base import BaseUserCommand
from cli.core.utils import EXIT_SUCCESS, EXIT_NOT_FOUND, EXIT_ERROR
from cli.user.display import (
    display_user,
    display_user_projects,
    display_user_search_results,
    display_abandoned_users,
    display_users_with_projects
)
from sam import User
from rich.progress import track


class UserSearchCommand(BaseUserCommand):
    """Exact user search by username."""

    def execute(self, username: str, list_projects: bool = False) -> int:
        try:
            user = self.get_user(username)
            if not user:
                self.console.print(f"❌ User not found: {username}", style="bold red")
                return EXIT_NOT_FOUND

            display_user(self.ctx, user, list_projects)
            return EXIT_SUCCESS
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
                self.console.print(f"❌ No users found matching: {pattern}", style="red")
                return EXIT_NOT_FOUND

            display_user_search_results(self.ctx, users, pattern)
            return EXIT_SUCCESS
        except Exception as e:
            return self.handle_exception(e)


class UserAbandonedCommand(BaseUserCommand):
    """Find 'active' users with no active projects."""

    def execute(self) -> int:
        try:
            active_users = User.get_active_users(self.session)
            abandoned_users = set()

            for user in track(active_users, description=" --> determining abandoned users..."):
                if len(user.active_projects) == 0:
                    abandoned_users.add(user)

            display_abandoned_users(self.ctx, abandoned_users, len(active_users))
            return EXIT_SUCCESS
        except Exception as e:
            return self.handle_exception(e)


class UserWithProjectsCommand(BaseUserCommand):
    """Find 'active' users with at least one active project."""

    def execute(self, list_projects: bool = False) -> int:
        try:
            active_users = User.get_active_users(self.session)
            users_with_projects = set()

            for user in track(active_users, description="Determining users with at least one active project..."):
                if len(user.active_projects) > 0:
                    users_with_projects.add(user)

            if users_with_projects:
                display_users_with_projects(self.ctx, users_with_projects, list_projects)

            return EXIT_SUCCESS
        except Exception as e:
            return self.handle_exception(e)


class UserAdminCommand(UserSearchCommand):
    """Admin command for users - extends search with validation."""

    def execute(self, username: str, validate: bool = False, **kwargs) -> int:
        # First run base search
        exit_code = super().execute(username, **kwargs)
        if exit_code != EXIT_SUCCESS:
            return exit_code

        # Add admin-specific logic
        if validate:
            return self._validate_user(username)

        return EXIT_SUCCESS

    def _validate_user(self, username: str) -> int:
        """Admin-only: validate user data integrity."""
        user = self.get_user(username)
        self.console.print(f"[dim]Validating user {username}...[/dim]")

        # Placeholder validation logic
        issues = []
        if not user.primary_email:
            issues.append("Missing primary email")
        if not user.unix_uid:
            issues.append("Missing unix_uid")

        if issues:
            self.console.print(f"⚠️  Validation issues:", style="yellow")
            for issue in issues:
                self.console.print(f"  - {issue}", style="yellow")
            return EXIT_ERROR

        self.console.print(f"✅ User {username} validated", style="green")
        return EXIT_SUCCESS
