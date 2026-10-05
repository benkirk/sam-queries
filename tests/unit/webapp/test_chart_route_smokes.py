"""Chart routes that had only a route-map pin: each renders an SVG with a working drill.

Data is patched in (the status tier is an empty SQLite file, and a snapshot refresh
may leave a project with one user or none); the route, template and chart are real.
"""
from datetime import date, datetime
from unittest.mock import patch

import pytest

from webapp.dashboards.charts import links
from webapp.dashboards.status import blueprint as status_bp
from webapp.dashboards.user import blueprint as user_bp

pytestmark = pytest.mark.usefixtures('session')

_DAYS = [date(2026, 3, d) for d in range(1, 6)]
_BY_USER = {'dates': _DAYS, 'series': [
    {'label': 'Others', 'values': [1.0, 0.0, 2.0, 3.0, 1.5]},
    {'label': 'alice', 'values': [5.0, 0.0, 20.0, 30.0, 20.0]},
    {'label': 'bob', 'values': [4.0, 0.0, 13.5, 29.0, 19.75]},
]}
_FLAT = {'daily_charges': {'dates': _DAYS, 'values': [10.0, 0.0, 35.5, 62.0, 41.25]}}
_QUEUE_LOAD = {
    'dates': [datetime(2026, 3, 1, h) for h in range(5)],
    'series': [{'label': 'Others', 'values': [2, 2, 3, 3, 4]},
               {'label': 'PROJ0002', 'values': [10, 11, 12, 13, 14]},
               {'label': 'PROJ0001', 'values': [20, 22, 24, 26, 28]}],
    'metric_label': 'Jobs', 'group_by_label': 'project', 'has_gpus': False,
}


def _usage_chart(auth_client, project, stacked, detail):
    with patch.object(user_bp, 'get_daily_user_usage_for_project', return_value=stacked), \
            patch.object(user_bp, 'get_resource_detail_data', return_value=detail):
        response = auth_client.get(
            f'/user/resource-details/usage-chart/{project.projcode}?resource=Derecho')
    assert response.status_code == 200
    return response.get_data(as_text=True)


def test_usage_chart_stacks_by_user_and_drills_to_day_and_user(auth_client, active_project):
    body = _usage_chart(auth_client, active_project, _BY_USER, _FLAT)
    assert '<svg' in body
    assert links.DAY.url('2026-03-01') in body and links.USAGE_USER.url('alice') in body


def test_usage_chart_with_one_user_draws_flat_bars_that_drill_to_the_day(
        auth_client, active_project):
    one_user = {'dates': _DAYS, 'series': _BY_USER['series'][:2]}
    body = _usage_chart(auth_client, active_project, one_user, _FLAT)
    assert '<svg' in body and links.DAY.url('2026-03-01') in body
    assert links.USAGE_USER.url('alice') not in body


@pytest.mark.parametrize('url', ['/status/htmx/system/derecho/user-proj-chart',
                                 '/status/htmx/queue-history/derecho/main/user-proj-chart'])
def test_status_user_project_chart_links_its_legend_for_an_operator(auth_client, url):
    with patch.object(status_bp.status_queries, 'get_user_proj_timeseries',
                      return_value=_QUEUE_LOAD):
        response = auth_client.get(f'{url}?group_by=project')
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert '<svg' in body and '/PROJ0001' in body


def test_facilities_card_draws_the_fair_share_sunburst_with_a_facility_drill(auth_client):
    body = auth_client.get('/admin/htmx/facilities').get_data(as_text=True)
    if 'No active facility has a fair share' in body:
        pytest.skip('the snapshot has no facility with a fair share')
    chart = body[body.index('fair-share-chart'):]
    assert '<svg' in chart and links.FACILITY_ROW.url('') in chart
