"""FacetSet: the in-memory chip engine behind the account and XRAS cards."""

from werkzeug.datastructures import MultiDict

from webapp.utils.facets import Facet, FacetSet

ROWS = [
    {'state': 'open', 'kind': 'a', 'tags': ['x', 'y']},
    {'state': 'open', 'kind': 'b', 'tags': ['x']},
    {'state': 'done', 'kind': 'a', 'tags': []},
    {'state': 'odd', 'kind': None, 'tags': ['y']},
]

FACETS = FacetSet(
    Facet('state', order=('open', 'done', 'held')),
    Facet('kind', hide_zero=True),
    Facet('tag', key='tags'),
)


def _counts(strip):
    return {chip['value']: chip['count'] for chip in strip}


class TestRead:

    def test_every_dimension_is_present_and_blanks_are_dropped(self):
        args = MultiDict([('state', 'open'), ('state', ''), ('state', 'done')])
        assert FACETS.read(args) == {'state': ['open', 'done'], 'kind': [], 'tag': []}

    def test_a_single_value_dimension_keeps_only_the_last(self):
        facets = FacetSet(Facet('view', multi=False))
        args = MultiDict([('view', 'a'), ('view', 'b')])
        assert facets.read(args) == {'view': ['b']}


class TestApply:

    def test_values_are_ored_within_a_dimension(self):
        rows = FACETS.apply(ROWS, {'state': ['open', 'done']})
        assert rows == ROWS[:3]

    def test_dimensions_are_anded(self):
        rows = FACETS.apply(ROWS, {'state': ['open'], 'kind': ['a']})
        assert rows == ROWS[:1]

    def test_a_collection_key_matches_on_any_member(self):
        assert FACETS.apply(ROWS, {'tag': ['y']}) == [ROWS[0], ROWS[3]]

    def test_skip_leaves_one_dimension_out(self):
        selected = {'state': ['done'], 'kind': ['b']}
        assert FACETS.apply(ROWS, selected, skip='state') == [ROWS[1]]


class TestStrip:

    def test_a_dimension_does_not_count_its_own_selection(self):
        strip = FACETS.strip(ROWS, {'state': ['done']}, 'state')
        assert _counts(strip)['open'] == 2

    def test_other_dimensions_scope_the_counts(self):
        strip = FACETS.strip(ROWS, {'kind': ['a']}, 'state')
        assert _counts(strip) == {'open': 1, 'done': 1, 'held': 0, 'odd': 0}

    def test_declared_order_then_extras_alphabetically(self):
        strip = FACETS.strip(ROWS, {}, 'state')
        assert [chip['value'] for chip in strip] == ['open', 'done', 'held', 'odd']

    def test_hide_zero_drops_an_empty_value_unless_selected(self):
        only_done = {'state': ['done']}
        assert _counts(FACETS.strip(ROWS, only_done, 'kind')) == {'a': 1}
        selected = {'state': ['done'], 'kind': ['b']}
        assert _counts(FACETS.strip(ROWS, selected, 'kind')) == {'a': 1, 'b': 0}

    def test_a_row_counts_once_under_each_value_it_carries(self):
        assert _counts(FACETS.strip(ROWS, {}, 'tag')) == {'x': 2, 'y': 2}

    def test_labels_and_icons_ride_on_the_chip(self):
        facets = FacetSet(Facet('state', labels={'open': 'Open'},
                                icons={'open': 'fa-door-open'}))
        chips = {c['value']: c for c in facets.strip(ROWS, {}, 'state')}
        assert chips['open']['label'] == 'Open'
        assert chips['open']['icon'] == 'fa-door-open'
        assert chips['done']['label'] == 'done' and 'icon' not in chips['done']

    def test_by_count_puts_the_busiest_value_first(self):
        facets = FacetSet(Facet('state', by_count=True))
        strip = facets.strip(ROWS, {}, 'state')
        assert [chip['value'] for chip in strip] == ['open', 'done', 'odd']

    def test_a_limit_caps_the_chips_but_keeps_the_selected(self):
        facets = FacetSet(Facet('state', by_count=True, limit=1))
        strip = facets.strip(ROWS, {'state': ['odd']}, 'state')
        assert [chip['value'] for chip in strip] == ['open', 'odd']

    def test_strips_returns_every_dimension(self):
        assert set(FACETS.strips(ROWS, {})) == {'state', 'kind', 'tag'}
