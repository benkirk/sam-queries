"""Allocations dashboard: the HPC/DAV Used sunburst over a trailing window."""

import re
from unittest.mock import patch

import pytest

from webapp.dashboards.allocations import blueprint
from webapp.dashboards.allocations.blueprint import window_sunburst_rows

_URL = '/allocations/htmx/used-sunburst/Derecho'
_CHARGES = [
    {'resource': 'Derecho', 'facility': 'UNIV', 'allocation_type': 'Small', 'charges': 300.0},
    {'resource': 'Derecho', 'facility': 'NCAR', 'allocation_type': 'NSC', 'charges': 100.0},
    {'resource': 'Derecho', 'facility': None, 'allocation_type': None, 'charges': 5.0},
]
_FACILITIES = [(1, 'NCAR', True), (2, 'UNIV', True), (6, 'XSEDE', False)]


class TestWindowSunburstRows:
    def test_rate_ring_order_and_slots_unknown_last(self):
        rows = window_sunburst_rows(_CHARGES, _FACILITIES, 1)
        assert [(r['facility'], r['slot']) for r in rows] == [('NCAR', 1), ('UNIV', 2), ('Unknown', None)]

    def test_values_are_scaled(self):
        univ = window_sunburst_rows(_CHARGES, _FACILITIES, 365 / 90)[1]
        assert univ['value'] == pytest.approx(300 * 365 / 90)
        assert univ['types'] == [{'name': 'Small', 'value': pytest.approx(300 * 365 / 90)}]


@pytest.fixture
def captured():
    seen = {}

    def fake_chart(rows, center='', **kw):
        seen['rows'], seen['center'] = rows, center
        return '<svg data-test="used"></svg>'

    with patch.object(blueprint, 'cached_charges_by_facility_type',
                      side_effect=lambda *a, **kw: seen.update(query=kw) or list(_CHARGES)), \
         patch.object(blueprint, 'generate_allocation_sunburst', side_effect=fake_chart):
        yield seen


def test_defaults_to_a_year_with_four_pills(auth_client, captured):
    body = auth_client.get(f'{_URL}?active_at=2026-10-03').get_data(as_text=True)
    assert 'data-test="used"' in body
    assert body.count('used-sunburst/Derecho?') == 4
    assert re.search(r'class="btn btn-secondary active"[^>]*>\s*1 yr', body)
    assert (captured['query']['end'] - captured['query']['start']).days == 364
    assert captured['center'] == 'Use\nrate'
    assert 'Charges in the year to' in body


def test_invalid_days_falls_back_to_a_year(auth_client, captured):
    auth_client.get(f'{_URL}?days=7')
    assert (captured['query']['end'] - captured['query']['start']).days == 364


def test_ninety_days_annualizes(auth_client, captured):
    body = auth_client.get(f'{_URL}?days=90').get_data(as_text=True)
    assert (captured['query']['end'] - captured['query']['start']).days == 89
    univ = next(r for r in captured['rows'] if r['facility'] == 'UNIV')
    assert univ['value'] == pytest.approx(300 * 365 / 90)
    assert 'at an annual rate' in body


def test_facility_filter_narrows_and_survives_the_pills(auth_client, captured):
    body = auth_client.get(f'{_URL}?facilities=UNIV').get_data(as_text=True)
    assert [r['facility'] for r in captured['rows']] == ['UNIV']
    assert len(re.findall(r'used-sunburst/Derecho\?[^"]*facilities=UNIV', body)) == 4
    assert re.search(r'sunburst-expanded/Derecho\?[^"]*facilities=UNIV', body)


def test_unauthenticated_redirects(client):
    assert client.get(_URL).status_code == 302


def test_index_loads_hpc_usage_lazily_and_storage_inline(auth_client):
    body = auth_client.get('/allocations/projects').get_data(as_text=True)
    assert '/allocations/htmx/used-sunburst/Derecho' in body
    assert '/allocations/htmx/used-sunburst/Campaign_Store' not in body
