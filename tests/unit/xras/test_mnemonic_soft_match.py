"""Tolerant org→mnemonic resolution (`&`↔`and`, `Lab`↔`Laboratory`).

The 2026 IdMS rename shortened NCAR lab org names ("Laboratory"→"Lab",
"and"→"&") away from their hand-kept mnemonic descriptions, breaking the
internal-PI projcode resolution. `resolve_for_organization` gains an exact-first,
injective soft-key fallback; institutions stay exact (matching legacy).
"""
from types import SimpleNamespace

import pytest

from sam.core.organizations import MnemonicCode

from factories import make_mnemonic_code

pytestmark = pytest.mark.unit

_N = "Zzq"  # nonce prefix: disjoint from real snapshot descriptions


def _org(name):
    return SimpleNamespace(name=name)


def _inst(name, city=None):
    return SimpleNamespace(name=name, city=city)


class TestOrgSoftMatch:

    def test_lab_resolves_against_a_laboratory_description(self, session):
        mc = make_mnemonic_code(session, description=f"{_N} Widget Laboratory")
        lookup = MnemonicCode.build_lookup(session)
        assert MnemonicCode.resolve_for_organization(_org(f"{_N} Widget Lab"), lookup) == mc.code

    def test_ampersand_resolves_against_an_and_description(self, session):
        mc = make_mnemonic_code(session, description=f"{_N} Alpha and Beta Lab")
        lookup = MnemonicCode.build_lookup(session)
        assert MnemonicCode.resolve_for_organization(_org(f"{_N} Alpha & Beta Lab"), lookup) == mc.code

    def test_exact_match_still_wins_over_a_soft_alias(self, session):
        exact = make_mnemonic_code(session, description=f"{_N} Dup Lab")
        make_mnemonic_code(session, description=f"{_N} Dup Laboratory")   # soft-collides
        lookup = MnemonicCode.build_lookup(session)
        # The org name equals the short description exactly -> its code, never the
        # "Laboratory" one, and the collision is suppressed (exact owns the key).
        assert MnemonicCode.resolve_for_organization(_org(f"{_N} Dup Lab"), lookup) == exact.code

    def test_a_genuine_non_match_is_none(self, session):
        lookup = MnemonicCode.build_lookup(session)
        assert MnemonicCode.resolve_for_organization(_org(f"{_N} No Such Xyzzy"), lookup) is None

    def test_a_plain_dict_lookup_degrades_to_exact_only(self, session):
        # Robustness: a caller/test that hands a bare dict (no .soft) must not blow up.
        exact_key = f"{_N} Bare Lab".casefold()
        assert MnemonicCode.resolve_for_organization(
            _org(f"{_N} Bare Lab"), {exact_key: 'BAR'}) == 'BAR'
        assert MnemonicCode.resolve_for_organization(_org(f"{_N} Bare Lab"), {}) is None


class TestInstitutionStaysExact:

    def test_institution_does_not_soft_match(self, session):
        # Institutions were exact in legacy (findOneByDescription); the Lab/Laboratory
        # drift must NOT resolve for them, or we reopen the punctuation-collision risk.
        make_mnemonic_code(session, description=f"{_N} Institute Laboratory")
        lookup = MnemonicCode.build_lookup(session)
        assert MnemonicCode.resolve_for_institution(_inst(f"{_N} Institute Lab"), lookup) is None
        # Exact still resolves.
        assert MnemonicCode.resolve_for_institution(_inst(f"{_N} Institute Laboratory"), lookup)


class TestSoftKeyIsInjective:

    def test_no_two_active_codes_share_a_soft_key(self, session):
        """Guard: the soft normalization must stay injective across the live table,
        so a soft match can never be ambiguous. Fails if a mnemonic is ever added
        that collides under &/Laboratory normalization (then handle it explicitly)."""
        rows = session.query(MnemonicCode).filter(MnemonicCode.is_active).all()
        exact = {mc.description.casefold() for mc in rows}
        by_soft = {}
        for mc in rows:
            key = MnemonicCode._soft_key(mc.description)
            if key in exact:                       # exact owns it — not a soft alias
                continue
            by_soft.setdefault(key, set()).add(mc.code)
        collisions = {k: v for k, v in by_soft.items() if len(v) > 1}
        assert not collisions, f"soft-key collisions (punctuation must NOT be normalized): {collisions}"
