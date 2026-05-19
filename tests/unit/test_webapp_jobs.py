"""Tests for the hpc-usage-queries Flask integration (webapp/jobs/*).

Covers four layers:

1. ``init_job_history`` — startup hook: disabled state when no machines
   configured, mocked-plugin success path, plugin-missing graceful path,
   per-machine error containment.

2. ``service.search_jobs`` — always pins ``account=project.projcode`` and
   forwards every other filter verbatim to ``JobQueries.jobs_search``.

3. ``routes.jobs_fragment`` — HTMX endpoint surface: disabled banner,
   400 on bad machine, 404 on unknown projcode, happy path when the
   service layer returns rows.

4. ``gather_runtime_state`` — admin-config DB card adds one row per
   cached engine; produces no extra rows when the plugin is disabled.

The session-scoped ``app`` fixture from ``tests/conftest.py`` uses
``TestingConfig`` which sets ``JOB_HISTORY_MACHINES = []``, so the
plugin starts off disabled in every test and individual cases enable
it via fresh Flask apps + monkeypatch on the plugin loader.
"""

from __future__ import annotations

import types
from unittest.mock import MagicMock

import pytest
from flask import Flask


# ---------------------------------------------------------------------------
# init_job_history — startup hook
# ---------------------------------------------------------------------------

def test_init_job_history_disabled_when_no_machines(app):
    """TestingConfig sets JOB_HISTORY_MACHINES=[], so is_enabled() is False."""
    from webapp.jobs import is_enabled, get_engines, get_module

    with app.app_context():
        assert is_enabled() is False
        assert get_engines() == {}
        assert get_module() is None


def _build_isolated_app(machines):
    """A minimal Flask app with only the JOB_HISTORY_* config init_job_history needs."""
    a = Flask(__name__)
    a.config['JOB_HISTORY_MACHINES'] = machines
    a.config['JOB_HISTORY_POOL_KWARGS'] = {}
    return a


def test_init_job_history_with_mock_plugin_registers_engines(monkeypatch):
    """A mock plugin produces engines for each configured machine."""
    from webapp.jobs.session import init_job_history, get_engines, get_module, is_enabled

    fake_engines = {
        'derecho': MagicMock(name='derecho_engine'),
        'casper':  MagicMock(name='casper_engine'),
    }
    fake_mod = types.SimpleNamespace(
        get_engine=lambda machine, pool_kwargs=None: fake_engines[machine],
        get_session=lambda machine, engine=None: None,
        JobQueries=object,
    )
    monkeypatch.setattr('sam.plugins.HPC_USAGE_QUERIES.load', lambda: fake_mod)

    a = _build_isolated_app(['derecho', 'casper'])
    init_job_history(a)

    with a.app_context():
        assert is_enabled() is True
        assert get_module() is fake_mod
        eng = get_engines()
        assert eng['derecho'] is fake_engines['derecho']
        assert eng['casper']  is fake_engines['casper']


def test_init_job_history_plugin_missing_does_not_raise(monkeypatch):
    """When the plugin import fails, the webapp still boots."""
    from sam.plugins import PluginUnavailableError
    from webapp.jobs.session import init_job_history, is_enabled

    def _raise():
        raise PluginUnavailableError('plugin not installed')
    monkeypatch.setattr('sam.plugins.HPC_USAGE_QUERIES.load', _raise)

    a = _build_isolated_app(['derecho'])
    init_job_history(a)  # must not raise

    with a.app_context():
        assert is_enabled() is False


def test_init_job_history_engine_failure_skips_machine(monkeypatch):
    """One bad machine logs and is skipped; healthy machines still come up."""
    from webapp.jobs.session import init_job_history, get_engines, is_enabled

    good_engine = MagicMock(name='good_engine')

    def _get_engine(machine, pool_kwargs=None):
        if machine == 'casper':
            raise RuntimeError('postgres down')
        return good_engine

    fake_mod = types.SimpleNamespace(
        get_engine=_get_engine,
        get_session=lambda machine, engine=None: None,
        JobQueries=object,
    )
    monkeypatch.setattr('sam.plugins.HPC_USAGE_QUERIES.load', lambda: fake_mod)

    a = _build_isolated_app(['derecho', 'casper'])
    init_job_history(a)

    with a.app_context():
        assert is_enabled() is True            # at least one engine came up
        eng = get_engines()
        assert 'derecho' in eng
        assert 'casper' not in eng


# ---------------------------------------------------------------------------
# service.search_jobs — projcode pinning + filter forwarding
# ---------------------------------------------------------------------------

def _install_mock_plugin(app, monkeypatch, *, jobs_search_return=None,
                        machines=('derecho',)):
    """Wire a mock job_history module onto app.extensions and return the
    captured JobQueries kwargs so tests can assert on the call.

    Uses ``monkeypatch.setitem`` so the original (empty/None) extension
    state is restored at test teardown — the ``app`` fixture is
    session-scoped and shared across the whole xdist worker.
    """
    captured = {'last_jobs_search_kwargs': None}

    class FakeJobQueries:
        def __init__(self, session, machine='derecho'):
            self.session = session
            self.machine = machine
        def jobs_search(self, **kwargs):
            captured['last_jobs_search_kwargs'] = kwargs
            return jobs_search_return or []

    fake_session = MagicMock(name='jh_session')
    fake_mod = types.SimpleNamespace(
        get_engine=lambda machine, pool_kwargs=None: MagicMock(name=f'engine_{machine}'),
        get_session=lambda machine, engine=None: fake_session,
        JobQueries=FakeJobQueries,
    )
    new_state = {
        'module':  fake_mod,
        'engines': {m: MagicMock(name=f'engine_{m}') for m in machines},
        'enabled': True,
    }
    monkeypatch.setitem(app.extensions, 'hpc_usage_queries', new_state)
    return captured


def test_search_jobs_pins_account_to_projcode(app, active_project, monkeypatch):
    """projcode is forwarded as the account filter — regardless of caller input."""
    from webapp.jobs import service

    captured = _install_mock_plugin(app, monkeypatch)

    with app.app_context():
        service.search_jobs(
            'derecho',
            project=active_project,
            user='someone',
            queue='main',
            limit=50,
        )

    kw = captured['last_jobs_search_kwargs']
    assert kw['account'] == active_project.projcode
    assert kw['user']    == 'someone'
    assert kw['queue']   == 'main'
    assert kw['limit']   == 50


def test_search_jobs_requires_project():
    from webapp.jobs import service
    with pytest.raises(ValueError):
        service.search_jobs('derecho', project=None)


# ---------------------------------------------------------------------------
# routes.jobs_fragment — HTMX endpoint surface
# ---------------------------------------------------------------------------

def test_jobs_fragment_renders_disabled_banner(auth_client, active_project):
    """When the plugin is off the route returns 200 with the 'unavailable' alert."""
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}?machine=derecho'
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'Per-job data is unavailable' in body


def test_jobs_fragment_400_on_missing_machine(app, auth_client, active_project, monkeypatch):
    _install_mock_plugin(app, monkeypatch)
    resp = auth_client.get(f'/dashboards/user/jobs/{active_project.projcode}')
    assert resp.status_code == 400


def test_jobs_fragment_400_on_invalid_machine(app, auth_client, active_project, monkeypatch):
    _install_mock_plugin(app, monkeypatch)
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}?machine=gust'
    )
    assert resp.status_code == 400


def test_jobs_fragment_404_on_unknown_projcode(auth_client):
    """require_project_access raises 404 via get_project_or_404 for unknown codes."""
    resp = auth_client.get('/dashboards/user/jobs/NOPE9999?machine=derecho')
    assert resp.status_code == 404


def test_jobs_fragment_renders_rows_when_enabled(
    app, auth_client, active_project, monkeypatch,
):
    """Happy path: mocked plugin returns rows, fragment renders them."""
    rows = [
        {
            'job_id':    '12345.desched1',
            'user':      'benkirk',
            'queue':     'main',
            'start':     '2026-05-01 10:00:00',
            'end':       '2026-05-01 11:00:00',
            'elapsed':   3600,
            'cpu_hours': 64.0,
            'gpu_hours': 0.0,
        }
    ]
    _install_mock_plugin(app, monkeypatch, jobs_search_return=rows)

    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}?machine=derecho'
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert '12345.desched1' in body
    assert 'benkirk' in body
    # Disabled banner must NOT be present on the enabled path.
    assert 'Per-job data is unavailable' not in body


# ---------------------------------------------------------------------------
# Admin → Configuration DB card surfaces job_history engines
# ---------------------------------------------------------------------------

def test_gather_runtime_state_no_job_history_rows_when_disabled(app):
    """With the plugin off, state.databases contains only sam + system_status."""
    from webapp.extensions import db
    from webapp.utils.config_inspect import gather_runtime_state

    with app.app_context():
        state = gather_runtime_state(app, db)

    names = [d['name'] for d in state['databases']]
    assert any(n == 'sam' for n in names)
    # No job_history (*) rows when no engines registered.
    assert not any(n.startswith('job_history') for n in names), names


def test_gather_runtime_state_adds_row_per_engine(app, monkeypatch, tmp_path):
    """Each cached engine produces one databases[] entry, named with the machine."""
    from sqlalchemy import create_engine
    from webapp.extensions import db
    from webapp.utils.config_inspect import gather_runtime_state

    # Real Engine bound to a tmp_path SQLite so _ping_engine / pool_stats /
    # format_db_url_safe all exercise their real code paths.
    db_file = tmp_path / 'jh_test.db'
    engine = create_engine(f'sqlite:///{db_file}')
    monkeypatch.setitem(app.extensions, 'hpc_usage_queries', {
        'module':  types.SimpleNamespace(JobQueries=object),
        'engines': {'derecho': engine},
        'enabled': True,
    })

    with app.app_context():
        state = gather_runtime_state(app, db)

    names = [d['name'] for d in state['databases']]
    assert 'job_history (derecho)' in names

    row = next(d for d in state['databases'] if d['name'] == 'job_history (derecho)')
    assert row['status'] == 'healthy'
    # latency_ms can be 0 on a fast local file; check shape not value.
    assert row['latency_ms'] is not None
    assert row['url'].startswith('sqlite://')
