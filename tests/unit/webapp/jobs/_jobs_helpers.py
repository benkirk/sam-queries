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

from flask import Flask


__all__ = [
    '_DEFAULT_QOS_NAMES',
    '_EXPLORE_URLS',
    '_FAKE_COLUMN_SPECS',
    '_HIST_TABS',
    '_LENS',
    '_MACHINE_FRAGMENTS',
    '_PROJECT_USAGE',
    '_SAMPLE_FACETS',
    '_USER_FRAGMENTS',
    '_WASTED_HIST',
    '_banded_hist',
    '_build_isolated_app',
    '_capture_connect_listener',
    '_card_url',
    '_days_ago',
    '_explore_body',
    '_get_hist_body',
    '_hx_trigger_after',
    '_install_mock_plugin',
    '_job_sizes_body',
    '_labels',
    '_make_row',
    '_rel',
    '_sample_hist',
    '_sample_hist_owners',
    '_sample_hist_project_owners',
    '_sample_usage',
    '_sizes_hist',
    '_tag_for',
    '_two_user_rows',
]


def _build_isolated_app(machines):
    """A minimal Flask app with only the JOB_HISTORY_* config init_job_history needs."""
    a = Flask(__name__)
    a.config['JOB_HISTORY_MACHINES'] = machines
    a.config['JOB_HISTORY_POOL_KWARGS'] = {}
    return a


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


_MACHINE_FRAGMENTS = ['', '/by-user', '/by-project', '/wait-times',
                      '/job-sizes', '/durations']


_EXPLORE_URLS = (
    ('project', '/dashboards/user/jobs/{projcode}/explore?machine=derecho'),
    ('machine', '/dashboards/user/jobs/machine/derecho/explore'),
    ('user', '/dashboards/user/jobs/user/derecho/explore'),
)


_SAMPLE_FACETS = {
    'queue': [{'value': 'cpu', 'count': 120}, {'value': 'gpu', 'count': 30},
              {'value': None, 'count': 2}],
    'qos': [{'value': 'regular', 'count': 100}, {'value': 'premium', 'count': 50}],
    'exit_status': [{'value': '0', 'count': 140}, {'value': '271', 'count': 10}],
}


def _hx_trigger_after(body, anchor):
    """The hx-trigger of the element whose id attribute is *anchor*."""
    import re
    frag = body[body.index(f'id="{anchor}"'):][:600]
    m = re.search(r'hx-trigger="([^"]*)"', frag)
    assert m, f'no hx-trigger near {anchor}: {frag[:200]}'
    return m.group(1)


_USER_FRAGMENTS = ['', '/wait-times', '/job-sizes', '/durations']


_PROJECT_USAGE = {
    'dimension': 'account',
    'rows': [
        {'value': 'SCSG0001', 'job_count': 30, 'cpu_hours': 300.0, 'gpu_hours': 0.0},
        {'value': 'UABC0002', 'job_count': 12, 'cpu_hours': 120.0, 'gpu_hours': 2.0},
    ],
    'totals': {'job_count': 42, 'cpu_hours': 420.0, 'gpu_hours': 2.0},
}


def _sample_hist_owners(dimension='wait'):
    """_sample_hist plus per-bucket owners (plugin owners_limit envelope).

    Bucket '<1m' (10 jobs): alice 6 jobs / 30 cpu-h, bob 3 jobs / 70 cpu-h
    -> ranked alice-first on the jobs metric, bob-first on cpu_hours; 1 job
    unattributed (the "Other users" remainder row).
    Bucket '1-5m' (4 jobs): carol owns everything -> single-owner shortcut.
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


def _two_user_rows():
    return [
        _make_row(job_id='600.desched1', user='alice'),
        _make_row(job_id='601.desched1', user='zed'),
    ]


def _days_ago(days):
    from datetime import date, timedelta
    return date.today() - timedelta(days=days)


def _card_url(projcode, **extra):
    from urllib.parse import urlencode
    params = {'machine': 'derecho', 'cid': 'jobs-hist',
              'tablist_id': 'jobsCardTabs'}
    params.update(extra)
    return f'/dashboards/user/jobs/{projcode}/card?{urlencode(params)}'


def _rel(**kwargs):
    from webapp.jobs.routes import panel_relevance
    kwargs.setdefault('mode', 'machine')
    return panel_relevance(**kwargs)


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


def _job_sizes_body(app, auth_client, project, monkeypatch, hist):
    _install_mock_plugin(app, monkeypatch, jobs_histogram_return=hist)
    return auth_client.get(
        f'/dashboards/user/jobs/{project.projcode}/job-sizes'
        '?machine=derecho&dimension=nodes'
    ).get_data(as_text=True)


_HIST_TABS = ('wait-times', 'job-sizes', 'durations')


_LENS = ('data-chart-persist-shared='
         '"group_by metric:jobs dimension:jobs log:jobs"')


def _tag_for(body, needle):
    """The single HTML tag containing `needle`."""
    i = body.index(needle)
    return body[body.rindex('<', 0, i):body.index('>', i) + 1]


def _explore_body(app, auth_client, monkeypatch, projcode, query=''):
    _install_mock_plugin(app, monkeypatch)
    return auth_client.get(
        f'/dashboards/user/jobs/{projcode}/explore?machine=derecho{query}'
    ).get_data(as_text=True)
