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

from typing import Any, Dict, List


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
