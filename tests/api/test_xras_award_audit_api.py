"""``GET /api/v1/xras/award-audit/<projcode>``: auth, 404, 503, and one audited project.

The route reads through Flask-SQLAlchemy's ``db.session``, so the happy path picks a
committed snapshot allocation on an XRAS-mapped resource. XRAS is a real client whose
``_get`` is patched on the instance; nothing leaves the process.
"""
import base64
import dataclasses
from datetime import datetime
from unittest.mock import patch

import bcrypt
import pytest

from sam.integration.xras_api import XrasApiClient, XrasSourceUnavailable
from sam.integration.xras_api.config import XrasApiConfig
from sam.queries.xras_award_audit import AuditRow, AwardAudit
from sam.schemas.xras_award_audit import AwardAuditRowSchema, AwardAuditSchema
from sam.security.permissions import Permission
from sam.security.rbac_catalog import GrantDef, RoleDef, build_catalog
from webapp.utils import api_auth, rbac

URL = '/api/v1/xras/award-audit/{}'
RID = 4242


def _basic(username, password):
    return {'Authorization': 'Basic ' + base64.b64encode(f'{username}:{password}'.encode()).decode()}


def _client(family=(), catalog=None, fail=False):
    """A configured XRAS read client answering from memory."""
    client = XrasApiClient(XrasApiConfig(enabled=True, api_key='test-only'))

    def fake_get(path, **_):
        if fail:
            raise XrasSourceUnavailable('test: XRAS down')
        if path == '/v1/resources':
            return catalog
        if path.startswith('/v1/reports/request_numbers/'):
            return list(family)
        raise AssertionError(f'unexpected XRAS read {path}')

    client._get = fake_get
    return client


@pytest.fixture
def no_catalog_cache(monkeypatch):
    """Keep the fake catalog out of the process-wide day-long bucket."""
    from sam.integration.xras_api import cache
    monkeypatch.setattr(cache, 'cached_resources', lambda compute: compute())


def _patched(client):
    return patch('webapp.api.v1.xras_award_audit.read_client', return_value=client)


class TestAuth:

    def test_no_credentials_is_401(self, client, active_project):
        assert client.get(URL.format(active_project.projcode)).status_code == 401

    def test_a_session_without_the_permission_is_403(self, non_admin_client, active_project):
        assert non_admin_client.get(URL.format(active_project.projcode)).status_code == 403


class TestDbMode:

    @pytest.fixture
    def keys(self, app, monkeypatch):
        h = bcrypt.hashpw(b'pw', bcrypt.gensalt(rounds=4)).decode()
        monkeypatch.setitem(app.config, 'API_KEYS', {'heuv': h, 'ssg': h})
        monkeypatch.setitem(app.config, 'RBAC_SOURCE', 'db')
        monkeypatch.setitem(app.config, 'RBAC_DB_TTL', 60)
        monkeypatch.setattr(rbac, '_DB_CACHE', {'at': None, 'catalog': None})
        monkeypatch.setattr(rbac, '_load_db_catalog', lambda: build_catalog(
            [RoleDef('api_systems_integration', frozenset({Permission.VIEW_PROJECTS}))],
            [GrantDef('apikey', 'ssg', role='api_systems_integration'),
             GrantDef('apikey', 'heuv', role='api_systems_integration'),
             GrantDef('apikey', 'heuv', permission=Permission.VIEW_XRAS_AWARD_AUDIT)],
            source='db'))
        api_auth._DB_KEY_CACHE.update(at=None, map={})
        api_auth._VERIFY_CACHE.clear()
        yield
        api_auth._DB_KEY_CACHE.update(at=None, map={})
        api_auth._VERIFY_CACHE.clear()

    def test_a_key_without_the_permission_is_403(self, client, keys, active_project):
        resp = client.get(URL.format(active_project.projcode), headers=_basic('ssg', 'pw'))
        assert resp.status_code == 403

    def test_the_ungranted_key_cannot_probe_projcodes(self, client, keys):
        assert client.get(URL.format('NOPE9999'), headers=_basic('ssg', 'pw')).status_code == 403

    def test_the_granted_key_reaches_the_route(self, client, keys, active_project):
        resp = client.get(URL.format(active_project.projcode), headers=_basic('heuv', 'pw'))
        assert resp.status_code == 503   # admitted; XRAS is unconfigured under test


class TestNotFoundAndUnavailable:

    def test_unknown_projcode_is_404(self, auth_client):
        assert auth_client.get(URL.format('NOPE9999')).status_code == 404

    def test_unconfigured_xras_is_503_with_a_reason(self, auth_client, active_project):
        resp = auth_client.get(URL.format(active_project.projcode))
        assert resp.status_code == 503
        assert resp.get_json()['reason'] == 'not_configured'

    def test_unreachable_xras_is_503(self, auth_client, active_project, no_catalog_cache):
        with _patched(_client(fail=True)):
            resp = auth_client.get(URL.format(active_project.projcode))
        assert resp.status_code == 503
        assert resp.get_json() == {'error': 'XRAS did not answer', 'reason': 'unreachable'}

    def test_an_empty_catalog_is_503_not_every_award_unmapped(self, auth_client, active_project,
                                                              no_catalog_cache):
        with _patched(_client(catalog=[])):
            resp = auth_client.get(URL.format(active_project.projcode))
        assert resp.status_code == 503 and resp.get_json()['reason'] == 'no_resource_catalog'


@pytest.fixture
def mapped_allocation(session):
    """A live snapshot allocation on an active, XRAS-mapped resource of an active project."""
    from sam.accounting.accounts import Account
    from sam.accounting.allocations import Allocation
    from sam.integration.xras import XrasResourceRepositoryKeyResource as KeyMap
    from sam.projects.projects import Project
    from sam.resources.resources import Resource
    now = datetime.now()
    row = (session.query(Allocation, KeyMap.resource_repository_key)
           .join(Account, Account.account_id == Allocation.account_id)
           .join(Project, Project.project_id == Account.project_id)
           .join(Resource, Resource.resource_id == Account.resource_id)
           .join(KeyMap, KeyMap.resource_id == Resource.resource_id)
           .filter(Project.is_active, Resource.is_active, Allocation.is_active,
                   Allocation.deleted.is_(False), Allocation.end_date.isnot(None),
                   Allocation.start_date >= Resource.commission_date, Allocation.start_date <= now)
           .order_by(Allocation.allocation_id).first())
    if row is None:
        pytest.skip('snapshot has no live allocation on an XRAS-mapped resource')
    return row


class TestHappyPath:

    def test_a_matching_award_has_no_mismatch(self, auth_client, mapped_allocation,
                                              no_catalog_cache):
        alloc, key = mapped_allocation
        projcode = alloc.account.project.projcode
        family = [{'requestId': 1, 'requestNumber': projcode, 'actions': [{
            'actionId': 1, 'actionType': 'New', 'actionStatus': 'Approved',
            'allocationDates': [{'allocationDateType': 'Approved',
                                 'beginDate': alloc.start_date.date().isoformat(),
                                 'endDate': alloc.end_date.date().isoformat()}],
            'resources': [{'resourceId': RID, 'resourceName': 'Mapped', 'type': 'Approved',
                           'amount': alloc.amount, 'resourceUnits': 'Core-hours'}]}]}]
        catalog = [{'resourceId': RID, 'resourceRepositoryKey': key}]
        with _patched(_client(family, catalog)):
            resp = auth_client.get(URL.format(projcode))
        assert resp.status_code == 200, resp.get_json()
        body = resp.get_json()
        assert (body['kind'], body['projcode'], body['complete']) == ('xras_award_audit',
                                                                      projcode, True)
        [row] = [r for r in body['rows'] if r['xras_resource']]
        assert row['allocation_id'] == alloc.allocation_id
        assert row['sam_amount'] == pytest.approx(alloc.amount)
        assert not {'amount_differs', 'allocation_missing', 'end_differs'} & set(row['issues'])
        assert body['counts']['amount_differs'] == 0


class TestContract:

    def test_the_schema_declares_every_envelope_key(self):
        audit = AwardAudit(rows=[], today=datetime.now().date())
        assert set(AwardAuditSchema().fields) == set(audit.to_dict()) | {'projcode'}

    def test_the_row_schema_declares_every_row_key(self):
        row = AuditRow(projcode='X', xras_resource=None, sam_resource=None, issues=[])
        assert set(AwardAuditRowSchema().fields) == {f.name for f in dataclasses.fields(row)}
        assert set(AwardAuditRowSchema().fields) == set(row.to_dict())
