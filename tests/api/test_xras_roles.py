"""``POST /api/xras/v1/roles/{requestNumber}/{role}/{username}`` — the project-lead write.

Legacy endpoint #7, ported late: an audit of the deployed ``ROOT.war`` found it mapped in
Java and absent here. These tests pin the contract that port implements, which is
**deliberately not** what the deployed code does — see ``webapp/api/xras/roles.py`` for
why (in short: legacy's four-branch error ladder can never fire, so its real behavior is
400-for-everything carrying a leaked ``ValidationException:`` string).

House convention applies (CLAUDE.md § Testing): the HTTP tier covers auth, validation,
status codes and audit-row transitions. The happy-path *write* is asserted through a
patched ``Project.update`` rather than by inspecting the database — a route-level write
commits on ``db.session``'s own connection, outside the suite's per-test SAVEPOINT, and
would leak a changed ``project_lead_user_id`` into the shared xdist database.

WARNING: **Every test that reaches a route here takes ``action_log``, including the ones that
assert nothing about audit rows.** The fixture is not only a reader — it is the cleanup,
and the route commits its row on a private connection that the SAVEPOINT cannot reach.
Omitting it leaks a row per run into the shared database; this file did exactly that once
before the fixture was added to ``test_success_asks_for_the_right_write``.
"""

import pytest
from sqlalchemy import select

from xras_audit import action_log  # noqa: F401  — shared with tests/stress/
from xras_helpers import (  # noqa: F401  — pytest resolves fixtures by name
    reset_db_key_cache,
    xras_auth as _auth,
    xras_client,
    xras_keys,
)

from sam.core.users import User
from sam.projects.projects import Project


def _path(request_number='ABC1234', role='pi', username='someone'):
    return f'/api/xras/v1/roles/{request_number}/{role}/{username}'


@pytest.fixture
def capture_off(app):
    """Open the interlock for one test, and always close it again.

    ``XRAS_ACTIONS_CAPTURE_ONLY`` defaults on, and this endpoint honors it for the same
    reason ``POST /actions`` does: while legacy is still the system of record, applying
    the change here would fight it.
    """
    app.config['XRAS_ACTIONS_CAPTURE_ONLY'] = False
    yield app
    app.config['XRAS_ACTIONS_CAPTURE_ONLY'] = True


@pytest.fixture
def no_write(monkeypatch):
    """Record calls to ``Project.update`` instead of performing them.

    The assertion target is "the route asked for the right write", not "MySQL applied
    it" — the latter is a model-layer concern and is already covered there (the XRAS
    update handler drives the same method). Patching also keeps
    ``management_transaction``'s ``COMMIT`` a no-op, since nothing ends up dirty.
    """
    calls = []

    def _recording_update(self, **kwargs):
        calls.append((self.projcode, kwargs))
        return self

    monkeypatch.setattr(Project, 'update', _recording_update)
    return calls


@pytest.fixture
def inactive_project(session):
    """Any committed snapshot project with ``active = 0`` — Layer-1, any row of the shape."""
    row = session.execute(
        select(Project).where(~Project.is_active).limit(1)).scalars().first()
    if row is None:                                  # pragma: no cover - data-dependent
        pytest.skip('snapshot holds no inactive project')
    return row


@pytest.fixture
def inactive_user(session):
    """Any committed snapshot user that is not active."""
    row = session.execute(
        select(User).where(~User.is_active).limit(1)).scalars().first()
    if row is None:                                  # pragma: no cover - data-dependent
        pytest.skip('snapshot holds no inactive user')
    return row


# ---------------------------------------------------------------------------
# Auth — the blueprint's rules, not this route's
# ---------------------------------------------------------------------------

class TestAuth:

    def test_unauthenticated_gets_the_41_byte_body(self, xras_client, action_log):
        resp = xras_client.post(_path())
        assert resp.status_code == 401
        assert len(resp.data) == 41
        assert action_log.rows() == [], 'an unauthenticated call must not mint a row'

    def test_a_credential_without_role_xras_is_denied(self, xras_client, action_log):
        resp = xras_client.post(_path(), headers=_auth('nobody'))
        assert resp.status_code == 403
        assert action_log.rows() == []


# ---------------------------------------------------------------------------
# The role segment — checked first, before anything is looked up
# ---------------------------------------------------------------------------

class TestRoleSegment:

    @pytest.mark.parametrize('role', ['pi', 'PI', 'Pi', 'pI'])
    def test_every_casing_of_pi_is_accepted(self, xras_client, action_log, role):
        """Legacy uses ``equalsIgnoreCase``. Capture-only, so this stops at the interlock."""
        resp = xras_client.post(_path(role=role), headers=_auth())
        assert resp.status_code == 200
        assert action_log.one()['status'] == 'received'

    def test_any_other_role_is_404_with_the_callers_casing(self, xras_client, action_log):
        """Legacy's ``String.format("role %s does not exist", role)`` echoes the input."""
        resp = xras_client.post(_path(role='CoPI'), headers=_auth())
        assert resp.status_code == 404
        assert resp.data == b'{"message":"role CoPI does not exist","result":null}'

        row = action_log.one()
        assert row['status'] == 'failed'
        assert row['http_status'] == 404
        assert row['outcome_reason'] == 'role CoPI does not exist'

    def test_the_role_check_precedes_the_project_lookup(
            self, xras_client, action_log, capture_off):
        """A bad role against a nonexistent project is 404-*role*, not 404-*project*.

        Legacy's controller rejects the role before calling the service at all, so the
        two failures are not interchangeable. Runs with the interlock **open**, which is
        the only configuration where the project lookup could otherwise win.
        """
        resp = xras_client.post(
            _path(request_number='NOSUCHPROJ', role='chef'), headers=_auth())
        assert resp.status_code == 404
        assert resp.data == b'{"message":"role chef does not exist","result":null}'


# ---------------------------------------------------------------------------
# The capture-only interlock
# ---------------------------------------------------------------------------

class TestCaptureOnly:

    def test_capture_on_records_and_does_not_write(
            self, xras_client, action_log, no_write, active_project):
        """The default. XRAS sees success; SAM changes nothing and says so in the row.

        This is the case that would have been a live bug without the gate: at the repoint
        every action is captured-not-applied, and a naive port of this endpoint would have
        been the one XRAS write that applied immediately.
        """
        resp = xras_client.post(
            _path(request_number=active_project.projcode, username='anyone'),
            headers=_auth())

        assert resp.status_code == 200
        assert resp.data == b''
        assert no_write == [], 'capture-only must not reach the write'

        row = action_log.one()
        assert row['status'] == 'received'
        assert row['action_type'] == 'RoleChange'
        assert row['request_number'] == active_project.projcode

    def test_capture_on_skips_validation_too(self, xras_client, action_log, no_write):
        """A nonexistent project still answers 200 while the interlock is closed.

        Deliberate: the lookup is this route's equivalent of dispatch, and dispatch is
        what the interlock suppresses. Only the role check — its equivalent of schema
        validation — runs.
        """
        resp = xras_client.post(_path(request_number='NOSUCHPROJ'), headers=_auth())
        assert resp.status_code == 200
        assert action_log.one()['status'] == 'received'


# ---------------------------------------------------------------------------
# The error ladder, with the interlock open
# ---------------------------------------------------------------------------

class TestLadder:

    def test_missing_project_is_404(self, xras_client, action_log, capture_off, no_write):
        resp = xras_client.post(_path(request_number='NOSUCHPROJ'), headers=_auth())
        assert resp.status_code == 404
        assert resp.data == b'{"message":"non-existent project","result":null}'
        assert action_log.one()['outcome_reason'] == 'non-existent project'

    def test_inactive_project_is_409(
            self, xras_client, action_log, capture_off, no_write, inactive_project):
        """409, not legacy's intended 403.

        403 is an authorization verdict about the *caller*; on an endpoint behind Basic
        auth it is indistinguishable from a bad API key, which is the wrong first
        instinct during triage. The project exists and its state refuses the write —
        that is Conflict.
        """
        resp = xras_client.post(
            _path(request_number=inactive_project.projcode), headers=_auth())
        assert resp.status_code == 409
        assert resp.data == b'{"message":"inactive project","result":null}'
        assert action_log.one()['http_status'] == 409

    def test_missing_user_is_404(
            self, xras_client, action_log, capture_off, no_write, active_project):
        resp = xras_client.post(
            _path(request_number=active_project.projcode, username='nosuchuser'),
            headers=_auth())
        assert resp.status_code == 404
        assert resp.data == b'{"message":"non-existent user","result":null}'

    def test_inactive_user_is_409(
            self, xras_client, action_log, capture_off, no_write,
            active_project, inactive_user):
        resp = xras_client.post(
            _path(request_number=active_project.projcode,
                  username=inactive_user.username),
            headers=_auth())
        assert resp.status_code == 409
        assert resp.data == b'{"message":"inactive user","result":null}'

    def test_the_project_is_checked_before_the_user(
            self, xras_client, action_log, capture_off, no_write):
        """Both bad — legacy looks the project up first, so that is the answer."""
        resp = xras_client.post(
            _path(request_number='NOSUCHPROJ', username='nosuchuser'), headers=_auth())
        assert resp.data == b'{"message":"non-existent project","result":null}'


# ---------------------------------------------------------------------------
# Success
# ---------------------------------------------------------------------------

class TestSuccess:

    def test_success_is_an_empty_200_with_no_content_type(
            self, xras_client, action_log, capture_off, no_write,
            active_project, multi_project_user):
        """``createOkResponse()`` is ``new ResponseEntity(HttpStatus.OK)``.

        No body, and no ``Content-Type`` — Spring never invokes a message converter, so
        no header is negotiated. Flask would supply ``text/html; charset=utf-8`` on its
        own, which is why :func:`serialize.empty_ok` pops it.
        """
        resp = xras_client.post(
            _path(request_number=active_project.projcode,
                  username=multi_project_user.username),
            headers=_auth())

        assert resp.status_code == 200
        assert resp.data == b''
        assert 'Content-Type' not in resp.headers

    def test_success_asks_for_the_right_write(
            self, xras_client, action_log, no_write, capture_off,
            active_project, multi_project_user):
        """Exactly ``project_lead_user_id`` and nothing else — legacy's ``transact()``
        sets the lead and the modified time, no roster insert and no allocation touch.
        ``modified_time`` comes from ``TimestampMixin``'s ``onupdate``.
        """
        xras_client.post(
            _path(request_number=active_project.projcode,
                  username=multi_project_user.username),
            headers=_auth())

        assert no_write == [
            (active_project.projcode,
             {'project_lead_user_id': multi_project_user.user_id}),
        ]

    def test_success_closes_the_audit_row_as_processed(
            self, xras_client, action_log, no_write, capture_off,
            active_project, multi_project_user):
        xras_client.post(
            _path(request_number=active_project.projcode,
                  username=multi_project_user.username),
            headers=_auth())

        row = action_log.one()
        assert row['status'] == 'processed'
        assert row['http_status'] == 200
        assert row['projcode_result'] == active_project.projcode
        assert row['processed_time'] is not None
        # Not dispatched through the registry, so it claims no service.
        assert row['service'] is None
