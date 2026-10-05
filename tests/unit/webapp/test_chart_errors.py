"""A chart that raises: one rule at every call site (`webapp.utils.charts.draw_chart`)."""
import ast
import importlib
import inspect
from unittest.mock import patch

import pytest

from webapp.dashboards.allocations import blueprint as alloc_bp
from webapp.dashboards.status import blueprint as status_bp

pytestmark = pytest.mark.usefixtures('session')

_CALLERS = ('webapp.jobs.routes', 'webapp.disk_scans.routes',
            'webapp.dashboards.allocations.blueprint', 'webapp.dashboards.user.blueprint',
            'webapp.dashboards.status.blueprint', 'webapp.dashboards.admin.facilities_routes')


def _boom(*args, **kwargs):
    raise RuntimeError('savefig fell over: secret detail')


@pytest.mark.parametrize('mod_name', _CALLERS)
def test_no_route_calls_a_chart_directly(mod_name):
    """`BaseChart` swallows nothing, so every call goes through `draw_chart`."""
    tree = ast.parse(inspect.getsource(importlib.import_module(mod_name)))
    direct = [f'{node.func.id}:{node.lineno}' for node in ast.walk(tree)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
              and node.func.id.startswith('generate_')]
    assert not direct, f'{mod_name} calls a chart outside draw_chart: {direct}'


def test_a_fragment_whose_chart_raises_is_a_200_with_the_error_state(auth_client, caplog):
    """htmx does not swap a 500: the loader's spinner would stay up forever."""
    with patch.object(alloc_bp, 'generate_pace_chart_matplotlib', side_effect=_boom):
        response = auth_client.get('/allocations/htmx/pace-chart/Derecho?active_at=2026-10-01')
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'data-chart-error' in body and 'could not be drawn' in body
    assert 'secret detail' not in body          # the log has it; the page does not
    assert any('chart' in r.message and 'failed' in r.message for r in caplog.records)


def test_a_full_page_whose_chart_raises_still_renders(client, status_session):
    with patch.object(status_bp, 'generate_queue_history_matplotlib', side_effect=_boom):
        response = client.get('/status/queue-history/derecho/main')
    assert response.status_code == 200
    assert b'data-chart-error' in response.data and b'Queue History' in response.data


def test_a_cached_page_does_not_cache_a_chart_error(app):
    from flask import g
    from webapp.utils.charts import draw_chart, no_chart_failed
    with app.test_request_context('/allocations/projects'):
        assert no_chart_failed() is True
        assert 'data-chart-error' in draw_chart(_boom)
        assert g.chart_failed and no_chart_failed() is False
    # The one cached page that inlines charts declines to cache such a response.
    decorators = inspect.getsource(alloc_bp).split('def projects():')[0][-200:]
    assert 'response_filter=no_chart_failed' in decorators
