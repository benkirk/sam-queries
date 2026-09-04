"""The Mnemonic Codes console: model writes, the reassignment primitive, the
inventory insight query, the two form schemas, and the projcode-preview math.
"""
import pytest

from sam.core.organizations import MnemonicCode
from sam.resources.facilities import ProjectCode
from sam.projects.projects import formulate_projcode
from sam.queries.mnemonic_console import (
    claiming_code, describes_live_entity, mnemonic_inventory, search_targets,
    suggest_codes, suggest_discontinuity)
from sam.queries.mnemonic_console import _candidate_bases
from sam.schemas.forms import EditMnemonicCodeForm, ReassignMnemonicForm

from factories import (
    make_facility, make_institution, make_mnemonic_code, make_organization)

pytestmark = pytest.mark.unit

_N = "Zzq"  # nonce prefix: disjoint from real snapshot descriptions/names


class TestModelWrites:

    def test_update_description_and_active(self, session):
        mc = make_mnemonic_code(session, description=f"{_N} Before")
        mc.update(description=f"{_N} After", active=False)
        session.refresh(mc)
        assert mc.description == f"{_N} After"
        assert mc.active is False

    def test_update_leaves_unset_fields_alone(self, session):
        mc = make_mnemonic_code(session, description=f"{_N} Keep")
        mc.update(active=False)
        assert mc.description == f"{_N} Keep"  # untouched

    def test_code_is_not_editable(self, session):
        # update() has no `code` parameter — the 3-letter code is fixed.
        mc = make_mnemonic_code(session)
        original = mc.code
        mc.update(description=f"{_N} New desc")
        assert mc.code == original


class TestNumberFloor:

    def test_creates_row_at_floor_minus_one(self, session):
        fac = make_facility(session)
        mc = make_mnemonic_code(session)
        ProjectCode.set_number_floor(session, fac.facility_id, mc.mnemonic_code_id, 100)
        pc = session.get(ProjectCode, (fac.facility_id, mc.mnemonic_code_id))
        assert pc.digits == 99  # next mint is 100

    def test_raises_a_low_counter(self, session):
        fac = make_facility(session)
        mc = make_mnemonic_code(session)
        session.add(ProjectCode(facility_id=fac.facility_id,
                                mnemonic_code_id=mc.mnemonic_code_id, digits=42))
        session.flush()
        ProjectCode.set_number_floor(session, fac.facility_id, mc.mnemonic_code_id, 100)
        assert session.get(ProjectCode, (fac.facility_id, mc.mnemonic_code_id)).digits == 99

    def test_never_lowers_a_counter(self, session):
        fac = make_facility(session)
        mc = make_mnemonic_code(session)
        session.add(ProjectCode(facility_id=fac.facility_id,
                                mnemonic_code_id=mc.mnemonic_code_id, digits=99))
        session.flush()
        ProjectCode.set_number_floor(session, fac.facility_id, mc.mnemonic_code_id, 5)
        assert session.get(ProjectCode, (fac.facility_id, mc.mnemonic_code_id)).digits == 99

    def test_rejects_a_zero_floor(self, session):
        fac = make_facility(session)
        mc = make_mnemonic_code(session)
        with pytest.raises(ValueError):
            ProjectCode.set_number_floor(session, fac.facility_id, mc.mnemonic_code_id, 0)


class TestInventory:

    def test_linked_code_reports_its_org(self, session):
        org = make_organization(session, name=f"{_N} Linked Org XYZ")
        mc = make_mnemonic_code(session, description=f"{_N} Linked Org XYZ")
        row = _row_for(mnemonic_inventory(session, active_only=True), mc.code)
        assert row['orphaned'] is False
        assert any(l['name'] == f"{_N} Linked Org XYZ" for l in row['links_to'])

    def test_unmatched_active_code_is_orphaned(self, session):
        mc = make_mnemonic_code(session, description=f"{_N} Nothing Resolves Here Qux")
        row = _row_for(mnemonic_inventory(session, active_only=True), mc.code)
        assert row['orphaned'] is True
        assert row['links_to'] == []

    def test_minted_totals_from_project_code(self, session):
        fac = make_facility(session)
        mc = make_mnemonic_code(session, description=f"{_N} Minted Code Desc")
        session.add(ProjectCode(facility_id=fac.facility_id,
                                mnemonic_code_id=mc.mnemonic_code_id, digits=42))
        session.flush()
        row = _row_for(mnemonic_inventory(session, active_only=True), mc.code)
        assert row['minted_total'] == 42
        assert any(u['facility_id'] == fac.facility_id and u['last'] == 42
                   for u in row['usage'])

    def test_active_only_hides_retired_codes(self, session):
        mc = make_mnemonic_code(session, description=f"{_N} Retired One", active=False)
        assert _row_for(mnemonic_inventory(session, active_only=True), mc.code) is None
        assert _row_for(mnemonic_inventory(session, active_only=False), mc.code) is not None

    def test_retired_code_is_not_orphaned(self, session):
        mc = make_mnemonic_code(session, description=f"{_N} Retired Two", active=False)
        row = _row_for(mnemonic_inventory(session, active_only=False), mc.code)
        assert row['active'] is False
        assert row['orphaned'] is False  # retired != orphaned


class TestForms:

    def test_edit_form_requires_description(self, session):
        from marshmallow import ValidationError
        with pytest.raises(ValidationError):
            EditMnemonicCodeForm().load({'description': ''})

    def test_edit_form_drops_absent_active(self, session):
        # No `code` field, and active defaults to False when absent (checkbox off).
        data = EditMnemonicCodeForm().load({'description': 'A Name'})
        assert data['description'] == 'A Name'
        assert data['active'] is False
        assert 'code' not in data

    def test_reassign_form_validates(self, session):
        data = ReassignMnemonicForm().load(
            {'description': 'New Owner', 'facility_id': '3', 'next_start': '100'})
        assert data == {'description': 'New Owner', 'facility_id': 3, 'next_start': 100}

    def test_reassign_form_rejects_zero_floor(self, session):
        from marshmallow import ValidationError
        with pytest.raises(ValidationError):
            ReassignMnemonicForm().load(
                {'description': 'X', 'facility_id': '3', 'next_start': '0'})


class TestSuggestDiscontinuity:

    @pytest.mark.parametrize('last, expected', [
        (0, 100), (4, 100), (85, 100), (99, 100),
        (100, 200), (142, 200), (250, 300), (None, 100)])
    def test_next_round_hundred_strictly_above(self, last, expected):
        assert suggest_discontinuity(last) == expected


class TestPreviewMath:

    def test_effective_number_honors_floor_and_high_water(self):
        # The preview endpoint's logic: effective = max(next_start, last + 1).
        assert formulate_projcode('N', 'MMM', max(100, 85 + 1)) == 'NMMM0100'
        # A floor at or below the high-water mark yields last + 1, never a rewind.
        assert formulate_projcode('N', 'MMM', max(5, 85 + 1)) == 'NMMM0086'


class TestSearchTargets:

    def test_finds_org_by_name_with_exact_string(self, session):
        make_organization(session, name=f"{_N} Findable Org Alpha")
        hits = search_targets(session, f"{_N} Findable Org Alpha")
        assert any(t['kind'] == 'organization' and t['description'] == f"{_N} Findable Org Alpha"
                   for t in hits)

    def test_institution_description_is_name_comma_city(self, session):
        inst = make_institution(session, name=f"{_N} Findable Institute Beta")
        inst.city = "Testville"
        session.flush()
        hits = search_targets(session, f"{_N} Findable Institute Beta")
        t = next(h for h in hits if h['kind'] == 'institution')
        assert t['description'] == f"{_N} Findable Institute Beta, Testville"

    def test_empty_query_returns_nothing(self, session):
        assert search_targets(session, '  ') == []

    def test_claimed_entity_is_flagged(self, session):
        # An org whose name already equals a mnemonic's description cannot be re-used.
        make_organization(session, name=f"{_N} Taken Org Theta")
        mc = make_mnemonic_code(session, description=f"{_N} Taken Org Theta")
        hit = next(t for t in search_targets(session, f"{_N} Taken Org Theta")
                   if t['kind'] == 'organization')
        assert hit['claimed_by'] == mc.code

    def test_exclude_code_frees_its_own_claim(self, session):
        # Editing the very code that owns the description: its own entity stays pickable.
        make_organization(session, name=f"{_N} Self Org Iota")
        mc = make_mnemonic_code(session, description=f"{_N} Self Org Iota")
        hit = next(t for t in search_targets(session, f"{_N} Self Org Iota",
                                             exclude_code=mc.code)
                   if t['kind'] == 'organization')
        assert hit['claimed_by'] is None


class TestClaimingCode:

    def test_returns_the_owning_code(self, session):
        mc = make_mnemonic_code(session, description=f"{_N} Owned Desc Kappa")
        assert claiming_code(session, f"{_N} Owned Desc Kappa") == mc.code

    def test_case_insensitive(self, session):
        mc = make_mnemonic_code(session, description=f"{_N} Mixed Case Lambda")
        assert claiming_code(session, f"{_N} mixed case lambda") == mc.code

    def test_excludes_self(self, session):
        mc = make_mnemonic_code(session, description=f"{_N} Own It Mu")
        assert claiming_code(session, f"{_N} Own It Mu", exclude_code=mc.code) is None

    def test_unclaimed_is_none(self, session):
        assert claiming_code(session, f"{_N} Nobody Owns This Nu") is None


class TestDescribesLiveEntity:

    def test_exact_org_name(self, session):
        make_organization(session, name=f"{_N} Exact Org Gamma")
        m = describes_live_entity(session, f"{_N} Exact Org Gamma")
        assert m == {'kind': 'organization', 'name': f"{_N} Exact Org Gamma"}

    def test_soft_matched_org(self, session):
        # Mirrors the resolver's Lab<->Laboratory soft match.
        make_organization(session, name=f"{_N} Delta Lab")
        m = describes_live_entity(session, f"{_N} Delta Laboratory")
        assert m == {'kind': 'organization', 'name': f"{_N} Delta Lab"}

    def test_institution_name_comma_city(self, session):
        inst = make_institution(session, name=f"{_N} Epsilon University")
        inst.city = "Boulder"
        session.flush()
        m = describes_live_entity(session, f"{_N} Epsilon University, Boulder")
        assert m == {'kind': 'institution', 'name': f"{_N} Epsilon University, Boulder"}

    def test_a_genuine_miss_is_none(self, session):
        assert describes_live_entity(session, f"{_N} Nothing Resolves Zeta") is None

    def test_empty_is_none(self, session):
        assert describes_live_entity(session, '   ') is None


class TestCandidateBases:
    """The pure name->3-char logic, snapshot-independent (no DB)."""

    def test_org_acronym_wins_when_clean(self):
        assert _candidate_bases("Weather Modeling & Research", "WMR")[0] == "WMR"

    def test_initials_drop_stopwords_keep_single_letters(self):
        assert "UAF" in _candidate_bases("University of Alaska Fairbanks", None)
        assert "UAF" in _candidate_bases("U OF ALASKA FAIRBANKS", None)

    def test_initials_include_city_token(self):
        assert "UCB" in _candidate_bases("University of Colorado, Boulder", None)

    def test_ampersand_is_dropped(self):
        assert "WMR" in _candidate_bases("Weather Modeling & Research", None)

    def test_acronym_equal_to_name_is_ignored(self):
        # A dirty acronym (== the full name, spaces) never becomes a candidate.
        cands = _candidate_bases("Foo Bar Baz", "Foo Bar Baz")
        assert cands[0] == "FBB"

    def test_long_name_offers_first_two_plus_last(self):
        cands = _candidate_bases("National Center for Atmospheric Research", None)
        assert "NCR" in cands  # first-2 + last: National, Center, ... Research

    def test_every_candidate_is_three_upper(self):
        import re
        for c in _candidate_bases("University of Colorado, Boulder", "CUB"):
            assert re.fullmatch(r'[A-Z]{3}', c)


class TestSuggestCodes:

    def test_empty_description_is_empty_list(self, session):
        assert suggest_codes(session, '   ') == []

    def test_results_are_valid_unique_and_free(self, session):
        import re
        from sam.core.organizations import MnemonicCode
        taken = {mc.code for mc in session.query(MnemonicCode)}
        out = suggest_codes(session, f"{_N} University of Testing Springs")
        assert out, "expected at least one suggestion"
        assert len(out) == len(set(out))
        for c in out:
            assert re.fullmatch(r'[A-Z]{3}', c)
            assert c not in taken

    def test_collision_falls_through_to_variant(self, session):
        # A base already owned by a code is skipped; a 3rd-char variant is offered.
        org = make_organization(session, name=f"{_N} Qqz Collide Org")
        org.acronym = "QQZ"
        session.flush()
        make_mnemonic_code(session, code="QQZ", description=f"{_N} Owns QQZ")
        out = suggest_codes(session, f"{_N} Qqz Collide Org", limit=30)
        assert "QQZ" not in out  # the taken code is filtered out
        # the 3rd-char sweep off the QQZ base still offers QQ-variants
        assert any(c.startswith("QQ") and c != "QQZ" for c in out)

    def test_matched_org_contributes_its_acronym(self, session):
        org = make_organization(session, name=f"{_N} Suggestor Match Org")
        org.acronym = "SMX"
        session.flush()
        # SMX free? if taken the suggestor still must not return it.
        from sam.core.organizations import MnemonicCode
        taken = {mc.code for mc in session.query(MnemonicCode)}
        out = suggest_codes(session, f"{_N} Suggestor Match Org")
        if "SMX" not in taken:
            assert "SMX" in out


class TestResolveForOrganizationWalk:
    """The parent-walk in resolve_for_organization (kkeene: WMR -> parent MMM)."""

    def test_leaf_hit_is_unchanged(self, session):
        org = make_organization(session, name=f"{_N} Leaf Coded Org")
        mc = make_mnemonic_code(session, description=f"{_N} Leaf Coded Org")
        code = MnemonicCode.resolve_for_organization(
            org, MnemonicCode.build_lookup(session))
        assert code == mc.code

    def test_walks_to_coded_parent(self, session):
        parent = make_organization(session, name=f"{_N} Parent Lab Org")
        parent.level_code = '0300'
        child = make_organization(session, name=f"{_N} Child Uncoded Org",
                                  parent_org_id=parent.organization_id)
        child.level_code = '0400'
        session.flush()
        mc = make_mnemonic_code(session, description=f"{_N} Parent Lab Org")
        code = MnemonicCode.resolve_for_organization(
            child, MnemonicCode.build_lookup(session))
        assert code == mc.code

    def test_refuses_center_level_ancestor(self, session):
        center = make_organization(session, name=f"{_N} Center Org")
        center.level_code = '0200'  # NCAR-level, capped
        child = make_organization(session, name=f"{_N} Under Center Org",
                                  parent_org_id=center.organization_id)
        child.level_code = '0400'
        session.flush()
        make_mnemonic_code(session, description=f"{_N} Center Org")
        code = MnemonicCode.resolve_for_organization(
            child, MnemonicCode.build_lookup(session))
        assert code is None  # the only coded ancestor is too broad

    def test_null_level_code_ancestor_still_matches(self, session):
        parent = make_organization(session, name=f"{_N} Null Level Parent")
        child = make_organization(session, name=f"{_N} Null Level Child",
                                  parent_org_id=parent.organization_id)
        session.flush()
        mc = make_mnemonic_code(session, description=f"{_N} Null Level Parent")
        code = MnemonicCode.resolve_for_organization(
            child, MnemonicCode.build_lookup(session))
        assert code == mc.code  # NULL level_code is allowed, not floored

    def test_walk_parents_false_is_leaf_only(self, session):
        parent = make_organization(session, name=f"{_N} NoWalk Parent")
        child = make_organization(session, name=f"{_N} NoWalk Child",
                                  parent_org_id=parent.organization_id)
        session.flush()
        make_mnemonic_code(session, description=f"{_N} NoWalk Parent")
        code = MnemonicCode.resolve_for_organization(
            child, MnemonicCode.build_lookup(session), walk_parents=False)
        assert code is None

    def test_named_stub_does_not_crash(self, session):
        from sam.queries.mnemonic_console import _Named
        assert MnemonicCode.resolve_for_organization(
            _Named(f"{_N} Nonexistent Stub"),
            MnemonicCode.build_lookup(session)) is None


def _row_for(rows, code):
    return next((r for r in rows if r['code'] == code), None)
