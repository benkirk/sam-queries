"""Four reasons an action parks, and one row that cannot tell them apart.

This is the file that produces schema evidence rather than a fix. Each scenario asserts
what the row **does** say, then asserts what an operator still cannot learn from it —
so the gap is a green test with a name, not a TODO.

``DispatchResult`` already carries ``service`` and ``reason``. ``dispatch.py`` argues
for them in its own docstring: *"knowing that an Extension parked because Extension was
disabled, rather than because nothing matched, is the difference between a two-minute
triage and a long one."* The route logs both. k8s app logs are ephemeral, so within days
the row is all that is left.
"""

import json

import pytest

from .conftest import auth_headers

pytestmark = pytest.mark.stress

PATH = '/api/xras/v1/actions'


def _post(client, payload):
    return client.post(PATH, data=json.dumps(payload),
                       content_type='application/json', headers=auth_headers())


def _action(action_type='Supplement', request_number='PARK0001', **extra):
    payload = {'actionType': action_type, 'requestNumber': request_number,
               'allocationType': 'Small', 'resources': [], 'roles': []}
    payload.update(extra)
    return payload


#: The columns an operator can actually filter on in the XRAS dashboard. Two parked
#: rows agreeing on all of these are indistinguishable in practice, not just in theory.
_TRIAGE_COLUMNS = ('status', 'action_type', 'request_number', 'http_status',
                   'error_messages', 'projcode_result')


def _triage_view(row):
    return {k: row[k] for k in _TRIAGE_COLUMNS}


def test_park_no_service(xras_client, action_log, dispatching, scenario):
    """``Adjustment`` against a project that does not exist — no selector matches."""
    import sam.xras.handlers  # noqa: F401

    resp = _post(xras_client, _action('Adjustment', 'NOSUCH01'))

    assert resp.status_code == scenario['http']
    row = action_log.one()
    assert row['status'] == scenario['expect']
    assert row['error_messages'] is None
    assert row['projcode_result'] is None
    assert row['processed_time'] is not None


def test_park_disabled_type(xras_client, action_log, dispatching, scenario, app):
    """The same shape, parked for a completely different reason."""
    import sam.xras.handlers  # noqa: F401

    app.config['XRAS_ACTIONS_ENABLED'] = 'Extension'
    resp = _post(xras_client, _action('Adjustment', 'NOSUCH01'))

    assert resp.status_code == scenario['http']
    row = action_log.one()
    assert row['status'] == scenario['expect']


def test_a_disabled_park_and_an_unmatched_park_are_byte_identical(
        xras_client, action_log, dispatching, app):
    """The evidence, stated as an equality rather than as prose.

    ⚠️ **This test passing is the problem.** It is green today and must go red the day
    a ``service`` / ``outcome_reason`` column lands — at which point it becomes the
    test that proves the column works. Leave the assertion inverted-by-design and
    update it then.
    """
    import sam.xras.handlers  # noqa: F401

    _post(xras_client, _action('Adjustment', 'NOSUCH01'))
    app.config['XRAS_ACTIONS_ENABLED'] = 'Extension'
    _post(xras_client, _action('Adjustment', 'NOSUCH01'))

    rows = action_log.rows()
    assert len(rows) == 2
    unmatched, disabled = _triage_view(rows[0]), _triage_view(rows[1])

    assert unmatched == disabled, (
        'if these ever differ, the row has gained a discriminator and this test '
        'should be rewritten to assert it')

    # Spelled out, because the equality above is easy to read as a happy result.
    assert unmatched['error_messages'] is None
    assert unmatched['status'] == 'manual'


def test_park_transfer_by_design(xras_client, action_log, dispatching, scenario,
                                 any_active_project):
    """Transfer parks deliberately — and is the one park with a discriminator.

    Two, in fact, and only one of them was there before C.1a: ``action_type`` is
    dedicated to Transfer, and ``projcode_result`` now names the project.
    """
    import sam.xras.handlers  # noqa: F401

    resp = _post(xras_client, _action('Transfer', any_active_project.projcode))

    assert resp.status_code == scenario['http']
    row = action_log.one()
    assert row['status'] == scenario['expect']

    # The triage query `transfer.py`'s own docstring promises.
    assert row['action_type'] == 'Transfer'
    # C.1a: the projcode reaches the row rather than only the app log.
    assert row['projcode_result'] == any_active_project.projcode

    # ⚠️ Still missing: `NOT_IMPLEMENTED_REASON` — the sentence written specifically
    # for whoever reads this row at 3am with no context — reaches the log and stops.
    assert row['error_messages'] is None


@pytest.fixture
def any_active_project(app):
    """A committed snapshot project.

    ⚠️ Route tests cannot use factories. The route reads Flask-SQLAlchemy's
    ``db.session`` on its own connection, which sees only **committed** rows, so a
    factory-made project is invisible to it and every dispatch would park as "no
    service matches" — silently turning a scenario into a different scenario.
    """
    from sqlalchemy.orm import Session

    from sam.projects.projects import Project
    from webapp.extensions import db

    with app.app_context(), Session(db.engine) as session:
        project = session.query(Project).filter(Project.is_active).first()
        assert project is not None, 'the snapshot has no active project'
        session.expunge(project)
        return project
