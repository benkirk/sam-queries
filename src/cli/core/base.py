"""Base command classes for SAM CLI."""

from abc import ABC, abstractmethod
from typing import Optional
from cli.core.context import Context


class BaseCommand(ABC):
    """Base class for all commands."""

    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.session = ctx.session
        self.console = ctx.console

    @abstractmethod
    def execute(self, **kwargs) -> int:
        """Execute command. Returns exit code."""
        pass

    def handle_exception(self, e: Exception) -> int:
        """Common error handling."""
        self.ctx.stderr_console.print(f"❌ Error: {e}", style="bold red")
        if self.ctx.verbose:
            import traceback
            self.console.print(traceback.format_exc(), style="dim")
        return 2


class BaseUserCommand(BaseCommand):
    """Base for user commands."""

    def get_user(self, username: str):
        """Get user by username."""
        from sam import User
        return User.get_by_username(self.session, username)


class BaseProjectCommand(BaseCommand):
    """Base for project commands."""

    def get_project(self, projcode: str):
        """Get project by projcode."""
        from sam import Project
        return Project.get_by_projcode(self.session, projcode)


class BaseAllocationCommand(BaseCommand):
    """Base for allocation commands."""
    pass
