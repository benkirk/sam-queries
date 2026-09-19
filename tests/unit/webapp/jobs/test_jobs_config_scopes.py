from __future__ import annotations

import types
from unittest.mock import MagicMock

import pytest

from webapp.jobs.scope import (
    MachineJobScope,
    ProjectJobScope,
    UserJobScope,
)
from _jobs_helpers import (
    _capture_connect_listener,
    _install_mock_plugin,
)


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


def test_gather_runtime_state_surfaces_db_linked_api_keys(app, monkeypatch):
    """auth block reports the DB-linked API-key toggle + cache TTL from config."""
    from webapp.extensions import db
    from webapp.utils.config_inspect import gather_runtime_state

    monkeypatch.setitem(app.config, 'API_KEYS_DB_ENABLED', True)
    monkeypatch.setitem(app.config, 'API_KEYS_DB_TTL', 90)
    with app.app_context():
        state = gather_runtime_state(app, db)
    assert state['auth']['api_keys_db_enabled'] is True
    assert state['auth']['api_keys_db_ttl'] == 90

    monkeypatch.setitem(app.config, 'API_KEYS_DB_ENABLED', False)
    with app.app_context():
        state = gather_runtime_state(app, db)
    assert state['auth']['api_keys_db_enabled'] is False


def test_view_all_job_data_grants():
    """Auto-granted to operator bundles via ALL_VIEW; NOT to facility tier."""
    from webapp.utils.rbac import (
        GROUP_PERMISSIONS, USER_FACILITY_PERMISSIONS, Permission,
    )
    p = Permission.VIEW_ALL_JOB_DATA
    for bundle in ('nusd', 'csg', 'ssg'):
        assert p in GROUP_PERMISSIONS[bundle], bundle
    assert p not in USER_FACILITY_PERMISSIONS['sureshm']['WNA']


def test_apply_connection_settings_sets_name_and_timeout(monkeypatch):
    """The connect listener issues SET application_name + statement_timeout.

    Covers the shared PluginExtension implementation, which both the jobs and
    the fs-scans loaders warm their engines through.
    """
    registered = {'fn': _capture_connect_listener(
        monkeypatch,
        app_name='sam-webapp:pod:job_history:derecho',
        statement_timeout_ms=60000,
    )}

    executed = []

    class _Cursor:
        def execute(self, sql, params=None):
            executed.append((sql, params))
        def close(self):
            pass

    dbapi_conn = MagicMock()
    dbapi_conn.autocommit = False
    dbapi_conn.cursor = lambda: _Cursor()

    registered['fn'](dbapi_conn, None)

    assert executed[0] == (
        "SET application_name = %s",
        ('sam-webapp:pod:job_history:derecho',),
    )
    assert executed[1] == ("SET statement_timeout = %s", ('60000',))
    # autocommit restored after the SETs.
    assert dbapi_conn.autocommit is False


def test_apply_connection_settings_zero_timeout_skips_set(monkeypatch):
    """statement_timeout_ms=0 (disabled) issues only the app-name SET."""
    registered = {'fn': _capture_connect_listener(
        monkeypatch, app_name='tag', statement_timeout_ms=0)}

    executed = []

    class _Cursor:
        def execute(self, sql, params=None):
            executed.append(sql)
        def close(self):
            pass

    dbapi_conn = MagicMock()
    dbapi_conn.autocommit = False
    dbapi_conn.cursor = lambda: _Cursor()

    registered['fn'](dbapi_conn, None)

    assert executed == ["SET application_name = %s"]


@pytest.mark.parametrize('machines', [
    ['derecho', 'casper'],
    ['casper', 'derecho'],
])
def test_job_history_machines_follows_config_order(app, monkeypatch, machines):
    """Engine keys come back in insertion (== JOB_HISTORY_MACHINES) order.

    _warm() inserts one engine per configured machine in config order, so the
    UI leads with whichever machine the deployment lists first (derecho by
    default). Both orderings are exercised to pin that this tracks insertion
    rather than any hardcoded list — the old implementation sorted, which
    passed the first case by accident and led with Casper in production.
    """
    from webapp.jobs import service

    monkeypatch.setitem(app.extensions, 'hpc_usage_queries', {
        'module':  types.SimpleNamespace(JobQueries=object),
        'engines': {m: MagicMock() for m in machines},
        'enabled': True,
    })
    with app.app_context():
        assert service.job_history_machines() == machines


def test_job_history_machines_empty_when_disabled(app):
    """TestingConfig keeps the plugin off -> no machines offered."""
    from webapp.jobs import service

    with app.app_context():
        assert service.job_history_machines() == []


def test_search_jobs_machine_forwards_no_account(app, monkeypatch):
    """Machine mode issues an UNSCOPED query — no account key at all."""
    from webapp.jobs import service

    captured = _install_mock_plugin(app, monkeypatch)

    with app.app_context():
        service.search_jobs('derecho', MachineJobScope(), user='alice', limit=10)

    kw = captured['last_jobs_search_kwargs']
    assert 'account' not in kw
    assert kw['user'] == 'alice'
    assert kw['limit'] == 10


def test_count_jobs_machine_uses_plugin_count(app, monkeypatch):
    """Machine mode never touches the SAM per-project summary."""
    from webapp.jobs import service

    captured = _install_mock_plugin(app, monkeypatch, jobs_count_return=9)

    with app.app_context():
        total = service.count_jobs('derecho', MachineJobScope(), queue='main')

    assert total == 9
    ckw = captured['last_jobs_count_kwargs']
    assert ckw is not None
    assert 'account' not in ckw


def test_search_jobs_user_pins_username(app, monkeypatch):
    from webapp.jobs import service

    captured = _install_mock_plugin(app, monkeypatch)

    with app.app_context():
        service.search_jobs('derecho', UserJobScope('benkirk'), queue='main')

    kw = captured['last_jobs_search_kwargs']
    assert kw['user'] == 'benkirk'


def test_search_jobs_user_rejects_user_filter(app, monkeypatch):
    """A client-supplied user filter must raise, not be silently dropped."""
    from webapp.jobs import service

    _install_mock_plugin(app, monkeypatch)

    with app.app_context():
        with pytest.raises(ValueError, match='pin the user server-side'):
            service.search_jobs('derecho', UserJobScope('benkirk'), user='mallory')


def test_user_scope_requires_a_username():
    """An empty pin would silently widen to every user's jobs."""
    with pytest.raises(ValueError, match='username'):
        UserJobScope('')


def test_count_jobs_user_pins_username(app, monkeypatch):
    from webapp.jobs import service

    captured = _install_mock_plugin(app, monkeypatch, jobs_count_return=4)

    with app.app_context():
        total = service.count_jobs('derecho', UserJobScope('benkirk'))

    assert total == 4
    assert captured['last_jobs_count_kwargs']['user'] == 'benkirk'


def test_count_jobs_zero_bound_forces_plugin_path(app, active_project, monkeypatch):
    """max_gpus=0 is a REAL filter (CPU-only) — the falsy value must not
    slip through the fast-path gate onto the SAM summary."""
    from webapp.jobs import service

    captured = _install_mock_plugin(app, monkeypatch, jobs_count_return=2)

    with app.app_context():
        total = service.count_jobs(
            'derecho', ProjectJobScope(active_project), max_gpus=0,
        )

    assert total == 2
    ckw = captured['last_jobs_count_kwargs']
    assert ckw is not None
    assert ckw['max_gpus'] == 0


def test_count_jobs_ignore_case_alone_keeps_fast_path(
    app, active_project, monkeypatch,
):
    """ignore_case without a name filter changes nothing — stay on the
    SAM-summary fast path."""
    from webapp.jobs import service

    monkeypatch.setattr(
        service, '_count_via_sam_summary',
        lambda machine, **kw: 13,
    )
    captured = _install_mock_plugin(app, monkeypatch)

    with app.app_context():
        total = service.count_jobs(
            'derecho', ProjectJobScope(active_project), ignore_case=False,
        )

    assert total == 13
    assert captured['last_jobs_count_kwargs'] is None


def test_count_jobs_name_filter_forces_plugin_path(
    app, active_project, monkeypatch,
):
    from webapp.jobs import service

    captured = _install_mock_plugin(app, monkeypatch, jobs_count_return=5)

    with app.app_context():
        service.count_jobs(
            'derecho', ProjectJobScope(active_project),
            name='wrf*', ignore_case=True,
        )

    ckw = captured['last_jobs_count_kwargs']
    assert ckw is not None
    assert ckw['name'] == 'wrf*'
    assert ckw['ignore_case'] is True


def test_count_jobs_memory_bound_forces_plugin_path(
    app, active_project, monkeypatch,
):
    """The new memory bounds are outside the SAM summary's key set — any
    value (including a negative wasted bound) must take the plugin path."""
    from webapp.jobs import service

    captured = _install_mock_plugin(app, monkeypatch, jobs_count_return=3)

    with app.app_context():
        total = service.count_jobs(
            'derecho', ProjectJobScope(active_project), max_memory_wasted=-1,
        )

    assert total == 3
    ckw = captured['last_jobs_count_kwargs']
    assert ckw is not None
    assert ckw['max_memory_wasted'] == -1


def test_search_jobs_forwards_memory_filters(app, active_project, monkeypatch):
    """_plugin_filter_kwargs mirrors the plugin surface 1:1 — the four new
    memory bounds flow through search_jobs untouched."""
    from webapp.jobs import service

    captured = _install_mock_plugin(app, monkeypatch)

    with app.app_context():
        service.search_jobs(
            'derecho', ProjectJobScope(active_project),
            min_memory_used=2 * 1024 ** 3, max_memory_used=64 * 1024 ** 3,
            min_memory_wasted=-(4 * 1024 ** 3), max_memory_wasted=0,
        )

    kw = captured['last_jobs_search_kwargs']
    assert kw['min_memory_used'] == 2 * 1024 ** 3
    assert kw['max_memory_used'] == 64 * 1024 ** 3
    assert kw['min_memory_wasted'] == -(4 * 1024 ** 3)
    assert kw['max_memory_wasted'] == 0


def test_search_jobs_rejects_unknown_filter(app, active_project, monkeypatch):
    """_plugin_filter_kwargs is keyword-only: a typo'd filter raises
    TypeError instead of silently vanishing."""
    from webapp.jobs import service

    _install_mock_plugin(app, monkeypatch)

    with app.app_context():
        with pytest.raises(TypeError):
            service.search_jobs(
                'derecho', ProjectJobScope(active_project), min_gups=1,  # typo
            )
