"""Which contracts to create, ranked by the XRAS pushes their absence blocks.

A pivot over the push-readiness snapshot (never a second scan): the failing verdicts
carry ``resolved.unresolved_grants`` — the structured channel ``plan_contracts``
records, so nothing here parses a 422 string. Each number is re-checked against the
current ``contract`` table with the handler's own query, so a row created since the
sweep drops out the way a mapped org drops out of the mnemonic report. Read-only.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

#: Cap on request numbers echoed per target.
_SAMPLE_CAP = 10

#: ``fundingAgency`` spellings that mean NSF — the only source with a lookup provider.
_NSF_AGENCIES = {'NSF', 'NATIONAL SCIENCE FOUNDATION'}


def suggested_source(agency: Optional[str], award_like: bool) -> Optional[str]:
    """``'NSF'`` when the wire names NSF and the number is award-shaped; else None."""
    if award_like and (agency or '').strip().upper() in _NSF_AGENCIES:
        return 'NSF'
    return None


def _recheck(session, number: str, core: str) -> str:
    """``resolved`` | ``missing`` | ``ambiguous`` against the table as it is now."""
    from sam.projects.contracts import Contract
    from sam.xras.extractors import contract_candidates
    if Contract.get_by_number(session, number) is not None:
        return 'resolved'
    candidates = contract_candidates(session, core)
    if not candidates:
        return 'missing'
    return 'resolved' if len(candidates) == 1 else 'ambiguous'


def contract_unblock_report(session, snapshot) -> dict:
    """Rank the contract rows whose creation would unblock the most failing XRAS pushes."""
    from sam.projects.contracts import normalize_contract_number
    from sam.xras.extractors import has_core_number

    rows = (snapshot or {}).get('rows') or [] if snapshot else []
    targets: Dict[str, dict] = {}
    variants: Dict[str, dict] = {}
    rechecked: Dict[str, str] = {}
    actions_seen = 0

    for entry in rows:
        if not isinstance(entry, dict) or entry.get('preflight_rollup') != 'failed':
            continue
        request_number = entry.get('request_number')
        pi_username = (entry.get('pi') or {}).get('username')
        activity = entry.get('activity_date')
        for action in entry.get('actions') or ():
            pf = action.get('preflight') if isinstance(action, dict) else None
            if not pf or pf.get('status') != 'failed':
                continue
            grants = (pf.get('resolved') or {}).get('unresolved_grants') or ()
            if not grants:
                continue
            actions_seen += 1
            for grant in grants:
                number = str(grant.get('number') or '').strip()
                if not number:
                    continue
                key = normalize_contract_number(number) or number
                if key not in rechecked:
                    rechecked[key] = _recheck(session, number, grant.get('core') or number)
                state = rechecked[key]
                if state == 'resolved':
                    continue
                pool = targets if state == 'missing' else variants
                bucket = pool.get(key)
                if bucket is None:
                    award_like = has_core_number(number)
                    bucket = pool[key] = {
                        'number': number, 'core': grant.get('core'),
                        'award_like': award_like, 'agency': grant.get('agency'),
                        'suggested_source': suggested_source(grant.get('agency'), award_like),
                        'title': grant.get('title'), 'pi_name': grant.get('pi_name'),
                        'begin_date': grant.get('begin_date'), 'end_date': grant.get('end_date'),
                        'candidates': list(grant.get('candidates') or ()),
                        'unblock_count': 0, 'sample': [], 'pis': [],
                        'oldest_activity': None,
                    }
                bucket['unblock_count'] += 1
                if request_number and request_number not in bucket['sample'] \
                        and len(bucket['sample']) < _SAMPLE_CAP:
                    bucket['sample'].append(request_number)
                if pi_username and pi_username not in bucket['pis']:
                    bucket['pis'].append(pi_username)
                if activity and (bucket['oldest_activity'] is None
                                 or str(activity) < str(bucket['oldest_activity'])):
                    bucket['oldest_activity'] = activity

    def _ranked(pool):
        return sorted(pool.values(), key=lambda t: (-t['unblock_count'], t['number']))

    return {
        'kind': 'xras_contract_report',
        'generated_at': (snapshot or {}).get('generated_at') if snapshot else None,
        'actions_seen': actions_seen,
        'targets': _ranked(targets),
        'variants': _ranked(variants),
    }
