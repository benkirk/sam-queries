"""Context class for SAM CLI."""

import sys
from typing import Optional
from sqlalchemy.orm import Session
from rich.console import Console


class Context:
    """Shared context for CLI commands."""

    def __init__(self):
        # Backing store for the `session` property. NOT connected here — see
        # require_sam(). A plain attribute would reintroduce the eager connect
        # this class exists to defer.
        self._session: Optional[Session] = None
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

    # ------------------------------------------------------------ SAM MySQL
    @property
    def session(self) -> Optional[Session]:
        """The SAM MySQL session, or ``None`` if nothing has needed one yet.

        **Reading this does not connect.** Call :meth:`require_sam` to get a
        session, connecting on first use. The distinction matters for commands
        that touch a different database entirely — see the method docstring.
        """
        return self._session

    @session.setter
    def session(self, value: Optional[Session]) -> None:
        # Tests inject a session bound to the test transaction, and the group
        # callbacks used to assign here. Keeping the setter means neither has
        # to know about the backing attribute.
        self._session = value

    def require_sam(self) -> Session:
        """Return the SAM MySQL session, connecting on first use.

        The `sam-admin` / `sam-search` group callbacks used to build the engine
        unconditionally and ``sys.exit(1)`` on failure, so *every* subcommand
        needed SAM MySQL to be reachable — including ones that never query it.
        For ``sam-admin tasks --run-due``, whose only registered task prunes
        Postgres, that converted a SAM outage into a `system_status` retention
        outage: exactly the coupling the scheduled-task framework exists to
        remove (docs/plans/implemented/SCHEDULED_TASKS.md § 3.2).

        ``SAMConfig.validate()`` stays in the callbacks — it is cheap, needs no
        socket, and catching a misconfiguration early is still worth it.
        """
        if self._session is None:
            try:
                from sam.session import create_sam_engine
                engine, _ = create_sam_engine()
                self._session = Session(engine)
            except Exception as e:
                self.stderr_console.print(
                    f"Error connecting to database: {e}", style="bold red")
                sys.exit(1)
        return self._session
