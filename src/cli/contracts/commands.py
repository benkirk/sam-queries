"""Contract command classes.

Two exit-code conventions live in this module, and the difference is
deliberate rather than an oversight:

* ``ContractsAuditCommand`` (``sam-admin``) returns ``EXIT_ERROR`` to mean
  **findings exist**, matching ``ProjectTreeAuditCommand``. It is a linter.
* The two search commands (``sam-search``) return ``EXIT_ERROR`` only for a
  genuine error, ``EXIT_NOT_FOUND`` for "no such contract", matching every
  other ``sam-search`` subcommand. They are lookups.

Do not unify them; each matches the command family a user invokes it from.
"""

import time

from rich.progress import track

from cli.core.base import BaseCommand, BaseContractCommand
from cli.core.output import output_json
from cli.core.utils import EXIT_SUCCESS, EXIT_ERROR, EXIT_NOT_FOUND
from cli.contracts.builders import (
    build_contract, build_contract_audit, build_contract_search,
    build_source_check,
)
from cli.contracts.display import (
    display_contract, display_contract_audit, display_contract_search,
)
from sam.queries.contract_audit import audit_contracts, audit_nsf_programs


class ContractsAuditCommand(BaseCommand):
    """Audit contract data hygiene across the database.

    Extends ``BaseCommand`` directly rather than a ``BaseContractCommand``:
    those base classes exist only to hold a single-entity lookup helper, and
    this audit is scope-wide — the same reason ``ProjectTreeAuditCommand``
    short-circuits before the projcode requirement.

    Read-only by construction.  It reports what is wrong; correcting it stays
    a human decision through the webapp's edit form.
    """

    def execute(self, active_only: bool = True, check_sources: bool = False,
                limit: int = None, sleep_between: float = 0.3) -> int:
        try:
            findings = audit_contracts(self.session, active_only=active_only)
            program_findings = audit_nsf_programs(self.session)
        except Exception as e:
            return self.handle_exception(e)

        scope = 'open' if active_only else 'all'
        contracts_audited = self._count_audited(active_only)

        source_check = None
        if check_sources:
            try:
                source_check = self._check_sources(active_only, limit,
                                                   sleep_between)
            except Exception as e:
                return self.handle_exception(e)

        data = build_contract_audit(
            findings, program_findings,
            scope=scope,
            contracts_audited=contracts_audited,
            source_check=source_check,
        )

        if self.ctx.output_format == 'json':
            output_json(data)
        else:
            self.console.print(
                f"[dim]Auditing {contracts_audited} {scope} contracts...[/dim]"
            )
            display_contract_audit(self.ctx, data)

        divergent = (source_check or {}).get('divergent', 0)
        has_findings = bool(findings or program_findings or divergent)
        return EXIT_ERROR if has_findings else EXIT_SUCCESS

    def _count_audited(self, active_only: bool) -> int:
        """How many contracts the audit examined."""
        from sam.projects.contracts import Contract

        q = self.session.query(Contract)
        if active_only:
            q = q.filter(Contract.is_active)
        return q.count()

    def _check_sources(self, active_only: bool, limit, sleep_between: float):
        """Compare each contract against its funding agency. Network-bound.

        Throttled and progress-barred because this is ~424 requests worst case
        over the open set — roughly two minutes at the default rate.  The
        progress bar is disabled in JSON mode so it cannot corrupt stdout.

        A source that cannot be reached costs one "unchecked" contract, never
        the run: ``compare_contract`` converts ``AwardSourceUnavailable`` into
        a status rather than letting it propagate.
        """
        from sam.integration.awards.audit import compare_contract
        from sam.queries.admin import get_contracts_with_pi

        contracts = get_contracts_with_pi(self.session, active_only=active_only,
                                          with_source=True)
        if limit:
            contracts = contracts[:limit]

        json_mode = self.ctx.output_format == 'json'
        results = []
        for i, contract in enumerate(track(contracts,
                                           description="Checking sources...",
                                           disable=json_mode)):
            results.append((contract, compare_contract(self.session, contract)))
            if sleep_between and i < len(contracts) - 1:
                time.sleep(sleep_between)

        return build_source_check(results)


class ContractSearchCommand(BaseContractCommand):
    """Look one contract up by its exact number and show its detail."""

    def execute(self, contract_number: str, list_projects: bool = False) -> int:
        try:
            contract = self.get_contract(contract_number)
            if contract is None:
                if self.ctx.output_format == 'json':
                    output_json({'kind': 'contract', 'error': 'not_found',
                                 'contract_number': contract_number})
                else:
                    self.console.print(
                        f"❌ Contract not found: {contract_number}",
                        style="bold red")
                return EXIT_NOT_FOUND

            # Reload through the detail loader so the project chain and the
            # two user FKs are warm before serialization.
            from sam.queries.admin import get_contract_detail
            detailed = get_contract_detail(self.session, contract.contract_id)
            data = build_contract(detailed or contract)

            if self.ctx.output_format == 'json':
                output_json(data)
            else:
                display_contract(self.ctx, data, list_projects)
            return EXIT_SUCCESS
        except Exception as e:
            return self.handle_exception(e)


class ContractPatternSearchCommand(BaseContractCommand):
    """Search contracts by number/title pattern plus optional filters."""

    def execute(self, pattern: str = None, active_only: bool = True,
                source: str = None, pi: str = None, monitor: str = None,
                program: str = None, limit: int = 50) -> int:
        try:
            from sam.projects.contracts import Contract

            contracts = Contract.search_by_pattern(
                self.session, pattern, active_only=active_only,
                source=source, pi=pi, monitor=monitor, program=program,
                limit=limit, with_details=True)

            data = build_contract_search(
                contracts, pattern=pattern,
                filters={'source': source, 'pi': pi, 'monitor': monitor,
                         'program': program},
                scope='open' if active_only else 'all')

            if self.ctx.output_format == 'json':
                output_json(data)
            else:
                display_contract_search(self.ctx, data)

            # A JSON not-found still emits its envelope, then exits 1.
            return EXIT_SUCCESS if contracts else EXIT_NOT_FOUND
        except Exception as e:
            return self.handle_exception(e)
