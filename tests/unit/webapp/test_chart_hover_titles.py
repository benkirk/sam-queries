"""Project titles in chart hovers: who gets them (`webapp.utils.charts.hover_titles`)."""
import html
import re

import pytest
from flask_login import login_user, logout_user

from sam.queries.projects import project_facilities, project_titles
from webapp.dashboards.charts import (
    generate_jobs_usage_pie_chart, generate_pace_chart_matplotlib, generate_panel_sunburst,
    panel_rows,
)
from webapp.utils import charts as chart_utils
from webapp.utils import rbac
from webapp.utils.rbac import Permission

_TITLES = {'P1': 'Climate   of the\nfuture', 'P2': 'x' * 200}


def _stub(username):
    user = type('U', (), {})()
    user.is_authenticated, user.is_active, user.is_anonymous = True, True, False
    user.user_id, user.username, user.roles = 4242, username, set()
    user.get_id = lambda: '4242'
    return user


@pytest.fixture
def as_viewer(app, monkeypatch):
    """Run `hover_titles` as a stub viewer holding the given facility-scoped grants."""
    seen = {}
    monkeypatch.setattr(chart_utils, 'project_titles',
                        lambda session, codes, facility_names=None: seen.update(
                            codes=set(codes), facilities=facility_names) or {'P1': 'T'})

    def run(scoped, codes=('P1', 'P2', None, ''), **kw):
        monkeypatch.setattr(rbac, 'USER_FACILITY_PERMISSIONS', {'viewer': scoped})
        seen.clear()
        with app.test_request_context('/'):
            login_user(_stub('viewer'))
            try:
                return chart_utils.hover_titles(codes, **kw), dict(seen)
            finally:
                logout_user()
    return run


def test_a_viewer_with_no_project_scope_gets_no_titles_and_no_query(as_viewer):
    assert as_viewer({}) == ({}, {})


def test_a_facility_scoped_viewer_is_limited_to_that_facility(as_viewer):
    titles, seen = as_viewer({'WNA': {Permission.VIEW_PROJECTS}})
    assert titles == {'P1': 'T'}
    assert seen == {'codes': {'P1', 'P2'}, 'facilities': {'WNA'}}


def test_own_projects_need_no_scope(as_viewer):
    _titles, seen = as_viewer({}, own=True)
    assert seen == {'codes': {'P1', 'P2'}, 'facilities': None}


def test_an_anonymous_viewer_gets_none(app):
    with app.test_request_context('/'):
        assert chart_utils.hover_titles(['P1']) == {}


def test_the_query_filters_by_facility(session, active_project):
    code = active_project.projcode
    facility = project_facilities(session, [code]).get(code, (None, None))[1]
    if facility is None:
        pytest.skip('the representative project has no facility')
    assert project_titles(session, [code], facility_names=[facility]) == {code: active_project.title}
    assert project_titles(session, [code], facility_names=['NO-SUCH-FACILITY']) == {}
    assert project_titles(session, [code]) == {code: active_project.title}


def _hover(svg):
    return [html.unescape(t) for t in re.findall(r'<title>([^<]+)</title>', svg)]


def test_a_title_sits_beside_its_code_normalized_and_capped(app):
    usage = {'rows': [{'value': 'P1', 'cpu_hours': 60.0}, {'value': 'P2', 'cpu_hours': 30.0},
                      {'value': 'P3', 'cpu_hours': 10.0}], 'totals': {'cpu_hours': 100.0}}
    with app.test_request_context('/'):
        plain = _hover(generate_jobs_usage_pie_chart(usage, row_attr='data-job-project'))
        titled = _hover(generate_jobs_usage_pie_chart(usage, row_attr='data-job-project',
                                                      titles=_TITLES))
    assert plain[0] == 'P1 · 60.0% · 60'
    assert titled[0] == 'P1 · Climate of the future · 60.0% · 60'
    assert titled[1] == 'P2 · ' + 'x' * 79 + '… · 30.0% · 30'
    assert titled[2] == plain[2]                 # no title known: the hover is unchanged


def test_titles_are_part_of_the_cache_key():
    key = generate_jobs_usage_pie_chart.chart_class.cache_key
    usage = {'rows': [{'value': 'P1', 'cpu_hours': 1.0}], 'totals': {'cpu_hours': 1.0}}
    assert key(usage) == key(usage, titles=None) == key(usage, titles={})
    assert key(usage, titles={'P1': 'Old'}) != key(usage, titles={'P1': 'New'})


def test_the_three_ring_rim_and_pace_carry_titles(app):
    rows = panel_rows({'P1': 30.0, 'P2': 10.0}, {'P1': (1, 'NCAR', 'Labs'), 'P2': (1, 'NCAR', 'Labs')},
                      {1: 1})
    with app.test_request_context('/'):
        rim = _hover(generate_panel_sunburst(rows, 'CPU-h', titles={'P1': 'Storms'}))
    assert any(t.startswith('P1 · Storms · ') for t in rim)
    assert any(t.startswith('P2 · ') and 'Storms' not in t for t in rim)
    assert 'titles' in generate_pace_chart_matplotlib.chart_class.cache_key.__code__.co_varnames
