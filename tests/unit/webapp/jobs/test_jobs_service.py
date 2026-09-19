from __future__ import annotations

import types
from unittest.mock import MagicMock

import pytest

from webapp.jobs.scope import ProjectJobScope
from _jobs_helpers import (
    _build_isolated_app,
    _install_mock_plugin,
)


def test_init_job_history_disabled_when_no_machines(app):
    """TestingConfig sets JOB_HISTORY_MACHINES=[], so is_enabled() is False."""
    from webapp.jobs import is_enabled, get_engines, get_module

    with app.app_context():
        assert is_enabled() is False
        assert get_engines() == {}
        assert get_module() is None


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
    # No account_projcodes passed -> fall back to single-projcode string
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
        # No exit_status / GPU bounds -> goes through the SAM summary fast path.
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
    is set), it also runs the queue/qos resolver so 'cpu-special' ->
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
