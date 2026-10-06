"""The "By facility" switch on By Project and By User: the groupings and their route wiring."""

from __future__ import annotations

import pytest

from _jobs_helpers import _PROJECT_USAGE, _USER_ACCOUNT_USAGE, _install_mock_plugin

from webapp.jobs import routes
from webapp.jobs.routes import _facility_rings, _facility_user_rings, _users_from_pairs

_URL = '/dashboards/user/jobs/machine/derecho/by-project'
_USER_URL = '/dashboards/user/jobs/machine/derecho/by-user'

_ROWS = [
    {'value': 'NCAR0001', 'job_count': 5, 'cpu_hours': 500.0, 'gpu_hours': 0.0},
    {'value': 'UNIV0001', 'job_count': 50, 'cpu_hours': 400.0, 'gpu_hours': 0.0},
    {'value': 'NCAR0002', 'job_count': 6, 'cpu_hours': 300.0, 'gpu_hours': 9.0},
    {'value': 'NCAR0003', 'job_count': 7, 'cpu_hours': 200.0, 'gpu_hours': 0.0},
    {'value': 'NCAR0004', 'job_count': 8, 'cpu_hours': 100.0, 'gpu_hours': 0.0},
    {'value': 'ORPHAN01', 'job_count': 1, 'cpu_hours': 50.0, 'gpu_hours': 0.0},
    {'value': None, 'job_count': 1, 'cpu_hours': 10.0, 'gpu_hours': 0.0},
]
_FACILITY_OF = {'NCAR0001': (1, 'NCAR'), 'NCAR0002': (1, 'NCAR'), 'NCAR0003': (1, 'NCAR'),
                'NCAR0004': (1, 'NCAR'), 'UNIV0001': (2, 'UNIV')}
_SLOTS = {1: 1, 2: 2}


def _rings(metric='cpu_hours', linked=frozenset()):
    return _facility_rings(_ROWS, metric, _FACILITY_OF, _SLOTS, linked, top_n=3)


def test_facilities_in_slot_order_unknown_last():
    rings = _rings()
    assert [r['facility'] for r in rings] == ['NCAR', 'UNIV', 'Unknown']
    assert [r['slot'] for r in rings] == [1, 2, None]


def test_order_holds_when_the_metric_reorders_values():
    """UNIV leads on jobs, but keeps its slot position (and so its color)."""
    assert [r['facility'] for r in _rings('jobs')] == ['NCAR', 'UNIV', 'Unknown']


def test_facility_value_is_every_project_but_only_top_three_are_named():
    ncar = _rings()[0]
    assert ncar['value'] == 1100.0
    assert [t['name'] for t in ncar['types']] == ['NCAR0001', 'NCAR0002', 'NCAR0003']
    assert ncar['others'] == 1 and _rings()[1]['others'] == 0


def test_the_remainder_wedge_says_how_many_projects_it_holds(app):
    from webapp.dashboards.charts import generate_jobs_facility_sunburst
    with app.test_request_context('/'):
        svg = generate_jobs_facility_sunburst(_rings(), 'CPU-h')
    assert '<title>1 other NCAR projects · ' in svg


def test_unmapped_and_null_accounts_share_the_unknown_ring():
    unknown = _rings()[-1]
    assert unknown['value'] == 60.0
    assert [t['name'] for t in unknown['types']] == ['ORPHAN01', '(unknown)']


def test_only_projects_in_the_table_drill():
    ncar = _rings(linked={'NCAR0001', 'NCAR0003'})[0]
    assert [t['linked'] for t in ncar['types']] == [True, False, True]


def test_zero_value_facilities_drop():
    rings = _rings('gpu_hours')
    assert [r['facility'] for r in rings] == ['NCAR']
    assert [t['name'] for t in rings[0]['types']] == ['NCAR0002']


def test_charges_sum_both_charge_keys():
    rows = [{'value': 'UNIV0001', 'cpu_charges': 3.0, 'gpu_charges': 4.0}]
    assert _facility_rings(rows, 'charges', _FACILITY_OF, _SLOTS, set())[0]['value'] == 7.0


@pytest.fixture
def facilities(monkeypatch):
    monkeypatch.setattr(routes, 'project_facilities', lambda session, codes: {
        'SCSG0001': (1, 'NCAR'), 'UABC0002': (2, 'UNIV')})


def test_switch_off_by_default(app, auth_client, monkeypatch, facilities):
    captured = _install_mock_plugin(app, monkeypatch, jobs_usage_by_return=_PROJECT_USAGE)
    body = auth_client.get(_URL).get_data(as_text=True)
    assert captured['last_jobs_usage_by'][1]['limit'] == routes._BY_USER_LIMIT
    assert 'id="jobs-byfac-' in body
    assert 'By facility' in body
    assert 'name="by_facility"' not in body


def test_switch_on_fetches_every_project_and_round_trips(app, auth_client, monkeypatch,
                                                          facilities):
    captured = _install_mock_plugin(app, monkeypatch, jobs_usage_by_return=_PROJECT_USAGE)
    body = auth_client.get(f'{_URL}?by_facility=1').get_data(as_text=True)
    assert captured['last_jobs_usage_by'][1]['limit'] is None
    assert '<input type="hidden" name="by_facility" value="1">' in body
    assert "?by_facility=0&metric=cpu_hours" in body
    assert '#sam/row/data-job-project/SCSG0001' in body     # outer wedge drill
    assert 'UNIV' in body and 'NCAR' in body                 # inner ring labels
    assert 'data-job-project="SCSG0001"' in body             # table unchanged


_PAIRS = [
    {'user': 'alice', 'account': 'NCAR0001', 'job_count': 5, 'cpu_hours': 500.0, 'gpu_hours': 0.0},
    {'user': 'alice', 'account': 'UNIV0001', 'job_count': 2, 'cpu_hours': 50.0, 'gpu_hours': 0.0},
    {'user': 'bob', 'account': 'NCAR0002', 'job_count': 6, 'cpu_hours': 300.0, 'gpu_hours': 9.0},
    {'user': 'bob', 'account': 'NCAR0003', 'job_count': 1, 'cpu_hours': 100.0, 'gpu_hours': 0.0},
    {'user': 'carol', 'account': 'NCAR0004', 'job_count': 8, 'cpu_hours': 90.0, 'gpu_hours': 0.0},
    {'user': 'dave', 'account': 'NCAR0004', 'job_count': 8, 'cpu_hours': 80.0, 'gpu_hours': 0.0},
    {'user': 'erin', 'account': None, 'job_count': 1, 'cpu_hours': 10.0, 'gpu_hours': 0.0},
]


def _user_rings(metric='cpu_hours', linked=frozenset()):
    return _facility_user_rings(_PAIRS, metric, _FACILITY_OF, _SLOTS, linked, top_n=3)


def test_users_group_by_their_projects_facility_and_recur_across_facilities():
    rings = _user_rings()
    assert [r['facility'] for r in rings] == ['NCAR', 'UNIV', 'Unknown']
    ncar, univ, unknown = rings
    assert ncar['value'] == 1070.0                       # bob's two projects summed
    assert [(t['name'], t['value']) for t in ncar['types']] == \
        [('alice', 500.0), ('bob', 400.0), ('carol', 90.0)]
    assert ncar['others'] == 1
    assert [t['name'] for t in univ['types']] == ['alice']
    assert [t['name'] for t in unknown['types']] == ['erin']


def test_only_users_in_the_table_drill():
    assert [t['linked'] for t in _user_rings(linked={'bob'})[0]['types']] == [False, True, False]


def test_pairs_fold_to_one_row_per_user_ranked_by_the_metric():
    out = _users_from_pairs({'rows': _PAIRS, 'totals': {'job_count': 31}}, 'cpu_hours')
    assert out['dimension'] == 'user' and out['totals'] == {'job_count': 31}
    assert [(r['value'], r['cpu_hours'], r['job_count']) for r in out['rows']] == [
        ('alice', 550.0, 7), ('bob', 400.0, 7), ('carol', 90.0, 8), ('dave', 80.0, 8),
        ('erin', 10.0, 1)]
    assert [r['value'] for r in _users_from_pairs({'rows': _PAIRS}, 'jobs')['rows']][:2] == \
        ['carol', 'dave']


def test_by_user_switch_fetches_pairs_and_draws_users_on_the_rim(app, auth_client, monkeypatch,
                                                                 facilities):
    captured = _install_mock_plugin(app, monkeypatch, jobs_usage_by_return=_PROJECT_USAGE,
                                    jobs_usage_by_pair_return=_USER_ACCOUNT_USAGE)
    body = auth_client.get(f'{_USER_URL}?by_facility=1').get_data(as_text=True)
    assert captured['last_jobs_usage_by_pair'][0] == ('user', 'account')
    assert captured['last_jobs_usage_by'] is None            # one rollup serves both
    assert 'id="jobs-byfac-' in body and "top users outside" in body
    assert '#sam/row/data-job-user/alice' in body              # outer wedge drill
    assert 'data-job-user="alice"' in body                    # the folded table
    assert body.index('data-job-user="alice"') < body.index('data-job-user="bob"')
    assert '/machine/derecho/by-user/expanded?metric=cpu_hours' in body


def test_by_user_switch_off_keeps_the_single_rollup(app, auth_client, monkeypatch):
    captured = _install_mock_plugin(app, monkeypatch, jobs_usage_by_return=_PROJECT_USAGE)
    body = auth_client.get(_USER_URL).get_data(as_text=True)
    assert captured['last_jobs_usage_by'][0] == 'user'
    assert captured['last_jobs_usage_by_pair'] is None
    assert 'id="jobs-byfac-' in body


@pytest.fixture
def panels(monkeypatch):
    monkeypatch.setattr(routes, 'project_panels', lambda session, codes: {
        'SCSG0001': (1, 'NCAR', 'CISL USS'), 'UABC0002': (2, 'UNIV', 'UNIV USS')})


def test_by_facility_chart_carries_the_expand_opener(app, auth_client, monkeypatch, facilities):
    _install_mock_plugin(app, monkeypatch, jobs_usage_by_return=_PROJECT_USAGE)
    body = auth_client.get(f'{_URL}?metric=jobs').get_data(as_text=True)
    assert 'data-chart-expand' in body
    assert '/machine/derecho/by-project/expanded?metric=jobs' in body
    assert 'hx-include="#jobs-' in body


def test_expanded_fetches_every_project_by_panel(app, auth_client, monkeypatch, panels):
    captured = _install_mock_plugin(app, monkeypatch, jobs_usage_by_return=_PROJECT_USAGE)
    body = auth_client.get(f'{_URL}/expanded?metric=cpu_hours&start=2026-07-01')\
        .get_data(as_text=True)
    assert captured['last_jobs_usage_by'][1]['limit'] is None
    assert 'id="chartExpandModalTitle" hx-swap-oob="true">Derecho: CPU-hours by facility' in body
    assert '/user/project-details-modal/SCSG0001' in body     # project drill
    assert 'CISL USS' in body and 'UNIV USS' in body          # panel ring
    assert 'Jobs from 2026-07-01 to today.' in body
    assert 'data-bs-toggle' not in body                       # in-modal: no toggles


@pytest.mark.parametrize('entity', ['by-project', 'by-user'])
def test_expanded_is_machine_mode_only(app, auth_client, monkeypatch, entity):
    _install_mock_plugin(app, monkeypatch, jobs_usage_by_return=_PROJECT_USAGE)
    assert auth_client.get(f'/dashboards/user/jobs/user/derecho/{entity}/expanded')\
        .status_code == 404


@pytest.mark.parametrize('entity', ['by-project', 'by-user'])
def test_expanded_needs_view_all_job_data(app, non_admin_client, monkeypatch, entity):
    _install_mock_plugin(app, monkeypatch, jobs_usage_by_return=_PROJECT_USAGE)
    assert non_admin_client.get(f'/dashboards/user/jobs/machine/derecho/{entity}/expanded')\
        .status_code == 403


def test_user_expanded_puts_users_under_panels(app, auth_client, monkeypatch, panels):
    captured = _install_mock_plugin(app, monkeypatch, jobs_usage_by_pair_return=_USER_ACCOUNT_USAGE)
    body = auth_client.get(f'{_USER_URL}/expanded?metric=cpu_hours&start=2026-07-01')\
        .get_data(as_text=True)
    assert captured['last_jobs_usage_by_pair'][0] == ('user', 'account')
    assert 'hx-swap-oob="true">Derecho: CPU-hours by facility, panel and user' in body
    assert body.count('/admin/user/alice') == 2                # alice under both panels
    assert '/admin/user/bob' in body and '/admin/user/carol' in body
    assert 'CISL USS' in body and 'UNIV USS' in body          # panel ring
    assert 'click a user to open it' in body and "panel\u2019s users" in body
    assert 'data-bs-toggle' not in body


def test_user_expanded_links_nothing_without_view_users(app, auth_client, monkeypatch, panels):
    _install_mock_plugin(app, monkeypatch, jobs_usage_by_pair_return=_USER_ACCOUNT_USAGE)
    monkeypatch.setattr(routes, '_usage_affordance_permission', lambda entity, mode: False)
    body = auth_client.get(f'{_USER_URL}/expanded').get_data(as_text=True)
    assert '/admin/user/' not in body
    assert '<title>alice · ' in body                          # hovers stay
    assert 'click a user' not in body
