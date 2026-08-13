"""``sam-admin tasks`` — list, dispatch, and read the history of scheduled tasks.

One class with a mode method each, following ``cli/xras/commands.py``. Heavy
imports are deferred into the mode that needs them, so ``--list`` never pays
for the task registry's import graph.

**This command must not require SAM MySQL.** Its ledger is in `system_status`,
and the whole point of the framework is that a SAM outage cannot stop status
retention. It therefore never touches ``self.session`` — which, since the
lazy-connect refactor, means no SAM connection is ever opened.
"""

from __future__ import annotations

from typing import Optional

from cli.core.base import BaseCommand
from cli.core.output import output_json
from cli.core.utils import EXIT_ERROR, EXIT_NOT_FOUND, EXIT_SUCCESS
from cli.tasks import builders, display

#: Outcomes that mean a dispatch did real work badly. § 7: exit 2 so a nonzero
#: exit makes the k8s Job `Failed`, which is the only free alerting channel
#: this deployment has.
_BAD_OUTCOMES = ('failed', 'partial')


class TasksCommand(BaseCommand):
    """Query and dispatch scheduled tasks."""

    def execute(self, *, list_tasks: bool = False, run_due: bool = False,
                run: Optional[str] = None, history: bool = False,
                task: Optional[str] = None, limit: int = 20,
                dry_run: bool = False, force: bool = False) -> int:
        try:
            if run_due:
                return self._dispatch(only=None, dry_run=dry_run, force=False)
            if run:
                return self._dispatch(only=run, dry_run=dry_run, force=force)
            if history:
                return self._history(task_name=task, limit=limit)
            return self._list()
        except Exception as e:                       # noqa: BLE001
            return self.handle_exception(e)

    # ------------------------------------------------------------------ modes
    def _list(self) -> int:
        registry, ledger, now = self._wire()
        from scheduling.runner import disabled_tasks

        payload = builders.build_task_list(registry, ledger, now=now,
                                           disabled=disabled_tasks())
        if self.ctx.output_format == 'json':
            output_json(payload)
            return EXIT_SUCCESS
        display.display_task_list(self.ctx, payload)
        return EXIT_SUCCESS

    def _history(self, *, task_name: Optional[str], limit: int) -> int:
        registry, ledger, _now = self._wire()

        if task_name and task_name not in registry:
            return self._not_found('task_history', task_name)

        payload = builders.build_task_history(ledger, task_name=task_name,
                                              limit=limit)
        if self.ctx.output_format == 'json':
            output_json(payload)
            return EXIT_SUCCESS
        display.display_task_history(self.ctx, payload)
        return EXIT_SUCCESS

    def _dispatch(self, *, only: Optional[str], dry_run: bool,
                  force: bool) -> int:
        import os

        from scheduling.runner import run_due as _run_due

        registry, ledger, now = self._wire()

        if only and only not in registry:
            return self._not_found('task_dispatch', only)

        result = _run_due(
            now=now, ledger=ledger, registry=registry, only=only,
            force=force, dry_run=dry_run,
            runner_id=os.getenv('RUNNER_ID'),
            status_session_factory=self._status_session_factory(),
            # `open_sam`, NOT `require_sam` — the latter calls sys.exit(1), and
            # `runner._execute` catches Exception rather than BaseException, so a
            # SystemExit raised here would escape run_due and kill the dispatcher
            # instead of failing the one task. See Context.require_sam.
            sam_session_factory=self.ctx.open_sam,
        )
        payload = builders.build_task_dispatch(result, dry_run=dry_run)

        if self.ctx.output_format == 'json':
            output_json(payload)
        else:
            display.display_task_dispatch(self.ctx, payload)

        # Exit 2 on any bad outcome, following the *audit* convention in
        # src/cli/README.md § Exit Codes — here the CI gating on it is
        # Kubernetes, which marks the Job Failed.
        if any(r['outcome'] in _BAD_OUTCOMES for r in result['results']):
            return EXIT_ERROR
        return EXIT_SUCCESS

    # ---------------------------------------------------------------- plumbing
    def _wire(self):
        """The registry, a ledger, and `now`. Imported here, not at module load."""
        import scheduling.tasks                    # noqa: F401  (registers TASKS)
        from scheduling.ledger import TaskLedger
        from scheduling.registry import TASKS
        from system_status.timeutil import utcnow_naive

        ledger = TaskLedger(self._status_session_factory())
        return TASKS, ledger, utcnow_naive()

    def _status_session_factory(self):
        """A factory for `system_status` sessions, built once per command.

        The engine is created lazily and reused; each call returns a *new*
        Session, which is what `TaskLedger` requires.
        """
        if getattr(self, '_status_factory', None) is None:
            from sqlalchemy.orm import Session

            from system_status.session import create_status_engine
            engine, _ = create_status_engine()
            self._status_factory = lambda: Session(engine)
        return self._status_factory

    def _not_found(self, kind: str, name: str) -> int:
        if self.ctx.output_format == 'json':
            output_json({'kind': kind, 'error': 'not_found', 'task': name})
        else:
            self.ctx.stderr_console.print(
                f"Unknown task '{name}'. Try `sam-admin tasks --list`.",
                style='bold red')
        return EXIT_NOT_FOUND
