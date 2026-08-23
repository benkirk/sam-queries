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

#: ``Description`` **is** available on the search endpoint (verified
#: 2026-07-30) even though ``_to_record`` reads it from the detail payload —
#: its absence here previously made it look detail-only. Requesting it is
#: what lets :meth:`UsaSpendingProvider.search` build a title without a
#: per-row detail fetch.
SEARCH_FIELDS = [
    'Award ID', 'Description', 'Recipient Name', 'Start Date', 'End Date',
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
    """USAspending serializes dates as ``YYYY-MM-DD``."""
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

    # search

    def _resolve(self, contract_number: str) -> Optional[Mapping[str, Any]]:
        """Find the award row, escalating through the three lookup shapes."""
        candidates = award_id_candidates(contract_number)
        if not candidates:
            return None

        # Exact id match first, once per type group (trap 2).
        for codes in (ASSISTANCE_TYPE_CODES, CONTRACT_TYPE_CODES):
            hits = self._search({'award_ids': candidates,
                                 'award_type_codes': codes})
            if hits:
                return hits[0]

        # Suffixed variants (trap 3): keyword search on the bare number.
        for codes in (ASSISTANCE_TYPE_CODES, CONTRACT_TYPE_CODES):
            hits = self._search({'keywords': [candidates[-1]],
                                 'award_type_codes': codes})
            if hits:
                return hits[0]
        return None

    def _search(self, filters: Mapping[str, Any],
                limit: int = 5) -> List[Mapping[str, Any]]:
        """POST one search and return **every** result.

        ``_resolve`` wants only the best hit and takes ``[0]`` itself; free-text
        search wants them all. Returning the list keeps the request count the
        same either way — this used to discard four rows it had already paid
        for.
        """
        payload = self.client.post_json(SEARCH_URL, {
            'filters': dict(filters),
            'fields': SEARCH_FIELDS,
            'limit': limit,
        })
        results: Sequence[Mapping[str, Any]] = (payload or {}).get('results') or []
        return list(results)

    def search(self, query: str, limit: int = 10) -> List[AwardRecord]:
        """Keyword search across both award-type groups.

        Two POSTs, not one — mixing the groups errors or returns nothing
        (trap 2). Results are concatenated in group order and capped.
        """
        term = (query or '').strip()
        if not term:
            return []

        records: List[AwardRecord] = []
        for codes in (ASSISTANCE_TYPE_CODES, CONTRACT_TYPE_CODES):
            if len(records) >= limit:
                break
            hits = self._search({'keywords': [term],
                                 'award_type_codes': codes},
                                limit=limit)
            for hit in hits:
                if len(records) >= limit:
                    break
                record = self._to_search_record(hit)
                if record is not None:
                    records.append(record)
        return records

    # mapping

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
            #
            # This differs from `_to_search_record`, which DOES set it, and
            # the difference is deliberate — see that method. The policy is
            # "don't overwrite what the operator owns", not "never emit a
            # number". Do not "fix" the two into agreement.
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

    @staticmethod
    def _to_search_record(hit: Mapping[str, Any]) -> Optional[AwardRecord]:
        """Map one free-text search row. A **summary**, not a full record.

        Two differences from ``_to_record``, both forced by the endpoint:

        * ``program_name`` is ``None``. It comes from ``cfda_info``, which is
          detail-only, so it cannot be recovered without a per-row fetch.
          Chaining a chosen hit through ``fetch`` is what supplies it.
        * ``contract_number`` **is** set, from ``Award ID`` — the opposite of
          ``_to_record``'s deliberate ``None``. The situations differ: there,
          an operator has already typed a number and it must not be rewritten
          to USAspending's punctuation-stripped spelling; here there is no
          operator input yet, and without a number a search hit gives the form
          nothing to seed. Same policy, opposite outcome. Do not unify them.
        """
        internal_id = (hit.get('generated_internal_id') or '').strip()
        if not internal_id:
            return None

        description = (hit.get('Description') or '').strip()
        number = (hit.get('Award ID') or '').strip() or None

        return AwardRecord(
            provenance=UsaSpendingProvider.name,
            contract_number=number,
            title=description[:TITLE_MAX_LENGTH] or None,
            start_date=_parse_date(hit.get('Start Date')),
            end_date=_parse_date(hit.get('End Date')),
            url=AWARD_PAGE_URL.format(generated_internal_id=internal_id),
            program_name=None,
            pi=None,
            monitor=None,
            unavailable_fields=frozenset({'pi', 'monitor'}),
        )
