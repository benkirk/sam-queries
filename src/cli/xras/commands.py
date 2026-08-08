"""Command class for ``sam-admin xras``."""

from datetime import datetime, timedelta

from cli.core.base import BaseCommand
from cli.core.output import output_json
from cli.core.utils import EXIT_ERROR, EXIT_NOT_FOUND, EXIT_SUCCESS
from cli.xras import builders, display


class XrasCommand(BaseCommand):
    """Read and replay the XRAS action log.

    Extends ``BaseCommand`` directly rather than one of the entity-scoped bases:
    those exist only to carry a single-entity lookup helper, and this command is
    scoped to a table, not to a user/project/contract (see ``cli/core/base.py``).
    """

    def execute(self, *, action_id=None, replay=None, summary=False,
                validate_mapping=False,
                status=(), action_type=(), request_number=None, last=None,
                show_payload=False, limit=50, **_) -> int:
        try:
            filters = self._filters(status, action_type, request_number, last)

            if validate_mapping:
                return self._validate_mapping()
            if replay is not None:
                return self._replay(replay)
            if action_id is not None:
                return self._show(action_id, show_payload)
            if summary:
                return self._summary(filters)
            return self._list(filters, limit)
        except Exception as e:
            return self.handle_exception(e)

    # -- modes ------------------------------------------------------------

    def _validate_mapping(self) -> int:
        """Pre-cutover gate: report resources XRAS cannot name.

        Exits ``EXIT_NOT_FOUND`` when an **active** resource is unmapped, so the
        check can gate a deploy script rather than only inform a human. A
        decommissioned mapping is reported but does not fail the run — it is
        untidy, not broken.
        """
        payload = builders.build_mapping_report(self.session)
        if self.ctx.output_format == 'json':
            output_json(payload)
        else:
            display.display_mapping_report(self.ctx, payload)
        return EXIT_NOT_FOUND if payload['unmapped_active'] else EXIT_SUCCESS

    def _list(self, filters, limit) -> int:
        payload = builders.build_action_list(self.session, filters=filters,
                                             limit=limit)
        if self.ctx.output_format == 'json':
            output_json(payload)
            return EXIT_SUCCESS
        display.display_action_list(self.ctx, payload)
        return EXIT_SUCCESS

    def _show(self, action_id, show_payload) -> int:
        payload = builders.build_action_detail(self.session, action_id,
                                               include_payload=show_payload)
        if payload is None:
            if self.ctx.output_format == 'json':
                output_json({'kind': 'xras_action', 'error': 'not_found',
                             'action_log_id': action_id})
            else:
                self.ctx.stderr_console.print(
                    f'No XRAS action with id {action_id}.', style='bold red')
            return EXIT_NOT_FOUND

        if self.ctx.output_format == 'json':
            output_json(payload)
            return EXIT_SUCCESS
        display.display_action_detail(self.ctx, payload)
        return EXIT_SUCCESS

    def _summary(self, filters) -> int:
        payload = builders.build_summary(self.session, filters=filters)
        if self.ctx.output_format == 'json':
            output_json(payload)
            return EXIT_SUCCESS
        display.display_summary(self.ctx, payload)
        return EXIT_SUCCESS

    def _replay(self, action_id) -> int:
        """Re-submit a stored payload.

        Needs a Flask application context, and that is not incidental: replay
        writes through ``webapp.api.xras.actions._record``, which commits on its
        own connection so an audit row outlives a handler rollback, and it reads
        ``XRAS_ACTIONS_CAPTURE_ONLY`` from app config. Reimplementing either here
        would mean two write paths for the same table — and the API test suite's
        ``action_log`` fixture captures rows by patching that one function, so a
        second path would leak committed rows into the shared test database.

        The app is built lazily, inside this method, so ``sam-admin xras`` with no
        ``--replay`` never pays for it.
        """
        import contextlib
        import getpass
        import sys

        # Both the webapp import and create_app() print startup diagnostics
        # (a DB URI at import time; limiter config and log-directory fallbacks at
        # app build) straight to stdout. That is fine for a server boot and wrong
        # for a CLI, where stdout is the result channel — so the whole block is
        # redirected to stderr, matching the house rule that only payloads reach
        # stdout. The import must be inside the redirect, not just the call.
        with contextlib.redirect_stdout(sys.stderr):
            from webapp.api.xras.replay import replay_action
            from webapp.run import create_app
            app = create_app()

        with app.app_context():
            try:
                new_id = replay_action(action_id, actor=getpass.getuser())
            except LookupError:
                self.ctx.stderr_console.print(
                    f'No XRAS action with id {action_id}.', style='bold red')
                return EXIT_NOT_FOUND

        rows = builders.build_action_detail(self.session, new_id,
                                            include_payload=False)
        payload = builders.build_replay_result(
            action_id, new_id, actor=getpass.getuser(), action=rows['action'],
        )
        display.display_replay_result(self.ctx, payload)
        return EXIT_SUCCESS

    # -- helpers ----------------------------------------------------------

    def _filters(self, status, action_type, request_number, last):
        """Normalise CLI options into query kwargs.

        ``--last`` wins over nothing: with no window given the command shows all
        time rather than a silent default. The dashboard defaults to 30 days
        because a table needs a first page; a CLI invocation is explicit by
        nature, and a hidden window would make ``--summary`` quietly wrong.
        """
        start_date = None
        if last:
            start_date = datetime.now() - timedelta(days=self._parse_days(last))

        return {
            'status': list(status) or None,
            'action_type': list(action_type) or None,
            'request_number': request_number,
            'start_date': start_date,
            'end_date': None,
        }

    @staticmethod
    def _parse_days(value) -> int:
        """Accept ``7d`` / ``30`` / ``12h``, matching ``sam-search accounting --last``."""
        raw = str(value).strip().lower()
        if raw.endswith('d'):
            return int(raw[:-1])
        if raw.endswith('h'):
            # Round up: '12h' means "today", and truncating to 0 days would mean
            # "no window at all", which is the opposite of what was asked.
            hours = int(raw[:-1])
            return max(1, (hours + 23) // 24)
        if raw.endswith('w'):
            return int(raw[:-1]) * 7
        return int(raw)
