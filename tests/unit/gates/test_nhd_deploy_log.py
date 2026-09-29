"""containers/ncar-hpc-deploy's log() must succeed whether or not an update log is set: three
commands end on it, and a standalone `smoke` once exited 1 on "smoke PASS" (2026-09-29)."""

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
TOOL = REPO / 'containers' / 'ncar-hpc-deploy' / 'libexec' / 'ncar-hpc-deploy'


def _helpers():
    src = TOOL.read_text()
    return '\n'.join(re.findall(r'^(?:stamp|log)\(\).*$', src, re.M))


def _run(script, env=None):
    return subprocess.run(['bash', '-c', f'{_helpers()}\nNHD_LANE=t\n{script}'],
                          capture_output=True, text=True, env=env or {'PATH': '/usr/bin:/bin'})


class TestLogReturnsZero:
    def test_without_an_update_log(self):
        r = _run('log hello; echo rc=$?')
        assert r.returncode == 0 and 'rc=0' in r.stdout and '[t] hello' in r.stdout

    def test_with_an_update_log_it_appends_and_still_succeeds(self, tmp_path):
        f = tmp_path / 'u.log'
        r = _run(f'NHD_UPDATE_LOG={f}; log one; log two; echo rc=$?')
        assert 'rc=0' in r.stdout
        assert [l.rsplit('] ', 1)[-1] for l in f.read_text().splitlines()] == ['one', 'two']

    def test_a_function_ending_on_log_returns_zero(self):
        r = _run('f() { log done; }; f; echo rc=$?')
        assert 'rc=0' in r.stdout
