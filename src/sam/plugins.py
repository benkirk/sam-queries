"""
Optional plugin/dependency registry for SAM.

Provides a lightweight mechanism for declaring and loading optional
external packages (plugins) that are not required for core SAM functionality.
Any layer — CLI, webapp, system_status — can import from here.

Usage::

    from sam.plugins import HPC_USAGE_QUERIES, PluginUnavailableError

    # Option A: let the caller handle the error
    try:
        mod = HPC_USAGE_QUERIES.load()
    except PluginUnavailableError as exc:
        print(exc)
        return 2

    # Option B (CLI commands): use BaseCommand.require_plugin() which
    # catches the error, prints it via Rich, and returns None on failure.
    mod = self.require_plugin(HPC_USAGE_QUERIES)
    if mod is None:
        return 2
"""
import importlib
from dataclasses import dataclass
from types import ModuleType


class PluginUnavailableError(RuntimeError):
    """Raised when a required optional plugin/package is not installed."""


@dataclass
class Plugin:
    """Descriptor for an optional external dependency.

    Attributes:
        name:         Human-readable label (e.g. "HPC Usage Queries")
        package:      Top-level importable package name (e.g. "job_history")
        install_hint: Full command users should run to install the package
    """
    name: str
    package: str
    install_hint: str

    def load(self) -> ModuleType:
        """Import and return the plugin module.

        Python's import system caches modules in sys.modules, so repeated
        calls are cheap — no manual caching needed here.

        Returns:
            The imported module.

        Raises:
            PluginUnavailableError: If the package is not installed.
        """
        try:
            return importlib.import_module(self.package)
        except ImportError as exc:
            raise PluginUnavailableError(
                f"[bold red]Plugin '{self.name}' is not installed.[/bold red]\n"
                f"Install with:  {self.install_hint}"
            ) from exc

    @property
    def available(self) -> bool:
        """Return True if the plugin can be imported (no side effects)."""
        try:
            self.load()
            return True
        except PluginUnavailableError:
            return False


# ---------------------------------------------------------------------------
# Registry of known optional plugins
# Add new optional integrations here; one constant per plugin.
# ---------------------------------------------------------------------------

HPC_USAGE_QUERIES = Plugin(
    name="HPC Usage Queries",
    package="job_history",
    install_hint="pip install -e '.[hpc]'  # or: pip install 'hpc-usage-queries @ git+https://github.com/benkirk/hpc-usage-queries.git'",
)
