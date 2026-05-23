"""Tests for `sam-search accounting --jobs` (individual job listing).

The job listing reads from the optional hpc-usage-queries plugin, which is
not installed in CI, so every test injects a fake plugin module by
monkeypatching ``sam.plugins.HPC_USAGE_QUERIES.load`` — the same loader
``BaseCommand.require_plugin`` calls. A ``FakeJobQueries`` captures the
kwargs forwarded to ``jobs_search`` so we can assert filter/sort/columns
forwarding without a real database.
"""
import json
import types
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from cli.cmds.search import cli as search_cli


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def mock_search_session(session):
    with patch('sam.session.create_sam_engine') as mock_eng, \
         patch('cli.cmds.search.Session') as mock_cls:
        mock_eng.return_value = (MagicMock(), None)
        mock_cls.return_value = session
        yield session


def _job(**over):
    """Build a per-job dict shaped like JobQueries.jobs_search() output."""
    row = dict(
        job_id='1234567.desched1', account='SCSG0001', user='benkirk',
        queue='main', qos='regular', qos_factor=1.0, status='F',
        submit='2026-05-01T10:00:00', start='2026-05-01T10:05:00',
        end='2026-05-01T12:05:00', elapsed=7200, walltime=7200,
        numnodes=4, numcpus=512, numgpus=0, cputype='milan', gputype=None,
        cpu_hours=1024.0, gpu_hours=0.0, cpu_charges=1024.0, gpu_charges=0.0,
    )
    row.update(over)
    return row


def _install_fake_plugin(monkeypatch, *, rows=None, raise_unavailable=False):
    """Monkeypatch the plugin loader to return a fake module.

    Returns a ``captured`` dict accumulating every jobs_search kwargs call.
    """
    captured = {'calls': []}

    if raise_unavailable:
        from sam.plugins import PluginUnavailableError

        def _raise():
            raise PluginUnavailableError('hpc-usage-queries not installed')
        monkeypatch.setattr('sam.plugins.HPC_USAGE_QUERIES.load', _raise)
        return captured

    class FakeJobQueries:
        def __init__(self, session, machine='derecho'):
            self.session = session
            self.machine = machine

        def jobs_search(self, **kwargs):
            captured['calls'].append(kwargs)
            return [dict(r) for r in (rows or [])]

    fake_mod = types.SimpleNamespace(
        get_session=lambda machine, engine=None: MagicMock(name=f'sess_{machine}'),
        JobQueries=FakeJobQueries,
    )
    monkeypatch.setattr('sam.plugins.HPC_USAGE_QUERIES.load', lambda: fake_mod)
    return captured


# ----------------------------------------------------------------------
# Classifier parity (refactor guard)
# ----------------------------------------------------------------------

class TestClassifierParity:
    def test_classify_matches_adapt_jobstats_row(self):
        from cli.accounting.commands import classify_comp_resource, adapt_jobstats_row

        cases = [
            dict(queue='main', cpu_hours=1000.0, gpu_hours=0.0, cpu_charges=1000.0, gpu_charges=0.0),
            dict(queue='main', cpu_hours=0.0, gpu_hours=50.0, cpu_charges=0.0, gpu_charges=300.0),
            dict(queue='main', cpu_hours=1_000_000.0, gpu_hours=10.0, cpu_charges=5.0, gpu_charges=2.0),
            dict(queue='vis', cpu_hours=10.0, gpu_hours=3.0, cpu_charges=10.0, gpu_charges=99.0),
        ]
        for c in cases:
            row = dict(date='2026-05-01', user='u', account='P', **c)
            adapted = adapt_jobstats_row(row, 'derecho')
            classified = classify_comp_resource(
                'derecho', c['queue'], c['cpu_hours'], c['gpu_hours'],
                c['cpu_charges'], c['gpu_charges'],
            )
            # adapt skips zero-compute rows; otherwise the tuples match.
            if classified[2] <= 0.0:
                assert adapted is None
            else:
                assert adapted == classified

    def test_vis_queue_zeroes_gpu(self):
        from cli.accounting.commands import classify_comp_resource
        res, mach, comp_h, charges = classify_comp_resource(
            'derecho', 'vis', cpu_hours=10.0, gpu_hours=3.0,
            cpu_charges=10.0, gpu_charges=99.0,
        )
        assert res == 'Derecho'        # vis → CPU classification
        assert charges == 10.0          # GPU charge dropped


# ----------------------------------------------------------------------
# Happy path: --recent (default)
# ----------------------------------------------------------------------

class TestRecentMode:
    def test_default_recent_rich(self, runner, mock_search_session, monkeypatch):
        _install_fake_plugin(monkeypatch, rows=[_job()])
        result = runner.invoke(search_cli, [
            'accounting', '--jobs', '--last', '7d', '--machine', 'derecho',
        ])
        assert result.exit_code == 0
        assert '1234567.desched1' in result.output
        assert 'SCSG0001' in result.output

    def test_default_recent_json_envelope(self, runner, mock_search_session, monkeypatch):
        _install_fake_plugin(monkeypatch, rows=[_job()])
        result = runner.invoke(search_cli, [
            '--format', 'json', 'accounting', '--jobs', '--last', '7d',
            '--machine', 'derecho',
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data['kind'] == 'comp_jobs'
        assert data['mode'] == 'recent'
        assert data['machines'] == ['derecho']
        assert data['count'] == 1
        r = data['rows'][0]
        # derived fields appended by the classifier
        assert r['resource'] == 'Derecho'
        assert r['charges'] == 1024.0
        assert 'comp_hours' in r
        # memory must never appear
        for k in ('memory', 'vmemory', 'reqmem', 'memory_hours', 'memory_charges'):
            assert k not in r

    def test_recent_forwards_filters_and_sort(self, runner, mock_search_session, monkeypatch):
        captured = _install_fake_plugin(monkeypatch, rows=[_job()])
        result = runner.invoke(search_cli, [
            'accounting', '--jobs', '--recent', '25', '--last', '7d',
            '--machine', 'derecho', '--user', 'benkirk',
            '--project', 'SCSG0001,SCSG0002', '--queue', 'main', '--qos', 'regular',
        ])
        assert result.exit_code == 0
        kw = captured['calls'][0]
        assert kw['user'] == 'benkirk'
        assert kw['account'] == ['SCSG0001', 'SCSG0002']  # comma list → list
        assert kw['queue'] == 'main'
        assert kw['qos'] == 'regular'
        assert kw['sort_by'] == 'end'
        assert kw['sort_dir'] == 'desc'
        assert kw['limit'] == 25
        # columns requested must exclude memory
        for k in ('memory', 'vmemory', 'reqmem', 'memory_hours', 'memory_charges'):
            assert k not in kw['columns']
        assert 'cpu_charges' in kw['columns'] and 'gpu_charges' in kw['columns']

    def test_single_project_uses_scalar_account(self, runner, mock_search_session, monkeypatch):
        captured = _install_fake_plugin(monkeypatch, rows=[_job()])
        runner.invoke(search_cli, [
            'accounting', '--jobs', '--last', '7d', '--machine', 'derecho',
            '--project', 'SCSG0001',
        ])
        assert captured['calls'][0]['account'] == 'SCSG0001'


# ----------------------------------------------------------------------
# --largest
# ----------------------------------------------------------------------

class TestLargestMode:
    def test_largest_unions_cpu_and_gpu_sort(self, runner, mock_search_session, monkeypatch):
        captured = _install_fake_plugin(monkeypatch, rows=[_job()])
        result = runner.invoke(search_cli, [
            '--format', 'json', 'accounting', '--jobs', '--largest', '10',
            '--last', '30d', '--machine', 'derecho',
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data['mode'] == 'largest'
        sort_keys = sorted(c['sort_by'] for c in captured['calls'])
        assert sort_keys == ['cpu_charges', 'gpu_charges']
        # the same job from both queries is deduped
        assert data['count'] == 1

    def test_recent_and_largest_mutually_exclusive(self, runner, mock_search_session, monkeypatch):
        _install_fake_plugin(monkeypatch, rows=[_job()])
        result = runner.invoke(search_cli, [
            'accounting', '--jobs', '--recent', '5', '--largest', '5',
            '--last', '7d', '--machine', 'derecho',
        ])
        assert result.exit_code == 2


# ----------------------------------------------------------------------
# --job-id (single-job lookup via plugin's job_id filter)
# ----------------------------------------------------------------------

class TestJobIdMode:
    """Verifies SAM forwards `--job-id` to the plugin's job_id filter.

    The plugin (hpc-usage-queries) owns the shape classifier (digits vs
    `[N]` vs full-id). SAM's job is purely: forward the user's input
    unchanged, run one no-sort/no-limit query per machine, classify the
    returned rows like every other --jobs mode, label the envelope with
    `mode='job_id'`.
    """

    def test_job_id_forwards_to_plugin(self, runner, mock_search_session, monkeypatch):
        # Verify exact kwargs make it through: job_id=<input>, no sort_by,
        # no limit. The user's string is passed verbatim — boundary-anchor
        # logic is the plugin's responsibility.
        captured = _install_fake_plugin(
            monkeypatch,
            rows=[_job(job_id='6049117[28].desched1')],
        )
        result = runner.invoke(search_cli, [
            '--format', 'json', 'accounting', '--jobs',
            '--job-id', '6049117[28]', '--last', '365d',
            '--machine', 'derecho',
        ])
        assert result.exit_code == 0, result.output
        # One call per machine (one machine here → one call total).
        assert len(captured['calls']) == 1
        call = captured['calls'][0]
        assert call['job_id'] == '6049117[28]'
        # No sort/limit on the single-job path.
        assert 'sort_by' not in call
        assert 'limit' not in call

    def test_job_id_renders_single_row_rich(self, runner, mock_search_session, monkeypatch):
        _install_fake_plugin(
            monkeypatch,
            rows=[_job(job_id='6049117[28].desched1')],
        )
        result = runner.invoke(search_cli, [
            'accounting', '--jobs', '--job-id', '6049117[28].desched1',
            '--last', '365d', '--machine', 'derecho',
        ])
        assert result.exit_code == 0
        assert '6049117[28].desched1' in result.output
        # Mode label appears in the rich table title.
        assert 'job_id' in result.output

    def test_job_id_envelope_mode_label(self, runner, mock_search_session, monkeypatch):
        _install_fake_plugin(
            monkeypatch,
            rows=[_job(job_id='6049117[28].desched1')],
        )
        result = runner.invoke(search_cli, [
            '--format', 'json', 'accounting', '--jobs',
            '--job-id', '6049117', '--last', '365d', '--machine', 'derecho',
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data['kind'] == 'comp_jobs'
        assert data['mode'] == 'job_id'
        assert data['count'] == 1
        assert data['rows'][0]['job_id'] == '6049117[28].desched1'
        # The shared classifier still runs on the returned row.
        assert 'resource' in data['rows'][0]
        assert 'charges' in data['rows'][0]

    def test_job_id_returns_multiple_rows_for_array(self, runner, mock_search_session, monkeypatch):
        # The plugin returns N rows for digit-only input matching an array
        # job (parent + elements). SAM must NOT truncate — limit is None.
        rows = [
            _job(job_id=f'6049117[{i}].desched1') for i in range(31)
        ]
        _install_fake_plugin(monkeypatch, rows=rows)
        result = runner.invoke(search_cli, [
            '--format', 'json', 'accounting', '--jobs',
            '--job-id', '6049117', '--last', '365d', '--machine', 'derecho',
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data['count'] == 31

    def test_job_id_rejects_recent_combo(self, runner, mock_search_session, monkeypatch):
        _install_fake_plugin(monkeypatch, rows=[_job()])
        result = runner.invoke(search_cli, [
            'accounting', '--jobs', '--job-id', '6049117',
            '--recent', '5', '--last', '7d', '--machine', 'derecho',
        ])
        assert result.exit_code == 2
        assert '--job-id' in result.output

    def test_job_id_rejects_largest_combo(self, runner, mock_search_session, monkeypatch):
        _install_fake_plugin(monkeypatch, rows=[_job()])
        result = runner.invoke(search_cli, [
            'accounting', '--jobs', '--job-id', '6049117',
            '--largest', '5', '--last', '7d', '--machine', 'derecho',
        ])
        assert result.exit_code == 2

    def test_job_id_no_match_exits_1(self, runner, mock_search_session, monkeypatch):
        # Plugin returns empty for a nonexistent id; SAM emits the
        # "no rows" envelope and exits 1 (not 2 — there was no error).
        _install_fake_plugin(monkeypatch, rows=[])
        result = runner.invoke(search_cli, [
            '--format', 'json', 'accounting', '--jobs',
            '--job-id', '99999999', '--last', '365d', '--machine', 'derecho',
        ])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data['mode'] == 'job_id'
        assert data['count'] == 0


# ----------------------------------------------------------------------
# Multi-machine merge + truncate
# ----------------------------------------------------------------------

class TestMultiMachine:
    def test_merge_and_truncate(self, runner, mock_search_session, monkeypatch):
        # Each machine returns 2 jobs; --recent 3 should truncate the 4-row merge.
        rows = [_job(job_id='a', end='2026-05-02T00:00:00'),
                _job(job_id='b', end='2026-05-01T00:00:00')]
        _install_fake_plugin(monkeypatch, rows=rows)
        monkeypatch.setenv('JOB_HISTORY_MACHINES', 'derecho,casper')
        result = runner.invoke(search_cli, [
            '--format', 'json', 'accounting', '--jobs', '--recent', '3',
            '--last', '7d',
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data['machines'] == ['derecho', 'casper']
        assert data['count'] == 3                      # 4 merged → truncated to 3
        # sorted by end desc: newest first
        assert data['rows'][0]['end'] >= data['rows'][1]['end']


# ----------------------------------------------------------------------
# GPU column suppression
# ----------------------------------------------------------------------

class TestGpuSuppression:
    def test_gpu_column_hidden_when_all_zero(self, runner, mock_search_session, monkeypatch):
        _install_fake_plugin(monkeypatch, rows=[_job(numgpus=0)])
        result = runner.invoke(search_cli, [
            'accounting', '--jobs', '--last', '7d', '--machine', 'derecho',
        ])
        assert result.exit_code == 0
        assert 'GPUs' not in result.output

    def test_gpu_column_shown_when_nonzero(self, runner, mock_search_session, monkeypatch):
        _install_fake_plugin(monkeypatch, rows=[
            _job(numgpus=4, gpu_hours=8.0, gpu_charges=64.0, cpu_hours=0.0, cpu_charges=0.0),
        ])
        result = runner.invoke(search_cli, [
            'accounting', '--jobs', '--last', '7d', '--machine', 'derecho',
        ])
        assert result.exit_code == 0
        assert 'GPUs' in result.output


# ----------------------------------------------------------------------
# Error / edge paths
# ----------------------------------------------------------------------

class TestErrorPaths:
    def test_plugin_missing_exits_2(self, runner, mock_search_session, monkeypatch):
        _install_fake_plugin(monkeypatch, raise_unavailable=True)
        result = runner.invoke(search_cli, [
            'accounting', '--jobs', '--last', '7d', '--machine', 'derecho',
        ])
        assert result.exit_code == 2

    def test_no_rows_exits_1(self, runner, mock_search_session, monkeypatch):
        _install_fake_plugin(monkeypatch, rows=[])
        result = runner.invoke(search_cli, [
            '--format', 'json', 'accounting', '--jobs', '--last', '7d',
            '--machine', 'derecho',
        ])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data['kind'] == 'comp_jobs'
        assert data['count'] == 0

    def test_invalid_machine_exits_2(self, runner, mock_search_session, monkeypatch):
        _install_fake_plugin(monkeypatch, rows=[_job()])
        result = runner.invoke(search_cli, [
            'accounting', '--jobs', '--last', '7d', '--machine', 'frobnitz',
        ])
        assert result.exit_code == 2

    def test_jobs_only_flag_requires_jobs(self, runner, mock_search_session, monkeypatch):
        _install_fake_plugin(monkeypatch, rows=[_job()])
        result = runner.invoke(search_cli, [
            'accounting', '--qos', 'regular', '--last', '7d',
        ])
        assert result.exit_code == 2

    def test_resource_rejected_with_jobs(self, runner, mock_search_session, monkeypatch):
        _install_fake_plugin(monkeypatch, rows=[_job()])
        result = runner.invoke(search_cli, [
            'accounting', '--jobs', '--resource', 'Derecho', '--last', '7d',
            '--machine', 'derecho',
        ])
        assert result.exit_code == 2
