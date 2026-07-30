"""Provider selection: source-specific first, then generic.

Two providers today, and the survey behind
docs/plans/CONTRACT_IMPORTING_PLAN.md says it stays two: NSF is the only
public API carrying a program officer, and USAspending is the only
cross-agency source with award data at all (Federal RePORTER, the one other
candidate, has been dead since March 2022). The seam exists so a third can
be added without touching the form.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from sam.integration.awards.base import AwardProvider, AwardRecord
from sam.integration.awards.cache import cached_lookup
from sam.integration.awards.nsf import NsfAwardProvider
from sam.integration.awards.usaspending import UsaSpendingProvider

logger = logging.getLogger(__name__)

#: Declaration order is fallback order within each tier.
_PROVIDERS: List[AwardProvider] = [
    NsfAwardProvider(),
    UsaSpendingProvider(),
]


def providers() -> List[AwardProvider]:
    """Every registered provider, in declaration order."""
    return list(_PROVIDERS)


def providers_for(source_name: Optional[str],
                  contract_number: Optional[str]) -> List[AwardProvider]:
    """Candidate providers, source-specific ones first.

    A provider that names a ``source`` knows that agency's own system and
    carries more fields, so it is always tried before a generic fallback
    that merely happens to cover the same award.
    """
    eligible = [p for p in _PROVIDERS
                if p.supports(source_name, contract_number)]
    return ([p for p in eligible if p.source is not None]
            + [p for p in eligible if p.source is None])


def resolve_award(source_name: Optional[str],
                  contract_number: Optional[str]) -> Optional[AwardRecord]:
    """Look *contract_number* up, returning the first provider that knows it.

    Returns:
        The record, or ``None`` when no provider can serve this source /
        number, or when every provider that could reports no such award.

    Raises:
        AwardSourceUnavailable: a provider was reached-for and failed. This
            propagates rather than degrading to ``None`` because the two
            mean different things to the operator — "NSF has no award
            1234567" versus "NSF is down, try again".
    """
    number = (contract_number or '').strip()
    if not number:
        return None

    for provider in providers_for(source_name, number):
        record = cached_lookup(provider.name, number,
                               lambda p=provider: p.fetch(number))
        if record is not None:
            return record
        logger.debug('award lookup: %s has no record for %r',
                     provider.name, number)
    return None
