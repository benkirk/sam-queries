"""Context class for SAM CLI."""

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
        self.output_format: str = 'rich'

        # Host provisioning cross-check: defaults on where the data is
        # meaningful (NCAR_HOST / SAM_CHECK_PROVISIONING). The user/project
        # commands may override this from their --provisioning flag.
        from sam.provisioning import is_provisioned_host
        self.check_provisioning: bool = is_provisioned_host()
        self.console = Console()
        self.stderr_console = Console(file=sys.stderr)

        # NO mail configuration here, deliberately.
        #
        # This block used to re-read the same six MAIL_* vars off os.getenv
        # with the same defaults as src/config.py:31-37 — a SECOND source of
        # truth, which is why the CLI never honoured a SAMConfig change (and
        # why MAIL_USE_TLS could be flipped in one place and stay false in the
        # other). sam.notify.NotifyConfig replaces both and reads Flask config
        # or the environment, so the CLI and the webapp cannot disagree.
