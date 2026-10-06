"""Allocations dashboard: the expand modal's facility / panel / project sunburst."""

import re
from unittest.mock import patch

import pytest

from webapp.dashboards.allocations import blueprint

_URL = '/allocations/htmx/sunburst-expanded/Derecho'

_USAGE = [
    {'projcode': 'UABC0001', 'facility': 'UNIV', 'annualized_rate': 100.0, 'total_amount': 300.0,
     'total_used': 50.0},
    {'projcode': 'NCAR0001', 'facility': 'NCAR', 'annualized_rate': 40.0, 'total_amount': 80.0,
     'total_used': 20.0},
]
_PANELS = {'UABC0001': (2, 'UNIV', 'UNIV USS'), 'NCAR0001': (1, 'NCAR', 'NCAR Labs')}


@pytest.fixture
def captured():
    seen = {}

    def fake_chart(rows, center='', **kw):
        seen['rows'], seen['center'] = rows, center
        return '<svg data-test="panel"></svg>'

    with patch.object(blueprint, 'cached_allocation_usage',
                      side_effect=lambda **kw: seen.update(usage=kw) or list(_USAGE)), \
         patch.object(blueprint, 'cached_charges_by_project',
                      side_effect=lambda *a, **kw: seen.update(charges=kw) or {'UABC0001': 30.0}), \
         patch.object(blueprint, 'project_panels', return_value=_PANELS), \
         patch.object(blueprint, 'generate_panel_sunburst', side_effect=fake_chart):
        yield seen


def _values(seen):
    return {p['name']: p['value'] for r in seen['rows'] for pn in r['panels'] for p in pn['rim']}


def test_alloc_is_the_root_only_annual_rate(auth_client, captured):
    body = auth_client.get(f'{_URL}?measure=alloc&active_at=2026-10-03').get_data(as_text=True)
    assert 'data-test="panel"' in body
    assert captured['usage']['root_only'] is True
    assert _values(captured) == {'UABC0001': 100.0, 'NCAR0001': 40.0}
    assert captured['center'] == 'Annual\nrate'
    assert 'hx-swap-oob="true">Derecho: annualized allocation rate by facility' in body


def test_hpc_used_is_the_window_at_an_annual_rate_with_pills(auth_client, captured):
    body = auth_client.get(f'{_URL}?measure=used&days=90').get_data(as_text=True)
    query = captured['charges']
    assert (query['end'] - query['start']).days == 89
    assert _values(captured) == {'UABC0001': pytest.approx(30.0 * 365 / 90)}
    assert body.count('hx-target="#chartExpandModalBody"') == 4
    assert re.search(r'btn-outline-secondary active"[^>]*hx-get="[^"]*days=90', body)
    assert 'data-bs-toggle' not in body


def test_bad_measure_and_days_fall_back(auth_client, captured):
    auth_client.get(f'{_URL}?measure=bogus')
    assert 'usage' in captured and 'charges' not in captured
    auth_client.get(f'{_URL}?measure=used&days=7')
    assert (captured['charges']['end'] - captured['charges']['start']).days == 364


def test_facility_filter_narrows(auth_client, captured):
    auth_client.get(f'{_URL}?measure=used&facilities=NCAR')
    assert captured['rows'] == []


def test_unauthenticated_redirects(client):
    assert client.get(_URL).status_code == 302


def test_index_renders_the_openers(auth_client):
    body = auth_client.get('/allocations/projects').get_data(as_text=True)
    assert 'id="chartExpandModal"' in body
    assert '/allocations/htmx/sunburst-expanded/Derecho?' in body
    assert 'measure=alloc' in body
