"""Route tests for the disk "Usage Over Time" chart HTMX fragment.

Exercises ``resource_details_disk_usage_chart`` at
``webapp/dashboards/user/blueprint.py`` — the fragment the disk
resource-details page lazy-loads (and re-fetches on the Data Volume /
File Count tab swap).

Auth is via the standard ``auth_client`` fixture (logged in as
``benkirk``, which has ``VIEW_PROJECTS``). The matplotlib render is
patched to a sentinel so these stay pure route/markup tests; access
control (``require_project_access``) is covered centrally by the
decorator's own tests.
"""
import re

import pytest

pytestmark = pytest.mark.unit

_DISK_RESOURCE = 'Campaign_Store'


@pytest.fixture
def _sentinel_chart(monkeypatch):
    """Patch the SVG renderer so the route returns deterministic markup
    without invoking matplotlib. Captures the metric it was called with."""
    captured = {'metric': None}

    def _fake(timeseries, link_kind=None, metric='bytes'):
        captured['metric'] = metric
        return '<svg data-test="disk-chart"></svg>'

    monkeypatch.setattr(
        'webapp.dashboards.user.blueprint.generate_disk_usage_stacked_area',
        _fake,
    )
    return captured


def _active_tab_label(html):
    """Return the label of the tab carrying the ``active`` class."""
    m = re.search(r'nav-link active.*?(Data Volume|File Count)', html, re.S)
    return m.group(1) if m else None


def _url(projcode, **params):
    qs = '&'.join(f'{k}={v}' for k, v in params.items())
    return f'/user/resource-details/disk-usage-chart/{projcode}?{qs}'


class TestDiskUsageChartRoute:

    def test_default_metric_is_bytes_tab(self, auth_client, active_project, _sentinel_chart):
        resp = auth_client.get(_url(active_project.projcode, resource=_DISK_RESOURCE))
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'Data Volume' in html and 'File Count' in html
        assert _active_tab_label(html) == 'Data Volume'
        assert _sentinel_chart['metric'] == 'bytes'

    def test_files_metric_tab_active(self, auth_client, active_project, _sentinel_chart):
        resp = auth_client.get(
            _url(active_project.projcode, resource=_DISK_RESOURCE, metric='files')
        )
        assert resp.status_code == 200
        assert _active_tab_label(resp.get_data(as_text=True)) == 'File Count'
        assert _sentinel_chart['metric'] == 'files'

    def test_invalid_metric_coerces_to_bytes(self, auth_client, active_project, _sentinel_chart):
        resp = auth_client.get(
            _url(active_project.projcode, resource=_DISK_RESOURCE, metric='bogus')
        )
        assert resp.status_code == 200
        assert _active_tab_label(resp.get_data(as_text=True)) == 'Data Volume'
        assert _sentinel_chart['metric'] == 'bytes'

    def test_missing_resource_returns_400(self, auth_client, active_project):
        resp = auth_client.get(
            f'/user/resource-details/disk-usage-chart/{active_project.projcode}'
        )
        assert resp.status_code == 400

    def test_invalid_date_returns_400(self, auth_client, active_project):
        resp = auth_client.get(
            _url(active_project.projcode, resource=_DISK_RESOURCE, start_date='not-a-date')
        )
        assert resp.status_code == 400
