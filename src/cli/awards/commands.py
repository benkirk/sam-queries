"""Award command classes — the provider side of contracts.

Where ``cli.contracts`` asks SAM, this asks the funding agencies. The two are
separate subcommands rather than modes of one because they have different data
sources, different envelope shapes and independent exit-code semantics.

**Exit codes, three outcomes never conflated** — exactly the model
``htmx_contract_award_lookup`` uses for its three render paths:

===================================  ================
found                                ``EXIT_SUCCESS``
no such award / no matching results  ``EXIT_NOT_FOUND``
source unreachable                   ``EXIT_ERROR``
===================================  ================

"NSF has no award 1234567" and "NSF is down" are different answers and the
caller must be able to tell them apart.
"""

from cli.awards.builders import build_award, build_award_search, build_in_sam
from cli.awards.display import display_award, display_award_search
from cli.core.base import BaseContractCommand
from cli.core.output import output_json
from cli.core.utils import EXIT_SUCCESS, EXIT_ERROR, EXIT_NOT_FOUND

#: Seconds. ``AwardHttpClient.DEFAULT_TIMEOUT`` is 10, deliberately short
#: because the webapp path runs inside an htmx round-trip. A CLI has no worker
#: to hold, so it builds its own providers with a longer one rather than
#: raising the default and slowing the webapp's failure path.
CLI_TIMEOUT = 30


def _cli_providers():
    """Providers wired to a longer-timeout client."""
    from sam.integration.awards import AwardHttpClient, build_providers
    return build_providers(AwardHttpClient(timeout=CLI_TIMEOUT))


class AwardSearchCommand(BaseContractCommand):
    """Look one award up at its funding agency and cross-reference SAM."""

    def execute(self, contract_number: str, source: str = None) -> int:
        from sam.integration.awards import AwardSourceUnavailable

        json_mode = self.ctx.output_format == 'json'

        # The SAM contract is needed for the cross-reference anyway, and it
        # also tells us which agency to ask — see _resolve().
        contract = self.get_contract(contract_number)
        source = source or self._source_of(contract)

        try:
            record = self._resolve(source, contract_number)
        except AwardSourceUnavailable as exc:
            # Distinct from "no such award": the source could not be asked.
            if json_mode:
                output_json({'kind': 'award', 'error': 'source_unavailable',
                             'contract_number': contract_number,
                             'source': source, 'reason': str(exc)})
            else:
                self.ctx.stderr_console.print(
                    f"❌ Award source unavailable: {exc}", style="bold red")
            return EXIT_ERROR
        except Exception as e:
            return self.handle_exception(e)

        if record is None:
            if json_mode:
                output_json({'kind': 'award', 'error': 'not_found',
                             'contract_number': contract_number,
                             'source': source})
            else:
                self.console.print(
                    f"❌ No award found for {contract_number}"
                    + (f" at {source}" if source else ""),
                    style="bold red")
            return EXIT_NOT_FOUND

        try:
            in_sam = self._cross_reference(contract, record)
            data = build_award(record, contract_number=contract_number,
                               source=source, in_sam=in_sam)

            if json_mode:
                output_json(data)
            else:
                display_award(self.ctx, data)
            return EXIT_SUCCESS
        except Exception as e:
            return self.handle_exception(e)

    @staticmethod
    def _source_of(contract):
        """The funding source SAM has on file for *contract*, if any."""
        if contract is None or contract.contract_source is None:
            return None
        return contract.contract_source.contract_source

    def _resolve(self, source, contract_number):
        """Ask the providers, working around ``supports()`` needing a source.

        ``NsfAwardProvider.supports()`` returns False unless the source is
        literally ``'NSF'``, so a bare ``sam-search awards AGS-1852977`` with
        no ``--source`` would only ever reach USAspending — which has no NSF
        awards — and report "not found" for a number NSF knows perfectly well.

        The webapp never hits this because its source comes from a required
        dropdown. A CLI user has no such prompt, so when the source is unknown
        and the number parses as an NSF award id, NSF is tried as a fallback.
        """
        from sam.integration.awards import nsf_award_id, resolve_award

        record = resolve_award(source, contract_number)
        if record is None and not source and nsf_award_id(contract_number):
            record = resolve_award('NSF', contract_number)
        return record

    def _cross_reference(self, contract, record):
        """Compare the fetched record against SAM's contract, if we have one.

        ``compare_contract`` is handed the record we already have, so this
        costs no second fetch — and it brings the ``suspect_match`` guard with
        it, which is what stops a keyword-matched stranger's award being
        presented as this contract's data.
        """
        from sam.integration.awards.audit import compare_contract

        if contract is None:
            return None
        return build_in_sam(contract,
                            compare_contract(self.session, contract,
                                             record=record))


class AwardPatternSearchCommand(BaseContractCommand):
    """Composite free-text search across every eligible award provider."""

    def execute(self, query: str, source: str = None, limit: int = 10) -> int:
        from sam.integration.awards import search_awards

        json_mode = self.ctx.output_format == 'json'
        sources = [source] if source else None

        try:
            records, errors = search_awards(query, limit=limit,
                                            sources=sources,
                                            providers=_cli_providers())
            data = build_award_search(
                records, errors, query=query, limit=limit, sources=sources,
                known=self._known_numbers(records))

            if json_mode:
                output_json(data)
            else:
                display_award_search(self.ctx, data)
        except Exception as e:
            return self.handle_exception(e)

        if records:
            return EXIT_SUCCESS
        # No results and every provider failed is an error, not a miss —
        # otherwise a total outage reads as "this award does not exist".
        return EXIT_ERROR if errors else EXIT_NOT_FOUND

    def _known_numbers(self, records) -> dict:
        """Which of these award numbers SAM already has, by normalized number.

        This is the same protection ``_ContractCreateHandler.clean`` gives
        when the operator submits a duplicate number, surfaced before they
        start typing. Shares one implementation with the webapp's
        ``_annotate_known``.
        """
        from sam.projects.contracts import Contract

        return Contract.existing_by_number(
            self.session, [r.contract_number for r in records])
