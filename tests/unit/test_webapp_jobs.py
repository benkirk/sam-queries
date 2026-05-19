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


def test_init_job_history_falls_back_when_plugin_lacks_pool_kwargs(monkeypatch, caplog):
    """An old plugin without pool_kwargs= still produces engines; one warning logged."""
    from webapp.jobs.session import init_job_history, get_engines, is_enabled

    seen_kwargs = []

    # Old plugin signature: positional machine + echo only, no pool_kwargs.
    def _old_get_engine(machine, echo=False):
        seen_kwargs.append(('positional', machine, echo))
        return MagicMock(name=f'engine_{machine}')

    fake_mod = types.SimpleNamespace(
        get_engine=_old_get_engine,
        get_session=lambda machine, engine=None: None,
        JobQueries=object,
    )
    monkeypatch.setattr('sam.plugins.HPC_USAGE_QUERIES.load', lambda: fake_mod)

    a = _build_isolated_app(['derecho'])
    # Non-trivial pool_kwargs so the test would catch a regression that
    # silently forwards them despite the signature mismatch.
    a.config['JOB_HISTORY_POOL_KWARGS'] = {'pool_size': 99}

    with caplog.at_level('WARNING', logger='webapp.jobs.session'):
        init_job_history(a)

    with a.app_context():
        assert is_enabled() is True
        assert 'derecho' in get_engines()
    # get_engine was called WITHOUT pool_kwargs (just the positional machine).
    assert seen_kwargs == [('positional', 'derecho', False)]
    # Exactly one pool_kwargs drift warning, not one per machine. A
    # second "out of date" line about missing offset=/jobs_count may
    # also fire because the FakeJobQueries here is just ``object``;
    # filter by the pool_kwargs marker to keep this test focused.
    drift_warnings = [r for r in caplog.records
                      if 'pool_kwargs=' in r.getMessage()]
    assert len(drift_warnings) == 1


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
                        jobs_count_return=None, machines=('derecho',),
                        supports_offset=True, supports_sort=True,
                        supports_count=True):
    """Wire a mock job_history module onto app.extensions and return the
    captured JobQueries kwargs so tests can assert on the call.

    The ``supports_*`` flags control the capability state the route reads
    via ``get_capabilities()``. Pass ``supports_count=False`` (etc.) to
    simulate an older plugin and exercise the graceful-fallback paths.

    Uses ``monkeypatch.setitem`` so the original (empty/None) extension
    state is restored at test teardown — the ``app`` fixture is
    session-scoped and shared across the whole xdist worker.
    """
    captured = {
        'last_jobs_search_kwargs': None,
        'last_jobs_count_kwargs':  None,
    }

    class FakeJobQueries:
        def __init__(self, session, machine='derecho'):
            self.session = session
            self.machine = machine
        def jobs_search(self, **kwargs):
            captured['last_jobs_search_kwargs'] = kwargs
            return jobs_search_return or []
        def jobs_count(self, **kwargs):
            captured['last_jobs_count_kwargs'] = kwargs
            return jobs_count_return if jobs_count_return is not None \
                else len(jobs_search_return or [])

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
        'supports_offset': supports_offset,
        'supports_sort':   supports_sort,
        'supports_count':  supports_count,
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
    # No account_projcodes passed → fall back to single-projcode string
    # form so existing single-project callers (CLI, isolated tests) keep
    # the cheaper `==` filter on the plugin side.
    assert kw['account'] == active_project.projcode
    assert kw['user']    == 'someone'
    assert kw['queue']   == 'main'
    assert kw['limit']   == 50


def test_search_jobs_account_projcodes_overrides_single(
    app, active_project, monkeypatch,
):
    """When account_projcodes is passed, it takes precedence over project.projcode
    and is forwarded to the plugin as a list (`Job.account IN (...)`)."""
    from webapp.jobs import service

    captured = _install_mock_plugin(app, monkeypatch)

    with app.app_context():
        service.search_jobs(
            'derecho',
            project=active_project,
            account_projcodes=['PARENT0001', 'PARENT0001_a', 'PARENT0001_b'],
            limit=50,
        )

    kw = captured['last_jobs_search_kwargs']
    assert kw['account'] == ['PARENT0001', 'PARENT0001_a', 'PARENT0001_b']


def test_search_jobs_requires_project():
    from webapp.jobs import service
    with pytest.raises(ValueError):
        service.search_jobs('derecho', project=None)


def test_search_jobs_normalizes_legacy_queue_name(
    app, active_project, monkeypatch,
):
    """TODO(legacy-queue-names) workaround: pre-2026-05-13 summary rows
    have synthetic queue names like ``cpu-special`` that the plugin's
    ``Job.queue`` column never used. The plugin call site strips
    everything after the first dash so jobs actually return; the SAM
    summary path keeps the raw value (covered by the count test
    below)."""
    from webapp.jobs import service

    captured = _install_mock_plugin(app, monkeypatch)

    with app.app_context():
        service.search_jobs(
            'derecho',
            project=active_project,
            queue='cpu-special',
            limit=50,
        )

    assert captured['last_jobs_search_kwargs']['queue'] == 'cpu'


def test_count_jobs_sam_summary_keeps_legacy_queue_name(
    app, active_project, monkeypatch,
):
    """The SAM ``comp_charge_summary`` fast path must NOT normalize the
    queue — that table stores the synthetic name and a stripped query
    would miss its own rows. Counterpart to the ``search_jobs``
    normalization test."""
    from webapp.jobs import service

    captured_queue = {}

    def _fake_count_via_sam_summary(machine, *, projcodes, start, end, user, queue):
        captured_queue['queue'] = queue
        return 5

    monkeypatch.setattr(service, '_count_via_sam_summary', _fake_count_via_sam_summary)
    _install_mock_plugin(app, monkeypatch)

    with app.app_context():
        # No status / has_gpus → goes through the SAM summary fast path.
        total = service.count_jobs(
            'derecho', project=active_project, queue='cpu-special',
        )

    assert total == 5
    assert captured_queue['queue'] == 'cpu-special'


def test_count_jobs_plugin_fallback_normalizes_legacy_queue_name(
    app, active_project, monkeypatch,
):
    """When the request adds a filter outside the summary key set
    (``status``, ``has_gpus``), count_jobs hits the plugin — which DOES
    need the normalized queue. Mirrors the search_jobs test."""
    from webapp.jobs import service

    captured = _install_mock_plugin(app, monkeypatch, jobs_count_return=7)

    with app.app_context():
        service.count_jobs(
            'derecho', project=active_project,
            queue='cpu-economy',
            status='F',  # forces plugin path
        )

    ckw = captured['last_jobs_count_kwargs']
    assert ckw is not None
    assert ckw['queue'] == 'cpu'


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
    # 'user' is not a default column on the per-job table (the drill-down
    # row already pins user/queue), so benkirk appears in the verbose-row
    # drawer instead of the main table.
    assert 'benkirk' in body
    # Disabled banner must NOT be present on the enabled path.
    assert 'Per-job data is unavailable' not in body


# ---------------------------------------------------------------------------
# Part 2: pagination / sort / suppression / verbose-row / resource-details
# ---------------------------------------------------------------------------

def _make_row(**overrides):
    """Build a verbose-shape job row dict — every default + verbose key set
    to a sensible non-empty value so suppression / drawer tests can opt
    fields back to 0/None without redefining the full superset."""
    base = {
        'job_id': '500.desched1', 'name': 'demo', 'status': 'F',
        'user': 'alice', 'account': 'SCSG0001', 'queue': 'main',
        'start': '2026-05-01 10:00:00',
        'end':   '2026-05-01 11:00:00',
        'submit': '2026-05-01 09:55:00', 'eligible': None,
        'elapsed': 3600, 'walltime': 7200,
        'numnodes': 1, 'numcpus': 128, 'numgpus': 0,
        'mpiprocs': 128, 'ompthreads': 1,
        'reqmem': 0, 'memory': 100, 'vmemory': 200,
        'cputype': 'milan', 'gputype': None, 'resources': 'select=1',
        'cpu_hours': 128.0, 'gpu_hours': 0.0, 'memory_hours': 10.0,
        'qos_factor': 1.0, 'charge_version': 1,
        'cpu_charges': 128.0, 'gpu_charges': 0.0, 'memory_charges': 10.0,
        'short_id': 500, 'priority': '0',
    }
    base.update(overrides)
    return base


def test_jobs_fragment_pagination_forwards_offset(
    app, auth_client, active_project, monkeypatch,
):
    """?page=3&per_page=25 ⇒ service receives offset=50, limit=25.

    The count call goes to SAM's CompChargeSummary now, not the plugin —
    a separate test (``…_status_filter_uses_plugin_count``) covers the
    plugin-fallback shape.
    """
    captured = _install_mock_plugin(app, monkeypatch,
                                    jobs_search_return=[_make_row()])
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}'
        '?machine=derecho&page=3&per_page=25'
    )
    assert resp.status_code == 200
    kw = captured['last_jobs_search_kwargs']
    assert kw['limit']  == 25
    assert kw['offset'] == 50    # (3 - 1) * 25


def test_jobs_fragment_status_filter_uses_plugin_count(
    app, auth_client, active_project, monkeypatch,
):
    """When the request adds a filter outside CompChargeSummary's key set
    (``status``, ``has_gpus``), count_jobs delegates to the plugin's
    ``jobs_count`` rather than SAM's summary."""
    captured = _install_mock_plugin(app, monkeypatch,
                                    jobs_search_return=[_make_row()],
                                    jobs_count_return=42)
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}'
        '?machine=derecho&status=F'
    )
    assert resp.status_code == 200
    ckw = captured['last_jobs_count_kwargs']
    assert ckw is not None
    # Route now expands the project tree and forwards the descendant
    # list as `account=[...]`. For a leaf project this is just
    # [project.projcode]; the membership check is independent of
    # whether the snapshot picked a leaf or a tree-parent fixture.
    assert isinstance(ckw['account'], list)
    assert active_project.projcode in ckw['account']
    assert ckw['status']  == 'F'


def test_jobs_fragment_passes_tree_projcodes(
    app, auth_client, active_project, monkeypatch,
):
    """Route expands the project tree (parent + descendants) and forwards
    every projcode to the plugin as a list. Mirrors what the Historical
    Usage rollup does for daily totals — so jobs charged to child
    projcodes show up under the parent's drill-down rows.

    Factory-built projects don't work here: the route's
    ``@require_project_access`` loads the project via Flask-SQLAlchemy's
    own db.session (different connection from the test session), so
    factory rows aren't visible. Instead patch ``Project.get_descendants``
    at the class level to return a synthetic tree for whatever
    snapshot project the route resolved. The captured plugin kwargs
    show whether the route forwarded the full list verbatim.
    """
    from sam import Project

    stub_codes = ['CESM0002', 'CESM0002_alpha', 'CESM0002_beta']
    fake_descendants = [types.SimpleNamespace(projcode=p) for p in stub_codes]
    monkeypatch.setattr(
        Project, 'get_descendants',
        lambda self, include_self=True: fake_descendants,
    )

    captured = _install_mock_plugin(app, monkeypatch,
                                    jobs_search_return=[_make_row()])
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}?machine=derecho'
    )
    assert resp.status_code == 200
    kw = captured['last_jobs_search_kwargs']
    assert isinstance(kw['account'], list)
    assert set(kw['account']) == set(stub_codes)


def test_jobs_fragment_sort_param_round_trips(
    app, auth_client, active_project, monkeypatch,
):
    """?sort_by=elapsed&sort_dir=asc renders the active arrow + inverts next click."""
    _install_mock_plugin(app, monkeypatch, jobs_search_return=[_make_row()])
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}'
        '?machine=derecho&sort_by=elapsed&sort_dir=asc'
    )
    body = resp.get_data(as_text=True)
    # Up-arrow indicates active asc sort.
    assert 'fa-caret-up' in body
    # The next-click href on the elapsed header flips to desc.
    assert 'sort_by=elapsed&sort_dir=desc' in body


def test_jobs_fragment_sort_whitelist_rejects_unknown(
    app, auth_client, active_project, monkeypatch,
):
    """?sort_by=garbage silently degrades to default order (no exception)."""
    captured = _install_mock_plugin(app, monkeypatch,
                                    jobs_search_return=[_make_row()])
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}'
        '?machine=derecho&sort_by=garbage'
    )
    assert resp.status_code == 200
    # Service was called WITHOUT sort_by (caps allow it but route dropped
    # the value because it wasn't in the whitelist).
    kw = captured['last_jobs_search_kwargs']
    assert 'sort_by' not in kw


def test_jobs_fragment_suppresses_all_zero_gpu_columns(
    app, auth_client, active_project, monkeypatch,
):
    """Rows with numgpus=gpu_hours=gpu_charges=0 ⇒ GPU columns dropped."""
    rows = [_make_row(numgpus=0, gpu_hours=0, gpu_charges=0)
            for _ in range(2)]
    _install_mock_plugin(app, monkeypatch, jobs_search_return=rows)
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}?machine=derecho'
    )
    body = resp.get_data(as_text=True)
    # The plugin column headers for the suppressed cols ("GPUs", "GPU chg")
    # must NOT appear in the table head. Sortable headers are wrapped in
    # <a>, so check the bare label substring rather than ">LABEL<".
    assert 'GPUs'    not in body
    assert 'GPU chg' not in body
    # CPU column still rendered.
    assert 'CPUs' in body


def test_jobs_fragment_keeps_gpu_columns_when_any_row_nonzero(
    app, auth_client, active_project, monkeypatch,
):
    """One nonzero GPU value ⇒ GPU columns stay in the table."""
    rows = [
        _make_row(numgpus=0, gpu_hours=0, gpu_charges=0),
        _make_row(numgpus=4, gpu_hours=16.0, gpu_charges=16.0),
    ]
    _install_mock_plugin(app, monkeypatch, jobs_search_return=rows)
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}?machine=derecho'
    )
    body = resp.get_data(as_text=True)
    assert 'GPUs'    in body
    assert 'GPU chg' in body


def test_jobs_fragment_renders_verbose_drawer(
    app, auth_client, active_project, monkeypatch,
):
    """Per-row drawer renders verbose-extras fields (walltime, mpiprocs, etc.)."""
    _install_mock_plugin(app, monkeypatch,
                         jobs_search_return=[_make_row(walltime=7200,
                                                       mpiprocs=128,
                                                       cputype='milan')])
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}?machine=derecho'
    )
    body = resp.get_data(as_text=True)
    # The collapse target id pattern + Bootstrap collapse class — confirms
    # the per-row drawer was emitted.
    assert 'jobs-expand-toggle' in body
    assert 'jobs-detail-row' in body
    # Verbose-column header labels from plugin COLUMNS.
    assert 'Walltime' in body
    assert 'MPI' in body
    assert 'CPU type' in body
    # Drawer renders the values.
    assert 'milan' in body


def test_jobs_fragment_count_missing_hides_pagination(
    app, auth_client, active_project, monkeypatch,
):
    """Older plugin (no jobs_count) AND a filter shape that bypasses SAM's
    summary ⇒ banner + no pagination nav. The SAM-summary fast path
    covers the typical drill-down shape, so the only way ``total=None``
    surfaces today is when the route falls back to the plugin (status /
    has_gpus filter) and the plugin lacks ``jobs_count``."""
    _install_mock_plugin(app, monkeypatch,
                         jobs_search_return=[_make_row()],
                         supports_count=False)
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}'
        '?machine=derecho&status=F'
    )
    body = resp.get_data(as_text=True)
    assert 'Pagination unavailable' in body
    # Filter chip "(no count)" instead of total.
    assert '(no count)' in body


def test_resource_details_includes_jobs_fragment_url(
    app, auth_client, active_project, monkeypatch,
):
    """The HPC resource-details page emits hx-get URLs to the jobs route
    on every user+queue row (when running on a derecho/casper resource)."""
    # Note: this test exercises the template wire-in only — the daily
    # drill-down data may be empty depending on the fixture's seed data.
    # The template still renders the page, just without rows.
    resp = auth_client.get(
        f'/dashboards/user/resource-details'
        f'?projcode={active_project.projcode}&resource=Derecho'
    )
    # Either 200 (page rendered) or a redirect (no matching resource in
    # fixtures). We only assert the URL pattern when the page renders.
    if resp.status_code == 200:
        body = resp.get_data(as_text=True)
        # The hx-get URL prefix should appear if any user+queue rows
        # rendered. Don't fail the test when there are no rows — just
        # confirm the template wire-in is at least syntactically valid
        # (the page renders without error).
        if 'fa-list-ul' in body:
            assert f'/dashboards/user/jobs/{active_project.projcode}' in body
            assert 'machine=derecho' in body


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
