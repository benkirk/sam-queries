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

    def _get(self, path: str, *, allow_404: bool = False, allow_500: bool = False):
        url = f'{self.base_url}{path}'
        resp = self._session.get(url, timeout=self.timeout)
        if resp.status_code == 404 and allow_404:
            return None
        if resp.status_code == 500 and allow_500:
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
            allow_404=True,
            allow_500=True,
        )

    def queue(self, resource: str | None = None) -> dict | None:
        # /queue returns all active queues; /queue/{resource} filters to one.
        # Retired resources may 404/500 — return None and let the caller skip.
        if resource is None:
            return self._get('/api/protected/admin/ssg/queue')
        encoded = quote(resource, safe='')
        return self._get(
            f'/api/protected/admin/ssg/queue/{encoded}',
            allow_404=True,
            allow_500=True,
        )

    def wallclock_exemption(self) -> dict:
        return self._get('/api/protected/admin/ssg/wallClockExemption')


class NewClient(_BaseClient):
    """Client for samuel.k8s.ucar.edu new Python API."""

    def directory_access(self) -> dict:
        return self._get('/api/v1/directory_access/')

    def project_access(self) -> dict:
        return self._get('/api/v1/project_access/')

    def fstree_access(self) -> dict:
        return self._get('/api/v1/fstree_access/')

    def queue(self, resource: str | None = None) -> dict:
        if resource is None:
            return self._get('/api/v1/queue/')
        encoded = quote(resource, safe='')
        return self._get(f'/api/v1/queue/{encoded}', allow_404=True)

    def wallclock_exemption(self) -> dict:
        return self._get('/api/v1/wallclock_exemption/')


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
