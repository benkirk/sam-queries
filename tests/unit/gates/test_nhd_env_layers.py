"""containers/ncar-hpc-deploy's nhd_exec: which env layers a user gets, the gate on writer tools,
and that the merged copy (it can hold writer secrets) is removed however the run ends.

A fake `apptainer` first on PATH prints its --env args and the merged file, so no image is needed.
"""

import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
LANE_SH = REPO / 'containers' / 'ncar-hpc-deploy' / 'libexec' / 'lane.sh'

FAKE_APPTAINER = r'''#!/bin/bash
while (( $# )); do
    case "$1" in
        --env-file) echo "FILE $2"; f="$2"; shift 2 ;;
        --env) echo "ENV $2"; shift 2 ;;
        *) shift ;;
    esac
done
echo "--- merged"; cat "${f}"; echo "--- end"
if [[ -n "${FAKE_SLEEP}" ]]; then echo READY >&2; sleep "${FAKE_SLEEP}"; fi
exit "${FAKE_RC:-0}"
'''

pytestmark = pytest.mark.skipif(os.geteuid() == 0, reason='root reads chmod 000 files')


@pytest.fixture
def lane(tmp_path):
    root = tmp_path / 'deploy'
    lane = root / 'lanes' / 'prod'
    for d in ('images', 'state', 'logs'):
        (lane / d).mkdir(parents=True)
    (lane / 'images' / 'x.sif').write_bytes(b'not empty')
    (lane / 'current').symlink_to('images/x.sif')
    (lane / 'env').write_text('SAM_DB_USERNAME=reader\nSAM_DB_PASSWORD=public-secret\n')
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    (bindir / 'apptainer').write_text(FAKE_APPTAINER)
    (bindir / 'apptainer').chmod(0o755)
    (tmp_path / 'tmp').mkdir()
    yield lane
    for p in lane.rglob('*'):   # let tmp_path cleanup remove what a test locked down
        if not p.is_symlink():
            p.chmod(0o755)


def _env(lane, **extra):
    tmp = lane.parents[2]
    env = {'PATH': f'{tmp / "bin"}:/usr/bin:/bin', 'HOME': str(tmp), 'TMPDIR': str(tmp / 'tmp'),
           'NCAR_HOST': 'casper', 'NCAR_HPC_DEPLOY_ROOT': str(lane.parents[1]),
           'NCAR_HPC_DEPLOY_LANE': 'prod'}
    env.update(extra)
    return env


def _script(tool):
    return (f'source {LANE_SH}\nnhd_lane_init\nNHD_JOB={tool}\n'
            f'nhd_exec "${{NHD_LANE_DIR}}/current" {tool} --help')


def _run(lane, tool, **extra):
    return subprocess.run(['bash', '-c', _script(tool)], capture_output=True, text=True,
                          env=_env(lane, **extra), timeout=30)


def _merged(out):
    return out.split('--- merged\n', 1)[1].split('--- end', 1)[0].splitlines()


def _env_file(out):
    return Path(next(l for l in out.splitlines() if l.startswith('FILE '))[5:])


def _leftovers(lane):
    return sorted(p.name for p in (lane.parents[2] / 'tmp').glob('nhd-env.*'))


class TestLayers:
    def test_public_layer_only(self, lane):
        r = _run(lane, 'sam-search')
        assert r.returncode == 0, r.stderr
        assert _merged(r.stdout) == ['SAM_DB_USERNAME=reader', 'SAM_DB_PASSWORD=public-secret']

    def test_readable_overlay_follows_so_it_wins(self, lane):
        (lane / 'env.sam-admin').write_text('SAM_DB_USERNAME=writer\n')
        r = _run(lane, 'sam-admin')
        assert r.returncode == 0, r.stderr
        assert _merged(r.stdout)[-1] == 'SAM_DB_USERNAME=writer'

    def test_symlinked_overlay(self, lane):
        (lane / 'env.sam-admin').write_text('SAM_DB_USERNAME=writer\n')
        (lane / 'env.accounting-comp').symlink_to('env.sam-admin')
        assert _merged(_run(lane, 'accounting-comp').stdout)[-1] == 'SAM_DB_USERNAME=writer'

    def test_a_layer_without_a_final_newline_does_not_fuse(self, lane):
        (lane / 'env').write_text('A=1')
        (lane / 'env.sam-admin').write_text('B=2\n')
        assert _merged(_run(lane, 'sam-admin').stdout) == ['A=1', 'B=2']

    def test_debug_names_layers_never_values(self, lane):
        (lane / 'env.sam-admin').write_text('SAM_DB_PASSWORD=writer-secret\n')
        r = _run(lane, 'sam-admin', NHD_DEBUG='1')
        assert 'env layers: lanes/prod/env lanes/prod/env.sam-admin' in r.stderr
        assert 'secret' not in r.stderr


class TestGate:
    def test_gated_tool_refused_when_its_overlay_is_unreadable(self, lane):
        (lane / 'env.sam-admin').write_text('SAM_DB_USERNAME=writer\n')
        (lane / 'env.sam-admin').chmod(0)
        r = _run(lane, 'sam-admin')
        assert r.returncode == 2
        assert 'sam-admin needs read access to' in r.stderr and '(group ' in r.stderr
        assert 'FILE ' not in r.stdout and _leftovers(lane) == []

    def test_ungated_tool_falls_back_to_the_public_layer(self, lane):
        (lane / 'env.collectors').write_text('STATUS_API_KEY=k\n')
        (lane / 'env.collectors').chmod(0)
        r = _run(lane, 'collectors')
        assert r.returncode == 0, r.stderr
        assert _merged(r.stdout) == ['SAM_DB_USERNAME=reader', 'SAM_DB_PASSWORD=public-secret']

    def test_absent_overlay_means_a_single_audience_lane(self, lane):
        r = _run(lane, 'sam-admin')   # dev: one 0600 env, which is itself the gate
        assert r.returncode == 0, r.stderr


class TestPlainUser:
    def test_unwritable_state_moves_mplconfigdir_and_writes_nothing_there(self, lane):
        (lane / 'state').chmod(0o555)
        r = _run(lane, 'sam-search')
        assert r.returncode == 0, r.stderr
        assert f'MPLCONFIGDIR={lane.parents[2] / "tmp"}/mpl-' in r.stdout
        assert list((lane / 'state').iterdir()) == []

    def test_cron_keeps_mplconfigdir_in_state(self, lane):
        assert f'MPLCONFIGDIR={lane / "state"}/mpl\n' in _run(lane, 'sam-search').stdout

    def test_empty_image_refused(self, lane):
        (lane / 'images' / 'x.sif').write_bytes(b'')
        r = _run(lane, 'sam-search')
        assert r.returncode == 2 and 'empty' in r.stderr


class TestMergedCopyRemoved:
    def test_after_a_normal_run(self, lane):
        r = _run(lane, 'sam-search')
        assert _env_file(r.stdout).name.startswith('nhd-env.')
        assert _leftovers(lane) == []

    def test_after_the_container_fails(self, lane):
        r = _run(lane, 'sam-search', FAKE_RC='3')
        assert r.returncode == 3 and _leftovers(lane) == []

    @pytest.mark.parametrize('sig', [signal.SIGTERM, signal.SIGINT, signal.SIGHUP])
    def test_after_a_signal_to_the_process_group(self, lane, sig):
        p = subprocess.Popen(['bash', '-c', _script('sam-search')], env=_env(lane, FAKE_SLEEP='30'),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             start_new_session=True)
        assert p.stderr.readline().strip() == 'READY'
        assert len(_leftovers(lane)) == 1
        os.killpg(p.pid, sig)
        p.communicate(timeout=20)
        deadline = time.monotonic() + 5
        while _leftovers(lane) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert _leftovers(lane) == []
