"""Fixtures for the XRAS stress tier.

Everything here drives the **HTTP route**, not ``dispatch_action``, because the audit
row is the thing under test and it is written by ``_record`` / ``_finish`` on their own
connection — outside the suite's per-test SAVEPOINT. That is also why every test needs
``action_log``: those rows are already committed and must be deleted explicitly.

Mirrors ``tests/perf/conftest.py``: a JSON manifest read once at import, exposed through
a fixture, with the same "declare the expectation next to the number" convention.
"""

import base64
import json
from pathlib import Path

import bcrypt
import pytest

from xras_audit import action_log  # noqa: F401  — shared with tests/api/
from webapp.utils import api_auth

_SCENARIOS_PATH = Path(__file__).parent / 'scenarios.json'

XRAS_PW = 'xras-stress-pw'


def _load_scenarios():
    with open(_SCENARIOS_PATH) as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith('_')}


SCENARIOS = _load_scenarios()


@pytest.fixture
def scenario(request):
    """The manifest entry for the calling test, keyed on its name.

    ``test_oversize_error_messages`` reads ``scenarios.json``'s
    ``oversize_error_messages``, exactly as the perf tier maps test names to baselines.
    A test with no entry fails loudly rather than silently running unspecified.
    """
    name = request.node.name.split('[')[0].removeprefix('test_')
    assert name in SCENARIOS, (
        f'{name!r} has no entry in tests/stress/scenarios.json. Every stress '
        f'scenario declares what the audit row must say before it runs.')
    return SCENARIOS[name]


# ---------------------------------------------------------------------------
# Auth — same shape as tests/api/test_xras_access.py, which is the precedent.
# A `make_api_credentials` row is invisible to an HTTP request (routes read
# db.session on a separate connection), so the DB-key loader is patched instead.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_db_key_cache():
    api_auth._DB_KEY_CACHE.update(at=None, map={})
    yield
    api_auth._DB_KEY_CACHE.update(at=None, map={})


@pytest.fixture
def xras_client(client, monkeypatch):
    hashed = bcrypt.hashpw(XRAS_PW.encode(), bcrypt.gensalt(rounds=4)).decode()
    monkeypatch.setattr(
        api_auth, '_get_db_api_keys',
        lambda: {'samuel': {'hash': hashed, 'roles': ['ROLE_XRAS']}})
    return client


def auth_headers(username='samuel'):
    token = base64.b64encode(f'{username}:{XRAS_PW}'.encode()).decode('ascii')
    return {'Authorization': f'Basic {token}'}


@pytest.fixture
def dispatching(app):
    """Capture off, with a clean handler registry restored afterwards."""
    from sam.xras import dispatch

    saved = dict(dispatch._HANDLERS)
    app.config['XRAS_ACTIONS_CAPTURE_ONLY'] = False
    try:
        yield dispatch
    finally:
        app.config['XRAS_ACTIONS_CAPTURE_ONLY'] = True
        app.config.pop('XRAS_ACTIONS_ENABLED', None)
        dispatch._HANDLERS.clear()
        dispatch._HANDLERS.update(saved)


@pytest.fixture
def no_handlers(dispatching):
    """Capture off **and** an empty registry — the 'nothing is registered' park."""
    dispatching._HANDLERS.clear()
    return dispatching


@pytest.fixture
def committing_route(monkeypatch):
    """Neutralise the handler commit for scenarios that let a real handler run.

    ⚠️ Route-driven, so this is **not** the same hazard the unit tier's ``committing``
    fixture handles. Here the handler runs on Flask-SQLAlchemy's ``db.session``, a
    different connection from the test's, and a real ``COMMIT`` there writes rows no
    SAVEPOINT will ever roll back. The fixtures that need real writes clean up after
    themselves; the ones that do not use this.

    One patch point, because C.1a collapsed five import sites to one — see
    ``tests/unit/test_xras_transaction_seam.py``, which keeps it that way.
    """
    from contextlib import contextmanager

    import sam.xras.handlers.base as base

    @contextmanager
    def flushing(sess):
        yield sess
        sess.flush()

    monkeypatch.setattr(base, 'management_transaction', flushing)
