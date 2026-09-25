"""dbbrowse imports only SQLAlchemy, so a CLI can use it without Flask or the ORM.

Runs in a fresh interpreter: by the time the suite's conftest has run, everything is
already imported. FLASK_ACTIVE is stripped so `sam` would not be pulled in legitimately.
"""
import os
import subprocess
import sys
import textwrap

from _paths import REPO_ROOT


def test_dbbrowse_pulls_in_no_app_packages():
    body = textwrap.dedent("""
        import sys
        import dbbrowse
        banned = ('flask', 'sam', 'system_status', 'webapp', 'querykit', 'jinja2')
        print(','.join(sorted({m.split('.')[0] for m in sys.modules} & set(banned))))
    """)
    env = {k: v for k, v in os.environ.items() if k != 'FLASK_ACTIVE'}
    result = subprocess.run(
        [sys.executable, '-c',
         f'import sys; sys.path.insert(0, {str(REPO_ROOT / "src")!r})\n' + body],
        capture_output=True, text=True, timeout=120, env=env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == '', f'dbbrowse imported {result.stdout.strip()}'
