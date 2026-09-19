"""Repo-relative paths for tests, anchored to this file's location.

Importing these instead of computing ``Path(__file__).parents[N]`` in each test
keeps path arithmetic independent of how deep a test file sits, so moving a test
into a subdirectory cannot silently break its fixture or source lookups.

``tests/`` is on ``sys.path`` (see ``tests/conftest.py``), so ``from _paths import
REPO_ROOT`` works from any test module.
"""

from pathlib import Path

TESTS = Path(__file__).resolve().parent
REPO_ROOT = TESTS.parent
SRC = REPO_ROOT / "src"
SNAPSHOTS = TESTS / "unit" / "snapshots"
