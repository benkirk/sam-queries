"""The Postgres burn-down list stays honest: every entry names a real test.

conftest.py applies the entries as strict xfails (a fixed test fails the run
until its line is removed); this test catches the other direction, an entry
that no longer matches anything after a rename or deletion.
"""
import pytest

from _backends import (EXPECTED_FAILURES_FILE, expected_failure_matches,
                       read_expected_failures)

pytestmark = pytest.mark.unit


def test_entries_are_well_formed():
    entries = read_expected_failures()
    ids = [entry for entry, _ in entries]
    assert len(ids) == len(set(ids)), 'duplicate entries'
    assert all(entry.startswith('tests/') for entry in ids), 'entries are node ids relative to the repo root'


@pytest.mark.postgres_only
def test_every_entry_matches_a_collected_test(request):
    """Runs on the Postgres target only, where the whole suite is collected."""
    nodeids = [item.nodeid for item in request.session.items]
    stale = [entry for entry, _ in read_expected_failures()
             if not any(expected_failure_matches(entry, n) for n in nodeids)]
    assert not stale, f'remove from {EXPECTED_FAILURES_FILE.name}: {stale}'


def test_matching_is_exact_or_by_prefix():
    assert expected_failure_matches('tests/a.py::T::t', 'tests/a.py::T::t')
    assert expected_failure_matches('tests/a.py::T', 'tests/a.py::T::t')
    assert expected_failure_matches('tests/a.py::T::t', 'tests/a.py::T::t[x-1]')
    assert not expected_failure_matches('tests/a.py::T::t', 'tests/a.py::T::t2')
