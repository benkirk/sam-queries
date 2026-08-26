"""The CLI's logging contract: stderr, "LEVEL name: message", stdout untouched.

The CronJob's container log is scraped for a JSON envelope. kubectl merges
stdout and stderr, so `scripts/cirrus_healthcheck.sh` strips CLI logging by
shape before parsing — the format here and the regex there must agree.
"""

import io
import json
import logging
import re
import sys
from pathlib import Path

import pytest

from cli.core.utils import CLI_LOG_FORMAT, _StderrHandler, configure_logging

pytestmark = pytest.mark.unit

HEALTHCHECK = Path(__file__).resolve().parents[2] / 'scripts' / 'cirrus_healthcheck.sh'


@pytest.fixture
def clean_root():
    root = logging.getLogger()
    saved = (list(root.handlers), root.level)
    for handler in list(root.handlers):
        if isinstance(handler, _StderrHandler):
            root.removeHandler(handler)
    yield root
    root.handlers[:] = saved[0]
    root.setLevel(saved[1])


def _healthcheck_regex():
    match = re.search(r"CLI_LOG_RE='([^']+)'", HEALTHCHECK.read_text())
    assert match, 'cirrus_healthcheck.sh no longer declares CLI_LOG_RE'
    return re.compile(match.group(1))


class TestConfigureLogging:
    """A neutral logger name: webapp's configure_logging detaches `sam` from root
    (own stdout handler, propagate=False) once the Flask app fixture has run in
    this worker, so `sam.*` proves nothing here. The CronJob path is covered by
    TestContainerLogContract, in a fresh process."""

    def test_a_warning_lands_on_stderr_with_the_prefix(self, clean_root, monkeypatch):
        configure_logging()
        err, out = io.StringIO(), io.StringIO()
        monkeypatch.setattr(sys, 'stderr', err)
        monkeypatch.setattr(sys, 'stdout', out)
        logging.getLogger('cli.probe').warning('probe %s', 42)
        assert err.getvalue() == 'WARNING cli.probe: probe 42\n'
        assert out.getvalue() == ''

    def test_the_stream_is_resolved_at_emit_time(self, clean_root, monkeypatch):
        handler = configure_logging()
        later = io.StringIO()
        monkeypatch.setattr(sys, 'stderr', later)
        assert handler.stream is later

    def test_idempotent(self, clean_root):
        first = configure_logging()
        assert configure_logging() is first
        assert sum(isinstance(h, _StderrHandler) for h in clean_root.handlers) == 1

    def test_verbose_lowers_the_root_level(self, clean_root):
        configure_logging(verbose=False)
        assert clean_root.level == logging.WARNING
        configure_logging(verbose=True)
        assert clean_root.level == logging.INFO


class TestContainerLogContract:
    """What the CronJob scrape sees: `sam.xras.*` warnings on stderr, prefixed;
    the envelope alone on stdout. No Flask app exists in `sam-admin tasks`."""

    def test_sam_warnings_split_from_the_envelope(self):
        import subprocess
        code = (
            "import json, logging, sys\n"
            "import cli.cmds.admin\n"
            "from cli.core.utils import configure_logging\n"
            "configure_logging()\n"
            "logging.getLogger('sam.xras.roster').warning('disagreement: %s', 'x')\n"
            "print(json.dumps({'kind': 'probe'}))\n"
        )
        proc = subprocess.run([sys.executable, '-c', code], capture_output=True,
                              text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr
        assert json.loads(proc.stdout) == {'kind': 'probe'}
        assert proc.stderr == 'WARNING sam.xras.roster: disagreement: x\n'
        assert _healthcheck_regex().match(proc.stderr)


class TestHealthcheckAgrees:
    """The strip regex in the shell script matches every line this format emits."""

    @pytest.mark.parametrize('level', ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'])
    def test_every_level_is_stripped(self, level):
        record = logging.LogRecord('sam.xras.handlers.transfer', getattr(logging, level),
                                   __file__, 1, 'parked: %s', ('x',), None)
        line = logging.Formatter(CLI_LOG_FORMAT).format(record)
        assert _healthcheck_regex().match(line), line

    def test_envelope_lines_survive(self):
        envelope = json.dumps({'kind': 'task_dispatch', 'counts': {'INFO': 1}}, indent=2)
        regex = _healthcheck_regex()
        assert not any(regex.match(line) for line in envelope.splitlines())

    def test_a_bare_message_is_not_mistaken_for_logging(self):
        assert not _healthcheck_regex().match('XRAS role/roster disagreement: x — action 1')


class TestEntryPoints:

    @pytest.mark.parametrize('module', ['cli.cmds.admin', 'cli.cmds.search'])
    def test_both_groups_install_the_handler(self, module, clean_root):
        import importlib
        from click.testing import CliRunner
        cli = importlib.import_module(module).cli
        result = CliRunner().invoke(cli, ['--help'])
        assert result.exit_code == 0, result.output
        # --help exits before the group body runs; the wiring is a source check.
        source = Path(importlib.import_module(module).__file__).read_text()
        assert 'configure_logging(verbose)' in source
