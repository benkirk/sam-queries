"""The Mnemonic Codes console: model writes, the reassignment primitive, the
inventory insight query, the two form schemas, and the projcode-preview math.
"""
import pytest

from sam.core.organizations import MnemonicCode
from sam.resources.facilities import ProjectCode
from sam.projects.projects import formulate_projcode
from sam.queries.mnemonic_console import (
    claiming_code, describes_live_entity, mnemonic_inventory, search_targets,
    suggest_discontinuity)
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


def _row_for(rows, code):
    return next((r for r in rows if r['code'] == code), None)
