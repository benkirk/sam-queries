"""Context class for SAM CLI."""

from typing import Optional
from sqlalchemy.orm import Session
from rich.console import Console


class Context:
    """Shared context for CLI commands."""

    def __init__(self):
        self.session: Optional[Session] = None
        self.verbose: bool = False
        self.very_verbose: bool = False
        self.inactive_projects: bool = False
        self.inactive_users: bool = False
        self.console = Console()
