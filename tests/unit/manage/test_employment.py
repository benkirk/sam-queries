"""The affiliation matching ladder (``sam.manage.employment``), table-driven, no database."""

from datetime import datetime

import pytest

from sam.manage.employment import Affiliation, match, overlaps

JAN, JUN, DEC = datetime(2025, 1, 1), datetime(2025, 6, 1), datetime(2025, 12, 31, 23, 59, 59)
NEXT = datetime(2026, 3, 1)


def A(eid, employer, start, end=None, idms=None):
    return Affiliation(eid, employer, start, end, idms)


def _matched(incoming, existing):
    return [found.employment_id if found else None for _, found in match(incoming, existing)]


class TestLadder:

    def test_known_id_of_the_same_employer(self):
        assert _matched([A(10, 7, JUN)], [A(10, 7, JAN)]) == [10]

    def test_stale_id_falls_through_to_data(self):
        """Legacy 500'd here (73 times since August); the row is matched by its dates."""
        assert _matched([A(999, 7, JAN, DEC)], [A(10, 7, JAN, DEC)]) == [10]

    def test_id_naming_another_employer_is_not_trusted(self):
        assert _matched([A(10, 8, JAN)], [A(10, 7, JAN)]) == [None]

    def test_idms_name_beats_dates(self):
        existing = [A(1, 7, JAN, JUN, 'p1'), A(2, 7, JUN, None, 'p2')]
        assert _matched([A(None, 7, JAN, None, 'p2')], existing) == [2]

    def test_exact_dates_beat_overlap(self):
        existing = [A(1, 7, JAN, None), A(2, 7, JUN, DEC)]
        assert _matched([A(None, 7, JUN, DEC)], existing) == [2]

    def test_overlap_matches(self):
        assert _matched([A(None, 7, JUN, None)], [A(1, 7, JAN, DEC)]) == [1]

    def test_disjoint_range_inserts(self):
        assert _matched([A(None, 7, NEXT, None)], [A(1, 7, JAN, DEC)]) == [None]

    def test_a_sam_row_matches_once(self):
        """Legacy let two incoming records land on one row, the last silently winning."""
        assert _matched([A(None, 7, JAN, None), A(None, 7, JUN, None)],
                        [A(1, 7, JAN, None)]) == [1, None]

    def test_other_employers_never_match(self):
        assert _matched([A(None, 8, JAN, DEC)], [A(1, 7, JAN, DEC)]) == [None]


@pytest.mark.parametrize('a, b, expected', [
    (A(None, 1, JAN, JUN), A(None, 1, JUN, DEC), True),       # touching days overlap
    (A(None, 1, JAN, JUN), A(None, 1, NEXT, None), False),
    (A(None, 1, JAN, None), A(None, 1, NEXT, None), True),    # open end runs forever
    (A(None, 1, DEC, JAN), A(None, 1, JAN, DEC), False),      # inverted never overlaps
])
def test_overlaps(a, b, expected):
    assert overlaps(a, b) is expected
