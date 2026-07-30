"""USAspending provider — the cross-agency fallback.

Covers every federal agency SAM contracts with outside NSF (DOE, NASA,
DOD, AFOSR, NOAA, DOI, DOT, …) but supplies strictly less: dates, an award
URL, a CFDA program string, and a description that is *not* a title.

**No PI and no program officer, ever.** Those are pre-award administrative
attributes held in each agency's own grants-management system; FFATA/DATA
Act, which is what feeds USAspending, does not collect them. Hence
``unavailable_fields = {'pi', 'monitor'}`` rather than "we happened to get
nothing" — the form states the limitation instead of showing blanks.

Three traps, each verified empirically and each a silent-zero-hit source
if missed:

1. **Award ids are punctuation-stripped, inconsistently.**
   ``DE-SC0012671`` -> ``DESC0012671``; ``DE-FC02-97ER62402`` ->
   ``DEFC0297ER62402``; ``FA9550-14-C-0035`` -> ``FA955014C0035``; but
   ``80NSSC19K0855`` is unchanged. Submit both spellings as a candidate set.
2. **``award_type_codes`` must come from a single group.** Mixing
   assistance codes (``02``-``05``) with contract codes (``A``-``D``)
   errors or returns nothing, so the search runs twice.
3. **Suffixed variants.** ``NA18NWS4620043`` misses on exact match, but a
   keyword search finds ``NA18NWS4620043B``.

Coverage begins at FY2008 — the legacy NASA form ``NNG04EA00C`` (2004)
genuinely has no record, which is a *not found*, not an error.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, List, Mapping, Optional, Sequence

from sam.integration.awards.base import AwardProvider, AwardRecord
from sam.integration.awards.client import AwardHttpClient

logger = logging.getLogger(__name__)

API_BASE = 'https://api.usaspending.gov/api/v2'
SEARCH_URL = f'{API_BASE}/search/spending_by_award/'
DETAIL_URL = f'{API_BASE}/awards/{{generated_internal_id}}/'
AWARD_PAGE_URL = 'https://www.usaspending.gov/award/{generated_internal_id}/'

#: The two mutually exclusive award-type groups (trap 2).
ASSISTANCE_TYPE_CODES = ['02', '03', '04', '05']
CONTRACT_TYPE_CODES = ['A', 'B', 'C', 'D']

SEARCH_FIELDS = [
    'Award ID', 'Recipient Name', 'Start Date', 'End Date',
    'Awarding Agency', 'generated_internal_id',
]

#: ``contract.title`` is ``varchar(255) NOT NULL``; USAspending's
#: ``description`` is ALL-CAPS FPDS text and occasionally a whole abstract.
TITLE_MAX_LENGTH = 255

_NON_ALNUM = re.compile(r'[^A-Za-z0-9]')


def award_id_candidates(contract_number: str) -> List[str]:
    """The spellings to try for *contract_number* (trap 1).

    Returns the number as typed plus its alphanumeric-only form, in that
    order, de-duplicated. Both are submitted in one ``award_ids`` filter —
    the API ORs them, so this costs no extra request.
    """
    raw = (contract_number or '').strip()
    if not raw:
        return []
    stripped = _NON_ALNUM.sub('', raw).upper()
    candidates = [raw]
    if stripped and stripped != raw:
        candidates.append(stripped)
    return candidates


def _parse_date(raw: Optional[str]):
    """USAspending serialises dates as ``YYYY-MM-DD``."""
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw).strip(), '%Y-%m-%d').date()
    except ValueError:
        logger.warning('USAspending: unparseable date %r', raw)
        return None


class UsaSpendingProvider(AwardProvider):
    """Generic federal fallback for any non-NSF ``contract_source``."""

    name = 'USAspending'
    source = None   # generic — the registry tries it after source-specific

    def __init__(self, client: Optional[AwardHttpClient] = None) -> None:
        self.client = client or AwardHttpClient()

    def supports(self, source_name: Optional[str],
                 contract_number: Optional[str]) -> bool:
        # NSF is served in full by its own API; there is nothing this
        # provider could add for it.
        if (source_name or '').strip().upper() == 'NSF':
            return False
        return bool((contract_number or '').strip())

    def fetch(self, contract_number: str) -> Optional[AwardRecord]:
        hit = self._resolve(contract_number)
        if hit is None:
            return None

        internal_id = hit.get('generated_internal_id')
        if not internal_id:
            return None

        detail = self.client.get_json(
            DETAIL_URL.format(generated_internal_id=internal_id)) or {}
        return self._to_record(hit, detail, internal_id)

    # ── search ──────────────────────────────────────────────────────────

    def _resolve(self, contract_number: str) -> Optional[Mapping[str, Any]]:
        """Find the award row, escalating through the three lookup shapes."""
        candidates = award_id_candidates(contract_number)
        if not candidates:
            return None

        # Exact id match first, once per type group (trap 2).
        for codes in (ASSISTANCE_TYPE_CODES, CONTRACT_TYPE_CODES):
            hit = self._search({'award_ids': candidates,
                                'award_type_codes': codes})
            if hit is not None:
                return hit

        # Suffixed variants (trap 3): keyword search on the bare number.
        for codes in (ASSISTANCE_TYPE_CODES, CONTRACT_TYPE_CODES):
            hit = self._search({'keywords': [candidates[-1]],
                                'award_type_codes': codes})
            if hit is not None:
                return hit
        return None

    def _search(self, filters: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
        payload = self.client.post_json(SEARCH_URL, {
            'filters': dict(filters),
            'fields': SEARCH_FIELDS,
            'limit': 5,
        })
        results: Sequence[Mapping[str, Any]] = (payload or {}).get('results') or []
        return results[0] if results else None

    # ── mapping ─────────────────────────────────────────────────────────

    @staticmethod
    def _to_record(hit: Mapping[str, Any], detail: Mapping[str, Any],
                   internal_id: str) -> AwardRecord:
        pop = detail.get('period_of_performance') or {}
        start = _parse_date(pop.get('start_date') or hit.get('Start Date'))
        end = _parse_date(pop.get('end_date') or hit.get('End Date'))

        description = (detail.get('description') or '').strip()
        title = description[:TITLE_MAX_LENGTH] or None

        program_name = None
        cfda = detail.get('cfda_info') or []
        if cfda:
            number = (cfda[0].get('cfda_number') or '').strip()
            cfda_title = (cfda[0].get('cfda_title') or '').strip()
            program_name = ' '.join(p for p in (number, cfda_title) if p) or None

        return AwardRecord(
            provenance=UsaSpendingProvider.name,
            # USAspending reports the punctuation-stripped id; keeping it
            # would rewrite the operator's number to a form no other system
            # uses, so the number field is left for them to own.
            contract_number=None,
            title=title,
            start_date=start,
            end_date=end,
            url=AWARD_PAGE_URL.format(generated_internal_id=internal_id),
            program_name=program_name,
            pi=None,
            monitor=None,
            unavailable_fields=frozenset({'pi', 'monitor'}),
        )
