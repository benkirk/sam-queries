"""Contract command classes."""

import time

from rich.progress import track

from cli.core.base import BaseCommand
from cli.core.output import output_json
from cli.core.utils import EXIT_SUCCESS, EXIT_ERROR
from cli.contracts.builders import build_contract_audit, build_source_check
from cli.contracts.display import display_contract_audit
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
