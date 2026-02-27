"""Base command classes for SAM CLI."""

from abc import ABC, abstractmethod
from typing import Optional
from cli.core.context import Context
from sam.plugins import Plugin, PluginUnavailableError


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

    def require_plugin(self, plugin: Plugin):
        """Load an optional plugin module, printing a friendly error on failure.

        Returns the imported module on success, or None if the plugin is not
        installed. The exit code decision remains with the calling command.

        Example::

            mod = self.require_plugin(HPC_USAGE_QUERIES)
            if mod is None:
                return EXIT_ERROR
            jh_get_session = mod.get_session
        """
        try:
            return plugin.load()
        except PluginUnavailableError as exc:
            self.console.print(str(exc))
            return None


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
