"""Tests for the /api/v1/fairshare endpoint (webapp/api/v1/fairshare.py).

The hpc-scheduling-tools plugin is not installed in the test image, so these
mock ``sam.plugins.HPC_SCHEDULING_TOOLS.load`` with a fake module (the house
convention) and stub ``get_fstree_data`` — proving the endpoint feeds the plugin
an in-process fetcher over SAM's DB rather than looping back through HTTP.

Coverage: auth (401 / 403 / token bypass), the disabled kill-switch (503),
unknown machine (400), bad options (400), empty resource (404), JSON + text
rendering, case-insensitive machine resolution, and that the injected fetch
actually calls get_fstree_data.
"""
from __future__ import annotations

import base64
import types

import bcrypt
import pytest


class FakeOptionError(ValueError):
    """Stands in for the plugin's OptionError (a ValueError, never SystemExit)."""


@pytest.fixture(autouse=True)
def _clear_cache():
    from webapp.extensions import cache
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def fake_plugin(monkeypatch):
    """A minimal stand-in for the loaded hpc_scheduling_tools package."""
    def normalize_options(rollup, equalize):
        if rollup not in ('none', 'all', 'facility', 'group'):
            raise FakeOptionError(f'--rollup: unknown value {rollup!r}')
        toks = {t for t in (equalize.split(',') if isinstance(equalize, str) else equalize) if t}
        if toks - {'none', 'leaf', 'facility'}:
            raise FakeOptionError('--equalize: unknown token(s)')
        toks.discard('none')
        return rollup, toks

    def build_tree(machine, rollup, equalize, fetch):
        # Exercise the injected in-process fetch (CPU + GPU resources), the way
        # the real build_tree does, so the get_fstree_data wiring is tested.
        payload = fetch(machine)
        fetch(machine + ' GPU')
        n = len(payload.get('facilities', []))
        return ([f'{machine.lower()} 2 root 100', f'NCAR 3 {machine.lower()} {n}'],
                [('rollup', 'U_X', 70.0, 16.62)])

    mod = types.SimpleNamespace(
        available_machines=lambda: ['Casper', 'Derecho', 'Gust'],
        normalize_options=normalize_options,
        OptionError=FakeOptionError,
        build_tree=build_tree,
        load_config=lambda m: ({'cpu_resource': m, 'gpu_resource': m + ' GPU',
                                'N_cpu': 100, 'N_gpu': 4}, 365, 100),
        format_warnings=lambda ws: [f'{s}: {name}' for (s, name, *_rest) in ws],
    )
    monkeypatch.setattr('sam.plugins.HPC_SCHEDULING_TOOLS.load', lambda: mod)
    return mod


@pytest.fixture
def enabled(app, monkeypatch):
    """Turn the feature on and stub the DB read; returns the recorded resources."""
    monkeypatch.setitem(app.config, 'HPC_SCHEDULING_TOOLS_ENABLED', True)
    resources = []

    def _stub(session, resource_name=None):
        resources.append(resource_name)
        return {'facilities': [{'name': 'NCAR'}]}

    monkeypatch.setattr('webapp.api.v1.fairshare.get_fstree_data', _stub)
    return resources


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def test_requires_authentication(client):
    assert client.get('/api/v1/fairshare/casper').status_code == 401


def test_session_without_permission_forbidden(non_admin_client, enabled, fake_plugin):
    assert non_admin_client.get('/api/v1/fairshare/casper').status_code == 403


def test_token_path_bypasses_permission(app, client, enabled, fake_plugin):
    original = app.config.get('API_KEYS')
    app.config['API_KEYS'] = {'svc': bcrypt.hashpw(b'pw', bcrypt.gensalt(rounds=4)).decode()}
    try:
        hdr = 'Basic ' + base64.b64encode(b'svc:pw').decode()
        resp = client.get('/api/v1/fairshare/casper', headers={'Authorization': hdr})
        assert resp.status_code == 200
    finally:
        app.config['API_KEYS'] = original


# ---------------------------------------------------------------------------
# Kill switch
# ---------------------------------------------------------------------------

def test_disabled_returns_503(auth_client, fake_plugin):
    # TestingConfig pins HPC_SCHEDULING_TOOLS_ENABLED=False (no `enabled` fixture).
    assert auth_client.get('/api/v1/fairshare/casper').status_code == 503


# ---------------------------------------------------------------------------
# Happy path + rendering
# ---------------------------------------------------------------------------

def test_json_envelope(auth_client, enabled, fake_plugin):
    resp = auth_client.get('/api/v1/fairshare/casper')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['machine'] == 'Casper'          # case normalized to the exact key
    assert data['rollup'] == 'none'
    assert data['equalize'] == []
    assert data['lines'][0] == 'casper 2 root 100'
    assert data['warnings'] == ['rollup: U_X']


def test_json_includes_loaded_capacities(auth_client, enabled, fake_plugin):
    data = auth_client.get('/api/v1/fairshare/casper').get_json()
    assert data['capacities'] == {
        'cpu_resource': 'Casper', 'gpu_resource': 'Casper GPU',
        'N_cpu': 100, 'N_gpu': 4, 'scale': 100, 'default_duration_days': 365,
    }


def test_format_text(auth_client, enabled, fake_plugin):
    resp = auth_client.get('/api/v1/fairshare/casper?format=text')
    assert resp.status_code == 200
    assert resp.mimetype == 'text/plain'
    body = resp.get_data(as_text=True)
    assert body.startswith('casper 2 root 100\n')
    assert 'N_cpu' not in body          # text is raw PBS lines only, no capacities


def test_rollup_and_equalize_passthrough(auth_client, enabled, fake_plugin):
    resp = auth_client.get('/api/v1/fairshare/derecho?rollup=facility&equalize=leaf')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['rollup'] == 'facility'
    assert data['equalize'] == ['leaf']


def test_injected_fetch_calls_get_fstree_data(auth_client, enabled, fake_plugin):
    auth_client.get('/api/v1/fairshare/casper')
    # In-process DB read (not an HTTP loopback); both resources fetched.
    assert 'Casper' in enabled and 'Casper GPU' in enabled


def test_case_insensitive_machine(auth_client, enabled, fake_plugin):
    resp = auth_client.get('/api/v1/fairshare/GUST')
    assert resp.status_code == 200
    assert resp.get_json()['machine'] == 'Gust'


# ---------------------------------------------------------------------------
# Validation / errors
# ---------------------------------------------------------------------------

def test_unknown_machine_400(auth_client, enabled, fake_plugin):
    assert auth_client.get('/api/v1/fairshare/nope').status_code == 400


def test_bad_rollup_400(auth_client, enabled, fake_plugin):
    assert auth_client.get('/api/v1/fairshare/casper?rollup=bogus').status_code == 400


def test_bad_format_400(auth_client, enabled, fake_plugin):
    assert auth_client.get('/api/v1/fairshare/casper?format=xml').status_code == 400


def test_empty_resource_404(auth_client, app, monkeypatch, fake_plugin):
    monkeypatch.setitem(app.config, 'HPC_SCHEDULING_TOOLS_ENABLED', True)
    monkeypatch.setattr('webapp.api.v1.fairshare.get_fstree_data',
                        lambda session, resource_name=None: {})
    assert auth_client.get('/api/v1/fairshare/casper').status_code == 404
