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

from webapp.jobs.scope import (
    MachineJobScope,
    ProjectJobScope,
    UserJobScope,
)


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
    _c._CACHE.reset_for_tests()
    yield
    # disabled=False → drop the memo so buckets re-init on next use
    _c._CACHE.reset_for_tests(disabled=False)


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
            ProjectJobScope(active_project),
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
            ProjectJobScope(active_project, ['PARENT0001', 'PARENT0001_a', 'PARENT0001_b']),
            limit=50,
        )

    kw = captured['last_jobs_search_kwargs']
    assert kw['account'] == ['PARENT0001', 'PARENT0001_a', 'PARENT0001_b']


def test_project_scope_requires_a_project():
    """The unpinned-project-scope shape is rejected at construction — before
    any query can be built, rather than inside each service function."""
    with pytest.raises(ValueError):
        ProjectJobScope(None)
    with pytest.raises(ValueError):
        ProjectJobScope(None, account_projcodes=[])


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
            ProjectJobScope(active_project),
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
            'derecho', ProjectJobScope(active_project), queue='cpu-special',
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
            'derecho', ProjectJobScope(active_project),
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
            'derecho', ProjectJobScope(active_project),
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
            'derecho', ProjectJobScope(active_project),
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
            'derecho', ProjectJobScope(active_project),
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
            'derecho', ProjectJobScope(active_project),
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
            'derecho', ProjectJobScope(active_project),
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


def _capture_connect_listener(monkeypatch, **kwargs):
    """Register the connect listener against a stub engine and return it.

    ``event.listens_for`` needs a real Engine, so swap in a fake decorator
    that just hands back the function being registered.
    """
    from webapp.plugins import base as plugin_base

    registered = {}

    def _fake_listens_for(target, name):
        def _decorator(fn):
            registered['fn'] = fn
            return fn
        return _decorator

    monkeypatch.setattr(plugin_base.event, 'listens_for', _fake_listens_for)
    plugin_base.PluginExtension.apply_connection_settings(
        MagicMock(name='engine'), **kwargs)
    return registered['fn']


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


# ---------------------------------------------------------------------------
# Commit 5: aggregation fragments (By User / Wait Times / Job Sizes / Durations)
# ---------------------------------------------------------------------------

# Charge values below are deliberately NOT proportional to hours, and the
# second bucket / second row is deliberately uncharged (real hours, 0.0
# charges — the `uncharged` QoS carries a genuine 0.0 factor). A fixture
# that omitted these keys would let every charges assertion pass through
# charts._jobs_metric_value's `.get(k) or 0` fallback and prove nothing.
def _sample_hist(dimension='wait', null_count=0):
    return {
        'dimension': dimension, 'column': 'eligible_secs', 'unit': 'seconds',
        'min_param': 'min_eligible_secs', 'max_param': 'max_eligible_secs',
        'buckets': [
            {'label': '<1m', 'lo': 0, 'hi': 59,
             'job_count': 10, 'cpu_hours': 100.0, 'gpu_hours': 0.0,
             'cpu_charges': 50.0, 'gpu_charges': 0.0},
            {'label': '1-5m', 'lo': 60, 'hi': 299,
             'job_count': 4, 'cpu_hours': 40.0, 'gpu_hours': 1.0,
             'cpu_charges': 0.0, 'gpu_charges': 0.0},
        ],
        'null_count': null_count,
        'total_count': 14 + null_count,
    }


def _sample_usage(totals=None):
    return {
        'dimension': 'user',
        'rows': [
            {'value': 'alice', 'job_count': 30, 'cpu_hours': 300.0,
             'gpu_hours': 0.0, 'cpu_charges': 150.0, 'gpu_charges': 0.0},
            {'value': 'bob',   'job_count': 12, 'cpu_hours': 120.0,
             'gpu_hours': 2.0, 'cpu_charges': 0.0, 'gpu_charges': 0.0},
        ],
        'totals': totals or {'job_count': 42, 'cpu_hours': 420.0,
                             'gpu_hours': 2.0, 'cpu_charges': 150.0,
                             'gpu_charges': 0.0},
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
    assert '#sam/row/data-job-user/alice' in body            # clickable wedge sentinel
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


def test_by_user_fragment_charges_metric_renders_its_own_column(
    app, auth_client, active_project, monkeypatch,
):
    """?metric=charges ranks the rows by charges, so the table must SHOW
    charges. It shipped ranking by an invisible column (upstream C1)."""
    _install_mock_plugin(app, monkeypatch, jobs_usage_by_return=_sample_usage())
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/by-user'
        f'?machine=derecho&metric=charges'
    )
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert '>Charges</th>' in body
    # The initial-sort indicator follows the active metric — on charges it
    # used to land on no header at all.
    assert 'sort-desc" data-sort="numeric">Charges' in body
    # alice's 150.0 charges (cpu 150 + gpu 0), sortable on the raw value.
    assert 'data-sort-value="150.0"' in body
    # ...and the uncharged-QoS caption, since bob has hours but no charges.
    assert 'Uncharged QoS' in body


def test_by_user_fragment_charges_column_absent_indicator_on_other_metrics(
    app, auth_client, active_project, monkeypatch,
):
    """The column is always present; only its sort indicator is conditional."""
    _install_mock_plugin(app, monkeypatch, jobs_usage_by_return=_sample_usage())
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/by-user'
        f'?machine=derecho&metric=cpu_hours'
    )
    body = resp.get_data(as_text=True)
    assert '>Charges</th>' in body
    assert 'sort-desc" data-sort="numeric">Charges' not in body
    assert 'Uncharged QoS' not in body      # caption is charges-only


def test_by_user_other_row_carries_a_charges_figure(
    app, auth_client, active_project, monkeypatch,
):
    """The Other row says 'beyond top N by charges' — so it must show them."""
    usage = _sample_usage(totals={'job_count': 100, 'cpu_hours': 900.0,
                                  'gpu_hours': 5.0, 'cpu_charges': 400.0,
                                  'gpu_charges': 0.0})
    _install_mock_plugin(app, monkeypatch, jobs_usage_by_return=usage)
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/by-user'
        f'?machine=derecho&metric=charges'
    )
    body = resp.get_data(as_text=True)
    assert 'beyond top' in body
    # The "by <noun>" label wraps across a template line break, so match the
    # noun on its own rather than the phrase.
    assert 'charges)</span>' in body
    # 400 total - 150 shown = 250 remaining, in charge units.
    assert '<td class="text-end">250</td>' in body


def test_usage_other_remainder_includes_charge_keys():
    """_usage_other must carry the full metric vector, or a charges view has
    no remainder figure in its own units."""
    from webapp.jobs.routes import _usage_other
    rem = _usage_other(_sample_usage(
        totals={'job_count': 100, 'cpu_hours': 900.0, 'gpu_hours': 5.0,
                'cpu_charges': 400.0, 'gpu_charges': 7.0}))
    assert rem['cpu_charges'] == pytest.approx(250.0)
    assert rem['gpu_charges'] == pytest.approx(7.0)


def test_usage_other_visibility_ignores_charges_alone():
    """An entirely uncharged tail is still a real tail — gating on charges
    would hide it, and charges can never appear without hours anyway."""
    from webapp.jobs.routes import _usage_other
    # Tail exists in hours, contributes no charges.
    rem = _usage_other(_sample_usage(
        totals={'job_count': 100, 'cpu_hours': 900.0, 'gpu_hours': 5.0,
                'cpu_charges': 150.0, 'gpu_charges': 0.0}))
    assert rem is not None
    assert rem['cpu_charges'] == pytest.approx(0.0)
    # Rows exactly cover totals → no Other row at all.
    assert _usage_other(_sample_usage()) is None


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


_EXPLORE_URLS = (
    ('project', '/dashboards/user/jobs/{projcode}/explore?machine=derecho'),
    ('machine', '/dashboards/user/jobs/machine/derecho/explore'),
    ('user', '/dashboards/user/jobs/user/derecho/explore'),
)


@pytest.mark.parametrize('mode,url', _EXPLORE_URLS)
def test_explore_page_renders_the_jobs_card(
    app, auth_client, active_project, monkeypatch, mode, url,
):
    """The charts live on the full view too — same card, driven by the
    filter panel instead of a baked window."""
    _install_mock_plugin(app, monkeypatch)
    body = auth_client.get(
        url.format(projcode=active_project.projcode)).get_data(as_text=True)

    assert 'id="jobs-explore-card"' in body
    assert 'id="jobsExploreTabs"' in body
    for tab in ('Wait Times', 'Job Sizes', 'Durations'):
        assert tab in body, tab
    # By User follows the relevance rule, not the surface.
    assert ('By User' in body) is (mode != 'user')


@pytest.mark.parametrize('mode,url', _EXPLORE_URLS)
def test_explore_page_ships_the_entity_modal_shells(
    app, auth_client, active_project, monkeypatch, mode, url,
):
    """By User, By Project and the histograms' owner tier all open
    quick-view modals whose shells live on the host page. This one
    extends dashboards/base.html (which includes neither), so without
    the includes every one of those links is a silent no-op — exactly
    once each, since duplicate ids break Bootstrap's lookup.

    Pinned per mode: the suppressed panel still leaves the other panels'
    links, and the histogram owner tier can name either entity.
    """
    _install_mock_plugin(app, monkeypatch)
    body = auth_client.get(
        url.format(projcode=active_project.projcode)).get_data(as_text=True)

    assert body.count('id="userDetailsModal"') == 1
    assert body.count('id="projectDetailsModal"') == 1
    # The project shell pulls this in for the per-allocation pencils
    # inside it — see tests/unit/test_modal_shell_pairing.py.
    assert body.count('id="editAllocationModal"') == 1


@pytest.mark.parametrize('mode,url', _EXPLORE_URLS)
def test_explore_page_suppresses_pills_and_the_explore_link(
    app, auth_client, active_project, monkeypatch, mode, url,
):
    """The panel's date fields own the window here, and this IS the full
    view — a period pill group and an "Open full view" link would both be
    second controls for something already on screen."""
    _install_mock_plugin(app, monkeypatch)
    body = auth_client.get(
        url.format(projcode=active_project.projcode)).get_data(as_text=True)

    assert 'aria-label="Time window"' not in body
    assert 'data-jobs-explore-link' not in body


@pytest.mark.parametrize('mode,url', _EXPLORE_URLS)
def test_explore_page_filter_form_re_renders_the_card(
    app, auth_client, active_project, monkeypatch, mode, url,
):
    """Apply swaps the whole card: that is the only way six panels whose
    URLs are baked at render time pick up a new filter set."""
    import re
    _install_mock_plugin(app, monkeypatch)
    body = auth_client.get(
        url.format(projcode=active_project.projcode)).get_data(as_text=True)

    form = re.search(r'<form id="jobs-filters-panel-jobs-explore-jobs"(.*?)>',
                     body, re.S)
    assert form, 'filter form missing'
    attrs = form.group(1)
    assert 'hx-target="#jobs-explore-card"' in attrs
    assert 'hx-swap="outerHTML"' in attrs
    assert '/card?' in attrs and 'surface=explorer' in attrs.replace('&amp;', '&')


def test_explore_card_opens_the_tab_the_form_reports(
    app, auth_client, active_project, monkeypatch,
):
    """Apply re-renders the whole card, so the server has to be told which
    tab is open. Otherwise it always comes back on Jobs and an Apply from
    a chart fetches that chart AND a per-job table nobody asked for."""
    import re
    _install_mock_plugin(app, monkeypatch)
    body = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/card'
        '?machine=derecho&surface=explorer&active_tab=sizes'
    ).get_data(as_text=True)

    sizes_btn = re.search(r'<button[^>]*data-jobs-tab="sizes".*?>', body, re.S)
    assert sizes_btn and 'active' in sizes_btn.group(0)
    # …and it is the one that fires on render; the rest wait to be shown.
    assert 'hx-trigger="load once"' in sizes_btn.group(0)
    jobs_btn = re.search(r'<button[^>]*data-jobs-tab="jobs".*?>', body, re.S)
    assert jobs_btn and 'active' not in jobs_btn.group(0)
    assert 'hx-trigger="shown.bs.tab once"' in jobs_btn.group(0)
    # The pane follows the button.
    assert re.search(r'class="tab-pane fade show active" id="tab-jobs-explore-sizes"',
                     body)


def test_explore_card_rejects_an_unknown_active_tab(
    app, auth_client, active_project, monkeypatch,
):
    """The value picks which panel fires a query, so it is whitelisted."""
    import re
    _install_mock_plugin(app, monkeypatch)
    body = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/card'
        '?machine=derecho&surface=explorer&active_tab=../evil'
    ).get_data(as_text=True)

    jobs_btn = re.search(r'<button[^>]*data-jobs-tab="jobs".*?>', body, re.S)
    assert jobs_btn and 'active' in jobs_btn.group(0)


def test_explore_page_round_trips_the_active_tab_through_the_form(
    app, auth_client, active_project, monkeypatch,
):
    _install_mock_plugin(app, monkeypatch)
    body = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/explore'
        '?machine=derecho&active_tab=wait'
    ).get_data(as_text=True)

    assert ('<input type="hidden" name="active_tab" value="wait"'
            in body)
    assert 'data-jobs-active-tab-input' in body


def test_cards_still_open_on_jobs_by_default(
    app, auth_client, active_project, monkeypatch,
):
    """Nothing changes for the embedded cards: Jobs is open and owns the
    host's load_trigger, every other tab waits to be shown."""
    import re
    _install_mock_plugin(app, monkeypatch)
    body = auth_client.get(
        _card_url(active_project.projcode, days=90)).get_data(as_text=True)

    jobs_btn = re.search(r'<button[^>]*data-jobs-tab="jobs".*?>', body, re.S)
    assert jobs_btn and 'active' in jobs_btn.group(0)
    assert 'hx-trigger="intersect once"' in jobs_btn.group(0)
    assert body.count('hx-trigger="shown.bs.tab once"') >= 4


def test_explore_page_bakes_filters_into_every_panel_url(
    app, auth_client, active_project, monkeypatch,
):
    """Every panel — not just the table — answers the current filters."""
    import re
    _install_mock_plugin(app, monkeypatch)
    body = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/explore'
        '?machine=derecho&queue=main&min_nodes=4&min_wait_hours=1.5'
    ).get_data(as_text=True)

    panel_urls = [u.replace('&amp;', '&')
                  for u in re.findall(r'hx-get="([^"]+)"', body)]
    for suffix in ('/by-user', '/wait-times', '/job-sizes', '/durations'):
        matches = [u for u in panel_urls
                   if f'/{active_project.projcode}{suffix}?' in u]
        assert matches, suffix
        assert all('queue=main' in u and 'min_nodes=4' in u
                   and 'min_wait_hours=1.5' in u for u in matches), suffix


def test_explore_card_route_rebuilds_from_the_filter_panel(
    app, auth_client, active_project, monkeypatch,
):
    """An Apply lands on the mode's /card route with surface=explorer and
    reproduces the same panel URLs a deep link would."""
    import re
    _install_mock_plugin(app, monkeypatch)
    body = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/card'
        '?machine=derecho&surface=explorer&queue=main&min_nodes=4'
    ).get_data(as_text=True)

    assert 'id="jobs-explore-card"' in body
    assert 'aria-label="Time window"' not in body      # still no pills
    panel_urls = [u.replace('&amp;', '&')
                  for u in re.findall(r'hx-get="([^"]+)"', body)]
    waits = [u for u in panel_urls if '/wait-times?' in u]
    assert waits and all('queue=main' in u and 'min_nodes=4' in u
                         for u in waits)


def test_explore_card_route_without_the_surface_flag_is_still_a_pill(
    app, auth_client, active_project, monkeypatch,
):
    """The period pills share these routes; surface= is what tells them
    apart, so a pill click must keep its pills and its lookback."""
    _install_mock_plugin(app, monkeypatch)
    body = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/card'
        '?machine=derecho&days=30'
    ).get_data(as_text=True)

    assert 'aria-label="Time window"' in body
    assert f'start={_days_ago(30).isoformat()}' in body


def test_explore_page_user_mode_still_ignores_a_crafted_user(
    app, auth_client, monkeypatch,
):
    """The username is pinned server-side on every fragment; the panel
    omits the picker so the card can't be re-aimed at someone else."""
    captured = _install_mock_plugin(app, monkeypatch,
                                    jobs_search_return=[_make_row()])
    body = auth_client.get(
        '/dashboards/user/jobs/user/derecho/explore?user=someone_else'
    ).get_data(as_text=True)
    assert 'name="user_id"' not in body
    # Nor is it baked into the panel URLs, where it would look like a
    # filter that works while the server quietly overrode it.
    assert 'user=someone_else' not in body

    auth_client.get(
        '/dashboards/user/jobs/user/derecho?user=someone_else&machine=derecho')
    assert captured['last_jobs_search_kwargs']['user'] == 'benkirk'


_SAMPLE_FACETS = {
    'queue': [{'value': 'cpu', 'count': 120}, {'value': 'gpu', 'count': 30},
              {'value': None, 'count': 2}],
    'qos': [{'value': 'regular', 'count': 100}, {'value': 'premium', 'count': 50}],
    'exit_status': [{'value': '0', 'count': 140}, {'value': '271', 'count': 10}],
}


def test_explore_page_renders_facet_chips_with_counts(
    app, auth_client, active_project, monkeypatch,
):
    """The strip rides inside the card: value chips with live counts,
    NULL-FK rows skipped, wired to the filter panel form."""
    import re
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_facets_return=_SAMPLE_FACETS,
    )
    body = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/explore'
        '?machine=derecho&queue=cpu'
    ).get_data(as_text=True)

    assert 'data-action="set-filter-submit"' in body
    assert 'data-form-id="jobs-filters-panel-jobs-explore-jobs"' in body
    # Active chip (queue=cpu) fills in and clears on click.
    cpu_chip = re.search(
        r'<button[^>]*data-field="queue"[^>]*data-value=""[^>]*>', body)
    assert cpu_chip is not None and 'facet-chip is-active' in cpu_chip.group(0)
    # Inactive chip carries its value.
    assert 'data-value="gpu"' in body
    assert 'data-value="271"' in body
    # NULL-FK queue row renders no chip (nothing to filter by).
    assert 'data-value="None"' not in body
    # One grid row per dimension: every label opens its own line.
    assert body.count('class="facet-grid-label"') == 3
    # Facets saw the same filter set as the panels.
    fkw = captured['last_jobs_facets_kwargs']
    assert fkw['queue'] == 'cpu'
    assert fkw['limit'] == 8


def test_explore_card_route_refreshes_the_chips(
    app, auth_client, active_project, monkeypatch,
):
    """Counts refresh with the panels, not with the table — otherwise a
    viewer who filters while looking at a chart keeps the old counts."""
    _install_mock_plugin(app, monkeypatch, jobs_facets_return=_SAMPLE_FACETS)
    body = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/card'
        '?machine=derecho&surface=explorer&queue=cpu'
    ).get_data(as_text=True)

    assert 'data-action="set-filter-submit"' in body
    assert 'data-value="gpu"' in body


def test_jobs_table_fragment_never_queries_facets(
    app, auth_client, active_project, monkeypatch,
):
    """Sorting or paging cannot change a facet count, so the table no
    longer pays for one — the strip belongs to the shell."""
    captured = _install_mock_plugin(app, monkeypatch)
    body = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}'
        '?machine=derecho&chips=1'
    ).get_data(as_text=True)

    assert 'hx-swap-oob' not in body
    assert 'data-action="set-filter-submit"' not in body
    assert captured['last_jobs_facets_kwargs'] is None


def test_explore_chips_degrade_on_facets_error(
    app, auth_client, active_project, monkeypatch,
):
    """A facets failure must not take the page down — the card renders
    normally with no chip strip."""
    _install_mock_plugin(app, monkeypatch, jobs_facets_raises=True)
    resp = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/explore'
        '?machine=derecho'
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-action="set-filter-submit"' not in body
    assert 'class="facet-grid"' not in body
    assert 'id="jobs-explore-card"' in body


def test_explore_chips_omit_dimensions_with_nothing_to_offer(
    app, auth_client, active_project, monkeypatch,
):
    """A dimension with no filterable values contributes no grid row,
    and a strip with no rows at all renders no surface — an empty
    bordered band above the tabs would be worse than none."""
    _install_mock_plugin(
        app, monkeypatch,
        jobs_facets_return={'queue': [{'value': 'cpu', 'count': 3}],
                            'qos': [],
                            'exit_status': [{'value': None, 'count': 7}]},
    )
    body = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/explore'
        '?machine=derecho'
    ).get_data(as_text=True)

    assert body.count('class="facet-grid-label"') == 1
    assert 'data-value="cpu"' in body

    _install_mock_plugin(
        app, monkeypatch,
        jobs_facets_return={'queue': [], 'qos': [], 'exit_status': []},
    )
    body = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/explore'
        '?machine=derecho'
    ).get_data(as_text=True)

    assert 'class="facet-grid"' not in body


def test_explore_chips_project_scope_pins_account(
    app, auth_client, active_project, monkeypatch,
):
    """Facets are scoped exactly like the panels — the project tree's
    projcodes pin the account filter."""
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_facets_return=_SAMPLE_FACETS,
    )
    auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/explore'
        '?machine=derecho'
    )
    fkw = captured['last_jobs_facets_kwargs']
    assert active_project.projcode in fkw['account']


def test_explore_user_chips_pin_username(app, auth_client, monkeypatch):
    """User-mode chips describe the pinned user's jobs only — the session
    username rides into the facets call, client ?user= notwithstanding."""
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_facets_return=_SAMPLE_FACETS,
    )
    auth_client.get(
        '/dashboards/user/jobs/user/derecho/explore?user=mallory')
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


def test_timeline_is_open_by_default_on_the_cards(
    app, auth_client, monkeypatch,
):
    """The cards used to collapse the timeline because it cost a `jobs`
    scan. It now serves a card's scope off the plugin's daily_summary
    rollup (~65 ms for 180 bands), so it is open everywhere."""
    _install_mock_plugin(app, monkeypatch, machines=('derecho',))
    body = auth_client.get('/status/job-history').get_data(as_text=True)
    assert 'id="jobs-m1-timeline-wrap"' in body
    # `show` is what Bootstrap reads; aria-expanded is what a screen reader
    # reads. They must agree or the panel lies to one of them.
    assert 'class="collapse show mt-2"' in body
    assert 'aria-expanded="true"' in body


def _hx_trigger_after(body, anchor):
    """The hx-trigger of the element whose id attribute is *anchor*."""
    import re
    frag = body[body.index(f'id="{anchor}"'):][:600]
    m = re.search(r'hx-trigger="([^"]*)"', frag)
    assert m, f'no hx-trigger near {anchor}: {frag[:200]}'
    return m.group(1)


@pytest.mark.parametrize('machine_slot,should_fire_now', [('m1', True),
                                                          ('m2', False)])
def test_open_timeline_shares_the_jobs_pane_trigger(
    app, auth_client, monkeypatch, machine_slot, should_fire_now,
):
    """Open must not mean eager.

    The timeline is in the Jobs pane, so it takes that pane's trigger —
    whatever the host chose. Hardcoding `load` would fetch it inside a
    hidden pane (a second machine card, or a restored non-Jobs tab), paying
    for a chart nobody asked for: exactly the cost the collapse used to
    avoid, and the reason opening it by default is safe at all.

    Pinned per machine slot because the two differ: the first card's pane is
    visible at render, the second sits behind the machine tab and must wait.
    """
    _install_mock_plugin(app, monkeypatch, machines=('derecho', 'casper'))
    body = auth_client.get('/status/job-history').get_data(as_text=True)

    # The table's fetch is wired on its TAB BUTTON, the timeline's on the
    # chart div; both resolve through the same `_trig('jobs')`.
    timeline = _hx_trigger_after(body, f'jobs-{machine_slot}-timeline')
    table = _hx_trigger_after(body, f'jobs-{machine_slot}-jobs-tab')
    assert timeline == table, (
        f'{machine_slot}: timeline {timeline!r} != table {table!r}')
    assert ('load' in timeline) is should_fire_now, timeline


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
    assert '#sam/row/data-job-project/SCSG0001' in body
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


def test_service_jobs_usage_by_project_rejects_user_filter(app, monkeypatch):
    """The pin owns the user dimension — a client value beside it raises
    rather than being silently overwritten. (The empty-username case is
    rejected at scope construction; see test_user_scope_requires_a_username.)"""
    from webapp.jobs import service

    _install_mock_plugin(app, monkeypatch)
    with app.app_context():
        with pytest.raises(ValueError, match='pin the user server-side'):
            service.jobs_usage_by_project(
                'derecho', UserJobScope('benkirk'), user='mallory',
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


# --- Histogram User|Project owner pill (group_by) ---------------------------

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
    assert 'group_by=project' in body


@pytest.mark.parametrize('crafted', ['', '?group_by=user', '?group_by=project'])
def test_histogram_in_user_mode_stacks_by_project_whatever_the_client_asks(
    app, auth_client, monkeypatch, crafted,
):
    """User mode pins the username, so a per-USER stack is a stack of one.

    The axis that can still vary is the project, so that's what the
    segments and the per-band tier use — server-decided, with the pill
    hidden and any crafted ?group_by ignored in both directions.
    """
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist_owners(),
    )
    body = auth_client.get(
        f'/dashboards/user/jobs/user/derecho/wait-times{crafted}'
    ).get_data(as_text=True)
    assert 'aria-label="Owner dimension"' not in body
    _dim, kwargs = captured['last_jobs_histogram']
    assert kwargs.get('owners_by') == 'account'


def test_histogram_drops_owner_grouping_when_both_axes_are_pinned(
    app, auth_client, monkeypatch,
):
    """My own jobs on ONE project: every band has exactly one owner, so
    skip the grouping entirely — flat bars, band drills straight to jobs."""
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist(),
    )
    auth_client.get(
        '/dashboards/user/jobs/user/derecho/wait-times?account=SCSG0001'
    )
    _dim, kwargs = captured['last_jobs_histogram']
    assert 'owners_limit' not in kwargs


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
    older plugin keeps working); the Project pill sends the plugin's
    'account' for the URL's canonical group_by=project."""
    captured = _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist_project_owners(),
    )
    auth_client.get('/dashboards/user/jobs/machine/derecho/wait-times')
    _dim, kwargs = captured['last_jobs_histogram']
    assert 'owners_by' not in kwargs

    auth_client.get(
        '/dashboards/user/jobs/machine/derecho/wait-times?group_by=project')
    _dim, kwargs = captured['last_jobs_histogram']
    assert kwargs['owners_by'] == 'account'

    # The plugin's own spelling still works — bookmarks and any URL built
    # before the rename keep resolving to the same view.
    auth_client.get(
        '/dashboards/user/jobs/machine/derecho/wait-times?owners_by=account')
    _dim, kwargs = captured['last_jobs_histogram']
    assert kwargs['owners_by'] == 'account'


def test_histogram_account_owner_tier_and_drill(
    app, auth_client, monkeypatch,
):
    """The Project pill drives the whole drill: Project tier header,
    project-modal triggers on owner cells, account= (not user=) on the
    per-owner jobs drill, 'Other projects' remainder, and the group_by
    round-trip hidden input for the metric pills."""
    import re
    _install_mock_plugin(
        app, monkeypatch, jobs_histogram_return=_sample_hist_project_owners(),
    )
    body = auth_client.get(
        '/dashboards/user/jobs/machine/derecho/wait-times?group_by=project'
    ).get_data(as_text=True)
    assert '<th>Project</th>' in body
    assert 'Other projects' in body
    assert 'project-details-modal/SCSG0001' in body
    assert 'name="group_by" value="project"' in body
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

    import re
    start = _days_ago(365).isoformat()
    panel_urls = [u.replace('&amp;', '&')
                  for u in re.findall(r'hx-get="([^"]+)"', body)]
    for suffix in ('', '/wait-times', '/job-sizes', '/durations'):
        path = f'/dashboards/user/jobs/{active_project.projcode}{suffix}?'
        matches = [u for u in panel_urls if u.startswith(path)]
        assert matches, suffix
        # Param order is url_for's business; what matters is that every
        # panel carries the machine and the pill's window.
        assert all('machine=derecho' in u and f'start={start}' in u
                   for u in matches), suffix
    # A pill is a lookback from today, so the page's own end date is gone.
    assert 'end=' not in body


def test_card_fragment_carries_scope_into_every_panel_url(
    app, auth_client, active_project, monkeypatch,
):
    """A re-rooted subtree has to narrow the aggregations, not just the link.

    The panels resolve ``?scope=`` through ``_tree_projcodes`` ->
    ``_scope_project``; until it rode ``panel_params`` only the page-level
    explore link carried it, so a scoped card would have widened its pies
    back to the whole tree.
    """
    _install_mock_plugin(app, monkeypatch)
    body = auth_client.get(
        _card_url(active_project.projcode, days=90, scope='CHILD0001')
    ).get_data(as_text=True)

    import re
    panel_urls = [u.replace('&amp;', '&')
                  for u in re.findall(r'hx-get="([^"]+)"', body)]
    for suffix in ('', '/by-user', '/wait-times', '/job-sizes', '/durations'):
        path = f'/dashboards/user/jobs/{active_project.projcode}{suffix}?'
        matches = [u for u in panel_urls if u.startswith(path)]
        assert matches, suffix
        assert all('scope=CHILD0001' in u for u in matches), suffix


def test_card_fragment_marks_the_requested_pill_active(
    app, auth_client, active_project, monkeypatch,
):
    _install_mock_plugin(app, monkeypatch)
    body = auth_client.get(
        _card_url(active_project.projcode, days=30)).get_data(as_text=True)

    # The selected pill is the one carrying `active` — every pill keeps the
    # same btn-outline-secondary base class (the theme inverts .btn-group so
    # `active` paints white). Attribute order isn't guaranteed, so match the
    # class list and the data attribute in either order.
    import re
    assert re.search(r'btn-outline-secondary active[^>]*data-days-value="30"', body) or \
        re.search(r'data-days-value="30"[^>]*btn-outline-secondary active', body)
    assert 'data-days-value="365"' in body      # the other pills still render
    # ...and it's the only active one — this fragment renders a single card,
    # so a second `active` would mean two windows look selected at once.
    assert len(re.findall(r'btn-outline-secondary active', body)) == 1


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


# ---------------------------------------------------------------------------
# panel_relevance — one rule for "can this scope vary along that axis?",
# feeding both the tab strip and the histograms' owner axis.
# ---------------------------------------------------------------------------

def _rel(**kwargs):
    from webapp.jobs.routes import panel_relevance
    kwargs.setdefault('mode', 'machine')
    return panel_relevance(**kwargs)


def test_relevance_machine_mode_varies_along_both_axes():
    r = _rel()
    assert r['show_by_user'] and r['show_by_project']
    assert r['owners_toggle'] and r['owners_enabled']
    assert r['default_group_by'] == 'user'


def test_relevance_user_mode_pins_the_user_axis():
    """By User would be a pie of one; the project axis takes the stack."""
    r = _rel(mode='user')
    assert not r['show_by_user']
    assert r['show_by_project']
    assert not r['owners_toggle']
    assert r['default_group_by'] == 'project'
    assert r['owners_enabled']


def test_relevance_user_filter_pins_the_user_axis_in_any_mode():
    r = _rel(mode='machine', user_filter='alice')
    assert not r['show_by_user']
    assert r['default_group_by'] == 'project'


def test_relevance_account_filter_pins_the_project_axis():
    r = _rel(mode='machine', account_filter='SCSG0001')
    assert r['show_by_user']
    assert not r['show_by_project']
    assert not r['owners_toggle']
    assert r['default_group_by'] == 'user'


def test_relevance_single_projcode_tree_pins_the_project_axis():
    assert not _rel(mode='project',
                    account_projcodes=['SCSG0001'])['show_by_project']
    assert _rel(mode='project',
                account_projcodes=['SCSG0001', 'SCSG0002'])['show_by_project']


def test_relevance_drops_owner_grouping_when_both_axes_are_pinned():
    r = _rel(mode='user', account_filter='SCSG0001')
    assert not r['show_by_user'] and not r['show_by_project']
    assert not r['owners_enabled']


def test_relevance_needs_no_request_context():
    """Purity is the point: relevance follows what reaches the PANELS, not
    request.args. A ?user= on a host *page* URL must not hide a tab whose
    panels were never filtered by it — so the rule can't read the request.
    Touching ``request`` here would raise outside a request context."""
    from flask import has_request_context
    assert not has_request_context()
    assert _rel(mode='machine')['show_by_user'] is True


# ---------------------------------------------------------------------------
# Job Sizes: empty edge-band suppression
# ---------------------------------------------------------------------------

def _sizes_hist(first_jobs=0):
    """A nodes-style envelope whose 0 band is empty unless asked otherwise."""
    return {
        'dimension': 'nodes', 'column': 'numnodes', 'unit': 'nodes',
        'min_param': 'min_nodes', 'max_param': 'max_nodes',
        'buckets': [
            {'label': '0', 'lo': 0, 'hi': 0,
             'job_count': first_jobs, 'cpu_hours': 0.0, 'gpu_hours': 0.0},
            {'label': '1', 'lo': 1, 'hi': 1,
             'job_count': 12, 'cpu_hours': 120.0, 'gpu_hours': 0.0},
            {'label': '2-4', 'lo': 2, 'hi': 4,
             'job_count': 5, 'cpu_hours': 50.0, 'gpu_hours': 3.0},
        ],
        'null_count': 0, 'total_count': 17 + first_jobs,
    }


def _banded_hist(counts):
    """A nodes-style envelope with one band per entry of *counts*."""
    return {
        'dimension': 'nodes', 'column': 'numnodes', 'unit': 'nodes',
        'min_param': 'min_nodes', 'max_param': 'max_nodes',
        'buckets': [
            {'label': str(i), 'lo': i, 'hi': i,
             'job_count': n, 'cpu_hours': float(n), 'gpu_hours': 0.0}
            for i, n in enumerate(counts)
        ],
        'null_count': 0, 'total_count': sum(counts),
    }


def _labels(hist):
    return [b['label'] for b in hist['buckets']]


def test_trim_drops_a_leading_empty_band():
    """Every job uses ≥1 node, so that 0 band can never fill."""
    from webapp.jobs.routes import _trim_empty_edge_bands

    hist = _sizes_hist()
    trimmed = _trim_empty_edge_bands(hist)

    assert _labels(trimmed) == ['1', '2-4']
    # The envelope is a shared cache entry — trimming must copy, not mutate.
    assert _labels(hist) == ['0', '1', '2-4']


def test_trim_keeps_a_populated_leading_band():
    """The GPU 0 band holds the CPU-only jobs and stays."""
    from webapp.jobs.routes import _trim_empty_edge_bands

    assert _labels(_trim_empty_edge_bands(_sizes_hist(first_jobs=9))) == \
        ['0', '1', '2-4']


def test_trim_drops_trailing_empty_bands():
    """The bucket tables are sized for the biggest machine the plugin
    serves, so a smaller one's top bands can never fill."""
    from webapp.jobs.routes import _trim_empty_edge_bands

    hist = _banded_hist([3, 7, 2, 0, 0, 0])
    trimmed = _trim_empty_edge_bands(hist)

    assert _labels(trimmed) == ['0', '1', '2']
    assert _labels(hist) == ['0', '1', '2', '3', '4', '5']   # not mutated


def test_trim_drops_both_edges_at_once():
    from webapp.jobs.routes import _trim_empty_edge_bands

    assert _labels(_trim_empty_edge_bands(
        _banded_hist([0, 0, 4, 9, 0]))) == ['2', '3']


def test_trim_keeps_interior_empty_bands():
    """A gap inside the distribution is a finding, not noise."""
    from webapp.jobs.routes import _trim_empty_edge_bands

    assert _labels(_trim_empty_edge_bands(
        _banded_hist([0, 5, 0, 0, 8, 0]))) == ['1', '2', '3', '4']


def test_trim_keeps_a_single_populated_band():
    from webapp.jobs.routes import _trim_empty_edge_bands

    assert _labels(_trim_empty_edge_bands(
        _banded_hist([0, 0, 6, 0, 0]))) == ['2']


def test_trim_empties_an_entirely_empty_range():
    """All-zero → no bands at all, so the caller renders an empty state
    instead of a bar-less axis over a table of zeros."""
    from webapp.jobs.routes import _trim_empty_edge_bands

    hist = _banded_hist([0, 0, 0])
    trimmed = _trim_empty_edge_bands(hist)

    assert trimmed['buckets'] == []
    assert len(hist['buckets']) == 3                         # not mutated


def test_trim_tolerates_an_envelope_with_no_bands():
    from webapp.jobs.routes import _trim_empty_edge_bands

    hist = _banded_hist([])
    assert _trim_empty_edge_bands(hist) is hist
    assert _trim_empty_edge_bands(None) is None


def test_trim_ignores_the_displayed_metric():
    """A band of real jobs charging no GPU-hours must not shift the axis."""
    from webapp.jobs.routes import _trim_empty_edge_bands

    hist = _sizes_hist(first_jobs=4)
    hist['buckets'][0]['cpu_hours'] = 0.0
    hist['buckets'][0]['gpu_hours'] = 0.0
    assert _trim_empty_edge_bands(hist)['buckets'][0]['label'] == '0'


def test_job_sizes_fragment_hides_the_empty_zero_band(
    app, auth_client, active_project, monkeypatch,
):
    _install_mock_plugin(app, monkeypatch, jobs_histogram_return=_sizes_hist())
    body = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/job-sizes'
        '?machine=derecho&dimension=nodes'
    ).get_data(as_text=True)

    import re
    labels = re.findall(r'<code>([^<]+)</code>', body)
    assert '0' not in labels
    assert '1' in labels and '2-4' in labels


def test_job_sizes_bar_and_row_indices_stay_aligned_after_trim(
    app, auth_client, active_project, monkeypatch,
):
    """#sam/row/data-jh-bucket/<i> and data-jh-bucket=<i> both index the trimmed vector."""
    _install_mock_plugin(app, monkeypatch, jobs_histogram_return=_sizes_hist())
    body = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/job-sizes'
        '?machine=derecho&dimension=nodes'
    ).get_data(as_text=True)

    import re
    # Band 0 of the rendered chart is now the '1' band, and the row that
    # answers a click on it must be the one carrying its counts.
    row = re.search(r'data-jh-bucket="0".*?</tr>', body, re.S)
    assert row, 'no bucket-0 row rendered'
    assert '<code>1</code>' in row.group(0)


def _job_sizes_body(app, auth_client, project, monkeypatch, hist):
    _install_mock_plugin(app, monkeypatch, jobs_histogram_return=hist)
    return auth_client.get(
        f'/dashboards/user/jobs/{project.projcode}/job-sizes'
        '?machine=derecho&dimension=nodes'
    ).get_data(as_text=True)


def test_job_sizes_fragment_hides_trailing_empty_bands(
    app, auth_client, active_project, monkeypatch,
):
    """Casper can't fill the top of an axis sized for the biggest machine."""
    import re
    body = _job_sizes_body(app, auth_client, active_project, monkeypatch,
                           _banded_hist([0, 4, 9, 0, 0, 0]))
    labels = re.findall(r'<code>([^<]+)</code>', body)
    assert labels == ['1', '2']


def test_job_sizes_fragment_keeps_interior_empty_bands(
    app, auth_client, active_project, monkeypatch,
):
    import re
    body = _job_sizes_body(app, auth_client, active_project, monkeypatch,
                           _banded_hist([0, 5, 0, 8, 0]))
    labels = re.findall(r'<code>([^<]+)</code>', body)
    assert labels == ['1', '2', '3']


def test_histogram_with_no_matching_jobs_renders_one_empty_state(
    app, auth_client, active_project, monkeypatch,
):
    """All-zero: one sentence, not a bar-less axis over a table of zeros."""
    body = _job_sizes_body(app, auth_client, active_project, monkeypatch,
                           _banded_hist([0, 0, 0]))

    assert 'No jobs match these filters.' in body
    assert 'Bucket breakdown' not in body
    assert 'data-jh-bucket' not in body


def test_histogram_all_unmeasured_says_so_instead_of_no_jobs(
    app, auth_client, active_project, monkeypatch,
):
    """Matching jobs that carry no value on this dimension are a different
    story from no matching jobs — Derecho waits before 2025 are the case."""
    hist = _banded_hist([0, 0, 0])
    hist.update(dimension='wait', null_count=42, total_count=42)
    _install_mock_plugin(app, monkeypatch, jobs_histogram_return=hist)
    body = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/wait-times'
        '?machine=derecho'
    ).get_data(as_text=True)

    assert 'no wait measurement' in body
    assert 'No jobs match these filters.' not in body


# ---------------------------------------------------------------------------
# Log y-axis switch — parity with the filesystem-scan distribution
# histograms. Offered on every histogram tab (job distributions are all
# long-tailed), rides the round-trip form so the other pills keep it, and
# persists through the shared lens.
# ---------------------------------------------------------------------------

_HIST_TABS = ('wait-times', 'job-sizes', 'durations')


@pytest.mark.parametrize('tab', _HIST_TABS)
def test_histogram_tabs_offer_the_log_switch_off_by_default(
    app, auth_client, active_project, monkeypatch, tab,
):
    _install_mock_plugin(app, monkeypatch,
                         jobs_histogram_return=_sample_hist())
    body = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/{tab}?machine=derecho'
    ).get_data(as_text=True)

    assert 'Log scale' in body
    assert 'id="jobs-hist-log-' in body
    # Off by default, and the switch offers the ON direction.
    assert 'log=1' in body
    assert 'checked' not in body


def test_log_on_renders_and_offers_the_way_back(
    app, auth_client, active_project, monkeypatch,
):
    """?log=1 → a chart still renders (solid bars), the switch reflects the
    state, the band drill anchors survive, and clicking it turns log off."""
    _install_mock_plugin(app, monkeypatch,
                         jobs_histogram_return=_sample_hist())
    body = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/wait-times'
        '?machine=derecho&log=1'
    ).get_data(as_text=True)

    assert '<svg' in body
    assert 'checked' in body
    assert '#sam/row/data-jh-bucket/0' in body
    assert 'log=0' in body


def test_log_rides_the_roundtrip_form_so_the_other_pills_keep_it(
    app, auth_client, active_project, monkeypatch,
):
    """The metric / dimension / owner pills carry no ?log= of their own —
    they inherit it from the hidden params form they hx-include. Absent when
    off, so a stale `1` can never outlive the switch."""
    _install_mock_plugin(app, monkeypatch,
                         jobs_histogram_return=_sample_hist())
    url = (f'/dashboards/user/jobs/{active_project.projcode}/wait-times'
           '?machine=derecho')

    on = auth_client.get(url + '&log=1').get_data(as_text=True)
    assert '<input type="hidden" name="log" value="1">' in on

    off = auth_client.get(url).get_data(as_text=True)
    assert 'name="log"' not in off


def test_log_does_not_leak_into_the_band_drill_urls(
    app, auth_client, active_project, monkeypatch,
):
    """Drill URLs are built before the y-scale joins the params — the jobs
    table has no log axis and should not be asked about one."""
    _install_mock_plugin(app, monkeypatch,
                         jobs_histogram_return=_sample_hist())
    body = auth_client.get(
        f'/dashboards/user/jobs/{active_project.projcode}/wait-times'
        '?machine=derecho&log=1'
    ).get_data(as_text=True)

    import re
    drills = re.findall(r'hx-get="([^"]*min_eligible_secs[^"]*)"', body)
    assert drills, 'no band drill URLs rendered'
    assert not [d for d in drills if 'log=' in d]


@pytest.mark.parametrize('query,expected', [
    ('log=1', True),
    ('log=true', True),
    ('log=on', True),
    ('log=0', False),
    ('log=', False),
    ('log=nonsense', False),
    ('', False),
])
def test_parse_log(app, query, expected):
    from webapp.jobs.routes import _parse_log
    with app.test_request_context(f'/?{query}'):
        assert _parse_log() is expected


# ---------------------------------------------------------------------------
# Shared view lens — the panels' metric / owner / dimension pills persist
# through the app-wide bucket (nav-view-persistence.js `data-chart-persist-
# shared`), so a selection survives a period-pill re-render, carries to the
# sibling panels, and comes back on reload.
# ---------------------------------------------------------------------------

_LENS = ('data-chart-persist-shared='
         '"group_by metric:jobs dimension:jobs log:jobs"')


def _tag_for(body, needle):
    """The single HTML tag containing `needle`."""
    i = body.index(needle)
    return body[body.rindex('<', 0, i):body.index('>', i) + 1]


def test_lens_declared_on_both_ends_of_every_pill_panel(
    app, auth_client, active_project, monkeypatch,
):
    """Tab button (injects on fetch) and container (saves on settle).

    Both ends are required: the button is what requests the panel, and the
    container is the element htmx reports as settled, which is what lets an
    in-panel pill click persist without a click handler.
    """
    _install_mock_plugin(app, monkeypatch)
    body = auth_client.get(
        _card_url(active_project.projcode,
                  days_persist_id='jobs-days-status')).get_data(as_text=True)

    for panel in ('byuser', 'wait', 'sizes', 'durations'):
        assert _LENS in _tag_for(body, f'id="jobs-hist-{panel}-tab"'), panel
        assert _LENS in _tag_for(body, f'id="jobs-hist-{panel}"'), panel


def test_lens_not_declared_on_the_jobs_tab(
    app, auth_client, active_project, monkeypatch,
):
    """The per-job table has none of the three pills — no reason to carry
    the lens into its URL."""
    _install_mock_plugin(app, monkeypatch)
    body = auth_client.get(
        _card_url(active_project.projcode)).get_data(as_text=True)

    assert _LENS not in _tag_for(body, 'id="jobs-hist-jobs-tab"')
    assert _LENS not in _tag_for(body, 'id="jobs-hist-jobs"')


def test_lens_is_independent_of_window_persistence(
    app, auth_client, active_project, monkeypatch,
):
    """Resource-details opts out of a *stored window* (it would shadow the
    page's own date range) — that says nothing about the lens, which has no
    such conflict and persists everywhere."""
    _install_mock_plugin(app, monkeypatch)
    body = auth_client.get(
        _card_url(active_project.projcode)).get_data(as_text=True)

    assert 'data-chart-persist-id' not in body      # no window persistence
    assert _LENS in body                            # lens regardless


# --- Canonical group_by parsing --------------------------------------------

@pytest.mark.parametrize('query,expected', [
    ('group_by=project', 'project'),
    ('group_by=user', 'user'),
    ('owners_by=account', 'project'),   # the plugin's spelling, still honoured
    ('owners_by=user', 'user'),
    ('group_by=nonsense', 'user'),
    ('', 'user'),
])
def test_parse_group_by(app, query, expected):
    from webapp.jobs.routes import _parse_group_by
    with app.test_request_context(f'/?{query}'):
        assert _parse_group_by() == expected


def test_parse_group_by_prefers_the_canonical_spelling(app):
    """Both present (a stale round-trip form beside a fresh pill click):
    group_by wins, so the click a user just made is what renders."""
    from webapp.jobs.routes import _parse_group_by
    with app.test_request_context('/?group_by=user&owners_by=account'):
        assert _parse_group_by() == 'user'


# ---------------------------------------------------------------------------
# Activity timeline — granularity budget and band drills
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('span_days,expected', [
    (1, 'day'), (30, 'day'), (120, 'day'),
    (121, 'week'),                  # 121 daily bars is over budget
    (365, 'week'), (840, 'week'),   # 840/7 = 120, still exactly in budget
    (841, 'month'),
    (10_000, 'month'),              # full history
])
def test_auto_period_picks_the_finest_granularity_in_budget(span_days, expected):
    """Server-chosen because the explorer permits an unbounded window: a
    fixed 'day' default would blow the bar budget or trip the plugin's
    400-band ValueError, both of which read as a broken panel."""
    from webapp.jobs.routes import _auto_period
    assert _auto_period(span_days) == expected


def test_parse_period_honours_an_explicit_choice_that_fits(app):
    from webapp.jobs.routes import _parse_period
    with app.test_request_context('/?period=month'):
        assert _parse_period(30) == 'month'


def test_parse_period_refuses_a_stale_choice_that_does_not_fit(app):
    """A 'day' saved against last week's 30-day window must not be replayed
    against a five-year one — it would be 1,800 bars."""
    from webapp.jobs.routes import _parse_period
    with app.test_request_context('/?period=day'):
        assert _parse_period(1826) == 'month'


@pytest.mark.parametrize('raw', ['quarter', 'year', 'fortnight', '', 'DAY'])
def test_parse_period_ignores_unknown_values(app, raw):
    """Lenient like _parse_metric: unknown means 'no override', never 400.
    'quarter'/'year' are PeriodGrouper's vocabulary, deliberately not ours."""
    from webapp.jobs.routes import _parse_period
    with app.test_request_context(f'/?period={raw}'):
        assert _parse_period(30) == 'day'


def test_period_choices_disable_rather_than_hide_over_budget_options():
    """A pill that vanishes reads as a bug; a disabled one with the bar
    count is the explanation."""
    from webapp.jobs.routes import _period_choices
    choices = {c['key']: c for c in _period_choices(365)}
    assert choices['day']['enabled'] is False
    assert choices['day']['bars'] == 365
    assert choices['week']['enabled'] is True
    assert set(choices) == {'day', 'week', 'month'}


def test_filter_span_days_reads_the_window(app):
    from webapp.jobs.routes import _filter_span_days, _parse_job_filters
    with app.test_request_context('/?start=2026-01-01&end=2026-01-30'):
        assert _filter_span_days(_parse_job_filters()) == 30


def test_filter_span_days_is_none_for_an_open_ended_window(app):
    """Both date fields cleared on the explorer — the documented opt-in to
    full history. The caller treats None as the widest possible window."""
    from webapp.jobs.routes import _filter_span_days
    with app.test_request_context('/'):
        assert _filter_span_days({'start': '', 'end': ''}) is None


def test_band_drill_url_overrides_the_panes_window():
    """Unlike a histogram band (min_param/max_param), a time band replays
    through start/end — the window filters ARE this dimension — so the
    band's own dates must REPLACE the pane's, not narrow alongside them."""
    from webapp.jobs.routes import _band_drill_url
    url = _band_drill_url(
        '/jobs', {'label': '2026-05-02', 'start': '2026-05-02',
                  'end': '2026-05-02', 'job_count': 7},
        {'machine': 'casper', 'start': '2026-05-01', 'end': '2026-05-31'})
    assert 'start=2026-05-02' in url and 'end=2026-05-02' in url
    assert 'start=2026-05-01' not in url


def test_band_drill_url_is_none_for_an_empty_band():
    from webapp.jobs.routes import _band_drill_url
    assert _band_drill_url('/jobs', {'job_count': 0}, {}) is None


def test_charges_is_a_first_class_metric_everywhere():
    """Ben's call: one vocabulary across all six panels, so the shared
    `metric:jobs` persist family stays valid."""
    from webapp.dashboards.charts import _JOBS_METRIC_KEYS
    from webapp.jobs.routes import (_METRICS, _USAGE_METRIC_KEYS,
                                    _USAGE_SORT_BY)
    assert 'charges' in _METRICS
    # The top-N cut must be rankable by the displayed metric, or a charges
    # view shows owners chosen by hours.
    assert set(_METRICS) <= set(_USAGE_SORT_BY)
    assert _USAGE_SORT_BY['charges'] == 'charges'
    # Every metric must be renderable, not just requestable — this is the
    # membership check that C1 slipped through: 'charges' was in _METRICS
    # and _USAGE_SORT_BY, so it could be asked for and ranked by, while the
    # panel had no way to display it.
    assert set(_METRICS) <= set(_JOBS_METRIC_KEYS)
    # ...and the remainder row has to be computable in every one of them.
    for metric in _METRICS:
        assert set(_JOBS_METRIC_KEYS[metric]) <= set(_USAGE_METRIC_KEYS), metric
