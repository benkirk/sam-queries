"""NSF Awards API provider.

``https://api.nsf.gov/services/v1/awards/{id}.json`` — no key, no auth.
97 % of SAM's contracts are NSF, and this API maps close to 1:1 onto our
``contract`` columns: round-tripping ``AGS-1852977`` reproduces our stored
row including the "Atmoshperic" typo in the title, so the existing data was
originally sourced from these awards.

It is also the *only* surveyed source that carries a program officer
(``poName``/``poEmail``) — FFATA/DATA Act, which feeds USAspending, does
not collect one. That is why Monitor prefill exists for NSF and nowhere
else.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, List, Mapping, Optional

from sam.integration.awards.base import (
    AwardProvider, AwardRecord, PersonRef,
)
from sam.integration.awards.client import AwardHttpClient

logger = logging.getLogger(__name__)

AWARD_URL = 'https://api.nsf.gov/services/v1/awards/{award_id}.json'

#: Free-text search over the same corpus. Measured 2026-07-30: 0.58 s, and it
#: returns *exactly* the 62 keys ``/awards/{id}.json`` does — verified by
#: diffing the two key sets, which came back empty in both directions. NSF
#: also ignores ``printFields`` and hands back the full record regardless.
#: So a search hit carries everything :meth:`NsfAwardProvider._to_record`
#: reads and there is no per-row detail fetch and no second mapper.
SEARCH_URL = 'https://api.nsf.gov/services/v1/awards.json'

#: The modern award-search URL. SAM's 1,895 legacy bulk-loaded rows carry a
#: scheme-less ``showAward?…&HistoricalAwards=false`` instead, but every
#: contract entered by hand in recent years uses this form, so new rows
#: follow the humans rather than the bulk load.
AWARD_PAGE_URL = 'https://www.nsf.gov/awardsearch/show-award?AWD_ID={award_id}'


def nsf_award_id(contract_number: Optional[str]) -> Optional[str]:
    """Return the numeric NSF award id for *contract_number*, or ``None``.

    SAM stores NSF numbers two ways, plus whitespace noise from manual
    entry::

        "2317820"       -> "2317820"    bare
        "AGS-1852977"   -> "1852977"    division-prefixed
        "OCE- 1419584"  -> "1419584"    stray space after the hyphen
        "AGS - 2410913" -> "2410913"    stray spaces both sides

    Anything whose final hyphen-segment is not all digits returns ``None``
    (e.g. ``OCE-UCSC0001``, ``NCAR0880``, or a DOE number filed under
    ``contract_source='NSF'``).

    This deliberately duplicates ``sql/queries/nsf_awards.py:nsf_award_id``
    rather than importing it: ``sql/`` is a standalone script tree with no
    package, and coupling the webapp to it would make either one hard to
    change. The rule is three lines; the duplication is cheaper than the
    dependency.
    """
    if not contract_number:
        return None
    tail = str(contract_number).strip().rsplit('-', 1)[-1].strip()
    return tail if tail.isdigit() else None


def _parse_date(raw: Optional[str]):
    """NSF serialises dates as ``MM/DD/YYYY``. Return a ``date`` or ``None``."""
    if not raw:
        return None
    try:
        return datetime.strptime(raw.strip(), '%m/%d/%Y').date()
    except (ValueError, AttributeError):
        logger.warning('NSF: unparseable date %r', raw)
        return None


def _clean(value: Any) -> Optional[str]:
    """Trim a string field; empty becomes ``None``."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


class NsfAwardProvider(AwardProvider):
    """Full-fidelity provider for ``contract_source = 'NSF'``."""

    name = 'NSF Awards API'
    source = 'NSF'

    def __init__(self, client: Optional[AwardHttpClient] = None) -> None:
        self.client = client or AwardHttpClient()

    def supports(self, source_name: Optional[str],
                 contract_number: Optional[str]) -> bool:
        if (source_name or '').strip().upper() != 'NSF':
            return False
        return nsf_award_id(contract_number) is not None

    def fetch(self, contract_number: str) -> Optional[AwardRecord]:
        award_id = nsf_award_id(contract_number)
        if award_id is None:
            return None

        payload = self.client.get_json(AWARD_URL.format(award_id=award_id))
        if not payload:
            return None

        awards = (payload.get('response') or {}).get('award') or []
        if not awards:
            return None
        return self._to_record(awards[0], award_id)

    def search(self, query: str, limit: int = 10) -> List[AwardRecord]:
        """Keyword search, one GET, mapped through the *same* ``_to_record``.

        Reusing the fetch mapper is safe rather than merely convenient: the
        search and detail endpoints return identical field sets (see
        :data:`SEARCH_URL`), so there is nothing here that can drift away
        from ``fetch``.
        """
        term = (query or '').strip()
        if not term:
            return []

        payload = self.client.get_json(
            SEARCH_URL, params={'keyword': term, 'rpp': limit})
        if not payload:
            return []

        awards = (payload.get('response') or {}).get('award') or []
        records = []
        for award in awards[:limit]:
            award_id = _clean(award.get('id'))
            if not award_id:
                continue
            records.append(self._to_record(award, award_id))
        return records

    # ── mapping ─────────────────────────────────────────────────────────

    @staticmethod
    def _to_record(award: Mapping[str, Any], award_id: str) -> AwardRecord:
        # NSF returns the division abbreviation separately; SAM stores the
        # two joined ("AGS-1852977") for 2,109 of 2,162 NSF contracts, so
        # rebuilding it also normalises the operator's stray whitespace.
        div = _clean(award.get('divAbbr'))
        number = f'{div}-{award_id}' if div else award_id

        pi_name = ' '.join(
            part for part in (_clean(award.get('piFirstName')),
                              _clean(award.get('piLastName')))
            if part
        ) or None
        pi = PersonRef(name=pi_name, email=_clean(award.get('piEmail')))
        monitor = PersonRef(name=_clean(award.get('poName')),
                            email=_clean(award.get('poEmail')))

        return AwardRecord(
            provenance=NsfAwardProvider.name,
            contract_number=number,
            title=_clean(award.get('title')),
            start_date=_parse_date(award.get('startDate')),
            end_date=_parse_date(award.get('expDate')),
            url=AWARD_PAGE_URL.format(award_id=award_id),
            # fundProgramName, NOT primaryProgram: the latter is a funding
            # *account* string ("01002324DB NSF RESEARCH & RELATED ACTIVIT")
            # and 66 contracts already point at nsf_program rows created by
            # someone pasting it in. Do not repeat that.
            program_name=_clean(award.get('fundProgramName')),
            pi=pi if pi else None,
            monitor=monitor if monitor else None,
            unavailable_fields=frozenset(),
        )
