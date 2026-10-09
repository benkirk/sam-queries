"""Base command classes for SAM CLI."""

from abc import ABC, abstractmethod
from typing import Optional
from cli.core.context import Context
from cli.core.output import output_json
from cli.core.utils import EXIT_ERROR, EXIT_NOT_FOUND, EXIT_SUCCESS
from sam.plugins import Plugin, PluginUnavailableError


class BaseCommand(ABC):
    """Base class for all commands."""

    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.console = ctx.console

    @property
    def session(self):
        """The SAM MySQL session, connected on first access.

        A property rather than an attribute set in ``__init__`` so that
        *constructing* a command does not open a connection. Commands that
        never touch SAM MySQL — ``sam-admin tasks``, whose ledger lives in
        `system_status` — therefore cost nothing, while every existing command
        keeps the attribute name it already uses.
        """
        return self.ctx.require_sam()

    @abstractmethod
    def execute(self, **kwargs) -> int:
        """Execute command. Returns exit code."""
        pass

    def emit(self, payload: dict, renderer, *args, **kwargs) -> int:
        """Print ``payload`` as JSON, or render it with ``renderer(ctx, payload, ...)``."""
        if self.ctx.output_format == 'json':
            output_json(payload)
        else:
            renderer(self.ctx, payload, *args, **kwargs)
        return EXIT_SUCCESS

    def not_found(self, kind: str, message: str, **ids) -> int:
        """The ``not_found`` envelope in JSON mode, else ``message`` on stderr."""
        if self.ctx.output_format == 'json':
            output_json({'kind': kind, 'error': 'not_found', **ids})
        else:
            self.ctx.stderr_console.print(message, style='bold red')
        return EXIT_NOT_FOUND

    def handle_exception(self, e: Exception) -> int:
        """Common error handling."""
        self.ctx.stderr_console.print(f"❌ Error: {e}", style="bold red")
        if self.ctx.verbose:
            import traceback
            self.ctx.stderr_console.print(traceback.format_exc(), style="dim")
        return EXIT_ERROR

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
            self.ctx.stderr_console.print(str(exc))
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


class BaseContractCommand(BaseCommand):
    """Base for single-contract commands.

    ``ContractsAuditCommand`` deliberately stays on ``BaseCommand``: it is
    scope-wide and has no single contract to resolve.
    """

    def get_contract(self, contract_number: str):
        """Get contract by its exact number."""
        from sam.projects.contracts import Contract
        return Contract.get_by_number(self.session, contract_number)


class BaseAllocationCommand(BaseCommand):
    """Base for allocation commands."""
    pass
