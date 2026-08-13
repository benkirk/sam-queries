"""Provider selection: source-specific first, then generic.

Two providers today, and the survey behind
docs/plans/implemented/CONTRACT_IMPORTING_PLAN.md says it stays two: NSF is the only
public API carrying a program officer, and USAspending is the only
cross-agency source with award data at all (Federal RePORTER, the one other
candidate, has been dead since March 2022). The seam exists so a third can
be added without touching the form.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sam.integration.awards.base import (
    AwardProvider, AwardRecord, AwardSourceUnavailable,
)
from sam.integration.awards.cache import cached_lookup, cached_search
from sam.integration.awards.client import AwardHttpClient
from sam.integration.awards.nsf import NsfAwardProvider
from sam.integration.awards.usaspending import UsaSpendingProvider

logger = logging.getLogger(__name__)


def build_providers(
        client: Optional[AwardHttpClient] = None) -> List[AwardProvider]:
    """Fresh provider instances, optionally sharing one HTTP client.

    The module-level providers are built with default clients, whose timeout
    is deliberately short (``AwardHttpClient.DEFAULT_TIMEOUT`` is 10 s because
    the webapp path runs inside an htmx round-trip). A CLI has no worker to
    hold and wants longer, so it builds its own set rather than raising the
    default and slowing the webapp's failure path::

        providers = build_providers(AwardHttpClient(timeout=30))
        records, errors = search_awards(q, providers=providers)

    Declaration order is fallback order within each tier.
    """
    return [
        NsfAwardProvider(client),
        UsaSpendingProvider(client),
    ]


_PROVIDERS: List[AwardProvider] = build_providers()


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


def search_providers(sources: Optional[Iterable[str]] = None,
                     pool: Optional[List[AwardProvider]] = None
                     ) -> List[AwardProvider]:
    """Which providers a free-text search fans out to.

    ``supports()`` cannot be reused here — it is number-scoped and a search
    has no number — so the tiering rule is restated rather than shared:
    naming a source that a provider is specific to narrows to that provider,
    and any other source falls back to the generics. That mirrors
    ``UsaSpendingProvider.supports()`` returning ``False`` for NSF: asking
    USAspending about an NSF award adds nothing NSF's own API lacks.
    """
    candidates = list(pool) if pool is not None else list(_PROVIDERS)
    if not sources:
        return candidates

    names = {str(s).strip().upper() for s in sources if s}
    if not names:
        return candidates

    specific = [p for p in candidates if (p.source or '').upper() in names]
    return specific or [p for p in candidates if p.source is None]


def search_awards(query: str, limit: int = 10,
                  sources: Optional[Iterable[str]] = None,
                  providers: Optional[List[AwardProvider]] = None
                  ) -> Tuple[List[AwardRecord], List[Dict[str, Any]]]:
    """Free-text search across every eligible provider.

    Returns ``(records, errors)``. **One dead provider must not kill the
    search**: an :class:`AwardSourceUnavailable` from NSF becomes an entry in
    *errors* while USAspending's hits still come back. This is the same stance
    ``--check-sources`` takes, and the reason ``resolve_award``'s
    raise-vs-``None`` split exists — "NSF has no award X" and "NSF is down"
    are different answers and must never be conflated.

    Serial, not concurrent: measured 2026-07-30 the whole fan-out is ~1.4 s
    over three requests, which is fine behind an explicit button. Revisit if a
    third provider ever lands.

    Args:
        query: the free-text term.
        limit: cap **per provider**, so the composite can return more.
        sources: restrict to these ``contract_source`` names (see
            :func:`search_providers`).
        providers: override the provider pool — used by the CLI to inject a
            longer-timeout client (see :func:`build_providers`).
    """
    term = (query or '').strip()
    if not term:
        return [], []

    records: List[AwardRecord] = []
    errors: List[Dict[str, Any]] = []

    for provider in search_providers(sources, pool=providers):
        try:
            found = cached_search(provider.name, term, limit,
                                  lambda p=provider: p.search(term, limit))
        except AwardSourceUnavailable as exc:
            logger.warning('award search: %s unavailable: %s',
                           provider.name, exc)
            errors.append({'provenance': provider.name, 'reason': str(exc)})
            continue
        records.extend(found or [])

    return records, errors
