"""Shared setup for every Python example: the connection variables and two calls.

Standard library only (urllib), so any Python 3.8+ runs the examples unchanged.
An optional sam.env beside the tree (see sam.env.example) is read first.
"""

import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def _load_env_file():
    env_file = Path(__file__).resolve().parents[1] / 'sam.env'
    if not env_file.is_file():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line.startswith('export '):
            line = line[len('export '):]
        if line and not line.startswith('#') and '=' in line:
            name, _, value = line.partition('=')
            os.environ[name.strip()] = value.strip().strip('"').strip("'")


def _require(name, hint):
    value = os.environ.get(name)
    if not value:
        sys.exit(f'Set {name}: {hint} (see sam.env.example)')
    return value


_load_env_file()
BASE = _require('SAM_API_BASE', 'e.g. https://samuel.k8s.ucar.edu').rstrip('/')
USER = _require('SAM_API_USER', 'your API key name')
PASS = _require('SAM_API_PASS', 'your API key secret')


def credentials(family):
    """A family's own key (SAM_XRAS_USER / SAM_XRAS_PASS, ...), else the default one."""
    return (os.environ.get(f'SAM_{family}_USER') or USER,
            os.environ.get(f'SAM_{family}_PASS') or PASS)


class ApiError(Exception):
    def __init__(self, method, path, code, body):
        super().__init__(f'{method} {path} -> HTTP {code}: {body[:300]}')
        self.code, self.body = code, body


def request(method, path, params=None, *, auth=None, missing_ok=False):
    """Call SAM and return the decoded JSON; ``missing_ok`` turns a 404 into ``None``."""
    url = BASE + path
    if params:
        url += '?' + urllib.parse.urlencode(params)
    user, password = auth or (USER, PASS)
    token = base64.b64encode(f'{user}:{password}'.encode()).decode()
    req = urllib.request.Request(url, method=method, headers={
        'Authorization': f'Basic {token}', 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as err:
        if missing_ok and err.code == 404:
            return None
        raise ApiError(method, path, err.code, err.read().decode(errors='replace')) from None


def get(path, *, auth=None, missing_ok=False, **params):
    return request('GET', path, params, auth=auth, missing_ok=missing_ok)


def post(path, *, auth=None, **params):
    return request('POST', path, params, auth=auth)


def example(title):
    """A heading before each call, so the output reads in the same order as the script."""
    print(f'\n== {title}')


def show(value):
    print(json.dumps(value, indent=2))
