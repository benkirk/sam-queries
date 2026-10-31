"""``AllocationTypeGuide``: the request page's card per XRAS allocation type (XRAS_SUBMISSION_SPRINT2.md).

Rows are built on XRAS type ids the seed never names, so these hold whether or not the test
database carries the seed. Each test draws its own ids: xdist workers inserting one unique
key deadlock.
"""

import re
import secrets
from _paths import REPO_ROOT

import pytest

from sam.accounting.allocation_type_guide import AllocationTypeGuide, public_guides


@pytest.fixture
def xras_type():
    """A fresh id range for this test, far above any XRAS id."""
    return 10_000_000 + secrets.randbelow(10_000_000) * 10


def _guide(session, xras_type, **kw):
    kw.setdefault('title', 'Probe')
    return AllocationTypeGuide.create(session, xras_allocation_type_id=xras_type,
                                      created_by='t', **kw)


class TestCreate:

    def test_blanks_store_as_null_and_text_is_trimmed(self, session, xras_type):
        row = _guide(session, xras_type, title='  Probe ', summary='  ', funding=' Any funding source ')
        assert (row.title, row.summary, row.funding) == ('Probe', None, 'Any funding source')
        assert row.is_active and row.creation_time == row.modified_time

    @pytest.mark.parametrize('kw', [dict(title='  '), dict(audience='everyone')])
    def test_a_blank_title_or_unknown_audience_is_refused(self, session, xras_type, kw):
        with pytest.raises(ValueError):
            _guide(session, xras_type, **kw)

    def test_one_row_per_xras_type(self, session, xras_type):
        _guide(session, xras_type)
        with pytest.raises(ValueError, match='already has a guide row'):
            _guide(session, xras_type)


class TestUpdate:

    def test_given_fields_change_and_omitted_ones_stay(self, session, xras_type):
        row = _guide(session, xras_type, summary='Old', cadence='Continuous')
        row.update(summary='New', display_order=5)
        assert (row.summary, row.cadence, row.display_order) == ('New', 'Continuous', 5)

    def test_blank_clears_and_title_cannot_be_cleared(self, session, xras_type):
        row = _guide(session, xras_type, notes='Something')
        row.update(notes='')
        assert row.notes is None
        with pytest.raises(ValueError):
            row.update(title=' ')

    def test_an_unknown_field_is_a_type_error(self, session, xras_type):
        with pytest.raises(TypeError):
            _guide(session, xras_type).update(colour='blue')


class TestPublicGuides:

    def test_ncar_and_inactive_rows_stay_off_the_public_page(self, session, xras_type):
        shown = _guide(session, xras_type, title='Shown', audience='university')
        everyone = _guide(session, xras_type + 1, title='Everyone')
        staff = _guide(session, xras_type + 2, title='Staff', audience='ncar')
        retired = _guide(session, xras_type + 3, title='Retired')
        retired.update(active=False)
        ids = {g.allocation_type_guide_id for g in public_guides(session)}
        assert {shown.allocation_type_guide_id, everyone.allocation_type_guide_id} <= ids
        assert not {staff.allocation_type_guide_id, retired.allocation_type_guide_id} & ids


_SEED_ROW = re.compile(r"SELECT\s+(\d+)\s*,\s*('[^']*'|NULL)\s*,\s*'([^']+)'\s*,\s*(\d+)\s*,"
                       r"\s*('[^']*'|NULL)")


def _seed_rows(name):
    return _SEED_ROW.findall((REPO_ROOT / 'scripts' / 'sql' / name).read_text())


class TestTheSeed:

    def test_both_dialects_carry_the_same_rows(self):
        mysql = (REPO_ROOT / 'scripts' / 'sql' / 'seed_allocation_type_guide.sql').read_text()
        pg = (REPO_ROOT / 'scripts' / 'sql' / 'seed_allocation_type_guide_pg.sql').read_text()
        body = re.compile(r'^\s+(?:UNION ALL )?SELECT \d+.*$', re.M)
        assert len(body.findall(mysql)) == 7
        assert body.findall(mysql) == body.findall(pg)

    def test_every_seeded_audience_is_known(self):
        for _, _, title, _, audience in _seed_rows('seed_allocation_type_guide.sql'):
            assert audience == 'NULL' or audience.strip("'") in ('university', 'ncar'), title

    def test_every_seeded_type_resolves_to_a_sam_type(self, session):
        from sam.accounting.allocations import AllocationType
        names = {n for (n,) in session.query(AllocationType.allocation_type)}
        for _, sam_type, title, _, _ in _seed_rows('seed_allocation_type_guide.sql'):
            assert sam_type.strip("'") in names, title
