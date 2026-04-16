"""Subprocess entry-point smoke for the sam-search CLI.

Phase 4f port residual: the legacy tests/integration/test_sam_search_cli.py
ran 60+ subprocess invocations against the live database to test every
CLI permutation. We replaced that with CliRunner-based unit tests
(test_sam_search_cli.py + test_sam_search_cli_allocations.py) which
cover the same Click command logic an order of magnitude faster.

The one thing CliRunner can't verify is the actual `sam-search` entry
point — the pyproject.toml `[project.scripts]` wiring and the venv's
PATH resolution. This single smoke test runs `sam-search --help` as a
real subprocess to confirm the entry point is installed and produces
expected text. If this fails, the install is broken; CI catches it
before the unit tests run.
"""
import shutil
import subprocess
import sys

import pytest


pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    shutil.which('sam-search') is None,
    reason='sam-search not on PATH (run `pip install -e .` first)'
)
def test_sam_search_entry_point_installed():
    """`sam-search --help` runs and produces the expected top-line help text.

    Confirms: pyproject.toml [project.scripts] entry point, the script
    shim, and the click `cli` group are all wired together correctly.
    """
    result = subprocess.run(
        ['sam-search', '--help'],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f'sam-search --help failed (exit {result.returncode}):\n'
        f'STDOUT: {result.stdout}\nSTDERR: {result.stderr}'
    )
    assert 'Usage:' in result.stdout
    assert 'sam-search' in result.stdout or 'cli' in result.stdout.lower()
    # Verify the major subcommands are registered
    assert 'user' in result.stdout
    assert 'project' in result.stdout
    assert 'allocations' in result.stdout
