"""Compare a stored contract against what the funding agency says.

The network half of ``sam-admin contracts --validate --check-sources``. It
lives here rather than in the CLI because it needs :func:`resolve_award`, and
mapping an agency person onto a SAM user is :func:`resolve_person`'s job.

This is the check that justifies the flag: nothing local surfaces a *stale*
value, only a missing or self-inconsistent one. SAM's Monitor measured stale
versus NSF in roughly one of three sampled contracts.

Four rules keep the output from being noise, and all four cost real findings if
dropped:

1. A field in :attr:`AwardRecord.unavailable_fields` is skipped -- USAspending
   has no program-officer concept, so a blank Monitor there is structural.
2. :func:`resolve_person` returning ``None`` is NOT a divergence: the agency's
   person is not a SAM user (314 of 387 monitors exist purely as contract
   contacts). Reported as a hint carrying the raw name/email.
3. Contract numbers are normalized before comparison. ``NsfAwardProvider``
   rebuilds the number as ``{divAbbr}-{award_id}``, so a raw compare flags
   every hand-entered ``OCE- 1419584`` as divergent.
4. ``url`` is NEVER compared. NSF emits the modern ``show-award?AWD_ID=`` form
   while ~1,895 legacy bulk-loaded rows carry the old ``showAward?...``;
   comparing them would flag almost every legacy row.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from sam.integration import awards
from sam.projects.contracts import normalize_contract_number

# Reached through the module object rather than ``from ... import resolve_award``
# so that ``patch('sam.integration.awards.resolve_award')`` takes effect here —
# the patch target the existing award tests already use
# (tests/unit/test_contract_create_modes.py).  A ``from`` import would bind the
# original function into this module's namespace at import time and silently
# ignore the patch.

#: Fields compared for every source.
SCALAR_FIELDS = ('contract_number', 'title', 'start_date', 'end_date',
                 'nsf_program')

#: Fields compared only where the provider supplies people.
PERSON_FIELDS = ('pi', 'monitor')


def _normalize_text(value: Optional[str]) -> Optional[str]:
    """Collapse whitespace and case for comparison (raw values are reported)."""
    if not value:
        return None
    return re.sub(r'\s+', ' ', str(value).strip()).casefold()


def _as_date(value):
    """Coerce a SAM naive ``datetime`` to a ``date`` for comparison."""
    return value.date() if hasattr(value, 'date') else value


def _diff(field: str, sam, source) -> Optional[Dict]:
    """A divergence entry, or ``None`` when the source has nothing to say.

    A field the provider left blank is never a divergence: providers differ in
    what they carry, and "NSF didn't tell us" is not "SAM is wrong".
    """
    if source is None:
        return None
    return None if sam == source else {'field': field, 'sam': sam, 'source': source}


def _compare_scalars(contract, record) -> List[Dict]:
    """Divergences for the non-person fields."""
    program = (contract.nsf_program.nsf_program_name
               if contract.nsf_program else None)
    sam_source = {
        'contract_number': normalize_contract_number(contract.contract_number),
        'title':           _normalize_text(contract.title),
        'start_date':      _as_date(contract.start_date),
        'end_date':        _as_date(contract.end_date),
        'nsf_program':     _normalize_text(program),
    }
    provider = {
        'contract_number': normalize_contract_number(record.contract_number),
        'title':           _normalize_text(record.title),
        'start_date':      _as_date(record.start_date),
        'end_date':        _as_date(record.end_date),
        'nsf_program':     _normalize_text(record.program_name),
    }
    # Report the raw stored/received values, compare the normalized ones.
    raw_sam = {
        'contract_number': contract.contract_number,
        'title':           contract.title,
        'start_date':      _as_date(contract.start_date),
        'end_date':        _as_date(contract.end_date),
        'nsf_program':     program,
    }
    raw_source = {
        'contract_number': record.contract_number,
        'title':           record.title,
        'start_date':      _as_date(record.start_date),
        'end_date':        _as_date(record.end_date),
        'nsf_program':     record.program_name,
    }

    divergences = []
    for field in SCALAR_FIELDS:
        if field in record.unavailable_fields:
            continue
        if _diff(field, sam_source[field], provider[field]) is not None:
            divergences.append({'field':  field,
                                'sam':    raw_sam[field],
                                'source': raw_source[field]})
    return divergences


#: Diverging on all three at once means the provider matched a *different*
#: award, not that SAM is stale.  See :func:`_looks_like_different_award`.
_MISMATCH_SIGNATURE = frozenset({'title', 'start_date', 'end_date'})


def _looks_like_different_award(divergences: List[Dict]) -> bool:
    """Whether these divergences say "wrong award" rather than "stale value".

    ``UsaSpendingProvider`` escalates to a *keyword* search when the exact-id
    lookup misses, and short internal numbers find something plausible-looking
    every time: SAM's ``014421`` (a DOD 4DWX contract) resolves to a 2009 award
    titled "MEALS".  ``AwardRecord`` carries no confidence signal, so the
    consumer has to notice.

    The rule is deliberately blunt — title *and* both dates all disagree — for
    the reason a tighter one would not be worth it: a genuine stale field
    (a reassigned program officer, a no-cost extension) moves one or two
    fields, never all three.  A record that disagrees on everything is not
    describing the same award, and reporting it field-by-field would tell an
    operator to overwrite a correct title with a stranger's.
    """
    return _MISMATCH_SIGNATURE <= {d['field'] for d in divergences}


def _compare_person(session, field: str, sam_user, person_ref, record):
    """``(divergence, hint)`` for one person field — at most one is non-None."""
    if field in record.unavailable_fields or not person_ref:
        return None, None

    resolved = awards.resolve_person(session, person_ref)
    if resolved is None:
        # Rule 2: the agency's person is not a SAM user.  Suggest, don't
        # impose — carry the raw label so an operator can search or create.
        return None, {'field':  field,
                      'source': person_ref.label,
                      'note':   'no matching SAM user'}

    if sam_user is not None and resolved.user_id == sam_user.user_id:
        return None, None

    return {'field':  field,
            'sam':    sam_user.username if sam_user else None,
            'source': resolved.username}, None


def compare_contract(session, contract, record=None) -> Dict:
    """
    Compare one contract against its funding source.

    Never raises for a source problem: :class:`AwardSourceUnavailable` becomes
    ``status='unavailable'`` so a dead API costs one "unchecked" contract
    instead of aborting a 368-contract run.  A dead API is not a data-hygiene
    finding.

    Args:
        session:  SQLAlchemy session (needed to map agency people onto users).
        contract: a ``Contract``, with ``contract_source`` loaded.
        record:   an already-fetched :class:`AwardRecord` to compare against,
                  skipping the lookup.  ``sam-search awards <number>`` has one
                  in hand and would otherwise fetch twice; the cache makes the
                  second call nearly free, but threading it through is clearer
                  than relying on that.  Passing ``None`` (the default) does
                  the lookup, which is the only path that can report
                  ``status='unavailable'``.

    Returns:
        ::

            {
                'status':      'ok' | 'no_record' | 'unavailable'
                               | 'suspect_match',
                'provenance':  'NSF Awards API' | None,
                'divergences': [{'field': 'monitor',
                                 'sam': 'buz', 'source': 'skennan'}, ...],
                'hints':       [{'field': 'monitor',
                                 'source': 'Sean Kennan <skennan@nsf.gov>',
                                 'note': 'no matching SAM user'}, ...],
            }

        ``status='no_record'`` means the agency was reached and has no such
        award — worth a look, but not the same as SAM being wrong.
        ``status='suspect_match'`` means the provider returned an award that
        does not appear to be this one; it carries a ``source_summary`` for
        eyeballing and deliberately reports **no** divergences.  Empty
        ``divergences`` with ``status='ok'`` means SAM agrees with the source.
    """
    if record is None:
        source_name = (contract.contract_source.contract_source
                       if contract.contract_source else None)
        try:
            record = awards.resolve_award(source_name,
                                          contract.contract_number)
        except awards.AwardSourceUnavailable as exc:
            return {'status': 'unavailable', 'provenance': None,
                    'reason': str(exc), 'divergences': [], 'hints': []}

    if record is None:
        return {'status': 'no_record', 'provenance': None,
                'divergences': [], 'hints': []}

    divergences = _compare_scalars(contract, record)

    if _looks_like_different_award(divergences):
        return {'status': 'suspect_match', 'provenance': record.provenance,
                'divergences': [], 'hints': [],
                'source_summary': {'contract_number': record.contract_number,
                                   'title': record.title,
                                   'start_date': _as_date(record.start_date),
                                   'end_date': _as_date(record.end_date)}}

    hints: List[Dict] = []

    sam_people = {'pi': contract.principal_investigator,
                  'monitor': contract.contract_monitor}
    source_people = {'pi': record.pi, 'monitor': record.monitor}
    for field in PERSON_FIELDS:
        divergence, hint = _compare_person(session, field, sam_people[field],
                                           source_people[field], record)
        if divergence:
            divergences.append(divergence)
        if hint:
            hints.append(hint)

    # Rule 4: never a divergence, only a fill for the rows that have no URL.
    if record.url and not (contract.url or '').strip():
        hints.append({'field': 'url', 'source': record.url,
                      'note': 'source has a URL, SAM has none'})

    return {'status': 'ok', 'provenance': record.provenance,
            'divergences': divergences, 'hints': hints}
