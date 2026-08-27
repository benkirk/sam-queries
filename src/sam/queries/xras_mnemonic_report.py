"""Which organizations to link, ranked by the XRAS pushes their missing mnemonic blocks.

A pivot over the push-readiness snapshot (never a second scan): the failing verdicts already
name the mnemonic 422, so this resolves each failing PI's org/institution — with the SAME
`_best_*` helpers the ingest resolver uses — confirms it is still unmapped against the current
DB, and ranks by how many actions cite it. The highest-leverage XRAS data fix (playbook: 24%
of legacy failures; 153 of 171 active orgs unlinked). Read-only, no network.

WARNING: org resolution is at the leaf org (`_best_organization`), matching the resolver's
org-fallback arm and the Organizations card's own `org_to_mnemonic` derivation exactly. The
resolver's NCAR-lab arm walks to a lab-level org instead; for those internal opportunities the
name here is the PI's leaf org, still the right thing to link in the common case.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

FAMILY_ORGANIZATION = 'organization'
FAMILY_INSTITUTION = 'institution'

#: Cap on request numbers echoed per target.
_SAMPLE_CAP = 10


def _mnemonic_family(message: str) -> Optional[str]:
    """Which mnemonic family a 422 string is, matched on the legacy sentence as a prefix.

    The named detail after the colon is never parsed: the target comes from the
    DB in `_resolve_target`, so the message only says which family to look in.
    """
    from sam.xras.errors import MNEMONIC_EXTERNAL_PREFIX, MNEMONIC_INTERNAL_PREFIX
    if message.startswith(MNEMONIC_INTERNAL_PREFIX):
        return FAMILY_ORGANIZATION
    if message.startswith(MNEMONIC_EXTERNAL_PREFIX):
        return FAMILY_INSTITUTION
    return None


def _resolve_target(user, family: str, lookup: dict) -> Tuple[str, Optional[str], Optional[str]]:
    """``(status, name, prefill)`` — status is unmapped | mapped | no_affiliation.

    ``mapped`` means an affiliation exists AND already resolves to a code (fixed since the
    sweep) — dropped from the report. ``no_affiliation`` means the PI has no current active
    org/institution to link at all (the frozen ``user_organization`` cohort).
    """
    from sam.core.organizations import MnemonicCode

    if user is None:
        return 'no_affiliation', None, None
    if family == FAMILY_ORGANIZATION:
        from sam.xras.extractors import _best_organization
        org = _best_organization(user)
        if org is None:
            return 'no_affiliation', None, None
        code = MnemonicCode.resolve_for_organization(org, lookup)
        return ('mapped' if code else 'unmapped'), org.name, org.name
    from sam.xras.extractors import _best_institution
    inst = _best_institution(user)
    if inst is None:
        return 'no_affiliation', None, None
    code = MnemonicCode.resolve_for_institution(inst, lookup)
    prefill = f'{inst.name}, {inst.city}' if getattr(inst, 'city', None) else inst.name
    return ('mapped' if code else 'unmapped'), inst.name, prefill


def mnemonic_unblock_report(session, snapshot) -> dict:
    """Rank the org/institution links that would unblock the most failing XRAS pushes."""
    from sam.core.organizations import MnemonicCode
    from sam.core.users import User

    rows = (snapshot or {}).get('rows') or [] if snapshot else []
    lookup = MnemonicCode.build_lookup(session)

    targets: Dict[Tuple[str, str], dict] = {}
    unresolved: List[dict] = []
    users: Dict[str, Any] = {}
    actions_seen = 0

    for entry in rows:
        if not isinstance(entry, dict) or entry.get('preflight_rollup') != 'failed':
            continue
        pi_username = (entry.get('pi') or {}).get('username')
        request_number = entry.get('request_number')
        for action in entry.get('actions') or ():
            pf = action.get('preflight') if isinstance(action, dict) else None
            if not pf or pf.get('status') != 'failed':
                continue
            families = {f for m in (pf.get('messages') or ())
                        if (f := _mnemonic_family(m))}
            if not families:
                continue
            actions_seen += 1

            if pi_username and pi_username not in users:
                users[pi_username] = User.get_by_username(session, pi_username)
            user = users.get(pi_username)

            statuses = set()
            for family in families:
                status, name, prefill = _resolve_target(user, family, lookup)
                statuses.add(status)
                if status != 'unmapped':
                    continue
                bucket = targets.setdefault(
                    (family, name),
                    {'family': family, 'name': name, 'prefill': prefill,
                     'unblock_count': 0, 'sample': []})
                bucket['unblock_count'] += 1
                if request_number and request_number not in bucket['sample'] \
                        and len(bucket['sample']) < _SAMPLE_CAP:
                    bucket['sample'].append(request_number)
            # An action goes to `unresolved` ONLY when nothing is linkable and its
            # affiliation is genuinely absent — a `mapped` result is a between-sweep
            # fix, not work, and must not surface as "no affiliation".
            if 'unmapped' not in statuses and statuses == {'no_affiliation'}:
                unresolved.append({'request_number': request_number,
                                   'pi': pi_username, 'families': sorted(families)})

    ranked = sorted(targets.values(),
                    key=lambda t: (-t['unblock_count'], t['name'] or ''))
    return {
        'kind': 'xras_mnemonic_report',
        'generated_at': (snapshot or {}).get('generated_at') if snapshot else None,
        'actions_seen': actions_seen,
        'targets': ranked,
        'unresolved': unresolved,
    }
