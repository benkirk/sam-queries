"""Command class for ``sam-admin xras``."""

from datetime import datetime, timedelta

from cli.core.base import BaseCommand
from cli.core.output import output_json
from cli.core.utils import EXIT_ERROR, EXIT_NOT_FOUND, EXIT_SUCCESS
from cli.xras import builders, display


class XrasCommand(BaseCommand):
    """Read and re-check the XRAS action log.

    Extends ``BaseCommand`` directly rather than one of the entity-scoped bases:
    those exist only to carry a single-entity lookup helper, and this command is
    scoped to a table, not to a user/project/contract (see ``cli/core/base.py``).
    """

    def execute(self, *, action_id=None, recheck=None, summary=False,
                validate_mapping=False, validate_opportunities=False,
                accounts=False, person=None,
                enrich=False,
                status=(), action_type=(), request_number=None, last=None,
                show_payload=False, limit=50, **_) -> int:
        try:
            filters = self._filters(status, action_type, request_number, last)

            if validate_mapping:
                return self._validate_mapping()
            if validate_opportunities:
                return self._validate_opportunities()
            if person is not None:
                return self._person(person)
            if accounts:
                return self._accounts(filters, enrich)
            if recheck is not None:
                return self._recheck(recheck)
            if action_id is not None:
                return self._show(action_id, show_payload)
            if summary:
                return self._summary(filters)
            return self._list(filters, limit)
        except Exception as e:
            return self.handle_exception(e)

    # -- modes ------------------------------------------------------------

    def _validate_mapping(self) -> int:
        """Report the state of ``xras_resource_repository_key_resource``.

        WARNING: **An unmapped active resource is NOT a failure.** Not every
        internal resource is offered for allocation through XRAS, so most unmapped
        ones have no mapping *by design* — 11 of them, stably, across snapshot
        refreshes. Exiting non-zero on that would make the command unusable as a
        deploy gate: it would fail every time, forever.

        What it is instead: a **diagnostic**. If a resource that *should* be
        allocatable through XRAS appears in the unmapped list, that is the data fix
        behind ``No resource found in SAM corresponding to key %s`` — and a human
        reading the list is the only thing that can tell the two cases apart.

        Non-zero is reserved for the one genuinely broken state: a **dangling key**,
        a mapping row pointing at a resource row that does not exist. A
        decommissioned mapping is reported and does not fail — untidy, not broken.
        """
        payload = builders.build_mapping_report(self.session,
                                                xras_keys=self._live_keys())
        if self.ctx.output_format == 'json':
            output_json(payload)
        else:
            display.display_mapping_report(self.ctx, payload)
        # Two failing states now: a dangling key (a broken FK on our side) and
        # a key XRAS sends that SAM cannot resolve — the one that breaks awards.
        return (EXIT_NOT_FOUND
                if payload['dangling_keys'] or payload['xras_only_keys']
                else EXIT_SUCCESS)

    def _live_keys(self):
        """The XRAS resource catalog, or ``None`` if we could not ask.

        Auto-detecting rather than taking a flag: an operator running the
        pre-cutover gate wants the strongest check available, and the
        unconfigured case must degrade to today's one-sided report rather than
        fail. A configured-but-unreachable API is a warning, not an error —
        the local half of the audit is still worth reporting.
        """
        from sam.integration.xras_api import (XrasSourceUnavailable,
                                              resource_repository_keys,
                                              xras_api_configured)

        if not xras_api_configured():
            return None
        try:
            return resource_repository_keys()
        except XrasSourceUnavailable as exc:
            # WARNING: stderr, not stdout. This is a diagnostic about the run, not
            # part of the report — printed to stdout it lands *inside* the
            # `--format json` envelope and breaks every consumer piping to jq.
            self.ctx.stderr_console.print(
                f'[yellow]Could not reach the XRAS API ({exc}); reporting the '
                f'local half only.[/yellow]')
            return None

    def _validate_opportunities(self) -> int:
        """Report the state of ``xras_opportunity_allocation_type``.

        The ``opportunityId`` twin of :meth:`_validate_mapping`, and it exists
        because this map is the only one in the integration whose failure is
        **silent**. An unmapped resource key 422s the action; an unmapped
        opportunity falls through to the free-text ladder, whose twelve
        ``SelectionParms`` pairs never name ``UW``, ``WRAP`` or ``LCAP`` — so a
        Wyoming ``Small`` request resolves to panel ``UNIV USS``, the join
        *succeeds*, and the project is created with a UNIV-series projcode.
        Nothing fails, and projcodes are not undoable.

        **Exit code discipline is copied from ``--validate-mapping``, including
        the mistake it already corrected once.** Non-zero is reserved for
        ``dangling_ids`` — a mapping row whose ``allocation_type`` has vanished
        or has no panel, which the ingest-side lookup must treat as a miss and
        which nothing else would surface. It is deliberately **not** returned
        for:

        * ``unmapped_ids`` — with an empty table *every* opportunity is unmapped
          and ingestion is completely healthy, because the ladder resolves them
          exactly as it did before the table existed. A gate keyed on this would
          fail on the day the feature shipped and every day after.
        * ``review`` — two pairs sit there **permanently by design** (XRAS files
          the unsponsored family under ``Educational``, and gives ``NCAR - ASD
          Opportunity`` NSC's own type *and* panel; both change the facility).
          A non-zero exit on a bucket that is never empty trains an operator to
          ignore the bucket that matters.
        """
        payload = builders.build_opportunity_report(
            self.session, opportunities=self._live_opportunities())
        if self.ctx.output_format == 'json':
            output_json(payload)
        else:
            display.display_opportunity_report(self.ctx, payload)
        return (EXIT_NOT_FOUND if payload['dangling_ids'] else EXIT_SUCCESS)

    def _live_opportunities(self):
        """Every currently-open XRAS opportunity, or ``None`` if we could not ask.

        Auto-detecting rather than flagged, for the same reasons as
        :meth:`_live_keys`: the operator wants the strongest check available and
        the unconfigured case must degrade rather than fail.

        WARNING: **Open ones only** (``GET /v1/opportunities``), which is the right
        scope rather than a limitation. ``reports/requests`` cannot mention an
        opportunity nobody has submitted against — and that is precisely the one
        this check exists for, because it is the one an imminent action would
        silently mis-resolve.
        """
        from sam.integration.xras_api import (XrasApiClient,
                                              XrasSourceUnavailable,
                                              xras_api_configured)

        if not xras_api_configured():
            return None
        try:
            return XrasApiClient.from_environment().get_open_opportunities()
        except XrasSourceUnavailable as exc:
            self.ctx.stderr_console.print(
                f'[yellow]Could not reach the XRAS API ({exc}); reporting the '
                f'local half only.[/yellow]')
            return None

    def _accounts(self, filters, enrich) -> int:
        """The account-creation worklist.

        An empty worklist exits 0. It is a successful report — "nobody is
        blocked" — not a miss, and a deploy gate that treated it as one would
        fail every healthy day.
        """
        if enrich:
            from sam.integration.xras_api import xras_api_configured

            if not xras_api_configured():
                self.ctx.console.print(
                    '[red]--enrich needs the XRAS API: set '
                    'XRAS_OUTGOING_ENABLED=1 and XRAS_API_KEY.[/red]')
                return EXIT_ERROR

        pending, checked = self._pending_worklist()
        payload = builders.build_account_worklist(
            self.session, since=filters.get('start_date'),
            until=filters.get('end_date'), enrich=enrich,
            pending_rows=pending, pending_checked=checked)
        if self.ctx.output_format == 'json':
            output_json(payload)
        else:
            display.display_account_worklist(self.ctx, payload)
        return EXIT_SUCCESS

    def _pending_worklist(self):
        """Feed B, as ``xras_sweep`` last published it — or ``(None, False)``.

        WARNING: **This is why the CLI and the dashboard used to disagree.** The card
        reads the sweep's snapshot; ``--accounts`` only ever read the action
        log, so on a stack where XRAS had not yet repointed the card showed a
        real queue and the CLI reported zero. Same question, two answers, and
        nothing said which was partial.

        Returns the rows and *whether we were able to look*, kept separate for
        the reason ``live_checked`` exists on the mapping audit: an empty Feed B
        and an unreadable one are different facts, and only the second means the
        printed count is a subset.

        Degrades rather than fails. A laptop with no ``CACHE_REDIS_URL`` gets
        the Feed-A half and is told so — the same posture as an unconfigured
        ``--validate-mapping``.
        """
        from sam.integration.xras_api import xras_api_configured

        if not xras_api_configured():
            return None, False
        try:
            from sam.integration.xras_api.cache import load_pending_worklist

            snapshot = load_pending_worklist()
        except Exception as exc:                     # noqa: BLE001
            # The cache backend is infrastructure, not a contract — a laptop
            # without Redis raises from somewhere in the adapter stack rather
            # than returning empty, and that must not take the report down.
            self.ctx.stderr_console.print(
                f'[yellow]Could not read the published worklist ({exc}); '
                f'reporting posted actions only.[/yellow]')
            return None, False
        if snapshot is None:
            self.ctx.stderr_console.print(
                '[yellow]No sweep has published a pending worklist yet; '
                'reporting posted actions only.[/yellow]')
            return None, False
        return list(snapshot.get('rows') or []), True

    def _person(self, username) -> int:
        """Probe one username through ``GET /v1/people``.

        The three-outcome model reaches the exit code intact: found 0,
        no such username 1, could-not-ask 2. Collapsing the last two would
        make "XRAS is down" indistinguishable from "this person does not
        exist", which are opposite conclusions for an operator.
        """
        from sam.integration.xras_api import (XrasSourceUnavailable, get_person,
                                              xras_api_configured)

        if not xras_api_configured():
            self.ctx.console.print(
                '[red]--person needs the XRAS API: set XRAS_OUTGOING_ENABLED=1 '
                'and XRAS_API_KEY.[/red]')
            return EXIT_ERROR
        try:
            person = get_person(username)
        except XrasSourceUnavailable as exc:
            self.ctx.console.print(f'[red]XRAS unavailable: {exc}[/red]')
            return EXIT_ERROR

        payload = builders.build_person_report(username, person)
        if self.ctx.output_format == 'json':
            output_json(payload)
        else:
            display.display_person(self.ctx, payload)
        return EXIT_SUCCESS if person else EXIT_NOT_FOUND

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

    def _recheck(self, action_id) -> int:
        """Re-submit a stored payload.

        Needs a Flask application context, and that is not incidental: re-check
        writes through ``webapp.api.xras.actions._record``, which commits on its
        own connection so an audit row outlives a handler rollback, and it reads
        ``XRAS_ACTIONS_CAPTURE_ONLY`` from app config. Reimplementing either here
        would mean two write paths for the same table — and the API test suite's
        ``action_log`` fixture captures rows by patching that one function, so a
        second path would leak committed rows into the shared test database.

        The app is built lazily, inside this method, so ``sam-admin xras`` with no
        ``--recheck`` never pays for it.
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
            from webapp.api.xras.recheck import recheck_action
            from webapp.run import create_app
            app = create_app()

        with app.app_context():
            try:
                new_id, _status = recheck_action(action_id,
                                                 actor=getpass.getuser())
            except LookupError:
                self.ctx.stderr_console.print(
                    f'No XRAS action with id {action_id}.', style='bold red')
                return EXIT_NOT_FOUND

        rows = builders.build_action_detail(self.session, new_id,
                                            include_payload=False)
        payload = builders.build_recheck_result(
            action_id, new_id, actor=getpass.getuser(), action=rows['action'],
        )
        display.display_recheck_result(self.ctx, payload)
        return EXIT_SUCCESS

    # -- helpers ----------------------------------------------------------

    def _filters(self, status, action_type, request_number, last):
        """Normalize CLI options into query kwargs.

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
