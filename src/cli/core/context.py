"""Context class for SAM CLI."""

import sys
from typing import Optional
from sqlalchemy.orm import Session
from rich.console import Console


class SamConnectionError(RuntimeError):
    """SAM MySQL could not be reached.

    Exists so the *connect* can fail without deciding what the process does
    about it. See :meth:`Context.require_sam` for why that separation matters.
    """


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

    def open_sam(self) -> Session:
        """Return the SAM MySQL session, connecting on first use. **Raises.**

        The connect half of :meth:`require_sam`, without the exit policy. Use
        this from anything that is not a CLI subcommand — a scheduled task body
        reaches it via ``TaskContext.sam_session``, and must get an exception it
        can be recorded as having failed on.

        Raises:
            SamConnectionError: the engine could not be built or the connection
                refused. Chained from the original.
        """
        if self._session is None:
            try:
                from sam.session import create_sam_engine
                engine, _ = create_sam_engine()
                self._session = Session(engine)
            except Exception as e:
                raise SamConnectionError(
                    f"Error connecting to database: {e}") from e
        return self._session

    def require_sam(self) -> Session:
        """Return the SAM MySQL session, connecting on first use. **Exits on failure.**

        The `sam-admin` / `sam-search` group callbacks used to build the engine
        unconditionally and ``sys.exit(1)`` on failure, so *every* subcommand
        needed SAM MySQL to be reachable — including ones that never query it.
        For ``sam-admin tasks --run-due``, whose only registered task prunes
        Postgres, that converted a SAM outage into a `system_status` retention
        outage: exactly the coupling the scheduled-task framework exists to
        remove (docs/plans/implemented/SCHEDULED_TASKS.md § 3.2).

        ``SAMConfig.validate()`` stays in the callbacks — it is cheap, needs no
        socket, and catching a misconfiguration early is still worth it.

        ⚠️ **This is the CLI-facing accessor and it calls ``sys.exit``. Never
        hand it to the task runner.** ``scheduling.runner._execute`` catches
        ``Exception``, not ``BaseException`` — deliberately, so a pod's
        ``activeDeadlineSeconds`` kill leaves the ledger row ``running`` for the
        reclaim path instead of being mislabelled ``failed``. A ``SystemExit``
        raised inside a task body therefore escapes ``run_due`` entirely and
        terminates the dispatcher, skipping every task after it. Task bodies get
        :meth:`open_sam`; see ``cli/tasks/commands.py``.

        The exit code stays **1** rather than ``EXIT_ERROR`` (2), which is what a
        connection failure arguably deserves. Changing it would touch 10+
        ``sys.exit(command.execute(...))`` call sites that have no top-level
        handler, and the codes are a contract kept in lockstep with
        `hpc-usage-queries` (see ``src/cli/README.md``). Left alone on purpose.
        """
        try:
            return self.open_sam()
        except SamConnectionError as e:
            self.stderr_console.print(str(e), style="bold red")
            sys.exit(1)
