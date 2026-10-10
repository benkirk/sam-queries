"""HTTP client wrappers for the legacy SAM API and the new samuel.k8s API.

Both endpoints use HTTP Basic Auth — the legacy Java endpoints check
credentials against their own user store; the new endpoints route through
`webapp.utils.api_auth.login_or_token_required`, which validates Basic Auth
against bcrypt-hashed `API_KEYS`.
"""

from __future__ import annotations

import requests
from urllib.parse import quote


class _BaseClient:
    """Shared session/auth/timeout machinery."""

    def __init__(self, base_url: str, auth: tuple[str, str], timeout: int = 120):
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self._session = requests.Session()
        self._session.auth = auth
        self._session.headers['Accept'] = 'application/json'

    def _get(self, path: str, *, allow: tuple[int, ...] = (200,)):
        """Parsed JSON on 200; None on another status in *allow*; raise otherwise."""
        url = f'{self.base_url}{path}'
        resp = self._session.get(url, timeout=self.timeout)
        if resp.status_code != 200 and resp.status_code in allow:
            return None
        if resp.status_code != 200:
            raise RuntimeError(
                f'GET {url} returned HTTP {resp.status_code}: {resp.text[:200]}'
            )
        return resp.json()


class LegacyClient(_BaseClient):
    """Client for sam.ucar.edu legacy Java endpoints."""

    def directory_access(self) -> dict:
        return self._get('/api/protected/admin/sysacct/directoryaccess')

    def group_status(self, branch: str) -> list:
        return self._get(f'/api/protected/admin/sysacct/groupstatus/{branch}')

    def fstree(self, resource: str) -> dict | None:
        # 404: resource not in legacy
        # 500: legacy Java errors out for retired/inactive resources (e.g. Cheyenne)
        # Both are expected for some resources — return None and let the caller skip.
        encoded = quote(resource, safe='')
        return self._get(
            f'/api/protected/admin/ssg/fairShareTree/v3/{encoded}',
            allow=(200, 404, 500),
        )

    def queue(self, resource: str | None = None) -> dict | None:
        # /queue returns all active queues; /queue/{resource} filters to one.
        # Retired resources may 404/500 — return None and let the caller skip.
        if resource is None:
            return self._get('/api/protected/admin/ssg/queue')
        encoded = quote(resource, safe='')
        return self._get(
            f'/api/protected/admin/ssg/queue/{encoded}',
            allow=(200, 404, 500),
        )

    def wallclock_exemption(self) -> dict:
        return self._get('/api/protected/admin/ssg/wallClockExemption')

    def disk_quota(self, *, allow_403: bool = False) -> list | None:
        # Requires ROLE_API_DASG, which the SAM_LEGACY_* account may not hold.
        # 403 -> return None so the caller can SKIP rather than fail.
        return self._get('/api/protected/admin/dasg/diskquota',
                         allow=(200, 403) if allow_403 else (200,))


class NewClient(_BaseClient):
    """Client for samuel.k8s.ucar.edu new Python API."""

    def directory_access(self) -> dict:
        return self._get('/api/v1/directory_access/')

    def project_access(self) -> dict:
        return self._get('/api/v1/project_access/')

    def fstree_access(self, resource: str | None = None) -> dict | None:
        if resource is None:
            return self._get('/api/v1/fstree_access/')
        return self._get(f'/api/v1/fstree_access/{quote(resource, safe="")}', allow=(200, 404))

    def queue(self, resource: str | None = None) -> dict:
        if resource is None:
            return self._get('/api/v1/queue/')
        encoded = quote(resource, safe='')
        return self._get(f'/api/v1/queue/{encoded}', allow=(200, 404))

    def wallclock_exemption(self) -> dict:
        return self._get('/api/v1/wallclock_exemption/')

    def disk_quota(self) -> list:
        return self._get('/api/v1/disk_quota/')


class XrasClient(_BaseClient):
    """Client for the `/api/xras/v1/*` surface, on either stack.

    Unlike the other clients this one is *base-URL parameterised* rather than
    stack-specific: legacy and the port serve the same paths under the same
    prefix, which is the whole point of a drop-in replacement. Instantiate it
    twice, once per host.

    It also needs its own credential (`SAM_XRAS_USER`/`SAM_XRAS_PASS`): the
    `/api/xras/**` chain requires `ROLE_XRAS`, which the `SAM_LEGACY_*` account
    does not hold.

    Every method returns **raw bytes**, not parsed JSON. Byte-exact comparison
    is the entire contract here — a length-preserving bug (swapped
    firstName/lastName, a `%.1f` drift, a reordered field) is invisible to a
    parsed comparison.
    """

    def _get_raw(self, path: str, *, allow: tuple[int, ...] = (200,)) -> tuple[int, bytes]:
        """Return (status, body-bytes) without parsing, raising on a status
        outside *allow*."""
        url = f'{self.base_url}{path}'
        resp = self._session.get(url, timeout=self.timeout)
        if resp.status_code not in allow:
            raise RuntimeError(
                f'GET {url} returned HTTP {resp.status_code}: {resp.text[:200]}'
            )
        return resp.status_code, resp.content

    def people(self) -> tuple[int, bytes]:
        # Legacy's own caller requests this as a bare `?`; keep it identical.
        return self._get_raw('/api/xras/v1/people?')

    def person(self, username: str, *, allow_404: bool = False) -> tuple[int, bytes]:
        allow = (200, 404) if allow_404 else (200,)
        return self._get_raw(
            f'/api/xras/v1/people/{quote(username, safe="")}', allow=allow)

    def request(self, request_number: str) -> tuple[int, bytes]:
        return self._get_raw(
            f'/api/xras/v1/requests/request/{quote(request_number, safe="")}')

    def requests_by_user(self, username: str) -> tuple[int, bytes]:
        return self._get_raw(
            f'/api/xras/v1/requests/user/{quote(username, safe="")}')

    def requests_by_role(self, role: str, username: str) -> tuple[int, bytes]:
        return self._get_raw(
            f'/api/xras/v1/requests/role/{quote(role, safe="")}'
            f'/{quote(username, safe="")}')

    def request_dates(self, request_numbers: str) -> tuple[int, bytes]:
        return self._get_raw(
            f'/api/xras/v1/dates/requests/{quote(request_numbers, safe=",")}')


class HeuvClient(_BaseClient):
    """The ``/api/protected/heuv/v1`` surface on either stack, as raw ``(status, bytes, content-type)``.

    Needs a ``ROLE_API_HEUV`` credential (``SAM_HEUV_USER``/``SAM_HEUV_PASS``); one
    credential serves both stacks, which read the same ``api_credentials`` table.
    """

    PREFIX = '/api/protected/heuv/v1'

    def get(self, rule: str) -> tuple[int, bytes, str]:
        resp = self._session.get(f'{self.base_url}{self.PREFIX}/{rule}', timeout=self.timeout)
        if resp.status_code in (401, 403):
            raise RuntimeError(f'GET {rule} returned HTTP {resp.status_code}: the credential lacks ROLE_API_HEUV')
        return resp.status_code, resp.content, resp.headers.get('Content-Type', '')
