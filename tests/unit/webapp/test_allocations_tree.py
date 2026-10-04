"""The allocations dashboard's facility tree and its sunbursts.

``build_facility_trees`` is pure, so its arithmetic is tested on literal rows; the
rendered page is parsed to hold the wiring a chart click depends on (every drill
names a facility row in its own Resource pane, and that row opens its tbody).
Design record: docs/plans/ALLOCATIONS_SUNBURST.md.
"""
import re
from datetime import datetime

import pytest

from webapp.dashboards.allocations.blueprint import build_facility_trees, elapsed_weights, sunburst_rows

from test_admin_table_conventions import _parse, _span


def _type(name, amount, count=1):
    return {'allocation_type': name, 'total_amount': amount, 'count': count}


FACILITIES = [(1, 'NCAR', True), (2, 'UNIV', True), (3, 'OLD', False), (4, 'WNA', True)]


def _trees(resource_type='HPC', elapsed=None):
    grouped = {'R': {'UNIV': [_type('Small', 30.0), _type('Large', 70.0)],
                     'NCAR': [_type('Labs', 100.0)],
                     'OLD': [_type('Legacy', 10.0)]}}
    overviews = {'R': [{'facility': 'UNIV', 'total_amount': 100.0, 'annualized_rate': 300.0, 'count': 2},
                       {'facility': 'NCAR', 'total_amount': 100.0, 'annualized_rate': 100.0, 'count': 1},
                       {'facility': 'OLD', 'total_amount': 10.0, 'annualized_rate': 0.0, 'count': 1}]}
    rates = {('R', 'UNIV', 'Small'): 75.0, ('R', 'UNIV', 'Large'): 225.0, ('R', 'NCAR', 'Labs'): 100.0}
    usage = {'R': [{'facility': 'UNIV', 'total_used': 40.0}, {'facility': 'NCAR', 'total_used': 60.0}]}
    usage_by_type = {('R', 'UNIV', 'Small'): 10.0, ('R', 'UNIV', 'Large'): 30.0, ('R', 'NCAR', 'Labs'): 60.0}
    return build_facility_trees(grouped, overviews, rates, usage, usage_by_type,
                                {'R': resource_type}, FACILITIES, elapsed)['R']


def test_rows_follow_the_slot_order_and_slots_come_from_active_facilities():
    tree = _trees()
    assert [r['facility'] for r in tree] == ['NCAR', 'UNIV', 'OLD']
    # WNA (id 4) has no rows here but still holds slot 3: colors never shift by scope.
    assert [r['slot'] for r in tree] == [1, 2, None]


def test_shares_are_of_the_parent_row():
    ncar, univ, _ = _trees()
    assert univ['alloc_share'] == pytest.approx(75.0)
    large = next(t for t in univ['types'] if t['name'] == 'Large')
    assert large['alloc_share'] == pytest.approx(75.0)
    assert ncar['types'][0]['alloc_share'] == pytest.approx(100.0)


def test_storage_charts_its_volume_not_a_rate():
    univ = _trees('DISK')[1]
    assert univ['alloc'] == 100.0
    assert [t['alloc'] for t in univ['types']] == [70.0, 30.0]


def test_rows_carry_remaining_and_percent_used():
    ncar, univ, old = _trees()
    assert (univ['remaining'], univ['pct_used']) == (60.0, pytest.approx(40.0))
    large = next(t for t in univ['types'] if t['name'] == 'Large')
    assert (large['remaining'], large['pct_used']) == (40.0, pytest.approx(30 / 70 * 100))
    assert (old['used'], old['pct_used']) == (0.0, 0.0)
    assert ncar['elapsed_pct'] is None    # no dated allocations


def _row(facility, alloc_type, amount, start, end, **extra):
    return {'resource': 'R', 'facility': facility, 'allocation_type': alloc_type,
            'total_amount': amount, 'start_date': start, 'end_date': end, **extra}


def test_elapsed_is_weighted_by_allocation_size():
    at = datetime(2026, 7, 1)
    weights = elapsed_weights([
        _row('UNIV', 'Small', 30.0, datetime(2026, 1, 1), datetime(2027, 1, 1)),  # ~50%
        _row('UNIV', 'Large', 70.0, datetime(2026, 7, 1), datetime(2027, 7, 1)),  # 0%
        _row('UNIV', 'Large', 99.0, datetime(2026, 1, 1), None, is_open_ended=True),
        _row('NCAR', 'Labs', 100.0, datetime(2025, 1, 1), datetime(2026, 1, 1)),  # ended: 100%
    ], at)
    assert ('R', 'UNIV', 'Large') in weights and weights[('R', 'UNIV', 'Large')] == (0.0, 70.0)
    ncar, univ, _ = _trees(elapsed=weights)
    assert ncar['elapsed_pct'] == pytest.approx(100.0)
    assert univ['elapsed_pct'] == pytest.approx(30 * (181 / 365) / 100 * 100)


def test_storage_rows_get_no_elapsed_tick():
    weights = elapsed_weights([_row('NCAR', 'Labs', 100.0, datetime(2025, 1, 1), datetime(2026, 1, 1))],
                              datetime(2026, 7, 1))
    assert _trees('DISK', elapsed=weights)[0]['elapsed_pct'] is None


def test_sunburst_rows_carry_the_measure():
    rows = sunburst_rows(_trees(), 'used')
    assert rows[1] == {'id': 2, 'facility': 'UNIV', 'slot': 2, 'value': 40.0,
                       'types': [{'name': 'Large', 'value': 30.0}, {'name': 'Small', 'value': 10.0}]}


@pytest.fixture
def page_html(auth_client, session):
    resp = auth_client.get('/allocations/projects')
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def _panes(root):
    content = next(d for d in root.find_all('div') if d.attrs.get('id') == 'resourceTabsContent')
    return [d for d in content.kids('div') if 'tab-pane' in d.classes]


def test_each_pane_drills_into_its_own_tree(page_html):
    panes = _panes(_parse(page_html))
    assert panes, 'no Resource panes rendered'
    checked = 0
    for pane in panes:
        assert 'data-drill-scope' in pane.attrs, pane.attrs.get('id')
        bodies = {b.attrs['id']: b for b in pane.find_all('tbody') if 'data-alloc-facility' in b.attrs}
        rows = {r.attrs['data-facility-id']: r for r in pane.find_all('tr') if 'data-facility-id' in r.attrs}
        for fid, row in rows.items():
            assert row.attrs.get('data-bs-target', '').lstrip('#') in bodies, fid
        for a in (a for f in pane.find_all('figure') for a in f.find_all('a')):
            href = a.attrs.get('xlink:href') or a.attrs.get('href') or ''
            m = re.fullmatch(r'#sam/row/data-facility-id/(\d+)', href)
            if m:
                assert m.group(1) in rows, f'{pane.attrs.get("id")}: drill to a facility not in this tree'
                checked += 1
    assert checked, 'no sunburst drill links rendered'


def test_tree_rows_span_the_header(page_html):
    problems = []
    for table in _parse(page_html).find_all('table'):
        if 'alloc-tree' not in table.classes:
            continue
        assert 'align-middle' in table.classes
        width = _span(table.kids('thead')[0].kids('tr')[0])
        for section in table.kids('tbody', 'tfoot'):
            for row in section.kids('tr'):
                if _span(row) != width:
                    problems.append(' '.join(row.all_text().split())[:60])
    assert not problems, problems[:5]


def test_no_custom_row_toggles_remain(page_html):
    assert 'alloc-toggle-' not in page_html
