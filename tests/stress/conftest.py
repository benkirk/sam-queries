"""Fixtures for the XRAS stress tier.

Everything here drives the **HTTP route**, not ``dispatch_action``, because the audit
row is the thing under test and it is written by ``_record`` / ``_finish`` on their own
connection — outside the suite's per-test SAVEPOINT. That is also why every test needs
``action_log``: those rows are already committed and must be deleted explicitly.

Mirrors ``tests/perf/conftest.py``: a JSON manifest read once at import, exposed through
a fixture, with the same "declare the expectation next to the number" convention.
"""

import json
from pathlib import Path

import pytest

from xras_audit import action_log  # noqa: F401  — shared with tests/api/
from xras_helpers import (  # noqa: F401  — pytest resolves fixtures by name
    reset_db_key_cache,
    xras_auth as auth_headers,
    xras_client,
    xras_keys,          # `xras_client` requests it; importing one without the
)                       # other fails at setup, not at import




_SCENARIOS_PATH = Path(__file__).parent / 'scenarios.json'

#: The one path every route-driven scenario posts to. Defined here rather than
#: re-declared per module, which is how it came to be spelled three times.
ACTIONS_PATH = '/api/xras/v1/actions'


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
# Posting
# ---------------------------------------------------------------------------

def post_action(client, payload):
    """POST one action payload. Was defined byte-for-byte twice, in two modules."""
    return client.post(ACTIONS_PATH, data=json.dumps(payload),
                       content_type='application/json', headers=auth_headers())


@pytest.fixture
def snapshot_project(app):
    """A committed snapshot project.

    WARNING: Route tests cannot use factories. The route reads Flask-SQLAlchemy's
    ``db.session`` on its own connection, which sees only **committed** rows, so a
    factory-made project is invisible to it and every dispatch would park as "no
    service matches" — silently turning a scenario into a different scenario.

    Was two fixtures with identical bodies under two names, one per module —
    ``snapshot_project`` and ``any_active_project``. One name now.
    """
    from sqlalchemy.orm import Session

    from sam.projects.projects import Project
    from webapp.extensions import db

    with app.app_context(), Session(db.engine) as session:
        project = session.query(Project).filter(Project.is_active).first()
        assert project is not None, 'the snapshot has no active project'
        session.expunge(project)
        return project


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
def committing_route(monkeypatch):
    """Neutralize the handler commit for scenarios that let a real handler run.

    WARNING: Route-driven, so this is **not** the same hazard the unit tier's ``committing``
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
