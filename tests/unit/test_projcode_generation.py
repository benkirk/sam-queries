"""Unit tests for projcode generation — sam.projects.projects.next_projcode.

Legacy-faithful semantics under test (ports of legacy SAM's
``GroupSensitiveProjcodeGenerator`` + ``Facility.getNextProjectCodeDigits``):

- ``project_code.digits`` is a per-(facility, mnemonic) counter of the last
  sequence number issued — NOT a zero-pad width.
- Codes render as ``<facility.code><mnemonic.code><NNNN>`` (4-digit pad).
- Missing ProjectCode rows are created on demand starting at 1.
- Candidates colliding with an existing projcode OR adhoc_group name are
  skipped.
- ``allocate=False`` (preview) never mutates; ``allocate=True`` persists the
  consumed counter.

Layer composition: snapshot facility (any row with a projcode prefix
letter — Layer 1 shape) + factory-built mnemonics/projects/groups
(Layer 2), so tests never collide with real (facility, mnemonic) pairs.
"""
import pytest

from sam.projects.projects import (
    ProjcodeExhaustedError,
    formulate_projcode,
    next_projcode,
    projcode_collision,
)
from sam.resources.facilities import Facility, ProjectCode
from factories import (
    make_adhoc_group,
    make_facility,
    make_mnemonic_code,
    make_project,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def prefixed_facility(session):
    """Any snapshot facility that has a 1-letter projcode prefix."""
    facility = (
        session.query(Facility)
        .filter(Facility.code.isnot(None))
        .order_by(Facility.facility_id)
        .first()
    )
    assert facility is not None, "snapshot has no facility with a projcode prefix"
    return facility


def _rule(session, facility, mnemonic):
    return session.get(
        ProjectCode, (facility.facility_id, mnemonic.mnemonic_code_id))


class TestFormulateProjcode:

    def test_legacy_format(self):
        """%s%s%04d — facility letter + mnemonic + 4-digit pad."""
        assert formulate_projcode('U', 'ALB', 57) == 'UALB0057'
        assert formulate_projcode('S', 'CSG', 9) == 'SCSG0009'

    def test_counter_wider_than_pad(self):
        """Counters past 9999 grow the number rather than truncate."""
        assert formulate_projcode('U', 'CUB', 12345) == 'UCUB12345'


class TestProjcodeCollision:

    def test_existing_project_reported(self, session, prefixed_facility):
        project = make_project(session)
        detail = projcode_collision(session, project.projcode)
        assert detail is not None
        assert project.projcode in detail

    def test_existing_adhoc_group_reported(self, session):
        group = make_adhoc_group(session)
        detail = projcode_collision(session, group.group_name)
        assert detail is not None
        assert group.group_name in detail

    def test_case_insensitive(self, session):
        group = make_adhoc_group(session)
        assert projcode_collision(session, group.group_name.upper()) is not None

    def test_free_code_returns_none(self, session):
        assert projcode_collision(session, 'ZZZZ99999999') is None


class TestNextProjcodePreview:

    def test_missing_rule_starts_at_one(self, session, prefixed_facility):
        mnemo = make_mnemonic_code(session)
        code = next_projcode(
            session, prefixed_facility.facility_id, mnemo.mnemonic_code_id)
        assert code == f"{prefixed_facility.code}{mnemo.code}0001"
        # Preview must not create the counter row.
        assert _rule(session, prefixed_facility, mnemo) is None

    def test_counter_is_last_issued_not_pad_width(self, session, prefixed_facility):
        """The ALB-regression: digits=57 must mean 'next is 0058', never
        'zero-pad to 57 characters'."""
        mnemo = make_mnemonic_code(session)
        session.add(ProjectCode(
            facility_id=prefixed_facility.facility_id,
            mnemonic_code_id=mnemo.mnemonic_code_id,
            digits=57,
        ))
        session.flush()
        code = next_projcode(
            session, prefixed_facility.facility_id, mnemo.mnemonic_code_id)
        assert code == f"{prefixed_facility.code}{mnemo.code}0058"
        assert len(code) == len(prefixed_facility.code) + len(mnemo.code) + 4

    def test_preview_does_not_advance_counter(self, session, prefixed_facility):
        mnemo = make_mnemonic_code(session)
        session.add(ProjectCode(
            facility_id=prefixed_facility.facility_id,
            mnemonic_code_id=mnemo.mnemonic_code_id,
            digits=41,
        ))
        session.flush()
        first = next_projcode(
            session, prefixed_facility.facility_id, mnemo.mnemonic_code_id)
        second = next_projcode(
            session, prefixed_facility.facility_id, mnemo.mnemonic_code_id)
        assert first == second
        assert _rule(session, prefixed_facility, mnemo).digits == 41

    def test_skips_existing_project(self, session, prefixed_facility):
        mnemo = make_mnemonic_code(session)
        taken = formulate_projcode(prefixed_facility.code, mnemo.code, 1)
        make_project(session, projcode=taken)
        code = next_projcode(
            session, prefixed_facility.facility_id, mnemo.mnemonic_code_id)
        assert code == formulate_projcode(prefixed_facility.code, mnemo.code, 2)

    def test_skips_existing_adhoc_group_case_insensitively(
            self, session, prefixed_facility):
        """Projcodes become Unix group names; a lowercase group blocks the
        matching uppercase candidate (legacy GroupSensitiveProjcodeGenerator)."""
        mnemo = make_mnemonic_code(session)
        taken = formulate_projcode(prefixed_facility.code, mnemo.code, 1)
        make_adhoc_group(session, group_name=taken.lower())
        code = next_projcode(
            session, prefixed_facility.facility_id, mnemo.mnemonic_code_id)
        assert code == formulate_projcode(prefixed_facility.code, mnemo.code, 2)

    def test_exhaustion_raises(self, session, prefixed_facility):
        mnemo = make_mnemonic_code(session)
        for n in (1, 2, 3):
            make_project(session, projcode=formulate_projcode(
                prefixed_facility.code, mnemo.code, n))
        with pytest.raises(ProjcodeExhaustedError):
            next_projcode(
                session, prefixed_facility.facility_id,
                mnemo.mnemonic_code_id, max_attempts=3)

    def test_unknown_facility_raises(self, session):
        mnemo = make_mnemonic_code(session)
        with pytest.raises(ValueError, match='Facility'):
            next_projcode(session, 999_999_999, mnemo.mnemonic_code_id)

    def test_unknown_mnemonic_raises(self, session, prefixed_facility):
        with pytest.raises(ValueError, match='MnemonicCode'):
            next_projcode(session, prefixed_facility.facility_id, 999_999_999)

    def test_facility_without_prefix_raises(self, session):
        """Factory facilities deliberately have code=None — unusable for
        projcode generation and must fail loudly, not emit 'NoneABC0001'."""
        facility = make_facility(session)
        mnemo = make_mnemonic_code(session)
        with pytest.raises(ValueError, match='prefix'):
            next_projcode(session, facility.facility_id, mnemo.mnemonic_code_id)


class TestNextProjcodeAllocate:

    def test_allocate_creates_rule_at_one(self, session, prefixed_facility):
        mnemo = make_mnemonic_code(session)
        code = next_projcode(
            session, prefixed_facility.facility_id,
            mnemo.mnemonic_code_id, allocate=True)
        assert code.endswith('0001')
        assert _rule(session, prefixed_facility, mnemo).digits == 1

    def test_allocate_advances_counter(self, session, prefixed_facility):
        mnemo = make_mnemonic_code(session)
        session.add(ProjectCode(
            facility_id=prefixed_facility.facility_id,
            mnemonic_code_id=mnemo.mnemonic_code_id,
            digits=41,
        ))
        session.flush()
        code = next_projcode(
            session, prefixed_facility.facility_id,
            mnemo.mnemonic_code_id, allocate=True)
        assert code.endswith('0042')
        assert _rule(session, prefixed_facility, mnemo).digits == 42

    def test_sequential_allocations_monotonic(self, session, prefixed_facility):
        mnemo = make_mnemonic_code(session)
        codes = [
            next_projcode(session, prefixed_facility.facility_id,
                          mnemo.mnemonic_code_id, allocate=True)
            for _ in range(3)
        ]
        assert codes == [
            formulate_projcode(prefixed_facility.code, mnemo.code, n)
            for n in (1, 2, 3)
        ]

    def test_collision_burns_counter_values(self, session, prefixed_facility):
        """Legacy behavior: skipped candidates consume counter values, so
        the persisted counter always equals the number actually issued."""
        mnemo = make_mnemonic_code(session)
        make_project(session, projcode=formulate_projcode(
            prefixed_facility.code, mnemo.code, 1))
        code = next_projcode(
            session, prefixed_facility.facility_id,
            mnemo.mnemonic_code_id, allocate=True)
        assert code.endswith('0002')
        assert _rule(session, prefixed_facility, mnemo).digits == 2
