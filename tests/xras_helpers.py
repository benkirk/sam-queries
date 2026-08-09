"""Shared XRAS test helpers — the corpus loader, the wire builders, and ``committing``.

Its own module rather than a tier conftest, for the reason ``tests/xras_audit.py``
already gives: the unit, api and stress tiers all need these, and their hazards must
not drift between copies. ``tests/`` is on ``sys.path`` (the same route
``from factories import ...`` takes), so the bare module name works everywhere.

Import a fixture by name where it is wanted::

    from xras_helpers import committing  # noqa: F401  — pytest resolves it by name

Why this exists
---------------
These were duplicated across the per-handler test modules — ``FIXTURE_DIR`` twelve
times, ``load_fixture`` nine, ``committing`` seven, ``wire_resource`` five,
``txns_for`` four. Two of those had already cost something real:

* ``wire_resource`` said ``key`` where XRAS sends ``resourceRepositoryKey``, and
  because every copy said it, the handlers were written to read a field no payload
  has ever carried. It survived a whole sprint. One definition makes that a one-line
  fix; five make it a search-and-replace nobody runs.
* ``committing`` is the only thing standing between the suite and a real ``COMMIT``
  leaking rows into the shared xdist database — which has already happened once,
  mutating three ``end_date`` values. Seven copies is seven chances for one to drift.
"""

import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from sam.accounting.allocations import AllocationTransaction

__all__ = [
    'FIXTURE_DIR',
    'load_fixture',
    'wire_resource',
    'txns_for',
    'committing',
]

#: The eight real, scrubbed XRAS payloads. Prefer these over a hand-built dict for
#: anything about shape — a hand-built dict is where the ``key`` /
#: ``resourceRepositoryKey`` bug lived, because it agreed with the code rather than
#: with XRAS.
FIXTURE_DIR = Path(__file__).parent / 'fixtures' / 'xras' / 'actions'


def load_fixture(name):
    """One corpus payload, parsed."""
    return json.loads((FIXTURE_DIR / name).read_text())


def wire_resource(key, amount='250000', comments=None):
    """One ``resources[]`` entry, as XRAS actually sends it.

    ⚠️ ``resourceRepositoryKey``. **Not** ``key`` — no XRAS payload has ever carried
    a field by that name, and this helper claiming otherwise is how the handlers came
    to read one for a sprint. ``tests/unit/test_xras_wire_vocabulary.py`` now proves
    every field the handlers read is one a schema declares, from both directions.

    ``amount`` is a **string** because the wire sends strings.
    """
    return {'resourceRepositoryKey': key, 'awardedAmount': amount,
            'comments': comments}


def txns_for(session, allocation):
    """Every ``allocation_transaction`` row for *allocation*."""
    return (session.query(AllocationTransaction)
            .filter(AllocationTransaction.allocation_id == allocation.allocation_id)
            .all())


@pytest.fixture
def committing(session, monkeypatch):
    """Neutralise ``management_transaction``'s commit for handler tests.

    The handler commits by design — it is the write boundary. But the suite's
    per-test isolation is a SAVEPOINT on this connection, and a real ``COMMIT`` would
    release it and leak rows into the shared xdist database. Patching the context
    manager to flush instead keeps every assertion true (the rows exist, and are
    visible on this session) while leaving the rollback intact.

    ⚠️ **One patch point.** This used to name a handler module, and every test that
    drove more than one handler had to patch five of them — a missed one commits for
    real while the assertions still pass, which is the silent version of this failure
    and has already leaked rows once. ``management_transaction`` is imported only by
    ``sam.xras.handlers.base``, and ``tests/unit/test_xras_transaction_seam.py``
    enforces that by scanning module globals at runtime.

    ⚠️ It is also **one definition** now. Seven copies of a fixture whose entire job
    is preventing silent database corruption is seven chances for one to drift.
    """
    import sam.xras.handlers.base as base

    @contextmanager
    def flushing(sess):
        yield sess
        sess.flush()

    monkeypatch.setattr(base, 'management_transaction', flushing)
    return session
