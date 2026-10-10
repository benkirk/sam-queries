"""API keys on the v1 REST reads: a key is admitted by permission alone (never by
membership), every write stays session-only, and an absent login is a JSON 401."""

import base64
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sam.security.permissions import Permission as P
from sam.security.rbac_catalog import GrantDef, RoleDef, build_catalog
from webapp.utils import api_auth, rbac

KEY = {'Authorization': 'Basic ' + base64.b64encode(b'collector:test-api-key').decode()}
BAD = {'Authorization': 'Basic ' + base64.b64encode(b'collector:wrong').decode()}


@pytest.fixture(autouse=True)
def _reset_caches():
    api_auth._DB_KEY_CACHE.update(at=None, map={})
    api_auth._VERIFY_CACHE.clear()
    yield
    api_auth._DB_KEY_CACHE.update(at=None, map={})
    api_auth._VERIFY_CACHE.clear()


@pytest.fixture
def allocation_id(active_project):
    return next(a.allocation_id for acct in active_project.accounts for a in acct.allocations)


@pytest.fixture
def routes(active_project, allocation_id):
    """(path, permission a key needs in db mode) for every read opened to keys."""
    p = active_project.projcode
    return [
        ('/api/v1/projects/', P.VIEW_PROJECTS),
        (f'/api/v1/projects/{p}', P.VIEW_PROJECTS),
        (f'/api/v1/projects/{p}/members', P.VIEW_PROJECT_MEMBERS),
        (f'/api/v1/projects/{p}/allocations', P.VIEW_ALLOCATIONS),
        ('/api/v1/projects/expiring', P.VIEW_ALLOCATIONS),
        ('/api/v1/projects/recently_expired', P.VIEW_ALLOCATIONS),
        (f'/api/v1/projects/{p}/charges', P.VIEW_ALLOCATIONS),
        (f'/api/v1/projects/{p}/charges/summary', P.VIEW_ALLOCATIONS),
        (f'/api/v1/allocations/{allocation_id}', P.VIEW_ALLOCATIONS),
    ]


STATUS_ROUTES = ['/api/v1/status/derecho/latest', '/api/v1/status/casper/latest',
                 '/api/v1/status/jupyterhub/latest', '/api/v1/status/outages',
                 '/api/v1/status/reservations']


def _db_mode(app, monkeypatch, *permissions):
    """RBAC_SOURCE=db with `collector` granted exactly ``permissions``."""
    def load():
        return build_catalog([RoleDef('r', frozenset(permissions))],
                             [GrantDef('apikey', 'collector', role='r')] if permissions else [],
                             source='db')
    monkeypatch.setitem(app.config, 'RBAC_SOURCE', 'db')
    monkeypatch.setattr(rbac, '_DB_CACHE', {'at': None, 'catalog': None})
    monkeypatch.setattr(rbac, '_load_db_catalog', load)


class TestDefaultsMode:
    def test_a_valid_key_reads_every_route(self, client, routes):
        for path, _ in routes:
            resp = client.get(path, headers=KEY)
            assert resp.status_code == 200, (path, resp.status_code)

    @pytest.mark.parametrize('path', STATUS_ROUTES)
    def test_status_reads_take_a_key(self, client, path):
        # The *latest* routes answer 404 until a collector has posted.
        assert client.get(path, headers=KEY).status_code in (200, 404)

    def test_a_bad_key_is_401(self, client, routes):
        for path, _ in routes:
            assert client.get(path, headers=BAD).status_code == 401, path

    def test_no_login_is_a_json_401(self, client, routes):
        for path, _ in routes + [(s, None) for s in STATUS_ROUTES]:
            resp = client.get(path)
            assert resp.status_code == 401 and resp.is_json, path


class TestDbMode:
    def test_each_route_needs_its_permission(self, app, client, routes, monkeypatch):
        for path, needed in routes:
            _db_mode(app, monkeypatch, *(set(P) - {needed, P.SYSTEM_ADMIN}))
            assert client.get(path, headers=KEY).status_code == 403, path
            _db_mode(app, monkeypatch, needed)
            assert client.get(path, headers=KEY).status_code == 200, path

    def test_a_key_with_no_grant_still_reads_status(self, app, client, monkeypatch):
        _db_mode(app, monkeypatch)
        assert client.get('/api/v1/status/outages', headers=KEY).status_code == 200
        assert client.get('/api/v1/projects/', headers=KEY).status_code == 403


class TestWritesStaySessionOnly:
    def test_put_allocation(self, client, allocation_id):
        resp = client.put(f'/api/v1/allocations/{allocation_id}', headers=KEY,
                          data={'amount': '1'})
        assert resp.status_code in (302, 401)

    def test_add_member(self, client, active_project):
        resp = client.post(f'/api/v1/projects/{active_project.projcode}/members',
                           headers=KEY, json={'username': 'benkirk'})
        assert resp.status_code in (302, 401)


def test_development_config_layers_env_keys():
    code = ("from webapp.config import DevelopmentConfig as C; "
            "assert C.API_KEYS['demo'] == 'h' and 'collector' in C.API_KEYS")
    env = {**os.environ, 'API_KEYS_DEMO': 'h',
           'PYTHONPATH': str(Path(__file__).resolve().parents[2] / 'src')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True)
