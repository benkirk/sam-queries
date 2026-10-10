"""HTTP contract of the HEUV API: auth, the 400 envelope, the bare 404, key order on snapshot rows.

Exact bytes per rule are pinned at the query/schema layer (``tests/unit/queries/test_heuv_*``):
routes read ``db.session`` and see committed snapshot rows only (CLAUDE.md § Testing).
"""

import json

import pytest

from xras_helpers import basic_auth, reset_db_key_cache, role_keys  # noqa: F401
from webapp.limiter import limiter as facade

PREFIX = '/api/protected/heuv/v1'
PW = 'heuv-test-pw'


@pytest.fixture
def heuv_client(client, monkeypatch):
    role_keys(monkeypatch, {'heuv': ['ROLE_API_HEUV'], 'nobody': ['ROLE_XRAS']}, PW)
    return client


def _get(client, rule, user='heuv'):
    return client.get(f'{PREFIX}/{rule}', headers={'Authorization': basic_auth(user, PW)})


# --- auth ------------------------------------------------------------------

class TestAuth:

    def test_no_key_is_a_realm_challenge(self, heuv_client):
        resp = heuv_client.get(f'{PREFIX}/access')
        assert resp.status_code == 401
        assert resp.headers['WWW-Authenticate'] == 'Basic realm="Realm"'

    def test_key_without_the_role_is_forbidden(self, heuv_client):
        resp = _get(heuv_client, 'access', user='nobody')
        assert resp.status_code == 403 and 'errorMessage' in resp.get_json()

    def test_browser_session_is_not_enough(self, auth_client, monkeypatch):
        role_keys(monkeypatch, {'heuv': ['ROLE_API_HEUV']}, PW)
        assert auth_client.get(f'{PREFIX}/access').status_code == 401

    def test_heuv_key_is_served(self, heuv_client):
        resp = _get(heuv_client, 'access')
        assert resp.status_code == 200 and resp.mimetype == 'application/json'


def test_legacy_prefix_belongs_to_one_blueprint(app):
    owners = {rule.endpoint.split('.')[0] for rule in app.url_map.iter_rules()
              if rule.rule.startswith(PREFIX + '/')}
    assert owners == {'api_heuv'}


def test_writes_are_not_ported(heuv_client, multi_project_user):
    resp = heuv_client.put(f'{PREFIX}/user/{multi_project_user.username}/access',
                           headers={'Authorization': basic_auth('heuv', PW)}, json=[])
    assert resp.status_code == 405


# --- 400 / 404 envelopes ------------------------------------------------------

@pytest.mark.parametrize('rule, message', [
    ('user/nosuchuserxx/group', 'getUserGroups.username: Username nosuchuserxx does not exist.'),
    ('user/nosuchuserxx/wallclockexemption',
     'getUserWallclockExemptions.username: Username nosuchuserxx does not exist.'),
    ('report/project/NOSU9999', 'getReportProject.projcode: Project NOSU9999 does not exist.'),
    ('report/usage/project/nosu9999', 'getProjectUsageReport.projcode: Project nosu9999 does not exist.'),
    ('project/NOSU9999/hierarchy', 'getHierarchyOfProject.projcode: Project NOSU9999 does not exist.'),
    ('access/resource/Nosuch', 'getAccessibleResource.resourceName: Resource Nosuch does not exist.'),
])
def test_unknown_values_are_400_with_legacy_text(heuv_client, rule, message):
    resp = _get(heuv_client, rule)
    assert resp.status_code == 400
    assert resp.data == json.dumps({'errorMessage': message}, separators=(',', ':')).encode()


def test_unknown_resourcename_param_is_400(heuv_client, multi_project_user):
    resp = _get(heuv_client, f'user/{multi_project_user.username}/assignedproject?resourcename=Nosuch')
    assert resp.get_json() == {'errorMessage': 'getUserAssignmentByProject.resourceName: '
                                               'Resource Nosuch does not exist.'}


def test_inactive_project_usage_is_400(heuv_client, session):
    from sam.projects.projects import Project
    project = session.query(Project).filter(~Project.is_active).first()
    resp = _get(heuv_client, f'report/usage/project/{project.projcode}')
    assert resp.get_json() == {'errorMessage': f'getProjectUsageReport.projcode: Project {project.projcode} '
                                               'is not active.'}


def test_report_date_is_rejected(heuv_client, active_project):
    resp = _get(heuv_client, f'report/usage/project/{active_project.projcode}?reportDate=2026-01-01')
    assert resp.get_json() == {'errorMessage': 'getProjectUsageReport.reportDate: reportDate is not supported.'}


def test_resource_that_is_not_a_branch_is_a_bare_404(heuv_client, hpc_resource):
    resp = _get(heuv_client, f'access/resource/{hpc_resource.resource_name}')
    if resp.status_code == 200:
        pytest.skip('snapshot names an access branch after this resource')
    assert resp.status_code == 404 and resp.data == b'' and 'Content-Type' not in resp.headers


def test_crash_is_a_fixed_500(heuv_client, monkeypatch):
    from sam.queries import heuv as q
    monkeypatch.setattr(q, 'accessible_resources', lambda *a, **k: 1 / 0)
    resp = _get(heuv_client, 'access')
    assert resp.status_code == 500 and resp.get_json() == {'errorMessage': 'Internal server error.'}


# --- 200 bodies on snapshot rows ------------------------------------------------

USER_ROUTES = {
    'defaultproject': None, 'group': ['username', 'groupName', 'unixGid', 'primary', 'project', 'projcode'],
    'access': None, 'assignedproject?thresholdlimited=false': ['projcode', 'primary', 'title', 'resourceAssignments'],
    'assignedresource': ['resourceName', 'projectAssignments'], 'userrolelogin': None,
}


@pytest.mark.parametrize('route, keys', USER_ROUTES.items())
def test_user_routes_answer_in_legacy_key_order(heuv_client, multi_project_user, route, keys):
    resp = _get(heuv_client, f'user/{multi_project_user.username.upper()}/{route}')
    assert resp.status_code == 200
    body = resp.get_json()
    assert isinstance(body, list)
    if keys and body:
        assert list(json.loads(resp.data)[0]) == keys


def test_wallclock_echoes_the_username_as_sent(heuv_client, multi_project_user):
    sent = multi_project_user.username.upper()
    assert _get(heuv_client, f'user/{sent}/wallclockexemption').get_json()['username'] == sent


def test_report_routes_answer(heuv_client, active_project):
    pc = active_project.projcode.lower()
    report = _get(heuv_client, f'report/project/{pc}')
    assert report.status_code == 200 and report.get_json()['projcode'] == active_project.projcode
    usage = _get(heuv_client, f'report/usage/project/{pc}')
    assert usage.status_code == 200 and list(json.loads(usage.data)) == ['projcode', 'reportDate', 'accountReports']
    assert _get(heuv_client, f'project/{pc}/hierarchy').status_code == 200


def test_search_never_400s(heuv_client):
    assert _get(heuv_client, 'search/projcode?fragment=zzzz').data == b'[]'
    assert _get(heuv_client, 'search/projcode?fragment=a&username=nosuchuserxx').data == b'[]'


def test_blueprint_is_rate_limited(heuv_client, app):
    """Unlike ldapsync, HEUV rides the authed limit. Legacy never sent a 429, so the app-wide body stands."""
    with app.app_context():
        facade.limiter.enabled = True
        app.config['RATELIMIT_ENABLED'] = True
        old = app.config['RATELIMIT_AUTHED']
        app.config['RATELIMIT_AUTHED'] = '2 per minute'
        try:
            codes = [_get(heuv_client, 'search/projcode?fragment=zzzz').status_code for _ in range(4)]
            limited = _get(heuv_client, 'search/projcode?fragment=zzzz')
        finally:
            facade.limiter.enabled = False
            app.config['RATELIMIT_ENABLED'] = False
            app.config['RATELIMIT_AUTHED'] = old
    assert codes[:2] == [200, 200] and 429 in codes
    assert limited.status_code == 429 and limited.get_json()['error'] == 'rate_limit_exceeded'
