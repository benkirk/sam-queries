"""Context class for SAM CLI."""

import os
import sys
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
        self.stderr_console = Console(file=sys.stderr)

        # Email configuration from environment
        self.mail_server = os.getenv('MAIL_SERVER', 'ndir.ucar.edu')
        self.mail_port = int(os.getenv('MAIL_PORT', '25'))
        self.mail_use_tls = os.getenv('MAIL_USE_TLS', 'false').lower() == 'true'
        self.mail_username = os.getenv('MAIL_USERNAME')
        self.mail_password = os.getenv('MAIL_PASSWORD')
        self.mail_from = os.getenv('MAIL_DEFAULT_FROM', 'sam-admin@ucar.edu')
