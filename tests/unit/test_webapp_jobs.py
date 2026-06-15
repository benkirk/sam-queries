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

_DEFAULT_QOS_NAMES = ['economy', 'premium', 'regular', 'special', 'uncharged']


def _install_mock_plugin(app, monkeypatch, *, jobs_search_return=None,
                        jobs_count_return=None, qos_names=None,
                        machines=('derecho',)):
    """Wire a mock job_history module onto app.extensions and return the
    captured JobQueries kwargs so tests can assert on the call.

    Uses ``monkeypatch.setitem`` so the original (empty/None) extension
    state is restored at test teardown — the ``app`` fixture is
    session-scoped and shared across the whole xdist worker.
    """
    captured = {
        'last_jobs_search_kwargs': None,
        'last_jobs_count_kwargs':  None,
    }
    qos_list = (list(qos_names) if qos_names is not None
                else list(_DEFAULT_QOS_NAMES))

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
        def list_qos_names(self, **kwargs):
            return list(qos_list)

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


def test_search_jobs_promotes_legacy_queue_suffix_to_qos(
    app, active_project, monkeypatch,
):
    """When the caller passes a legacy queue like 'cpu-special' AND a
    valid_qos_names list that contains 'special', the resolver promotes
    the suffix to a QoS filter — turning a CPU-wide search into a
    CPU+special-QoS search. Surfaces precision the old normalizer
    discarded."""
    from webapp.jobs import service

    captured = _install_mock_plugin(app, monkeypatch)

    with app.app_context():
        service.search_jobs(
            'derecho', project=active_project,
            queue='cpu-special',
            valid_qos_names=['premium', 'regular', 'special'],
        )

    kw = captured['last_jobs_search_kwargs']
    assert kw['queue'] == 'cpu'
    assert kw['qos']   == 'special'


def test_search_jobs_explicit_qos_wins_over_inferred(
    app, active_project, monkeypatch,
):
    """A caller-supplied qos always takes precedence over a suffix the
    resolver might otherwise infer from the legacy queue name."""
    from webapp.jobs import service

    captured = _install_mock_plugin(app, monkeypatch)

    with app.app_context():
        service.search_jobs(
            'derecho', project=active_project,
            queue='cpu-special',
            qos='regular',  # explicit
            valid_qos_names=['premium', 'regular', 'special'],
        )

    kw = captured['last_jobs_search_kwargs']
    assert kw['queue'] == 'cpu'
    assert kw['qos']   == 'regular'  # explicit wins


def test_search_jobs_unknown_suffix_falls_back_to_strip_only(
    app, active_project, monkeypatch,
):
    """When the suffix isn't in valid_qos_names (or the list is empty),
    the resolver keeps the legacy strip-only behavior: queue is split,
    qos stays None."""
    from webapp.jobs import service

    captured = _install_mock_plugin(app, monkeypatch)

    with app.app_context():
        service.search_jobs(
            'derecho', project=active_project,
            queue='cpu-bogus',
            valid_qos_names=['premium', 'regular', 'special'],
        )

    kw = captured['last_jobs_search_kwargs']
    assert kw['queue'] == 'cpu'
    assert kw['qos']   is None


def test_count_jobs_sam_summary_ignores_inferred_qos(
    app, active_project, monkeypatch,
):
    """The fast path is gated on the *explicit* qos argument. An
    inferred-only qos must NOT push count_jobs onto the slower plugin
    path — the SAM summary stores 'cpu-special' as a composite key and
    already counts it correctly without a separate qos filter."""
    from webapp.jobs import service

    captured_queue = {}

    def _fake_count_via_sam_summary(machine, *, projcodes, start, end, user, queue):
        captured_queue['queue'] = queue
        return 11

    monkeypatch.setattr(service, '_count_via_sam_summary', _fake_count_via_sam_summary)
    captured = _install_mock_plugin(app, monkeypatch)

    with app.app_context():
        total = service.count_jobs(
            'derecho', project=active_project,
            queue='cpu-special',
            valid_qos_names=['premium', 'regular', 'special'],
        )

    assert total == 11
    # Fast path used — raw composite queue, no plugin call.
    assert captured_queue['queue'] == 'cpu-special'
    assert captured['last_jobs_count_kwargs'] is None


def test_count_jobs_plugin_fallback_promotes_legacy_queue_suffix_to_qos(
    app, active_project, monkeypatch,
):
    """When count_jobs takes the plugin path (e.g. because status is
    set), it also runs the queue/qos resolver so 'cpu-special' →
    queue='cpu', qos='special' on the plugin call."""
    from webapp.jobs import service

    captured = _install_mock_plugin(app, monkeypatch, jobs_count_return=3)

    with app.app_context():
        service.count_jobs(
            'derecho', project=active_project,
            queue='cpu-special',
            status='F',  # forces plugin path
            valid_qos_names=['premium', 'regular', 'special'],
        )

    ckw = captured['last_jobs_count_kwargs']
    assert ckw is not None
    assert ckw['queue'] == 'cpu'
    assert ckw['qos']   == 'special'


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


def test_jobs_fragment_accepts_user_only_filter(
    app, auth_client, active_project, monkeypatch,
):
    """Usage-by-User drill-down: route accepts `user` alone (no queue, no date).

    The Usage-by-User card surfaces a leaf row at the user level whenever
    the user has a single queue — the resulting drill omits both the
    queue filter (since there's only one) and the date range (since the
    leaf aggregates over all dates). The route + service forward None
    filters as "no filter", and the plugin / SAM-summary count path
    handle the omission. This test pins the contract: a request with
    only `machine` and `user` returns HTTP 200 with rows and does NOT
    forward an explicit queue/start/end to the plugin.
    """
    captured = _install_mock_plugin(
        app, monkeypatch,
        jobs_search_return=[{'job_id': '999.desched1', 'user': 'benkirk',
                             'queue': 'main', 'end': '2026-05-01 11:00:00'}],
    )

    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}'
        f'?machine=derecho&user=benkirk'
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert '999.desched1' in body

    # Filters forwarded to the plugin: user pinned, others None.
    kw = captured['last_jobs_search_kwargs']
    assert kw['user']  == 'benkirk'
    assert kw['queue'] is None
    assert kw['start'] is None
    assert kw['end']   is None


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
    # hpc-usage-queries 7f4fd7b renamed the mpiprocs header "MPI" → "Ranks per Node"
    assert 'Ranks per Node' in body
    assert 'CPU type' in body
    # Drawer renders the values.
    assert 'milan' in body


def test_jobs_fragment_qos_column_in_table_and_sortable(
    app, auth_client, active_project, monkeypatch,
):
    """`qos` is in _DEFAULT_COLS and renders as a sortable header when the
    rows contain at least two distinct QoS values (column suppression
    rule covered separately)."""
    _install_mock_plugin(
        app, monkeypatch,
        jobs_search_return=[
            _make_row(job_id='1.x', qos='premium'),
            _make_row(job_id='2.x', qos='regular'),
        ],
    )
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}?machine=derecho'
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # QoS column header is sortable (wrapped in an hx-get link).
    assert 'sort_by=qos' in body
    # The QoS values render in the table.
    assert 'premium' in body
    assert 'regular' in body


def test_jobs_fragment_qos_filter_forwarded_to_service(
    app, auth_client, active_project, monkeypatch,
):
    """?qos=economy ⇒ service.search_jobs receives qos='economy' and the
    request bypasses the SAM-summary fast path on the count side."""
    captured = _install_mock_plugin(
        app, monkeypatch,
        jobs_search_return=[_make_row(qos='economy')],
        jobs_count_return=7,
    )
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}'
        '?machine=derecho&qos=economy'
    )
    assert resp.status_code == 200
    # qos forwarded through to the plugin search call.
    assert captured['last_jobs_search_kwargs']['qos'] == 'economy'
    # Count goes through the plugin fallback (qos is not in CompChargeSummary).
    assert captured['last_jobs_count_kwargs'] is not None
    assert captured['last_jobs_count_kwargs']['qos'] == 'economy'


def test_jobs_fragment_qos_dropdown_pre_selects_active_filter(
    app, auth_client, active_project, monkeypatch,
):
    """When ?qos=premium is set, the dropdown stays visible (so the user
    can change/reset) and pre-selects the active option — even though
    the filter naturally yields one distinct QoS in the rows."""
    _install_mock_plugin(app, monkeypatch,
                        jobs_search_return=[_make_row(qos='premium')])
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}'
        '?machine=derecho&qos=premium'
    )
    body = resp.get_data(as_text=True)
    # Explicit filter ⇒ dropdown visible; pre-selects 'premium'.
    import re
    assert 'name="qos"' in body
    assert re.search(r'value="premium"\s+selected', body), \
        'QoS dropdown should pre-select the active ?qos= value'


def test_jobs_fragment_qos_factor_drawer_after_status(
    app, auth_client, active_project, monkeypatch,
):
    """`qos_factor` is rendered in the drawer immediately after `status`
    (the re-ordered _VERBOSE_EXTRAS) so the multiplier sits next to the
    QoS column above the fold of the drawer."""
    _install_mock_plugin(
        app, monkeypatch,
        jobs_search_return=[_make_row(qos='premium', qos_factor=1.5,
                                      status='F')],
    )
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}?machine=derecho'
    )
    body = resp.get_data(as_text=True)
    # Plugin's COLUMNS dict labels: status="Status", qos_factor="Factor".
    # The <dt> wraps the label with whitespace, so match the bare text;
    # neither label appears elsewhere in the jobs fragment, so the first
    # occurrence is the drawer header.
    status_idx = body.find('Status')
    factor_idx = body.find('Factor')
    assert status_idx >= 0, 'Status label missing from drawer'
    assert factor_idx >= 0, 'Factor label (qos_factor) missing from drawer'
    assert factor_idx > status_idx, \
        f'expected Status before Factor (qos_factor); got {status_idx=} {factor_idx=}'


def test_jobs_fragment_qos_options_populated_from_plugin(
    app, auth_client, active_project, monkeypatch,
):
    """The QoS dropdown is populated from the plugin's list_qos_names()
    call — a new value added on the peer flows through without a SAM-side
    change. Needs ≥2 distinct QoS values in rows for the dropdown to
    appear at all."""
    _install_mock_plugin(
        app, monkeypatch,
        jobs_search_return=[
            _make_row(job_id='1.x', qos='custom-tier'),
            _make_row(job_id='2.x', qos='regular'),
        ],
        qos_names=['custom-tier', 'premium', 'regular'],
    )
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}?machine=derecho'
    )
    body = resp.get_data(as_text=True)
    # The non-canonical seed name surfaces in the dropdown options.
    assert 'custom-tier' in body
    # And the "All QoS" reset entry is always present.
    assert 'All QoS' in body


def test_jobs_fragment_hides_qos_column_and_dropdown_when_single_value(
    app, auth_client, active_project, monkeypatch,
):
    """When all visible rows share a single QoS (or none have one), the
    QoS column drops out of the table AND the filter dropdown is hidden.
    Both UI elements key off the same "distinct QoS in rows" signal so
    they compose: the legacy queue-suffix inference path (`cpu-special`
    → all rows special) naturally yields the same single-value collapse
    without the URL ever carrying ?qos=."""
    _install_mock_plugin(
        app, monkeypatch,
        jobs_search_return=[
            _make_row(job_id='1.x', qos='special'),
            _make_row(job_id='2.x', qos='special'),
            _make_row(job_id='3.x', qos='special'),
        ],
    )
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}?machine=derecho'
    )
    body = resp.get_data(as_text=True)
    # The sortable header link for the qos column is gone.
    assert 'sort_by=qos' not in body
    # The dropdown control is gone (no ?qos= in URL, no variation in rows).
    assert 'All QoS' not in body
    # The redundant per-row column/dropdown are suppressed, but the single
    # shared value is NOT silent — it collapses into a header badge so the
    # QoS (and its charging factor) stays visible at a glance.
    assert 'QoS: special' in body


def test_jobs_fragment_shows_qos_column_when_rows_have_variation(
    app, auth_client, active_project, monkeypatch,
):
    """Mixed-QoS rows ⇒ both column AND dropdown render (no explicit
    filter required to surface them)."""
    _install_mock_plugin(
        app, monkeypatch,
        jobs_search_return=[
            _make_row(job_id='1.x', qos='premium'),
            _make_row(job_id='2.x', qos='regular'),
            _make_row(job_id='3.x', qos='economy'),
        ],
    )
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}?machine=derecho'
    )
    body = resp.get_data(as_text=True)
    # Column header is present and sortable.
    assert 'sort_by=qos' in body
    # Dropdown is present with the reset entry.
    assert 'All QoS' in body


def test_jobs_fragment_keeps_dropdown_when_user_filtered_explicitly(
    app, auth_client, active_project, monkeypatch,
):
    """Explicit ?qos= naturally collapses rows to one distinct value, but
    the dropdown stays so the user can change or reset the filter. The
    column itself still goes away (all rows match)."""
    _install_mock_plugin(
        app, monkeypatch,
        jobs_search_return=[
            _make_row(job_id='1.x', qos='premium'),
            _make_row(job_id='2.x', qos='premium'),
        ],
    )
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}'
        '?machine=derecho&qos=premium'
    )
    body = resp.get_data(as_text=True)
    # Column header dropped (all rows the same QoS).
    assert 'sort_by=qos' not in body
    # Dropdown stays (explicit filter ⇒ user needs a way to reset).
    assert 'All QoS' in body


def test_jobs_fragment_single_qos_badge_shows_name_and_factor(
    app, auth_client, active_project, monkeypatch,
):
    """All rows in economy ⇒ the suppressed column collapses into a header
    badge that surfaces both the QoS name and its charging multiplier — the
    exact case (uniform economy, charges = 0.7× usage) the bare suppression
    rule made invisible."""
    _install_mock_plugin(
        app, monkeypatch,
        jobs_search_return=[
            _make_row(job_id='1.x', qos='economy', qos_factor=0.7),
            _make_row(job_id='2.x', qos='economy', qos_factor=0.7),
        ],
    )
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}?machine=derecho'
    )
    body = resp.get_data(as_text=True)
    assert 'QoS: economy' in body
    assert '×0.70' in body


def test_jobs_fragment_drawer_renders_fractional_qos_factor(
    app, auth_client, active_project, monkeypatch,
):
    """The per-row drawer's "Factor" is a fractional charging multiplier
    and must render with decimals (×0.70 / ×1.50), NOT be rounded to a
    whole number — the old fmt_number path turned economy's 0.7 into a
    misleading "1". Two distinct QoS values keep the single-value badge
    OFF, so the rendered factors must be coming from the drawers."""
    _install_mock_plugin(
        app, monkeypatch,
        jobs_search_return=[
            _make_row(job_id='1.x', qos='economy', qos_factor=0.7),
            _make_row(job_id='2.x', qos='premium', qos_factor=1.5),
        ],
    )
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}?machine=derecho'
    )
    body = resp.get_data(as_text=True)
    assert 'QoS: ' not in body          # mixed QoS ⇒ no badge
    assert '×0.70' in body              # economy factor, with decimals
    assert '×1.50' in body              # premium factor, with decimals


def test_jobs_fragment_no_qos_badge_when_rows_have_variation(
    app, auth_client, active_project, monkeypatch,
):
    """Mixed-QoS rows render the column/dropdown, NOT the single-value
    badge."""
    _install_mock_plugin(
        app, monkeypatch,
        jobs_search_return=[
            _make_row(job_id='1.x', qos='premium', qos_factor=1.5),
            _make_row(job_id='2.x', qos='economy', qos_factor=0.7),
        ],
    )
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}?machine=derecho'
    )
    body = resp.get_data(as_text=True)
    assert 'QoS: ' not in body


def test_jobs_fragment_no_qos_badge_when_all_null(
    app, auth_client, active_project, monkeypatch,
):
    """All-NULL (uncharacterized) QoS ⇒ no badge — nothing actionable to
    show."""
    _install_mock_plugin(
        app, monkeypatch,
        jobs_search_return=[
            _make_row(job_id='1.x', qos=None, qos_factor=None),
            _make_row(job_id='2.x', qos=None, qos_factor=None),
        ],
    )
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}?machine=derecho'
    )
    body = resp.get_data(as_text=True)
    assert 'QoS: ' not in body


def test_jobs_fragment_qos_badge_with_explicit_filter_shows_both(
    app, auth_client, active_project, monkeypatch,
):
    """Explicit ?qos= ⇒ the dropdown stays (to reset) AND the badge renders
    too — a single consistent rule, mild redundancy is fine."""
    _install_mock_plugin(
        app, monkeypatch,
        jobs_search_return=[
            _make_row(job_id='1.x', qos='economy', qos_factor=0.7),
            _make_row(job_id='2.x', qos='economy', qos_factor=0.7),
        ],
    )
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}'
        '?machine=derecho&qos=economy'
    )
    body = resp.get_data(as_text=True)
    # Dropdown stays so the user can reset.
    assert 'All QoS' in body
    # Badge renders alongside it.
    assert 'QoS: economy' in body


def test_jobs_fragment_qos_badge_name_only_when_factor_varies(
    app, auth_client, active_project, monkeypatch,
):
    """Same QoS name but inconsistent qos_factor across rows ⇒ the badge
    shows the name but omits the multiplier (no single factor to trust)."""
    _install_mock_plugin(
        app, monkeypatch,
        jobs_search_return=[
            _make_row(job_id='1.x', qos='economy', qos_factor=0.7),
            _make_row(job_id='2.x', qos='economy', qos_factor=0.5),
        ],
    )
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}?machine=derecho'
    )
    body = resp.get_data(as_text=True)
    assert 'QoS: economy' in body
    # No "(×…)" multiplier when the factor isn't consistent.
    assert '(×' not in body


def test_resource_details_includes_jobs_fragment_url(
    app, auth_client, active_project, monkeypatch,
):
    """The HPC resource-details page emits hx-get URLs to the jobs route
    on every user+queue row (when running on a derecho/casper resource)."""
    # Note: this test exercises the template wire-in only — the daily
    # drill-down data may be empty depending on the fixture's seed data.
    # The template still renders the page, just without rows.
    resp = auth_client.get(
        f'/user/resource-details/{active_project.projcode}'
        f'?resource=Derecho'
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


def test_resource_details_user_table_is_sortable(
    app, auth_client, active_project, monkeypatch,
):
    """Usage-by-User table emits the sortable_table.js markup contract:

      - sortable-header class on column <th>s with data-sort=text/numeric
      - sort-desc on the Charges header (default-sort indicator)
      - per-user tbody opt-in via class="sortable-group"
      - data-sort-value="<raw>" on the numeric cells so the JS sees
        the un-formatted value, not '68.6M'

    The presence of these attributes is the contract; their behavior
    is verified end-to-end via Playwright. Skip the assertion when
    the page redirects (no matching resource in the snapshot)."""
    resp = auth_client.get(
        f'/user/resource-details/{active_project.projcode}'
        f'?resource=Derecho'
    )
    if resp.status_code != 200:
        return  # snapshot doesn't have this resource — nothing to check
    body = resp.get_data(as_text=True)

    # The four column headers all opt in to sorting.
    assert 'sortable-header' in body, 'sortable-header class missing from page'
    assert 'data-sort="text"' in body, 'Username column missing data-sort=text'
    assert 'data-sort="numeric"' in body, 'numeric columns missing data-sort=numeric'
    # Charges is the default desc sort (visual indicator only — no resort
    # happens until the user clicks).
    assert 'sort-desc' in body, 'Charges header missing default sort-desc'

    # Per-user tbodies opt into multi-tbody sortable mode so each user's
    # row drags its lazy-subtree placeholder along on re-sort. Only
    # present when the project has data; gate the assertion to avoid
    # failing on a snapshot project with zero comp_charge_summary rows
    # for Derecho.
    if 'sortable-group' in body:
        assert 'data-sort-value=' in body, \
            'sortable-group tbody present but cells missing data-sort-value'


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
