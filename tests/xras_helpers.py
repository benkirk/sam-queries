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

import base64
import json
from contextlib import contextmanager
from pathlib import Path

import bcrypt
import pytest

from sam.accounting.allocations import AllocationTransaction

__all__ = [
    'FIXTURE_DIR',
    'load_fixture',
    'wire_resource',
    'txns_for',
    'committing',
    'XRAS_PW',
    'basic_auth',
    'xras_auth',
    'reset_db_key_cache',
    'xras_keys',
    'xras_client',
]

#: The 41 real, scrubbed XRAS payloads. Prefer these over a hand-built dict for
#: anything about shape — a hand-built dict is where the ``key`` /
#: ``resourceRepositoryKey`` bug lived, because it agreed with the code rather than
#: with XRAS.
#:
#: Named ``{actionType}_{requestNumber}_{outcome}.json``, where the outcome is what
#: **legacy** did with it: ``ok`` / ``failed`` / ``manual``. That is not derivable
#: from the payload — it comes from the subject line of the notification email each
#: one arrived paired with, which is why the corpus is grown by
#: ``scripts/xras/extract_email_payloads.py`` from a forward rather than by saving
#: attachments.
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


# ---------------------------------------------------------------------------
# Authenticating against the blueprint
#
# Every module that drives an `/api/xras/*` route over HTTP needs the same four
# pieces, and they carry the same non-obvious constraint: a `make_api_credentials`
# row is **invisible** to a request, because routes read Flask-SQLAlchemy's
# `db.session` on a separate connection that only sees committed rows. So the DB-key
# loader is monkeypatched rather than seeded — the pattern `test_api_credentials_auth.py`
# established. Duplicating that reasoning per test module is how it drifts.
# ---------------------------------------------------------------------------

#: The password behind both fake keys. Any value; it only has to round-trip bcrypt.
XRAS_PW = 'xras-test-pw'


def basic_auth(username: str, password: str) -> str:
    """An HTTP Basic ``Authorization`` header value."""
    token = base64.b64encode(f'{username}:{password}'.encode()).decode('ascii')
    return f'Basic {token}'


def xras_auth(username: str = 'samuel') -> dict:
    """Headers authenticating as *username* against :func:`xras_keys`.

    Default holds ``ROLE_XRAS``; pass ``'nobody'`` for a valid credential that does
    not, which is the 403 path.
    """
    return {'Authorization': basic_auth(username, XRAS_PW)}


@pytest.fixture(autouse=True)
def reset_db_key_cache():
    """``_DB_KEY_CACHE`` is a process-global dict — wipe it around each test.

    Autouse, so importing it into a module is the whole of the wiring. Without it a
    key map leaks between tests in the same worker and auth assertions go
    order-dependent.
    """
    from webapp.utils import api_auth

    api_auth._DB_KEY_CACHE.update(at=None, map={})
    yield
    api_auth._DB_KEY_CACHE.update(at=None, map={})


@pytest.fixture
def xras_keys(monkeypatch):
    """Two DB-sourced keys: one holding ``ROLE_XRAS``, one holding something else."""
    from webapp.utils import api_auth

    hashed = bcrypt.hashpw(XRAS_PW.encode(), bcrypt.gensalt(rounds=4)).decode()
    monkeypatch.setattr(
        api_auth, '_get_db_api_keys',
        lambda: {
            'samuel': {'hash': hashed, 'roles': ['ROLE_XRAS']},
            'nobody': {'hash': hashed, 'roles': ['ROLE_SOMETHING']},
        },
    )


@pytest.fixture
def xras_client(client, xras_keys):
    """Unauthenticated test client with the XRAS key map installed.

    ⚠️ Import ``xras_keys`` alongside this one. pytest resolves a fixture's own
    dependencies by name in the *requesting* module's namespace, so importing
    ``xras_client`` alone raises ``fixture 'xras_keys' not found`` — at setup, not
    at import, so it surfaces as an error in every test rather than a bad import.
    """
    return client
