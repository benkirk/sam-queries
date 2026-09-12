"""Which backend the suite is running against, and the Postgres burn-down list.

Shared by conftest.py (strict xfails, marker skips) and
tests/unit/test_postgres_expected_failures.py (the list stays honest).
"""
import os
from pathlib import Path

from sqlalchemy.engine import make_url

# tests/postgres_expected_failures.txt: node ids that still fail on Postgres,
# applied as strict xfails so a fixed test cannot stay listed.
EXPECTED_FAILURES_FILE = Path(__file__).parent / "postgres_expected_failures.txt"


def current_backend() -> str:
    """'mysql' or 'postgresql', from SAM_TEST_DB_URL (valid after pytest_configure)."""
    return make_url(os.environ["SAM_TEST_DB_URL"]).get_backend_name()


def read_expected_failures(path=EXPECTED_FAILURES_FILE):
    """[(node id or prefix, reason)] from the file; blank and comment lines skipped."""
    entries = []
    if not path.exists():
        return entries
    for raw in path.read_text().splitlines():
        line, _, comment = raw.partition("#")
        line = line.strip()
        if line:
            entries.append((line, comment.strip() or "listed in postgres_expected_failures.txt"))
    return entries


def expected_failure_matches(entry: str, nodeid: str) -> bool:
    """An entry names one test exactly, or a file/class/test prefix of many."""
    return nodeid == entry or nodeid.startswith(entry + "::") or nodeid.startswith(entry + "[")
