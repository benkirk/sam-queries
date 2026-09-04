"""Operator-facing inventory of mnemonic codes: what each resolves to, and its usage.

The console's insight layer. For every `mnemonic_code` row it reports the live
org/institution its description resolves to (the reverse of `build_lookup`), an
`orphaned` flag (an active code nothing live resolves to — the stale-owner
signal), and the per-facility high-water mark of projcodes it has minted (from
`project_code.digits`). Read-only.

NOTE: resolution here is at the leaf org/institution, matching the Organizations
card's `org_to_mnemonic` derivation and `xras_mnemonic_report` — NOT the XRAS
push path's NCAR-lab arm.
"""

from __future__ import annotations

import re
from string import ascii_uppercase
from typing import Any, Dict, List, Optional

# Dropped when initialing a name; single letters are KEPT ("U OF ALASKA
# FAIRBANKS" -> UAF), so the list is words, not characters.
_STOPWORDS = frozenset({'of', 'the', 'and', 'at', 'for', 'in', 'on', 'to',
                        'a', 'an', '&', 'de', 'du', 'di', 'la', 'le'})


def mnemonic_inventory(session, *, active_only: bool = True) -> List[Dict[str, Any]]:
    """Rows of {code, description, active, links_to, orphaned, usage, minted_total}."""
    from sam.core.organizations import Institution, MnemonicCode, Organization
    from sam.resources.facilities import Facility, ProjectCode

    lookup = MnemonicCode.build_lookup(session)  # active codes only

    # Reverse index: code string -> the live orgs/institutions that resolve to it.
    reverse: Dict[str, List[dict]] = {}
    for org_id, name in session.query(Organization.organization_id, Organization.name)\
            .filter(Organization.is_active):
        code = MnemonicCode.resolve_for_organization(_Named(name), lookup)
        if code:
            reverse.setdefault(code, []).append(
                {'kind': 'organization', 'id': org_id, 'name': name})
    for inst_id, name, city in session.query(
            Institution.institution_id, Institution.name, Institution.city)\
            .filter(Institution.deleted.isnot(True)):
        code = MnemonicCode.resolve_for_institution(_Named(name, city), lookup)
        if code:
            reverse.setdefault(code, []).append(
                {'kind': 'institution', 'id': inst_id, 'name': name})

    # Per-facility high-water mark (digits is the LAST issued number, so an
    # upper bound on projects minted for the pair).
    usage: Dict[int, List[dict]] = {}
    for mc_id, fac_code, fac_id, digits in session.query(
            ProjectCode.mnemonic_code_id, Facility.code, Facility.facility_id,
            ProjectCode.digits).join(
            Facility, Facility.facility_id == ProjectCode.facility_id):
        usage.setdefault(mc_id, []).append(
            {'facility': fac_code, 'facility_id': fac_id, 'last': digits})

    q = session.query(MnemonicCode).order_by(MnemonicCode.code)
    if active_only:
        q = q.filter(MnemonicCode.is_active)

    rows = []
    for mc in q:
        links = reverse.get(mc.code, [])
        mc_usage = usage.get(mc.mnemonic_code_id, [])
        rows.append({
            'id': mc.mnemonic_code_id,
            'code': mc.code,
            'description': mc.description,
            'active': bool(mc.active),
            'links_to': links,
            'orphaned': bool(mc.active) and not links,
            'usage': sorted(mc_usage, key=lambda u: u['facility'] or ''),
            'minted_total': sum(u['last'] for u in mc_usage),
        })
    return rows


class _Named:
    """Minimal stand-in exposing .name/.city to the resolve_* helpers."""
    __slots__ = ('name', 'city')

    def __init__(self, name, city=None):
        self.name = name
        self.city = city


def _description_claims(session) -> Dict[str, str]:
    """casefold(description) -> code, over ALL mnemonics (description is UNIQUE,
    active or not) — so the picker can flag an entity a code already claims."""
    from sam.core.organizations import MnemonicCode
    return {mc.description.casefold(): mc.code for mc in session.query(MnemonicCode)}


def claiming_code(session, description, *, exclude_code=None) -> Optional[str]:
    """The code whose description already equals `description` (not `exclude_code`)."""
    d = (description or '').strip()
    if not d:
        return None
    code = _description_claims(session).get(d.casefold())
    return code if code and code != exclude_code else None


def search_targets(session, q, *, limit: int = 15,
                   exclude_code=None) -> List[Dict[str, Any]]:
    """Active orgs + institutions matching `q`, each with its resolver-exact string.

    `description` is what the operator must store for the code to route: `org.name`
    for an org, `"name, city"` (else `name`) for an institution. `claimed_by` is a
    code that already owns that description (unique) — non-`exclude_code` — so the
    UI can bar re-using it.
    """
    from sam.core.organizations import Institution, Organization

    q = (q or '').strip()
    if not q:
        return []
    claims = _description_claims(session)

    def _claimed(desc):
        code = claims.get(desc.casefold())
        return code if code and code != exclude_code else None

    like = f"%{q}%"
    out: List[Dict[str, Any]] = []
    for o in (session.query(Organization)
              .filter(Organization.is_active,
                      Organization.name.ilike(like) | Organization.acronym.ilike(like))
              .order_by(Organization.name).limit(limit)):
        out.append({'kind': 'organization', 'id': o.organization_id,
                    'name': o.name, 'city': None, 'description': o.name,
                    'claimed_by': _claimed(o.name)})
    for i in (session.query(Institution)
              .filter(Institution.deleted.isnot(True), Institution.name.ilike(like))
              .order_by(Institution.name).limit(limit)):
        desc = f"{i.name}, {i.city}" if i.city else i.name
        out.append({'kind': 'institution', 'id': i.institution_id,
                    'name': i.name, 'city': i.city, 'description': desc,
                    'claimed_by': _claimed(desc)})
    return out


def suggest_discontinuity(last_issued) -> int:
    """Next round hundred strictly above the high-water mark (min 100) — the
    reassignment gap the console pre-fills so an operator need not invent one."""
    return max(100, ((int(last_issued or 0) // 100) + 1) * 100)


def suggest_codes(session, description, *, limit: int = 6) -> List[str]:
    """Ranked, collision-free ``[A-Z]{3}`` candidates for a new mnemonic.

    The finder is the primary line of defense; this is the operator's safety net
    for the residual gaps it leaves (a genuinely new, unmapped org/institution).
    Prefers a clean acronym, then name-initials (stopwords dropped, single
    letters kept), then the first word, then a 3rd-char sweep to break collisions;
    everything already owned by a code is filtered out.
    """
    from sam.core.organizations import MnemonicCode

    d = (description or '').strip()
    if not d:
        return []
    _, acronym = _entity_for_suggest(session, d)
    taken = {mc.code for mc in session.query(MnemonicCode)}

    out: List[str] = []
    seen: set = set()
    for cand in _candidate_bases(d, acronym):
        if (re.fullmatch(r'[A-Z]{3}', cand) and cand not in taken
                and cand not in seen):
            seen.add(cand)
            out.append(cand)
            if len(out) >= limit:
                break
    return out


def _entity_for_suggest(session, description) -> tuple:
    """``(name, acronym)`` the description routes to, else ``(description, None)``.

    Same resolution order as ``describes_live_entity`` (org exact/soft, then
    institution), but returns the acronym so ``suggest_codes`` can prefer it.
    """
    from sqlalchemy import func

    from sam.core.organizations import (Institution, MnemonicCode, Organization,
                                        _MnemonicLookup)

    d = (description or '').strip()
    if not d:
        return '', None
    key = d.casefold()

    org = (session.query(Organization)
           .filter(Organization.is_active, func.lower(Organization.name) == key).first())
    if org:
        return org.name, org.acronym
    probe = _MnemonicLookup({key: 'HIT'})
    probe.soft = {MnemonicCode._soft_key(d): 'HIT'}
    for o in session.query(Organization).filter(Organization.is_active):
        if MnemonicCode.resolve_for_organization(o, probe) == 'HIT':
            return o.name, o.acronym

    inst = (session.query(Institution)
            .filter(Institution.deleted.isnot(True), func.lower(Institution.name) == key).first())
    if inst:
        return inst.name, inst.acronym
    inst = (session.query(Institution)
            .filter(Institution.deleted.isnot(True),
                    func.lower(func.concat(Institution.name, ', ', Institution.city)) == key).first())
    if inst:
        return inst.name, inst.acronym
    return d, None


def _clean(s) -> str:
    return re.sub(r'[^A-Z]', '', (s or '').upper())


def _sig_words(name) -> List[str]:
    raw = re.findall(r'[A-Za-z]+', name or '')
    words = [w for w in raw if w.lower() not in _STOPWORDS]
    return words or raw


def _candidate_bases(name, acronym) -> List[str]:
    """Ordered 3-char candidates (pre-filter): acronym, initials, first word,
    then a 3rd-char sweep off the best base to break collisions."""
    cands: List[str] = []
    ac = _clean(acronym)
    # A clean acronym only: no spaces, short, and not just the full name echoed
    # back (institution acronyms are frequently the whole name).
    if (acronym and ' ' not in acronym and len(acronym) <= 8
            and acronym.casefold() != (name or '').casefold() and len(ac) >= 3):
        cands.append(ac[:3])

    words = _sig_words(name)
    initials = [w[0].upper() for w in words]
    if len(initials) >= 3:
        cands.append(''.join(initials[:3]))
    if len(words) > 3:
        cands.append(initials[0] + initials[1] + initials[-1])
    if words:
        first = _clean(words[0])
        if len(first) >= 3:
            cands.append(first[:3])
        cons = _clean(''.join(c for c in words[0] if c.lower() not in 'aeiou'))
        if len(cons) >= 3:
            cands.append(cons[:3])

    base = next((c for c in cands if re.fullmatch(r'[A-Z]{3}', c)), None)
    if base:
        cands.extend(base[:2] + ch for ch in ascii_uppercase)
    return cands


def describes_live_entity(session, description) -> Optional[Dict[str, Any]]:
    """The org/institution a candidate description would route to, or None.

    Mirrors the resolver in reverse — org exact then the &/Lab soft match,
    institution exact ("name" or "name, city") as in legacy — so the console's
    match indicator can never disagree with real routing.
    """
    from sqlalchemy import func

    from sam.core.organizations import (Institution, MnemonicCode, Organization,
                                        _MnemonicLookup)

    d = (description or '').strip()
    if not d:
        return None
    key = d.casefold()

    org = (session.query(Organization)
           .filter(Organization.is_active, func.lower(Organization.name) == key).first())
    if org:
        return {'kind': 'organization', 'name': org.name}
    probe = _MnemonicLookup({key: 'HIT'})
    probe.soft = {MnemonicCode._soft_key(d): 'HIT'}
    for o in session.query(Organization).filter(Organization.is_active):
        if MnemonicCode.resolve_for_organization(o, probe) == 'HIT':
            return {'kind': 'organization', 'name': o.name}

    inst = (session.query(Institution)
            .filter(Institution.deleted.isnot(True), func.lower(Institution.name) == key).first())
    if inst:
        return {'kind': 'institution', 'name': inst.name}
    inst = (session.query(Institution)
            .filter(Institution.deleted.isnot(True),
                    func.lower(func.concat(Institution.name, ', ', Institution.city)) == key).first())
    if inst:
        return {'kind': 'institution', 'name': f"{inst.name}, {inst.city}"}
    return None
