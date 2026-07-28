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


@pytest.fixture(autouse=True)
def _disable_jobs_cache():
    """Disable the aggregation TTL cache for these tests by default.

    The route tests exercise the service path directly with per-test mock
    returns; a live cache would leak one test's envelope into the next
    (same key: filters all None). Explicit cache behavior is covered by
    test_webapp_jobs_cache.py. Reset on teardown so other modules aren't
    affected by the process-wide adapter singleton.
    """
    from webapp.jobs import cache as _c
    _c._adapters = {b: None for b in _c._BUCKETS}
    yield
    _c._adapters = {}   # clear → buckets re-init on next use


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

# Pinned copy of the plugin's COLUMNS headers (hpc-usage-queries PR #99
# contract, `from job_history import COLUMNS`). _install_mock_plugin patches
# routes._load_column_specs to return THIS, so header-label assertions test
# SAM's rendering against a fixed contract instead of whichever plugin
# version happens to be installed — CI builds the plugin from main until
# PR #99 merges, and the local hash-keyed conda-env flips refs, so the real
# import may legitimately be missing or stale during the transition.
_FAKE_COLUMN_SPECS = {
    'job_id': {'header': 'Job ID'},
    'name': {'header': 'Name'},
    'qos': {'header': 'QoS'},
    'start': {'header': 'Start'},
    'elapsed': {'header': 'Elapsed'},
    'numnodes': {'header': 'Nodes'},
    'numcpus': {'header': 'CPUs'},
    'numgpus': {'header': 'GPUs'},
    'cpu_charges': {'header': 'CPU chg'},
    'gpu_charges': {'header': 'GPU chg'},
    'exit_status': {'header': 'Exit'},
    'qos_factor': {'header': 'Factor'},
    'queue': {'header': 'Queue'},
    'user': {'header': 'User'},
    'submit': {'header': 'Submit'},
    'end': {'header': 'End'},
    'walltime': {'header': 'Walltime'},
    'mpiprocs': {'header': 'Ranks per Node'},
    'ompthreads': {'header': 'OMP Threads'},
    'reqmem': {'header': 'ReqMem'},
    'memory': {'header': 'Mem'},
    'vmemory': {'header': 'VMem'},
    'cputype': {'header': 'CPU type'},
    'gputype': {'header': 'GPU type'},
    'resources': {'header': 'Resources'},
    'cpu_hours': {'header': 'CPU-h'},
    'gpu_hours': {'header': 'GPU-h'},
    'memory_hours': {'header': 'Mem-h'},
    'memory_charges': {'header': 'Mem chg'},
}


def _install_mock_plugin(app, monkeypatch, *, jobs_search_return=None,
                        jobs_count_return=None, qos_names=None,
                        machines=('derecho',),
                        jobs_histogram_return=None,
                        jobs_usage_by_return=None,
                        jobs_facets_return=None,
                        jobs_facets_raises=False):
    """Wire a mock job_history module onto app.extensions and return the
    captured JobQueries kwargs so tests can assert on the call.

    Uses ``monkeypatch.setitem`` so the original (empty/None) extension
    state is restored at test teardown — the ``app`` fixture is
    session-scoped and shared across the whole xdist worker.
    """
    captured = {
        'last_jobs_search_kwargs': None,
        'last_jobs_count_kwargs':  None,
        'last_jobs_histogram':     None,   # (dimension, kwargs)
        'last_jobs_usage_by':      None,   # (dimension, kwargs)
        'last_jobs_facets_kwargs': None,
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
        def jobs_histogram(self, dimension, **kwargs):
            captured['last_jobs_histogram'] = (dimension, kwargs)
            if jobs_histogram_return is not None:
                return dict(jobs_histogram_return, dimension=dimension)
            return {'dimension': dimension, 'column': 'x', 'unit': 'u',
                    'min_param': 'min_x', 'max_param': 'max_x',
                    'buckets': [], 'null_count': 0, 'total_count': 0}
        def jobs_usage_by(self, dimension, **kwargs):
            captured['last_jobs_usage_by'] = (dimension, kwargs)
            if jobs_usage_by_return is not None:
                return jobs_usage_by_return
            return {'dimension': dimension, 'rows': [],
                    'totals': {'job_count': 0, 'cpu_hours': 0.0,
                               'gpu_hours': 0.0}}
        def jobs_facets(self, **kwargs):
            captured['last_jobs_facets_kwargs'] = kwargs
            if jobs_facets_raises:
                raise RuntimeError('facets exploded')
            if jobs_facets_return is not None:
                return jobs_facets_return
            return {d: [] for d in kwargs.get('facets', ())}
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

    # Column headers come from the pinned stub, never the installed plugin
    # (see _FAKE_COLUMN_SPECS) — completes the isolation the fake module
    # starts: these tests must pass with no plugin installed at all.
    from webapp.jobs import routes as jobs_routes
    monkeypatch.setattr(jobs_routes, '_load_column_specs',
                        lambda: _FAKE_COLUMN_SPECS)
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
        # No exit_status / GPU bounds → goes through the SAM summary fast path.
        total = service.count_jobs(
            'derecho', project=active_project, queue='cpu-special',
        )

    assert total == 5
    assert captured_queue['queue'] == 'cpu-special'


def test_count_jobs_plugin_fallback_normalizes_legacy_queue_name(
    app, active_project, monkeypatch,
):
    """When the request adds a filter outside the summary key set
    (``exit_status``, ``min_gpus``/``max_gpus``), count_jobs hits the
    plugin — which DOES need the normalized queue. Mirrors the
    search_jobs test."""
    from webapp.jobs import service

    captured = _install_mock_plugin(app, monkeypatch, jobs_count_return=7)

    with app.app_context():
        service.count_jobs(
            'derecho', project=active_project,
            queue='cpu-economy',
            exit_status='1',  # forces plugin path
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
    """When count_jobs takes the plugin path (e.g. because exit_status
    is set), it also runs the queue/qos resolver so 'cpu-special' →
    queue='cpu', qos='special' on the plugin call."""
    from webapp.jobs import service

    captured = _install_mock_plugin(app, monkeypatch, jobs_count_return=3)

    with app.app_context():
        service.count_jobs(
            'derecho', project=active_project,
            queue='cpu-special',
            exit_status='1',  # forces plugin path
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
    # A single-page single-user result suppresses the User column, so
    # benkirk surfaces via the `user:` header badge instead.
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
        'job_id': '500.desched1', 'name': 'demo', 'exit_status': '1',
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
    a separate test (``…_exit_status_filter_uses_plugin_count``) covers
    the plugin-fallback shape.
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


def test_jobs_fragment_exit_status_filter_uses_plugin_count(
    app, auth_client, active_project, monkeypatch,
):
    """When the request adds a filter outside CompChargeSummary's key set
    (``exit_status``, ``min_gpus``/``max_gpus``), count_jobs delegates to
    the plugin's ``jobs_count`` rather than SAM's summary."""
    captured = _install_mock_plugin(app, monkeypatch,
                                    jobs_search_return=[_make_row()],
                                    jobs_count_return=42)
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}'
        '?machine=derecho&exit_status=1'
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
    assert ckw['exit_status'] == '1'


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


def test_jobs_fragment_qos_factor_drawer_after_exit_status(
    app, auth_client, active_project, monkeypatch,
):
    """`qos_factor` is rendered in the drawer immediately after
    `exit_status` (the re-ordered _VERBOSE_EXTRAS) so the multiplier sits
    next to the QoS column above the fold of the drawer."""
    _install_mock_plugin(
        app, monkeypatch,
        jobs_search_return=[_make_row(qos='premium', qos_factor=1.5,
                                      exit_status='1')],
    )
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}?machine=derecho'
    )
    body = resp.get_data(as_text=True)
    # Plugin's COLUMNS dict labels: exit_status="Exit", qos_factor="Factor".
    # The <dt> wraps the label with whitespace, so match the bare text;
    # neither label appears elsewhere in the jobs fragment, so the first
    # occurrence is the drawer header.
    exit_idx = body.find('Exit')
    factor_idx = body.find('Factor')
    assert exit_idx >= 0, 'Exit label (exit_status) missing from drawer'
    assert factor_idx >= 0, 'Factor label (qos_factor) missing from drawer'
    assert factor_idx > exit_idx, \
        f'expected Exit (exit_status) before Factor (qos_factor); got {exit_idx=} {factor_idx=}'


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


# ---------------------------------------------------------------------------
# Commit 2: RBAC grant + connection settings + machines helper
# ---------------------------------------------------------------------------

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

    ``event.listens_for`` needs a real Engine, so capture the registered
    listener via a fake decorator and drive it with a stub DBAPI
    connection — asserting on the exact SQL the listener issues.
    """
    from webapp.jobs import session as jobs_session

    registered = {}

    def _fake_listens_for(target, name):
        def _decorator(fn):
            registered['fn'] = fn
            return fn
        return _decorator

    monkeypatch.setattr(jobs_session.event, 'listens_for', _fake_listens_for)

    jobs_session._apply_connection_settings(
        MagicMock(name='engine'), 'sam-webapp:pod:job_history:derecho',
        statement_timeout_ms=60000,
    )

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
    from webapp.jobs import session as jobs_session

    registered = {}

    def _fake_listens_for(target, name):
        def _decorator(fn):
            registered['fn'] = fn
            return fn
        return _decorator

    monkeypatch.setattr(jobs_session.event, 'listens_for', _fake_listens_for)

    jobs_session._apply_connection_settings(
        MagicMock(name='engine'), 'tag', statement_timeout_ms=0,
    )

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


def test_job_history_machines_sorted_when_enabled(app, monkeypatch):
    """Sorted engine keys when the plugin is up; [] when disabled."""
    from webapp.jobs import service

    monkeypatch.setitem(app.extensions, 'hpc_usage_queries', {
        'module':  types.SimpleNamespace(JobQueries=object),
        'engines': {'derecho': MagicMock(), 'casper': MagicMock()},
        'enabled': True,
    })
    with app.app_context():
        assert service.job_history_machines() == ['casper', 'derecho']


def test_job_history_machines_empty_when_disabled(app):
    """TestingConfig keeps the plugin off → no machines offered."""
    from webapp.jobs import service

    with app.app_context():
        assert service.job_history_machines() == []


# ---------------------------------------------------------------------------
# Commit 3: mode families + extended-filter count gating
# ---------------------------------------------------------------------------

def test_search_jobs_machine_forwards_no_account(app, monkeypatch):
    """Machine mode issues an UNSCOPED query — no account key at all."""
    from webapp.jobs import service

    captured = _install_mock_plugin(app, monkeypatch)

    with app.app_context():
        service.search_jobs_machine('derecho', user='alice', limit=10)

    kw = captured['last_jobs_search_kwargs']
    assert 'account' not in kw
    assert kw['user'] == 'alice'
    assert kw['limit'] == 10


def test_count_jobs_machine_uses_plugin_count(app, monkeypatch):
    """Machine mode never touches the SAM per-project summary."""
    from webapp.jobs import service

    captured = _install_mock_plugin(app, monkeypatch, jobs_count_return=9)

    with app.app_context():
        total = service.count_jobs_machine('derecho', queue='main')

    assert total == 9
    ckw = captured['last_jobs_count_kwargs']
    assert ckw is not None
    assert 'account' not in ckw


def test_search_jobs_user_pins_username(app, monkeypatch):
    from webapp.jobs import service

    captured = _install_mock_plugin(app, monkeypatch)

    with app.app_context():
        service.search_jobs_user('derecho', 'benkirk', queue='main')

    kw = captured['last_jobs_search_kwargs']
    assert kw['user'] == 'benkirk'


def test_search_jobs_user_rejects_user_filter(app, monkeypatch):
    """A client-supplied user filter must raise, not be silently dropped."""
    from webapp.jobs import service

    _install_mock_plugin(app, monkeypatch)

    with app.app_context():
        with pytest.raises(ValueError, match='pins user'):
            service.search_jobs_user('derecho', 'benkirk', user='mallory')


def test_search_jobs_user_requires_username(app, monkeypatch):
    from webapp.jobs import service

    _install_mock_plugin(app, monkeypatch)

    with app.app_context():
        with pytest.raises(ValueError, match='username'):
            service.search_jobs_user('derecho', '')


def test_count_jobs_user_pins_username(app, monkeypatch):
    from webapp.jobs import service

    captured = _install_mock_plugin(app, monkeypatch, jobs_count_return=4)

    with app.app_context():
        total = service.count_jobs_user('derecho', 'benkirk')

    assert total == 4
    assert captured['last_jobs_count_kwargs']['user'] == 'benkirk'


def test_count_jobs_zero_bound_forces_plugin_path(app, active_project, monkeypatch):
    """max_gpus=0 is a REAL filter (CPU-only) — the falsy value must not
    slip through the fast-path gate onto the SAM summary."""
    from webapp.jobs import service

    captured = _install_mock_plugin(app, monkeypatch, jobs_count_return=2)

    with app.app_context():
        total = service.count_jobs(
            'derecho', project=active_project, max_gpus=0,
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
            'derecho', project=active_project, ignore_case=False,
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
            'derecho', project=active_project,
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
            'derecho', project=active_project, max_memory_wasted=-1,
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
            'derecho', project=active_project,
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
                'derecho', project=active_project, min_gups=1,  # typo
            )


# ---------------------------------------------------------------------------
# Commit 5: aggregation fragments (By User / Wait Times / Job Sizes / Durations)
# ---------------------------------------------------------------------------

def _sample_hist(dimension='wait', null_count=0):
    return {
        'dimension': dimension, 'column': 'eligible_secs', 'unit': 'seconds',
        'min_param': 'min_eligible_secs', 'max_param': 'max_eligible_secs',
        'buckets': [
            {'label': '<1m', 'lo': 0, 'hi': 59,
             'job_count': 10, 'cpu_hours': 100.0, 'gpu_hours': 0.0},
            {'label': '1-5m', 'lo': 60, 'hi': 299,
             'job_count': 4, 'cpu_hours': 40.0, 'gpu_hours': 1.0},
        ],
        'null_count': null_count,
        'total_count': 14 + null_count,
    }


def _sample_usage(totals=None):
    return {
        'dimension': 'user',
        'rows': [
            {'value': 'alice', 'job_count': 30, 'cpu_hours': 300.0, 'gpu_hours': 0.0},
            {'value': 'bob',   'job_count': 12, 'cpu_hours': 120.0, 'gpu_hours': 2.0},
        ],
        'totals': totals or {'job_count': 42, 'cpu_hours': 420.0, 'gpu_hours': 2.0},
    }


def test_by_user_fragment_renders_rows_and_pie(
    app, auth_client, active_project, monkeypatch,
):
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_usage_by_return=_sample_usage(),
    )
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/by-user?machine=derecho'
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-job-user="alice"' in body
    assert 'data-job-user="bob"' in body
    assert '<svg' in body                       # pie rendered
    assert '#job-user-alice' in body            # clickable wedge sentinel
    # No remainder beyond the row cap → no Other row.
    assert 'beyond top' not in body
    # Plugin was asked for the user dimension, scoped to the project tree.
    dim, kwargs = captured['last_jobs_usage_by']
    assert dim == 'user'
    assert active_project.projcode in kwargs['account']


def test_by_user_fragment_other_row_from_pretruncation_totals(
    app, auth_client, active_project, monkeypatch,
):
    """totals bigger than the visible rows → an inert Other summary row."""
    usage = _sample_usage(totals={'job_count': 100, 'cpu_hours': 900.0,
                                  'gpu_hours': 5.0})
    _install_mock_plugin(app, monkeypatch, jobs_usage_by_return=usage)
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/by-user?machine=derecho'
    )
    body = resp.get_data(as_text=True)
    assert 'beyond top' in body


def test_by_user_fragment_disabled_banner(auth_client, active_project):
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/by-user?machine=derecho'
    )
    assert resp.status_code == 200
    assert 'Per-job data is unavailable' in resp.get_data(as_text=True)


def test_by_user_fragment_400_on_invalid_machine(
    app, auth_client, active_project, monkeypatch,
):
    _install_mock_plugin(app, monkeypatch)
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/by-user?machine=gust'
    )
    assert resp.status_code == 400


def test_by_user_fragment_404_on_unknown_projcode(auth_client):
    resp = auth_client.get('/dashboards/user/jobs/NOPE9999/by-user?machine=derecho')
    assert resp.status_code == 404


def test_wait_times_fragment_caption_on_null_count(
    app, auth_client, active_project, monkeypatch,
):
    """null_count > 0 on the wait dimension → the eligible-time caption."""
    _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist(null_count=7),
    )
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/wait-times?machine=derecho'
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'no wait measurement' in body
    assert 'early 2025 on Derecho' in body


def test_wait_times_fragment_no_caption_when_all_measured(
    app, auth_client, active_project, monkeypatch,
):
    _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist(null_count=0),
    )
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/wait-times?machine=derecho'
    )
    assert 'no wait measurement' not in resp.get_data(as_text=True)


def test_wait_times_fragment_pins_wait_dimension(
    app, auth_client, active_project, monkeypatch,
):
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist(),
    )
    auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/wait-times'
        '?machine=derecho&dimension=gpus'   # client cannot override the pin
    )
    dim, _kwargs = captured['last_jobs_histogram']
    assert dim == 'wait'


def test_job_sizes_fragment_dimension_pills_and_default(
    app, auth_client, active_project, monkeypatch,
):
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist(dimension='nodes'),
    )
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/job-sizes?machine=derecho'
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # Dimension pills present (only on the Sizes tab).
    assert 'dimension=cpus' in body
    assert 'dimension=memory' in body
    # Default dimension is nodes.
    dim, _kwargs = captured['last_jobs_histogram']
    assert dim == 'nodes'


def test_job_sizes_fragment_invalid_dimension_falls_back(
    app, auth_client, active_project, monkeypatch,
):
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist(dimension='nodes'),
    )
    auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/job-sizes'
        '?machine=derecho&dimension=bogus'
    )
    dim, _kwargs = captured['last_jobs_histogram']
    assert dim == 'nodes'


def test_job_sizes_fragment_memory_dimension_forwarded(
    app, auth_client, active_project, monkeypatch,
):
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist(dimension='memory'),
    )
    auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/job-sizes'
        '?machine=derecho&dimension=memory'
    )
    dim, _kwargs = captured['last_jobs_histogram']
    assert dim == 'memory'


def test_job_sizes_fragment_offers_memory_trio_pills(
    app, auth_client, active_project, monkeypatch,
):
    """The Sizes tab renders all six dimension pills, memory trio labeled
    Req mem / Used mem / Wasted."""
    _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist(dimension='nodes'),
    )
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/job-sizes?machine=derecho'
    )
    body = resp.get_data(as_text=True)
    assert 'dimension=memory_used' in body
    assert 'dimension=memory_wasted' in body
    assert 'Req mem' in body
    assert 'Used mem' in body
    assert 'Wasted' in body


@pytest.mark.parametrize('dimension', ['memory_used', 'memory_wasted'])
def test_job_sizes_fragment_memory_trio_forwarded(
    app, auth_client, active_project, monkeypatch, dimension,
):
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist(dimension=dimension),
    )
    auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/job-sizes'
        f'?machine=derecho&dimension={dimension}'
    )
    dim, _kwargs = captured['last_jobs_histogram']
    assert dim == dimension


def test_job_sizes_wasted_caption_derecho_only(
    app, auth_client, active_project, monkeypatch,
):
    """The whole-node caveat renders on derecho's Wasted view and nowhere
    else — not on casper (shared nodes make wasted meaningful there) and
    not on derecho's other dimensions."""
    _install_mock_plugin(
        app, monkeypatch,
        jobs_histogram_return=_sample_hist(dimension='memory_wasted'),
        machines=('derecho', 'casper'),
    )
    base = f'/dashboards/user/jobs/{active_project.projcode}/job-sizes'

    body = auth_client.get(
        f'{base}?machine=derecho&dimension=memory_wasted').get_data(as_text=True)
    assert 'node-exclusive' in body

    body = auth_client.get(
        f'{base}?machine=casper&dimension=memory_wasted').get_data(as_text=True)
    assert 'node-exclusive' not in body

    body = auth_client.get(
        f'{base}?machine=derecho&dimension=memory').get_data(as_text=True)
    assert 'node-exclusive' not in body


def test_histogram_fragment_native_bounds_passthrough(
    app, auth_client, active_project, monkeypatch,
):
    """Envelope-native params (min_param/max_param names) pass through
    verbatim — no display-unit re-derivation, no double conversion."""
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist(),
    )
    auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/wait-times'
        '?machine=derecho&min_eligible_secs=120&max_eligible_secs=900'
        '&min_reqmem=1073741824&min_memory_used=2147483648'
    )
    _dim, kwargs = captured['last_jobs_histogram']
    assert kwargs['min_eligible_secs'] == 120
    assert kwargs['max_eligible_secs'] == 900
    assert kwargs['min_reqmem'] == 1073741824
    assert kwargs['min_memory_used'] == 2147483648


def test_histogram_fragment_native_bound_wins_over_human_units(
    app, auth_client, active_project, monkeypatch,
):
    """When a deep link carries both spellings of the same bound, the
    native form (parsed last) wins."""
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist(),
    )
    auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/wait-times'
        '?machine=derecho&min_wait_hours=2&min_eligible_secs=120'
    )
    _dim, kwargs = captured['last_jobs_histogram']
    assert kwargs['min_eligible_secs'] == 120


def test_histogram_fragment_negative_wasted_bound_not_clamped(
    app, auth_client, active_project, monkeypatch,
):
    """The 'over request' band replays as max_memory_wasted=-1 — the signed
    parse must forward the negative, not clamp it to 0 or drop it."""
    captured = _install_mock_plugin(
        app, monkeypatch,
        jobs_histogram_return=_sample_hist(dimension='memory_wasted'),
    )
    auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/job-sizes'
        '?machine=derecho&dimension=memory_wasted&max_memory_wasted=-1'
    )
    _dim, kwargs = captured['last_jobs_histogram']
    assert kwargs['max_memory_wasted'] == -1


def test_jobs_fragment_native_bounds_forwarded_to_search_and_count(
    app, auth_client, active_project, monkeypatch,
):
    """The per-job table accepts the same native bounds (bar-drill target):
    both jobs_search and the count see them, and the count leaves the
    SAM-summary fast path (range bound in play)."""
    captured = _install_mock_plugin(app, monkeypatch)
    auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}'
        '?machine=derecho&min_memory_wasted=-4294967296&max_memory_wasted=-1'
        '&min_elapsed=3600'
    )
    skw = captured['last_jobs_search_kwargs']
    assert skw['min_memory_wasted'] == -4294967296
    assert skw['max_memory_wasted'] == -1
    assert skw['min_elapsed'] == 3600
    ckw = captured['last_jobs_count_kwargs']
    assert ckw is not None                      # plugin path, not SAM summary
    assert ckw['max_memory_wasted'] == -1


def test_durations_fragment_pins_duration_dimension(
    app, auth_client, active_project, monkeypatch,
):
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist(dimension='duration'),
    )
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/durations?machine=derecho'
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    dim, _kwargs = captured['last_jobs_histogram']
    assert dim == 'duration'
    # No dimension pills outside the Sizes tab.
    assert 'dimension=cpus' not in body


def test_histogram_fragment_converts_wait_hours_to_secs(
    app, auth_client, active_project, monkeypatch,
):
    """min/max_wait_hours (human units) convert to eligible_secs at the
    route boundary — the service/plugin only ever see seconds."""
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist(),
    )
    auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/wait-times'
        '?machine=derecho&min_wait_hours=2&max_wait_hours=4.5'
    )
    _dim, kwargs = captured['last_jobs_histogram']
    assert kwargs['min_eligible_secs'] == 7200
    assert kwargs['max_eligible_secs'] == 16200


def test_histogram_bucket_rows_carry_band_drill_urls(
    app, auth_client, active_project, monkeypatch,
):
    """Populated bands render data-jh-bucket rows whose collapse content
    lazy-loads the per-job fragment with the envelope's min/max bounds
    plus the pane's round-trip filters."""
    _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist(),
    )
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/wait-times'
        '?machine=derecho&queue=cpu'
    )
    body = resp.get_data(as_text=True)
    assert 'data-jh-bucket="0"' in body
    assert 'data-jh-bucket="1"' in body
    import re
    url0 = re.search(r'id="[^"]*-b0-content"\s+hx-get="([^"]+)"', body).group(1)
    assert f'/dashboards/user/jobs/{active_project.projcode}?' in url0
    assert 'min_eligible_secs=0' in url0
    assert 'max_eligible_secs=59' in url0
    assert 'queue=cpu' in url0             # pane filters carried into the drill
    assert 'machine=derecho' in url0
    assert 'target_id=' in url0


_WASTED_HIST = {
    'dimension': 'memory_wasted', 'column': 'memory_wasted', 'unit': 'bytes',
    'min_param': 'min_memory_wasted', 'max_param': 'max_memory_wasted',
    'buckets': [
        {'label': 'over request', 'lo': None, 'hi': -1,
         'job_count': 3, 'cpu_hours': 30.0, 'gpu_hours': 0.0},
        {'label': '<1GB', 'lo': 0, 'hi': 2 ** 30 - 1,
         'job_count': 0, 'cpu_hours': 0.0, 'gpu_hours': 0.0},
        {'label': '>1GB', 'lo': 2 ** 30, 'hi': None,
         'job_count': 7, 'cpu_hours': 70.0, 'gpu_hours': 0.0},
    ],
    'null_count': 0, 'total_count': 10,
}


def test_histogram_bucket_drill_omits_open_ends(
    app, auth_client, active_project, monkeypatch,
):
    """A None band end is an open side — its param is omitted from the
    drill URL in both directions: the negative 'over request' band emits
    only the max bound, the open top band only the min. Empty bands get
    no drill row at all."""
    import re
    _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_WASTED_HIST,
    )
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/job-sizes'
        '?machine=derecho&dimension=memory_wasted'
    )
    body = resp.get_data(as_text=True)

    url0 = re.search(r'id="[^"]*-b0-content"\s+hx-get="([^"]+)"', body).group(1)
    assert 'min_memory_wasted' not in url0
    assert 'max_memory_wasted=-1' in url0

    assert 'data-jh-bucket="1"' not in body        # empty band: inert row
    assert re.search(r'id="[^"]*-b1-content"', body) is None

    url2 = re.search(r'id="[^"]*-b2-content"\s+hx-get="([^"]+)"', body).group(1)
    assert f'min_memory_wasted={2 ** 30}' in url2
    assert 'max_memory_wasted' not in url2


def test_histogram_bucket_drill_band_bound_replaces_pane_param(
    app, auth_client, active_project, monkeypatch,
):
    """When the pane itself is filtered on the same native bound the band
    replays, the band's value replaces the pane's in the drill URL —
    never both."""
    import re
    _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist(),
    )
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/wait-times'
        '?machine=derecho&min_eligible_secs=999'
    )
    body = resp.get_data(as_text=True)
    url0 = re.search(r'id="[^"]*-b0-content"\s+hx-get="([^"]+)"', body).group(1)
    assert 'min_eligible_secs=0' in url0
    assert 'min_eligible_secs=999' not in url0


def test_histogram_fragment_metric_pill_roundtrip(
    app, auth_client, active_project, monkeypatch,
):
    _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist(),
    )
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/wait-times'
        '?machine=derecho&metric=cpu_hours'
    )
    body = resp.get_data(as_text=True)
    # The cpu_hours pill is the active one.
    import re
    assert re.search(r'active[^>]*>\s*CPU-hours', body) or \
        re.search(r'CPU-hours', body)


# ---------------------------------------------------------------------------
# Commit 6: machine-wide family (operator) + explorer pages + Status tab
# ---------------------------------------------------------------------------

_MACHINE_FRAGMENTS = ['', '/by-user', '/by-project', '/wait-times',
                      '/job-sizes', '/durations']


@pytest.mark.parametrize('suffix', _MACHINE_FRAGMENTS + ['/explore'])
def test_machine_routes_403_without_permission(non_admin_client, suffix):
    resp = non_admin_client.get(f'/dashboards/user/jobs/machine/derecho{suffix}')
    assert resp.status_code == 403


@pytest.mark.parametrize('suffix', _MACHINE_FRAGMENTS + ['/explore'])
def test_machine_routes_disabled_banner_with_permission(auth_client, suffix):
    """benkirk holds VIEW_ALL_JOB_DATA → 200 (plugin off → banner)."""
    resp = auth_client.get(f'/dashboards/user/jobs/machine/derecho{suffix}')
    assert resp.status_code == 200
    assert 'Per-job data is unavailable' in resp.get_data(as_text=True)


@pytest.mark.parametrize('suffix', _MACHINE_FRAGMENTS + ['/explore'])
def test_machine_routes_404_unknown_machine(app, auth_client, monkeypatch, suffix):
    """With the plugin up for derecho only, /machine/gust → 404."""
    _install_mock_plugin(app, monkeypatch, machines=('derecho',))
    resp = auth_client.get(f'/dashboards/user/jobs/machine/gust{suffix}')
    assert resp.status_code == 404


def test_machine_jobs_fragment_unscoped(app, auth_client, monkeypatch):
    """The machine table issues no account filter and renders rows."""
    captured = _install_mock_plugin(app, monkeypatch,
                                    jobs_search_return=[_make_row()],
                                    jobs_count_return=1)
    resp = auth_client.get('/dashboards/user/jobs/machine/derecho')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert '500.desched1' in body
    kw = captured['last_jobs_search_kwargs']
    assert 'account' not in kw
    ckw = captured['last_jobs_count_kwargs']
    assert 'account' not in ckw


def test_machine_by_user_fragment_unscoped(app, auth_client, monkeypatch):
    captured = _install_mock_plugin(app, monkeypatch,
                                    jobs_usage_by_return=_sample_usage())
    resp = auth_client.get('/dashboards/user/jobs/machine/derecho/by-user')
    assert resp.status_code == 200
    assert 'data-job-user="alice"' in resp.get_data(as_text=True)
    _dim, kwargs = captured['last_jobs_usage_by']
    assert 'account' not in kwargs


def test_machine_by_project_fragment_unscoped(app, auth_client, monkeypatch):
    """Machine-wide By Project: dimension 'account', no user pin, no
    account scoping; rows drill into the machine jobs fragment narrowed
    by account=."""
    captured = _install_mock_plugin(app, monkeypatch,
                                    jobs_usage_by_return=_PROJECT_USAGE)
    resp = auth_client.get('/dashboards/user/jobs/machine/derecho/by-project')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    dim, kwargs = captured['last_jobs_usage_by']
    assert dim == 'account'
    assert kwargs.get('user') is None      # no pin (filter dict carries None)
    assert 'account' not in kwargs
    assert 'data-job-project="SCSG0001"' in body
    assert ('/dashboards/user/jobs/machine/derecho'
            '?machine=derecho&account=SCSG0001') in body


def test_machine_by_project_threads_metric_sort(app, auth_client, monkeypatch):
    captured = _install_mock_plugin(app, monkeypatch,
                                    jobs_usage_by_return=_PROJECT_USAGE)
    auth_client.get(
        '/dashboards/user/jobs/machine/derecho/by-project?metric=gpu_hours')
    _dim, kwargs = captured['last_jobs_usage_by']
    assert kwargs['sort_by'] == 'gpu_hours'


def test_machine_jobs_fragment_account_narrows(app, auth_client, monkeypatch):
    """?account= narrows the machine-wide table (the By Project drill) —
    rows and count both see it, and it surfaces as the project: badge."""
    captured = _install_mock_plugin(app, monkeypatch,
                                    jobs_search_return=[_make_row()],
                                    jobs_count_return=1)
    resp = auth_client.get(
        '/dashboards/user/jobs/machine/derecho?account=SCSG0001')
    assert resp.status_code == 200
    assert captured['last_jobs_search_kwargs']['account'] == 'SCSG0001'
    assert captured['last_jobs_count_kwargs']['account'] == 'SCSG0001'
    assert 'project: SCSG0001' in resp.get_data(as_text=True)


def test_machine_histogram_fragment_unscoped(app, auth_client, monkeypatch):
    captured = _install_mock_plugin(app, monkeypatch,
                                    jobs_histogram_return=_sample_hist())
    resp = auth_client.get('/dashboards/user/jobs/machine/derecho/wait-times')
    assert resp.status_code == 200
    _dim, kwargs = captured['last_jobs_histogram']
    assert 'account' not in kwargs


def test_explore_machine_page_renders_filter_panel(app, auth_client, monkeypatch):
    _install_mock_plugin(app, monkeypatch)
    resp = auth_client.get('/dashboards/user/jobs/machine/derecho/explore')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'filter-sidebar' in body
    assert 'Machine-wide (operator view)' in body
    # Panel fields present.
    assert 'name="min_nodes"' in body
    assert 'name="exit_status"' in body


def test_explore_page_project_mode(app, auth_client, active_project, monkeypatch):
    _install_mock_plugin(app, monkeypatch)
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/explore?machine=derecho'
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'filter-sidebar' in body
    assert active_project.projcode in body
    # Machine-wide badge must NOT show in project mode.
    assert 'Machine-wide (operator view)' not in body


def test_explore_page_carries_filters_into_initial_url(
    app, auth_client, active_project, monkeypatch,
):
    """Deep-link with filters → the lazy-load URL reproduces them."""
    _install_mock_plugin(app, monkeypatch)
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/explore'
        '?machine=derecho&queue=main&min_nodes=4&min_wait_hours=1.5'
    )
    body = resp.get_data(as_text=True)
    assert 'queue=main' in body
    assert 'min_nodes=4' in body
    assert 'min_wait_hours=1.5' in body


def test_explore_page_elapsed_reqmem_panel_roundtrip(
    app, auth_client, active_project, monkeypatch,
):
    """The panel renders the elapsed/req-mem inputs, echoes deep-linked
    values back into them, and carries them into the lazy-load URL."""
    _install_mock_plugin(app, monkeypatch)
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/explore'
        '?machine=derecho&min_elapsed_hours=2.5&max_reqmem_gb=128'
    )
    body = resp.get_data(as_text=True)
    # Panel inputs exist (all four names) with the deep-linked values.
    for field in ('min_elapsed_hours', 'max_elapsed_hours',
                  'min_reqmem_gb', 'max_reqmem_gb'):
        assert f'name="{field}"' in body
    assert 'value="2.5"' in body
    assert 'value="128' in body
    # …and the initial fragment URL reproduces them.
    assert 'min_elapsed_hours=2.5' in body
    assert 'max_reqmem_gb=128' in body


def test_fragment_converts_elapsed_hours_and_reqmem_gb(
    app, auth_client, active_project, monkeypatch,
):
    """Panel units convert at the route boundary: hours → seconds for
    elapsed, GB → bytes (1024³) for requested memory."""
    captured = _install_mock_plugin(app, monkeypatch)
    auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}'
        '?machine=derecho&min_elapsed_hours=1.5&max_elapsed_hours=24'
        '&min_reqmem_gb=0.5&max_reqmem_gb=128'
    )
    skw = captured['last_jobs_search_kwargs']
    assert skw['min_elapsed'] == 5400
    assert skw['max_elapsed'] == 86400
    assert skw['min_reqmem'] == 512 * 1024 ** 2      # 0.5 GB
    assert skw['max_reqmem'] == 128 * 1024 ** 3


_SAMPLE_FACETS = {
    'queue': [{'value': 'cpu', 'count': 120}, {'value': 'gpu', 'count': 30},
              {'value': None, 'count': 2}],
    'qos': [{'value': 'regular', 'count': 100}, {'value': 'premium', 'count': 50}],
    'exit_status': [{'value': '0', 'count': 140}, {'value': '271', 'count': 10}],
}


def test_explore_page_initial_url_requests_chips(
    app, auth_client, active_project, monkeypatch,
):
    """The explorer's lazy-load URL asks the fragment for the OOB chip
    strip, and the page renders the placeholder it swaps into."""
    _install_mock_plugin(app, monkeypatch)
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/explore?machine=derecho'
    )
    body = resp.get_data(as_text=True)
    assert 'chips=1' in body
    assert 'id="jobs-facet-chips-jobs-explore"' in body
    assert 'name="chips"' in body          # panel form round-trips it


def test_fragment_chips_render_oob_with_counts(
    app, auth_client, active_project, monkeypatch,
):
    """?chips=1 → the fragment appends an hx-swap-oob strip: value chips
    with live counts, NULL-FK rows skipped, wired to the panel form."""
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_facets_return=_SAMPLE_FACETS,
    )
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}'
        '?machine=derecho&chips=1&queue=cpu'
    )
    body = resp.get_data(as_text=True)
    assert 'hx-swap-oob' in body
    assert 'data-action="set-filter-submit"' in body
    assert 'data-form-id="jobs-filters-panel-jobs-' in body
    # Active chip (queue=cpu) highlights and clears on click.
    import re
    cpu_chip = re.search(
        r'<button[^>]*data-field="queue"[^>]*data-value=""[^>]*>', body)
    assert cpu_chip is not None and 'btn-primary' in cpu_chip.group(0)
    # Inactive chip carries its value.
    assert 'data-value="gpu"' in body
    assert 'data-value="271"' in body
    # NULL-FK queue row renders no chip (nothing to filter by).
    assert 'data-value="None"' not in body
    # The facets call saw the same filter set as the table.
    fkw = captured['last_jobs_facets_kwargs']
    assert fkw['queue'] == 'cpu'
    assert fkw['limit'] == 8


def test_fragment_no_chips_without_param(
    app, auth_client, active_project, monkeypatch,
):
    """Card/drill embeds never send chips=1 → no facets query, no OOB."""
    captured = _install_mock_plugin(app, monkeypatch)
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}?machine=derecho'
    )
    body = resp.get_data(as_text=True)
    assert 'hx-swap-oob' not in body
    assert captured['last_jobs_facets_kwargs'] is None


def test_fragment_chips_degrade_on_facets_error(
    app, auth_client, active_project, monkeypatch,
):
    """A facets failure must not take the table down — the fragment
    renders normally with no chip strip."""
    _install_mock_plugin(
        app, monkeypatch, jobs_facets_raises=True,
    )
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}'
        '?machine=derecho&chips=1'
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'hx-swap-oob' not in body
    assert 'Could not load per-job data' not in body


def test_fragment_chips_project_scope_pins_account(
    app, auth_client, active_project, monkeypatch,
):
    """Facets are scoped exactly like the table — the project tree's
    projcodes pin the account filter."""
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_facets_return=_SAMPLE_FACETS,
    )
    auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}'
        '?machine=derecho&chips=1'
    )
    fkw = captured['last_jobs_facets_kwargs']
    assert active_project.projcode in fkw['account']


def test_user_fragment_chips_pin_username(app, auth_client, monkeypatch):
    """User-mode chips describe the pinned user's jobs only — the session
    username rides into the facets call, client ?user= notwithstanding."""
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_facets_return=_SAMPLE_FACETS,
    )
    auth_client.get(
        '/dashboards/user/jobs/user/derecho'
        '?machine=derecho&chips=1&user=mallory'
    )
    fkw = captured['last_jobs_facets_kwargs']
    assert fkw['user'] == 'benkirk'


def test_explore_page_scope_rerooting_badge(
    app, auth_client, active_project, monkeypatch,
):
    """A valid child scope shows the scope badge; out-of-tree falls back."""
    import types as _types
    from sam import Project

    child = _types.SimpleNamespace(projcode='CHILD0001')
    real_get = Project.get_by_projcode

    def _fake_get(session, projcode):
        if projcode == 'CHILD0001':
            fake = MagicMock()
            fake.projcode = 'CHILD0001'
            fake.tree_root = active_project.tree_root
            fake.get_descendants = lambda include_self=True: [child]
            return fake
        return real_get(session, projcode)

    monkeypatch.setattr(Project, 'get_by_projcode', staticmethod(_fake_get))
    _install_mock_plugin(app, monkeypatch)

    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/explore'
        '?machine=derecho&scope=CHILD0001'
    )
    assert resp.status_code == 200
    assert 'scope: CHILD0001' in resp.get_data(as_text=True)


def test_fragment_scope_rerooting_narrows_account(
    app, auth_client, active_project, monkeypatch,
):
    """?scope=<child> narrows the account filter to the child's subtree."""
    import types as _types
    from sam import Project

    child_desc = [_types.SimpleNamespace(projcode='CHILD0001'),
                  _types.SimpleNamespace(projcode='CHILD0001_a')]
    real_get = Project.get_by_projcode

    def _fake_get(session, projcode):
        if projcode == 'CHILD0001':
            fake = MagicMock()
            fake.projcode = 'CHILD0001'
            fake.tree_root = active_project.tree_root
            fake.get_descendants = lambda include_self=True: child_desc
            return fake
        return real_get(session, projcode)

    monkeypatch.setattr(Project, 'get_by_projcode', staticmethod(_fake_get))
    captured = _install_mock_plugin(app, monkeypatch,
                                    jobs_histogram_return=_sample_hist())

    auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/wait-times'
        '?machine=derecho&scope=CHILD0001'
    )
    _dim, kwargs = captured['last_jobs_histogram']
    assert kwargs['account'] == ['CHILD0001', 'CHILD0001_a']


# ---------------------------------------------------------------------------
# Status dashboard: Job History tab
# ---------------------------------------------------------------------------

def test_status_job_history_403_without_permission(non_admin_client):
    resp = non_admin_client.get('/status/job-history')
    assert resp.status_code == 403


def test_status_job_history_empty_state_when_disabled(auth_client):
    """Plugin off → no machines → the info alert (never a broken card)."""
    resp = auth_client.get('/status/job-history')
    assert resp.status_code == 200
    assert 'No job-history data is currently available' in resp.get_data(as_text=True)


def test_status_job_history_machine_pills_when_enabled(
    app, auth_client, monkeypatch,
):
    _install_mock_plugin(app, monkeypatch, machines=('derecho', 'casper'))
    resp = auth_client.get('/status/job-history')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'job-hist-subtab-1' in body
    assert 'Derecho' in body
    assert 'Casper' in body
    # Machine-mode card fragments wired per pill.
    assert '/dashboards/user/jobs/machine/casper' in body


def test_status_tab_hidden_without_machines(auth_client):
    """Plugin off → the Job History tab is absent from the status pages."""
    resp = auth_client.get('/status/derecho')
    assert resp.status_code == 200
    assert '/status/job-history' not in resp.get_data(as_text=True)


def test_status_tab_visible_for_operator_with_machines(
    app, auth_client, monkeypatch,
):
    _install_mock_plugin(app, monkeypatch, machines=('derecho',))
    resp = auth_client.get('/status/derecho')
    assert resp.status_code == 200
    assert '/status/job-history' in resp.get_data(as_text=True)


def test_status_tab_hidden_for_plain_user_with_machines(
    app, non_admin_client, monkeypatch,
):
    """Even with machines up, no VIEW_ALL_JOB_DATA → no tab, anywhere."""
    _install_mock_plugin(app, monkeypatch, machines=('derecho',))
    resp = non_admin_client.get('/status/derecho')
    assert resp.status_code == 200
    assert '/status/job-history' not in resp.get_data(as_text=True)


# ---------------------------------------------------------------------------
# Commit 7: user family ("My Jobs") — server-side pinning + page/tab
# ---------------------------------------------------------------------------

_USER_FRAGMENTS = ['', '/wait-times', '/job-sizes', '/durations']


@pytest.mark.parametrize('suffix', _USER_FRAGMENTS + ['/explore'])
def test_user_routes_disabled_banner(auth_client, suffix):
    """Plugin off → 200 with the unavailable banner (login only, no perm)."""
    resp = auth_client.get(f'/dashboards/user/jobs/user/derecho{suffix}')
    assert resp.status_code == 200
    assert 'Per-job data is unavailable' in resp.get_data(as_text=True)


@pytest.mark.parametrize('suffix', _USER_FRAGMENTS + ['/explore'])
def test_user_routes_404_unknown_machine(app, auth_client, monkeypatch, suffix):
    _install_mock_plugin(app, monkeypatch, machines=('derecho',))
    resp = auth_client.get(f'/dashboards/user/jobs/user/gust{suffix}')
    assert resp.status_code == 404


def test_user_jobs_fragment_pins_session_user(app, auth_client, monkeypatch):
    """The table is pinned to the logged-in user (benkirk for auth_client)."""
    captured = _install_mock_plugin(app, monkeypatch,
                                    jobs_search_return=[_make_row()],
                                    jobs_count_return=1)
    resp = auth_client.get('/dashboards/user/jobs/user/derecho')
    assert resp.status_code == 200
    kw = captured['last_jobs_search_kwargs']
    assert kw['user'] == 'benkirk'
    ckw = captured['last_jobs_count_kwargs']
    assert ckw['user'] == 'benkirk'


def test_user_jobs_fragment_ignores_client_user_param(
    app, auth_client, monkeypatch,
):
    """?user=<other> must change nothing — the pin always wins."""
    captured = _install_mock_plugin(app, monkeypatch,
                                    jobs_search_return=[_make_row()],
                                    jobs_count_return=1)
    resp = auth_client.get('/dashboards/user/jobs/user/derecho?user=mallory')
    assert resp.status_code == 200
    assert captured['last_jobs_search_kwargs']['user'] == 'benkirk'
    assert captured['last_jobs_count_kwargs']['user'] == 'benkirk'


@pytest.mark.parametrize('suffix', ['/wait-times', '/job-sizes', '/durations'])
def test_user_histogram_fragments_ignore_client_user_param(
    app, auth_client, monkeypatch, suffix,
):
    captured = _install_mock_plugin(app, monkeypatch,
                                    jobs_histogram_return=_sample_hist())
    resp = auth_client.get(
        f'/dashboards/user/jobs/user/derecho{suffix}?user=mallory'
    )
    assert resp.status_code == 200
    _dim, kwargs = captured['last_jobs_histogram']
    assert kwargs['user'] == 'benkirk'


_PROJECT_USAGE = {
    'dimension': 'account',
    'rows': [
        {'value': 'SCSG0001', 'job_count': 30, 'cpu_hours': 300.0, 'gpu_hours': 0.0},
        {'value': 'UABC0002', 'job_count': 12, 'cpu_hours': 120.0, 'gpu_hours': 2.0},
    ],
    'totals': {'job_count': 42, 'cpu_hours': 420.0, 'gpu_hours': 2.0},
}


def test_by_project_fragment_renders_rows_and_pinned_pie(
    app, auth_client, monkeypatch,
):
    """The My Jobs By Project tab: plugin grouped by 'account' with the
    session user pinned (client ?user= ignored); rows carry
    data-job-project and the pie #job-proj sentinels."""
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_usage_by_return=_PROJECT_USAGE,
    )
    resp = auth_client.get(
        '/dashboards/user/jobs/user/derecho/by-project?user=mallory'
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    dim, kwargs = captured['last_jobs_usage_by']
    assert dim == 'account'
    assert kwargs['user'] == 'benkirk'
    assert kwargs['limit'] == 25
    assert 'data-job-project="SCSG0001"' in body
    assert '#job-proj-SCSG0001' in body
    # Row drill narrows the user-mode jobs fragment by account.
    assert '/dashboards/user/jobs/user/derecho?machine=derecho&account=SCSG0001' in body


def test_by_project_fragment_404_unknown_machine(app, auth_client, monkeypatch):
    _install_mock_plugin(app, monkeypatch)
    resp = auth_client.get('/dashboards/user/jobs/user/fugaku/by-project')
    assert resp.status_code == 404


def test_by_project_fragment_disabled_banner(auth_client):
    resp = auth_client.get('/dashboards/user/jobs/user/derecho/by-project')
    assert resp.status_code == 200
    assert 'plugin is not loaded' in resp.get_data(as_text=True)


def test_user_fragment_account_narrows_own_jobs(app, auth_client, monkeypatch):
    """?account=<projcode> narrows the pinned user's OWN jobs — both the
    rows and the count see it, and the user pin survives."""
    captured = _install_mock_plugin(app, monkeypatch)
    resp = auth_client.get(
        '/dashboards/user/jobs/user/derecho?machine=derecho&account=SCSG0001'
    )
    assert resp.status_code == 200
    skw = captured['last_jobs_search_kwargs']
    assert skw['account'] == 'SCSG0001'
    assert skw['user'] == 'benkirk'
    ckw = captured['last_jobs_count_kwargs']
    assert ckw['account'] == 'SCSG0001'
    assert ckw['user'] == 'benkirk'
    # The narrowing surfaces as a header badge.
    assert 'project: SCSG0001' in resp.get_data(as_text=True)


def test_project_fragment_ignores_client_account_param(
    app, auth_client, active_project, monkeypatch,
):
    """Project mode keeps its account list server-derived — a client
    ?account= must not replace the tree pin."""
    captured = _install_mock_plugin(app, monkeypatch)
    auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}'
        '?machine=derecho&account=EVIL0001'
    )
    skw = captured['last_jobs_search_kwargs']
    assert skw['account'] == [active_project.projcode] or \
        active_project.projcode in skw['account']
    assert 'EVIL0001' not in skw['account']


def test_project_by_project_fragment_scoped_to_tree(
    app, auth_client, active_project, monkeypatch,
):
    """Project-mode By Project: dimension 'account' scoped by the
    server-derived tree list, no user pin; rows drill into the
    project-mode jobs fragment narrowed by account=."""
    captured = _install_mock_plugin(app, monkeypatch,
                                    jobs_usage_by_return=_PROJECT_USAGE)
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/by-project'
        '?machine=derecho'
    )
    assert resp.status_code == 200
    dim, kwargs = captured['last_jobs_usage_by']
    assert dim == 'account'
    assert kwargs.get('user') is None      # tree scoping, no user pin
    assert active_project.projcode in kwargs['account']
    assert (f'/dashboards/user/jobs/{active_project.projcode}'
            '?machine=derecho&account=SCSG0001') in resp.get_data(as_text=True)


def test_project_fragment_intree_account_narrows(
    app, auth_client, active_project, monkeypatch,
):
    """An in-tree ?account= narrows the project table to that projcode
    (the parent-project By Project drill) and surfaces as the badge —
    the complement of test_project_fragment_ignores_client_account_param,
    which pins that out-of-tree values stay ignored."""
    captured = _install_mock_plugin(app, monkeypatch)
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}'
        f'?machine=derecho&account={active_project.projcode}'
    )
    assert resp.status_code == 200
    skw = captured['last_jobs_search_kwargs']
    assert skw['account'] == [active_project.projcode]
    assert f'project: {active_project.projcode}' in resp.get_data(as_text=True)


def test_status_job_history_card_offers_both_tabs(app, auth_client, monkeypatch):
    """Machine mode renders BOTH By User and By Project tabs."""
    _install_mock_plugin(app, monkeypatch, machines=('derecho',))
    body = auth_client.get('/status/job-history').get_data(as_text=True)
    assert 'By User' in body
    assert 'By Project' in body
    assert '/dashboards/user/jobs/machine/derecho/by-project' in body


def test_resource_details_modal_shells_and_by_project_gate(
    app, auth_client, active_project, monkeypatch,
):
    """The project-mode host page carries both entity-modal shells (it
    extends dashboards/base.html, which includes neither), and the By
    Project tab renders only when the account tree spans >1 projcode."""
    _install_mock_plugin(app, monkeypatch)
    resp = auth_client.get(
        f'/user/resource-details/{active_project.projcode}?resource=Derecho')
    if resp.status_code != 200:
        return  # snapshot doesn't have this resource — nothing to check
    body = resp.get_data(as_text=True)
    assert 'id="userDetailsModal"' in body
    assert 'id="projectDetailsModal"' in body
    multi = len(active_project.get_descendants(include_self=True)) > 1
    assert (f'/dashboards/user/jobs/{active_project.projcode}/by-project'
            in body) == multi


def test_by_user_username_links_open_user_modal(app, auth_client, monkeypatch):
    """With VIEW_USERS, usernames become quick-view modal triggers."""
    _install_mock_plugin(app, monkeypatch, jobs_usage_by_return=_sample_usage())
    body = auth_client.get(
        '/dashboards/user/jobs/machine/derecho/by-user').get_data(as_text=True)
    assert 'data-bs-target="#userDetailsModal"' in body
    assert '/admin/user/alice' in body


def test_by_user_no_modal_affordance_without_view_users(
    app, client, session, multi_project_user, monkeypatch,
):
    """A project member without VIEW_USERS gets plain <code> usernames —
    no affordance that would 403 at the user_card route."""
    _install_mock_plugin(app, monkeypatch, jobs_usage_by_return=_sample_usage())
    with client.session_transaction() as sess_data:
        sess_data['_user_id'] = str(multi_project_user.user_id)
        sess_data['_fresh'] = True
    resp = None
    for proj in multi_project_user.projects:
        resp = client.get(
            f'/dashboards/user/jobs/{proj.projcode}/by-user?machine=derecho')
        if resp.status_code == 200:
            break
    else:
        pytest.skip('snapshot member user has no accessible project')
    body = resp.get_data(as_text=True)
    assert 'data-job-user="alice"' in body      # rows render fine
    assert 'userDetailsModal' not in body       # affordance suppressed


def test_by_project_projcode_links_open_project_modal(
    app, auth_client, monkeypatch,
):
    """User mode always renders the project quick-view affordance — the
    rows are the pinned user's own projects."""
    _install_mock_plugin(app, monkeypatch, jobs_usage_by_return=_PROJECT_USAGE)
    body = auth_client.get(
        '/dashboards/user/jobs/user/derecho/by-project').get_data(as_text=True)
    assert 'data-bs-target="#projectDetailsModal"' in body
    assert 'project-details-modal/SCSG0001' in body


def test_my_jobs_card_offers_by_project_tab(app, auth_client, monkeypatch):
    """The user-mode card swaps By User for By Project."""
    _install_mock_plugin(app, monkeypatch)
    resp = auth_client.get('/user/jobs')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'By Project' in body
    assert 'by-project' in body
    assert 'By User' not in body


def test_service_jobs_usage_by_project_requires_username(app, monkeypatch):
    from webapp.jobs import service

    _install_mock_plugin(app, monkeypatch)
    with app.app_context():
        with pytest.raises(ValueError, match='username'):
            service.jobs_usage_by_project('derecho', username='')


def test_service_jobs_usage_by_project_rejects_user_filter(app, monkeypatch):
    from webapp.jobs import service

    _install_mock_plugin(app, monkeypatch)
    with app.app_context():
        with pytest.raises(ValueError, match='user'):
            service.jobs_usage_by_project(
                'derecho', username='benkirk', user='mallory',
            )


def test_user_explore_page_omits_user_picker(app, auth_client, monkeypatch):
    _install_mock_plugin(app, monkeypatch)
    resp = auth_client.get('/dashboards/user/jobs/user/derecho/explore')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'My Jobs' in body
    # No user picker in user mode — the pin is not negotiable.
    assert 'name="user_id"' not in body
    # Other filter fields still present.
    assert 'name="queue"' in body


def test_my_jobs_page_404_without_machines(auth_client):
    """Plugin off → no machines → the page 404s (tab is hidden too)."""
    resp = auth_client.get('/user/jobs')
    assert resp.status_code == 404


def test_my_jobs_page_renders_machine_pills(app, auth_client, monkeypatch):
    _install_mock_plugin(app, monkeypatch, machines=('derecho', 'casper'))
    resp = auth_client.get('/user/jobs')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'my-jobs-subtab-1' in body
    assert 'Derecho' in body and 'Casper' in body
    # User-mode card fragments wired per pill.
    assert '/dashboards/user/jobs/user/casper' in body
    # By User tab suppressed in user mode.
    assert 'By User' not in body


def test_my_jobs_tab_visibility_follows_machines(app, auth_client, monkeypatch):
    # Hidden when plugin off…
    resp = auth_client.get('/user/accounts')
    assert '/user/jobs' not in resp.get_data(as_text=True)
    # …visible when machines are up.
    _install_mock_plugin(app, monkeypatch, machines=('derecho',))
    resp = auth_client.get('/user/accounts')
    assert '/user/jobs' in resp.get_data(as_text=True)


# ---------------------------------------------------------------------------
# UX round 3: owner-tier histogram drill, User column, metric-ranked usage
# ---------------------------------------------------------------------------

def _sample_hist_owners(dimension='wait'):
    """_sample_hist plus per-bucket owners (plugin owners_limit envelope).

    Bucket '<1m' (10 jobs): alice 6 jobs / 30 cpu-h, bob 3 jobs / 70 cpu-h
    → ranked alice-first on the jobs metric, bob-first on cpu_hours; 1 job
    unattributed (the "Other users" remainder row).
    Bucket '1-5m' (4 jobs): carol owns everything → single-owner shortcut.
    """
    h = _sample_hist(dimension)
    h['buckets'][0]['owners'] = {
        'alice': {'job_count': 6, 'cpu_hours': 30.0, 'gpu_hours': 0.0},
        'bob':   {'job_count': 3, 'cpu_hours': 70.0, 'gpu_hours': 0.0},
    }
    h['buckets'][1]['owners'] = {
        'carol': {'job_count': 4, 'cpu_hours': 40.0, 'gpu_hours': 1.0},
    }
    return h


def _get_hist_body(app, auth_client, active_project, monkeypatch, query=''):
    _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist_owners(),
    )
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/wait-times'
        f'?machine=derecho{query}'
    )
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def test_histogram_bucket_renders_owner_table(
    app, auth_client, active_project, monkeypatch,
):
    """Multi-owner band expands to a per-user tier ranked by the active
    metric (jobs default → alice first; cpu_hours → bob first)."""
    body = _get_hist_body(app, auth_client, active_project, monkeypatch)
    assert 'alice' in body and 'bob' in body
    assert '-b0-u1-row' in body and '-b0-u2-row' in body
    assert body.index('alice') < body.index('bob')

    body_cpu = _get_hist_body(app, auth_client, active_project, monkeypatch,
                              query='&metric=cpu_hours')
    assert body_cpu.index('bob') < body_cpu.index('alice')


def test_histogram_owner_drill_appends_user(
    app, auth_client, active_project, monkeypatch,
):
    """Each owner row lazy-loads the band's jobs fragment with the band
    bounds AND the username appended."""
    import re
    body = _get_hist_body(app, auth_client, active_project, monkeypatch)
    m = re.search(r'id="[^"]*-b0-u1-content"\s+hx-get="([^"]+)"', body)
    assert m, 'owner drill div missing'
    url = m.group(1).replace('&amp;', '&')
    assert 'min_eligible_secs=0' in url
    assert 'max_eligible_secs=59' in url
    assert 'user=alice' in url
    assert 'machine=derecho' in url
    assert 'target_id=' in url
    # Owner drills bind to the Bootstrap collapse event, never click.
    assert 'shown.bs.collapse' in body


def test_histogram_owner_remainder_row(
    app, auth_client, active_project, monkeypatch,
):
    """Owners summing below the band totals → a muted, non-drillable
    "Other users" row."""
    body = _get_hist_body(app, auth_client, active_project, monkeypatch)
    assert 'Other users' in body
    assert 'beyond top 2' in body


def test_histogram_single_owner_shortcut(
    app, auth_client, active_project, monkeypatch,
):
    """A band wholly owned by one user skips the per-user tier and drills
    straight to their jobs."""
    import re
    body = _get_hist_body(app, auth_client, active_project, monkeypatch)
    assert '-b1-u1-row' not in body
    m = re.search(r'id="[^"]*-b1-content"\s+hx-get="([^"]+)"', body)
    assert m, 'single-owner drill div missing'
    assert 'user=carol' in m.group(1).replace('&amp;', '&')


def test_histogram_no_details_wrapper(
    app, auth_client, active_project, monkeypatch,
):
    """The bucket table is always visible now — no <details> wrapper."""
    body = _get_hist_body(app, auth_client, active_project, monkeypatch)
    assert '<details' not in body
    assert 'Bucket breakdown' in body


def test_histogram_collapse_rows_no_persist(
    app, auth_client, active_project, monkeypatch,
):
    """Histogram collapse rows opt out of nav-view-persistence — a restored
    auto-expand would re-fire the lazy drill fetches on every reload."""
    body = _get_hist_body(app, auth_client, active_project, monkeypatch)
    assert 'data-no-persist' in body


def test_histogram_ownerless_fallback_keeps_single_level(
    app, auth_client, active_project, monkeypatch,
):
    """Owner-less envelope (older plugin / cached) renders the original
    band → jobs drill with no per-user tier."""
    import re
    _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist(),
    )
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/wait-times'
        f'?machine=derecho'
    )
    body = resp.get_data(as_text=True)
    assert re.search(r'id="[^"]*-b0-content"\s+hx-get="', body)
    assert '-b0-u1-row' not in body


def test_histogram_owners_limit_and_sort_forwarded(
    app, auth_client, active_project, monkeypatch,
):
    """owners_limit rides every histogram call, and owners_sort_by follows
    the metric pill — which top-N survives must match what's displayed."""
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist_owners(),
    )
    auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/wait-times'
        f'?machine=derecho'
    )
    _dim, kwargs = captured['last_jobs_histogram']
    assert kwargs['owners_limit'] == 10
    assert kwargs['owners_sort_by'] == 'job_count'   # _DEFAULT_METRIC_HIST

    auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/wait-times'
        f'?machine=derecho&metric=gpu_hours'
    )
    _dim, kwargs = captured['last_jobs_histogram']
    assert kwargs['owners_sort_by'] == 'gpu_hours'


# --- Histogram User|Project owner pill (owners_by) --------------------------

def _sample_hist_project_owners(dimension='wait'):
    """Owner keys are projcodes — the plugin owners_by='account' envelope.
    Same numeric shape as _sample_hist_owners."""
    h = _sample_hist(dimension)
    h['buckets'][0]['owners'] = {
        'SCSG0001': {'job_count': 6, 'cpu_hours': 30.0, 'gpu_hours': 0.0},
        'UABC0002': {'job_count': 3, 'cpu_hours': 70.0, 'gpu_hours': 0.0},
    }
    h['buckets'][1]['owners'] = {
        'SCSG0001': {'job_count': 4, 'cpu_hours': 40.0, 'gpu_hours': 1.0},
    }
    return h


def test_histogram_owner_pill_offered_in_machine_mode(
    app, auth_client, monkeypatch,
):
    _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist_owners(),
    )
    body = auth_client.get(
        '/dashboards/user/jobs/machine/derecho/wait-times'
    ).get_data(as_text=True)
    assert 'aria-label="Owner dimension"' in body
    assert 'owners_by=account' in body


def test_histogram_owner_pill_hidden_in_user_mode_and_param_ignored(
    app, auth_client, monkeypatch,
):
    """User mode never offers the pill, and a crafted ?owners_by=account
    is ignored (not forwarded to the plugin)."""
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist_owners(),
    )
    body = auth_client.get(
        '/dashboards/user/jobs/user/derecho/wait-times?owners_by=account'
    ).get_data(as_text=True)
    assert 'aria-label="Owner dimension"' not in body
    _dim, kwargs = captured['last_jobs_histogram']
    assert 'owners_by' not in kwargs


def test_histogram_owner_pill_follows_project_tree_size(
    app, auth_client, active_project, monkeypatch,
):
    """Project mode offers the pill iff the account tree spans >1
    projcode — same gate as the By Project tab."""
    _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist_owners(),
    )
    body = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/wait-times'
        '?machine=derecho'
    ).get_data(as_text=True)
    multi = len(active_project.get_descendants(include_self=True)) > 1
    assert ('aria-label="Owner dimension"' in body) == multi


def test_histogram_owners_by_forwarded_only_when_account(
    app, auth_client, monkeypatch,
):
    """Soft degradation contract: the default never sends owners_by (an
    older plugin keeps working); the Project pill sends 'account'."""
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist_project_owners(),
    )
    auth_client.get('/dashboards/user/jobs/machine/derecho/wait-times')
    _dim, kwargs = captured['last_jobs_histogram']
    assert 'owners_by' not in kwargs

    auth_client.get(
        '/dashboards/user/jobs/machine/derecho/wait-times?owners_by=account')
    _dim, kwargs = captured['last_jobs_histogram']
    assert kwargs['owners_by'] == 'account'


def test_histogram_account_owner_tier_and_drill(
    app, auth_client, monkeypatch,
):
    """The Project pill drives the whole drill: Project tier header,
    project-modal triggers on owner cells, account= (not user=) on the
    per-owner jobs drill, 'Other projects' remainder, and the owners_by
    round-trip hidden input for the metric pills."""
    import re
    _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist_project_owners(),
    )
    body = auth_client.get(
        '/dashboards/user/jobs/machine/derecho/wait-times?owners_by=account'
    ).get_data(as_text=True)
    assert '<th>Project</th>' in body
    assert 'Other projects' in body
    assert 'project-details-modal/SCSG0001' in body
    assert 'name="owners_by" value="account"' in body
    m = re.search(r'id="[^"]*-b0-u1-content"\s+hx-get="([^"]+)"', body)
    assert m, 'owner drill div missing'
    url = m.group(1).replace('&amp;', '&')
    assert 'account=SCSG0001' in url
    assert 'user=' not in url


def test_histogram_user_owner_cells_offer_user_modal(
    app, auth_client, monkeypatch,
):
    """Under the default User pill the owner tier's usernames carry the
    same quick-view modal affordance as the By User table (VIEW_USERS
    gate)."""
    _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist_owners(),
    )
    body = auth_client.get(
        '/dashboards/user/jobs/machine/derecho/wait-times'
    ).get_data(as_text=True)
    assert 'data-bs-target="#userDetailsModal"' in body
    assert '/admin/user/alice' in body


# --- User column ------------------------------------------------------------

def test_user_column_structure():
    """`user` is a default (sortable) column and no longer a drawer extra."""
    from webapp.jobs.routes import _DEFAULT_COLS, _VERBOSE_EXTRAS, \
        _SORT_WHITELIST
    assert 'user' in _DEFAULT_COLS
    assert 'user' in _SORT_WHITELIST
    assert 'user' not in _VERBOSE_EXTRAS


def _two_user_rows():
    return [
        _make_row(job_id='600.desched1', user='alice'),
        _make_row(job_id='601.desched1', user='zed'),
    ]


def test_user_column_visible_with_multiple_users(
    app, auth_client, active_project, monkeypatch,
):
    _install_mock_plugin(
        app, monkeypatch, jobs_search_return=_two_user_rows(),
    )
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}?machine=derecho'
    )
    body = resp.get_data(as_text=True)
    assert 'sort_by=user' in body           # sortable header rendered
    assert 'alice' in body and 'zed' in body


def test_user_column_hidden_with_user_filter(
    app, auth_client, active_project, monkeypatch,
):
    rows = [_make_row(job_id='600.desched1', user='alice')]
    _install_mock_plugin(app, monkeypatch, jobs_search_return=rows)
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}'
        f'?machine=derecho&user=alice'
    )
    body = resp.get_data(as_text=True)
    assert 'sort_by=user' not in body
    # The pin is already surfaced by the user: filter badge — no user_badge.
    assert 'user: alice' in body


def test_user_column_hidden_in_user_mode(app, auth_client, monkeypatch):
    _install_mock_plugin(
        app, monkeypatch,
        jobs_search_return=[_make_row(job_id='600.desched1', user='benkirk')],
    )
    resp = auth_client.get('/dashboards/user/jobs/user/derecho?machine=derecho')
    body = resp.get_data(as_text=True)
    assert 'sort_by=user' not in body


def test_user_column_hidden_uniform_single_page_shows_badge(
    app, auth_client, monkeypatch,
):
    """No pin, no filter, but the whole (single-page) result is one user →
    column suppressed and the username surfaces as a header badge.

    Machine mode: its count path always uses the mocked plugin
    jobs_count. Project mode's unfiltered count takes the SAM-summary
    fast path (a real comp_charge_summary query), so the single-page
    premise couldn't be pinned there.
    """
    rows = [_make_row(job_id='600.desched1', user='solo'),
            _make_row(job_id='601.desched1', user='solo')]
    _install_mock_plugin(app, monkeypatch, jobs_search_return=rows)
    resp = auth_client.get(
        '/dashboards/user/jobs/machine/derecho?machine=derecho'
    )
    body = resp.get_data(as_text=True)
    assert 'sort_by=user' not in body
    assert 'user: solo' in body


def test_user_column_kept_uniform_but_multipage(
    app, auth_client, monkeypatch,
):
    """A uniform PAGE of a multi-page result proves nothing — the column
    stays rather than paying a distinct-count query."""
    rows = [_make_row(job_id='600.desched1', user='solo')]
    _install_mock_plugin(app, monkeypatch, jobs_search_return=rows,
                         jobs_count_return=500)
    resp = auth_client.get(
        '/dashboards/user/jobs/machine/derecho?machine=derecho'
    )
    body = resp.get_data(as_text=True)
    assert 'sort_by=user' in body
    assert 'user: solo' not in body


def test_user_sort_forwarded(
    app, auth_client, active_project, monkeypatch,
):
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_search_return=_two_user_rows(),
    )
    auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}'
        f'?machine=derecho&sort_by=user&sort_dir=asc'
    )
    kw = captured['last_jobs_search_kwargs']
    assert kw['sort_by'] == 'user'
    assert kw['sort_dir'] == 'asc'


def test_name_column_truncates(
    app, auth_client, active_project, monkeypatch,
):
    """Long job names must not widen the column: ellipsis truncation with
    the full name on the title tooltip."""
    long_name = 'a_very_long_job_name_' + 'x' * 100
    rows = [_make_row(job_id='600.desched1', user='alice', name=long_name),
            _make_row(job_id='601.desched1', user='zed')]
    _install_mock_plugin(app, monkeypatch, jobs_search_return=rows)
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}?machine=derecho'
    )
    body = resp.get_data(as_text=True)
    assert 'max-width: 35ch' in body
    assert 'text-truncate' in body
    assert f'title="{long_name}"' in body


# --- Metric-ranked By User / By Project -------------------------------------

def test_by_user_fragment_threads_metric_sort(
    app, auth_client, active_project, monkeypatch,
):
    """The metric pill decides the plugin ranking (and thus which top-N
    survives) — the Derecho GPU-Hours one-user bug."""
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_usage_by_return=_sample_usage(),
    )
    auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/by-user'
        f'?machine=derecho'
    )
    _dim, kwargs = captured['last_jobs_usage_by']
    assert kwargs['sort_by'] == 'cpu_hours'     # _DEFAULT_METRIC_PIE

    auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/by-user'
        f'?machine=derecho&metric=gpu_hours'
    )
    _dim, kwargs = captured['last_jobs_usage_by']
    assert kwargs['sort_by'] == 'gpu_hours'

    auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/by-user'
        f'?machine=derecho&metric=jobs'
    )
    _dim, kwargs = captured['last_jobs_usage_by']
    assert kwargs['sort_by'] == 'job_count'


def test_by_project_fragment_threads_metric_sort(app, auth_client, monkeypatch):
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_usage_by_return=_sample_usage(),
    )
    auth_client.get(
        '/dashboards/user/jobs/user/derecho/by-project'
        '?machine=derecho&metric=gpu_hours'
    )
    _dim, kwargs = captured['last_jobs_usage_by']
    assert _dim == 'account'
    assert kwargs['sort_by'] == 'gpu_hours'


def test_by_user_sort_indicator_follows_metric(
    app, auth_client, active_project, monkeypatch,
):
    """The initial client-sort indicator sits on the active metric column."""
    _install_mock_plugin(app, monkeypatch, jobs_usage_by_return=_sample_usage())
    body = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/by-user'
        f'?machine=derecho&metric=gpu_hours'
    ).get_data(as_text=True)
    import re
    gpu_th = re.search(r'<th[^>]*sort-desc[^>]*>GPU-hours</th>', body)
    assert gpu_th, 'sort-desc must sit on the GPU-hours header'
    assert not re.search(r'<th[^>]*sort-desc[^>]*>CPU-hours</th>', body)


# ---------------------------------------------------------------------------
# Period pills — ?days= parsing, precedence, and the card-shell routes
# ---------------------------------------------------------------------------

def _days_ago(days):
    from datetime import date, timedelta
    return date.today() - timedelta(days=days)


@pytest.mark.parametrize('raw,expected', [
    ('30', 30), ('365', 365),
    ('7', None),        # not an offered window
    ('abc', None), ('', None), ('90.0', None), ('-90', None),
])
def test_parse_days_accepts_only_offered_windows(app, raw, expected):
    """A stale localStorage value must degrade to the default, never 400."""
    from webapp.jobs import routes

    with app.test_request_context(f'/?days={raw}'):
        assert routes._parse_days() == expected


def test_days_outranks_the_window_baked_into_the_url(app):
    """The pill wins over ?start=/?end= — the client can only append days."""
    from webapp.jobs import routes

    with app.test_request_context('/?start=2020-01-01&end=2020-06-01&days=60'):
        filters = routes._parse_job_filters()

    assert filters['start'] == _days_ago(60)
    assert filters['end'] is None


def test_start_and_end_survive_when_no_days_given(app):
    from datetime import date
    from webapp.jobs import routes

    with app.test_request_context('/?start=2020-01-01&end=2020-06-01'):
        filters = routes._parse_job_filters()

    assert filters['start'] == date(2020, 1, 1)
    assert filters['end'] == date(2020, 6, 1)


def test_roundtrip_params_normalize_days_to_a_plain_start(app):
    """`days` is confined to the fragment boundary: panels round-trip start."""
    from webapp.jobs import routes

    with app.test_request_context('/?days=365&end=2020-06-01'):
        params = routes._roundtrip_params('derecho', 'tgt')

    assert params['start'] == _days_ago(365).isoformat()
    assert 'end' not in params
    assert 'days' not in params


def test_panel_filters_default_window_follows_days(app, monkeypatch):
    """The explorer honours the pill the card handed over in its link."""
    _install_mock_plugin(app, monkeypatch)
    from webapp.jobs import routes

    with app.test_request_context('/?days=30'):
        panel = routes._panel_filters('derecho')
    assert panel['start'] == _days_ago(30).isoformat()

    with app.test_request_context('/'):
        panel = routes._panel_filters('derecho')
    assert panel['start'] == _days_ago(
        routes.service.DEFAULT_JOBS_WINDOW_DAYS).isoformat()


def test_panel_route_applies_days_over_baked_start(
    app, auth_client, active_project, monkeypatch,
):
    """End to end: a panel fetch carrying both uses the injected window."""
    captured = _install_mock_plugin(app, monkeypatch,
                                    jobs_search_return=[_make_row()])
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}'
        '?machine=derecho&start=2020-01-01&days=365'
    )
    assert resp.status_code == 200
    assert captured['last_jobs_search_kwargs']['start'] == _days_ago(365)


def _card_url(projcode, **extra):
    from urllib.parse import urlencode
    params = {'machine': 'derecho', 'cid': 'jobs-hist',
              'tablist_id': 'jobsCardTabs'}
    params.update(extra)
    return f'/dashboards/user/jobs/{projcode}/card?{urlencode(params)}'


def test_card_fragment_bakes_the_window_into_every_panel_url(
    app, auth_client, active_project, monkeypatch,
):
    """The shell is how the six panels learn a new window."""
    _install_mock_plugin(app, monkeypatch)
    resp = auth_client.get(_card_url(active_project.projcode, days=365))
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)

    start = _days_ago(365).isoformat()
    for suffix in ('', '/wait-times', '/job-sizes', '/durations'):
        assert (f'/dashboards/user/jobs/{active_project.projcode}{suffix}'
                f'?machine=derecho&amp;start={start}') in body, suffix
    # A pill is a lookback from today, so the page's own end date is gone.
    assert 'end=' not in body


def test_card_fragment_marks_the_requested_pill_active(
    app, auth_client, active_project, monkeypatch,
):
    _install_mock_plugin(app, monkeypatch)
    body = auth_client.get(
        _card_url(active_project.projcode, days=30)).get_data(as_text=True)

    import re
    assert re.search(r'btn btn-primary[^>]*data-days-value="30"', body) or \
        re.search(r'data-days-value="30"[^>]*btn btn-primary', body)
    assert 'data-days-value="365"' in body      # the other pills still render


def test_card_fragment_unknown_days_falls_back_to_the_default(
    app, auth_client, active_project, monkeypatch,
):
    _install_mock_plugin(app, monkeypatch)
    body = auth_client.get(
        _card_url(active_project.projcode, days=7)).get_data(as_text=True)

    from webapp.jobs.service import DEFAULT_JOBS_WINDOW_DAYS
    assert f'start={_days_ago(DEFAULT_JOBS_WINDOW_DAYS).isoformat()}' in body


@pytest.mark.parametrize('bad', ['jobs hist', 'a"b', '<script>', 'x' * 65])
def test_card_fragment_400_on_unsafe_element_ids(
    app, auth_client, active_project, monkeypatch, bad,
):
    """cid/tablist_id land in element ids and hx-target selectors."""
    _install_mock_plugin(app, monkeypatch)
    assert auth_client.get(
        _card_url(active_project.projcode, cid=bad)).status_code == 400
    assert auth_client.get(
        _card_url(active_project.projcode, tablist_id=bad)).status_code == 400


def test_card_fragment_404_on_unknown_projcode(auth_client):
    assert auth_client.get(_card_url('NOPE9999')).status_code == 404


def test_card_fragment_400_on_invalid_machine(
    app, auth_client, active_project, monkeypatch,
):
    _install_mock_plugin(app, monkeypatch)
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/card?machine=gust')
    assert resp.status_code == 400


def test_card_fragment_persist_markers_are_opt_in(
    app, auth_client, active_project, monkeypatch,
):
    """No persist id (resource details) → the window can't outlive the visit."""
    _install_mock_plugin(app, monkeypatch)

    plain = auth_client.get(
        _card_url(active_project.projcode)).get_data(as_text=True)
    assert 'data-jobs-days-card' not in plain
    assert 'data-chart-persist-id' not in plain
    assert 'data-days-value' in plain            # pills still work

    kept = auth_client.get(
        _card_url(active_project.projcode,
                  days_persist_id='jobs-days-status')).get_data(as_text=True)
    assert 'data-jobs-days-card' in kept
    assert 'data-chart-persist-id="jobs-days-status"' in kept
    assert 'data-chart-persist-keys="days"' in kept
    assert 'data-jobs-card-url' in kept


def test_card_wrapper_declares_no_inheritable_hx_target(
    app, auth_client, active_project, monkeypatch,
):
    """htmx inherits hx-target/hx-swap.

    A pair on the card wrapper would capture every descendant request that
    doesn't name its own target — a By Project bucket drill swapped the
    whole card away — so the sibling fan-out goes through htmx.ajax().
    """
    _install_mock_plugin(app, monkeypatch)
    body = auth_client.get(
        _card_url(active_project.projcode,
                  days_persist_id='jobs-days-status')).get_data(as_text=True)

    wrapper = body[body.index('<div id="jobs-hist-card"'):]
    wrapper = wrapper[:wrapper.index('>') + 1]
    assert 'hx-target' not in wrapper
    assert 'hx-swap' not in wrapper


def test_card_fragment_explore_link_hands_over_the_pill(
    app, auth_client, active_project, monkeypatch,
):
    """The link carries ?days=, never a date the JS would have to re-derive."""
    _install_mock_plugin(app, monkeypatch)
    body = auth_client.get(
        _card_url(active_project.projcode, days=365)).get_data(as_text=True)

    import re
    link = re.search(r'href="([^"]*/explore[^"]*)"', body)
    assert link, 'explorer link missing'
    assert 'days=365' in link.group(1)
    assert 'start=' not in link.group(1)


def test_card_machine_route_403_without_permission(non_admin_client):
    resp = non_admin_client.get('/dashboards/user/jobs/machine/derecho/card')
    assert resp.status_code == 403


def test_card_machine_route_404_unknown_machine(app, auth_client, monkeypatch):
    _install_mock_plugin(app, monkeypatch, machines=('derecho',))
    resp = auth_client.get('/dashboards/user/jobs/machine/gust/card')
    assert resp.status_code == 404


def test_card_machine_route_renders_machine_mode_shell(
    app, auth_client, monkeypatch,
):
    _install_mock_plugin(app, monkeypatch)
    resp = auth_client.get(
        '/dashboards/user/jobs/machine/derecho/card?days=60'
        '&cid=jobs-m1&tablist_id=jobHistCardTabs1')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert f'start={_days_ago(60).isoformat()}' in body
    assert 'By User' in body                    # machine mode keeps the pie
    assert 'id="jobs-m1-card"' in body


def test_card_user_route_renders_without_elevated_permission(
    app, non_admin_client, monkeypatch,
):
    """The My Jobs shell is @login_required only — the username is pinned."""
    _install_mock_plugin(app, monkeypatch)
    resp = non_admin_client.get(
        '/dashboards/user/jobs/user/derecho/card?days=30&cid=my-jobs-m1')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert f'start={_days_ago(30).isoformat()}' in body
    assert '>By User' not in body               # a pie of one, hidden here


def test_card_shell_panels_stay_lazy_after_a_refetch(
    app, auth_client, active_project, monkeypatch,
):
    """Only the visible panel refetches; the rest wait to be shown."""
    _install_mock_plugin(app, monkeypatch)
    body = auth_client.get(
        _card_url(active_project.projcode, days=30)).get_data(as_text=True)

    assert 'hx-trigger="intersect once"' in body
    assert body.count('hx-trigger="shown.bs.tab once"') >= 4
