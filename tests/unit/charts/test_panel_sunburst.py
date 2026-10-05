"""The facility / panel / project sunburst: `panel_rows` and the rim's fold rules."""

import re

import pytest

from webapp.dashboards.charts import PanelSunburst, panel_rows

_PANELS = {'A1': (1, 'NCAR', 'Labs'), 'A2': (1, 'NCAR', 'Labs'), 'A3': (1, 'NCAR', 'ARP'),
           'B1': (2, 'UNIV', 'CHAP')}
_SLOTS = {1: 2, 2: 1}


def _rows(values=None):
    return panel_rows(values or {'A1': 30.0, 'A2': 50.0, 'A3': 40.0, 'B1': 10.0, 'X9': 5.0,
                                 'ZERO': 0.0},
                      _PANELS, _SLOTS)


def test_groups_by_facility_then_panel_in_slot_and_value_order():
    rows = _rows()
    assert [r['facility'] for r in rows] == ['UNIV', 'NCAR', 'Unknown']
    ncar = rows[1]
    assert ncar['value'] == 120.0
    assert [(p['name'], p['value']) for p in ncar['panels']] == [('Labs', 80.0), ('ARP', 40.0)]
    assert [p['name'] for p in ncar['panels'][0]['projects']] == ['A2', 'A1']


def test_unmapped_projects_land_in_unknown_and_zeros_drop():
    unknown = _rows()[-1]
    assert unknown['slot'] is None
    assert unknown['panels'] == [{'name': 'Unknown', 'value': 5.0,
                                  'projects': [{'name': 'X9', 'value': 5.0}]}]
    assert 'ZERO' not in str(_rows())


def _prepared(data, **limits):
    chart = type('Limited', (PanelSunburst,), limits)(data, center='CPU-h')
    chart.layout, chart.theme = None, None
    chart.prepare()
    return chart


def _panel_totals(chart):
    totals = {}
    for w in chart.rim:
        totals[w['panel']['name']] = totals.get(w['panel']['name'], 0) + w['value']
    return totals


def test_every_project_is_drawn_by_default():
    chart = _prepared(_rows())
    assert [w['name'] for w in chart.rim] == ['B1', 'A2', 'A1', 'A3', 'X9']
    assert not chart.folded


def test_the_visibility_floor_folds_slivers_into_one_wedge_per_panel():
    tail = {f'T{i}': 0.001 for i in range(5)}
    panels = {**_PANELS, **{c: (2, 'UNIV', 'CHAP') for c in tail}}
    chart = _prepared(panel_rows({'B1': 100.0, **tail}, panels, _SLOTS))
    others = [w for w in chart.rim if w['others']]
    assert len(others) == 1 and others[0]['others'] == 5
    assert others[0]['value'] == pytest.approx(0.005)
    assert chart.folded


def test_the_wedge_budget_keeps_each_panels_top_n():
    chart = _prepared(_rows(), max_wedges=3, top_n=1)
    assert [w['name'] for w in chart.rim if not w['others']] == ['B1', 'A2', 'A3', 'X9']
    labs = [w for w in chart.rim if w['panel']['name'] == 'Labs']
    assert labs[-1]['others'] == 1 and labs[-1]['value'] == 30.0
    assert _panel_totals(chart) == {'CHAP': 10.0, 'Labs': 80.0, 'ARP': 40.0, 'Unknown': 5.0}


def test_rim_wedges_drill_to_the_project_modal_and_every_wedge_names_itself(app):
    with app.test_request_context('/'):
        svg = PanelSunburst(_rows(), center='CPU-h').render()
    assert svg.count('/user/project-details-modal/') == 5
    titles = re.findall(r'<title>([^<]+)</title>', svg)
    assert len(titles) == 3 + 4 + 5        # facilities, panels, projects
    assert any(t.startswith('A2 · ') for t in titles)


def test_the_rim_entity_is_the_charts_to_name(app):
    """Projects are today's rim; a subclass names another entity (users, by facility
    and panel) by its link and noun alone."""
    from webapp.dashboards.charts import links
    users = type('UserRim', (PanelSunburst,), {'rim_link': links.USER_MODAL, 'rim_noun': 'users',
                                              'min_wedge_deg': 100})
    with app.test_request_context('/'):
        svg = users(_rows(), center='CPU-h').render()
        assert links.USER_MODAL.url('A2') in svg
    assert '/user/project-details-modal/' not in svg
    assert '1 other Labs users · ' in svg


def test_empty():
    assert PanelSunburst([]).render().startswith('<div')
