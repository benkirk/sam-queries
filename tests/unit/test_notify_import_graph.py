"""The ORM must be importable without the mailer.

`sam/__init__.py` exports `NotificationLog`, and importing any submodule runs
`sam/notify/__init__.py` first. If that file imports eagerly, then jinja2,
three transports and `sam.fmt` land in the import graph of **every** consumer
of the ORM — every CLI invocation, every test, every script.

That is not merely wasteful. `sam.fmt` imports the top-level `config` module,
and `python3 src/webapp/run.py` puts `src/webapp` at `sys.path[0]`, where
`webapp/config.py` shadows it. Pulling `sam.fmt` to the front of the chain
turned webdev startup into:

    ImportError: cannot import name 'SAMConfig' from partially initialized
    module 'config'  (.../src/webapp/config.py)

Each test runs in a subprocess with a clean interpreter, because by the time
this suite's own conftest has run, everything is already imported.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / 'src'


def _run(body: str, *, path_shadow: bool = False) -> subprocess.CompletedProcess:
    """Execute `body` in a fresh interpreter that can see `src/`.

    ⚠️ `FLASK_ACTIVE` is stripped from the child's environment. pytest sets it
    in `pytest_configure` (it must be set before `system_status.base` is
    imported), and a subprocess would inherit it — under which `sam.base`
    binds to Flask-SQLAlchemy and importing `sam` pulls in flask, and
    therefore jinja2, entirely legitimately. That would mask the very leak
    these tests exist to catch. The unset case is also the one that matters:
    it is the CLI, which is not a Flask app.

    Args:
        path_shadow: reproduce `python3 src/webapp/run.py` by putting
            src/webapp first on the path, where config.py shadows the
            top-level `config` module.
    """
    prelude = f'import sys; sys.path.insert(0, {str(SRC)!r})\n'
    if path_shadow:
        prelude = (f'import sys; sys.path.insert(0, {str(SRC)!r});'
                   f' sys.path.insert(0, {str(SRC / "webapp")!r})\n')
    env = {k: v for k, v in os.environ.items() if k != 'FLASK_ACTIVE'}
    return subprocess.run(
        [sys.executable, '-c', prelude + textwrap.dedent(body)],
        capture_output=True, text=True, timeout=120, env=env)


class TestTheOrmDoesNotDragInTheMailer:

    def test_importing_sam_does_not_import_jinja2(self):
        """The CLI path: `sam-admin` must not pay for a template engine to
        look a project up."""
        result = _run("""
            import sam
            assert sam.NotificationLog is not None
            print('jinja2' in sys.modules)
        """)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == 'False', (
            'importing sam pulled in jinja2 — sam/notify/__init__.py has '
            'stopped being lazy')

    def test_importing_sam_does_not_import_the_renderer_or_transports(self):
        result = _run("""
            import sam
            leaked = [m for m in ('sam.notify.render', 'sam.notify.service',
                                  'sam.notify.transports', 'sam.notify.config')
                      if m in sys.modules]
            print(','.join(leaked))
        """)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == '', (
            f'importing sam pulled in {result.stdout.strip()}')

    def test_importing_sam_does_not_import_smtplib(self):
        result = _run("""
            import sam
            print('smtplib' in sys.modules)
        """)
        assert result.stdout.strip() == 'False', result.stderr


class TestTheLazySurfaceStillWorks:

    @pytest.mark.parametrize('name', [
        'Notifier', 'NotifyConfig', 'TemplateRenderer', 'SmtpTransport',
        'NullTransport', 'ConsoleTransport', 'NOTIFICATION_KINDS',
        'build_transport', 'NotificationLog', 'DEFAULT_FACILITY_TEMPLATE',
    ])
    def test_every_lazy_name_resolves(self, name):
        import sam.notify
        assert getattr(sam.notify, name) is not None

    def test_star_import_covers_all_of_dunder_all(self):
        """`from sam.notify import *` must not trip over a lazy name."""
        result = _run("""
            import sam.notify
            missing = [n for n in sam.notify.__all__
                       if not hasattr(sam.notify, n)]
            print(','.join(missing))
        """)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == ''

    def test_an_unknown_attribute_still_raises_attribute_error(self):
        import sam.notify
        with pytest.raises(AttributeError, match='has no attribute'):
            sam.notify.NoSuchThing

    def test_dir_reports_the_full_surface(self):
        import sam.notify
        assert set(sam.notify.__all__) <= set(dir(sam.notify))


class TestTheWebappEntryPointStartsUnderItsOwnPathShadow:

    def test_config_is_shadowed_by_webapp_config_under_sys_path_zero(self):
        """The landmine itself, pinned so its removal is a visible decision.

        `python3 src/webapp/run.py` makes `src/webapp` sys.path[0], so the
        top-level name `config` resolves to webapp/config.py rather than
        src/config.py. Any module reaching `from config import SAMConfig`
        before `config` is correctly cached detonates.
        """
        result = _run("""
            import importlib.util
            print(importlib.util.find_spec('config').origin)
        """, path_shadow=True)
        assert result.stdout.strip().endswith('webapp/config.py'), result.stdout

    def test_run_py_drops_its_own_directory_from_sys_path(self):
        """run.py disarms the shadow before any project import, so the entry
        point stops depending on import order for its correctness.

        Asserted against the source rather than by execution: running
        `run.py` starts a server.
        """
        source = (SRC / 'webapp' / 'run.py').read_text()
        assert 'sys.path[:] = [p for p in sys.path' in source, (
            'run.py no longer strips its own directory from sys.path — the '
            '`config` shadow is re-armed')
        assert source.index('sys.path[:]') < source.index('import sam'), (
            'the sys.path fix must run BEFORE any project import, or the '
            'shadow has already been resolved by the time it lands')

    def test_importing_sam_under_the_shadow_does_not_touch_config(self):
        """The property that keeps the shadow harmless: the ORM never asks
        for `config` at import time, so nothing resolves it wrongly."""
        result = _run("""
            import sam
            print('config' in sys.modules)
        """, path_shadow=True)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == 'False', (
            'importing sam resolved the top-level `config` module while '
            'src/webapp shadows it — this is exactly what broke webdev')
