"""Per-view facility scope on the admin Expirations tabs, and the line that
explains it to the reader.

The three tabs share ONE facility multi-select but not one default: Upcoming is
scoped to UNIV+WNA (the `expiration_notices` audience), while Expired and
Abandoned sweep every facility so the pane previews exactly what the monthly
`deactivate_expired_projects` task will do.

A shared control cannot display two defaults at once, which is why the
preselection was removed and why `_expirations_summary` exists. These pin both
halves — without the summary, the differing defaults are invisible, and the
Expired tab quietly showing one more project than Upcoming's scope would read as
a bug.
"""

import pytest

from webapp.dashboards.admin.blueprint import (
    _expiration_facility_default,
    _expirations_summary,
)

pytestmark = pytest.mark.unit


class TestTheFacilityDefault:

    @pytest.mark.parametrize('view', ['expired', 'abandoned'])
    def test_the_expired_pair_sweeps_every_facility(self, view):
        """`None` means no facility filter. Expired and Abandoned go together:
        Abandoned is derived from the same query, so a different scope would
        make it list users whose projects are absent from the Expired tab."""
        assert _expiration_facility_default(view) is None

    def test_upcoming_stays_on_the_notification_audience(self):
        assert _expiration_facility_default('upcoming') == ['UNIV', 'WNA']

    def test_an_unknown_view_falls_back_to_the_narrow_scope(self):
        """Fail narrow. A typo must not silently widen what an operator sees
        immediately above a bulk-deactivate button."""
        assert _expiration_facility_default('nonsense') == ['UNIV', 'WNA']


class TestTheSummaryLine:

    def test_it_names_all_facilities_when_unfiltered(self):
        line = _expirations_summary('expired', None, None)

        assert 'all facilities' in line
        assert '90 days' in line

    def test_it_marks_a_default_as_a_default(self):
        """The whole point: a reader must not have to guess why Expired shows a
        wider set than Upcoming."""
        assert 'default' in _expirations_summary('expired', None, None)

    def test_an_explicit_selection_is_not_called_a_default(self):
        """It would contradict the selection sitting right above it."""
        line = _expirations_summary('expired', ['UNIV'], None,
                                    explicit_facilities=True)

        assert 'default' not in line
        assert 'UNIV' in line

    @pytest.mark.parametrize('facilities,expected', [
        (['UNIV'], 'in UNIV'),
        (['UNIV', 'WNA'], 'in UNIV and WNA'),
        (['UNIV', 'WNA', 'NCAR'], 'in UNIV, WNA and NCAR'),
    ])
    def test_facility_lists_read_as_english(self, facilities, expected):
        assert expected in _expirations_summary('expired', facilities, None,
                                                explicit_facilities=True)

    def test_the_upcoming_window_tracks_the_time_range(self):
        assert 'next 7 days' in _expirations_summary(
            'upcoming', ['UNIV'], None, '7days', explicit_facilities=True)
        assert 'next 60 days' in _expirations_summary(
            'upcoming', ['UNIV'], None, '60days', explicit_facilities=True)

    def test_an_unknown_time_range_falls_back_to_the_preset_default(self):
        assert 'next 31 days' in _expirations_summary(
            'upcoming', ['UNIV'], None, 'fortnight', explicit_facilities=True)

    def test_a_resource_filter_is_named(self):
        line = _expirations_summary('expired', None, 'Derecho')

        assert 'Derecho' in line

    def test_abandoned_points_at_the_expired_tab(self):
        """They are one query. Saying so is what stops the two counts looking
        like they disagree by accident."""
        line = _expirations_summary('abandoned', None, None)

        assert 'Expired tab' in line
        assert '90 days' in line

    def test_expired_names_the_task_it_previews(self):
        assert 'monthly deactivation task' in _expirations_summary(
            'expired', None, None)

    @pytest.mark.parametrize('view', ['upcoming', 'expired', 'abandoned'])
    def test_every_view_produces_a_sentence(self, view):
        line = _expirations_summary(view, None, None, '31days')

        assert line.startswith('Showing ')
        assert line.endswith('.')
