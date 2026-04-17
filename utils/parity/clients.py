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


class NewClient(_BaseClient):
    """Client for samuel.k8s.ucar.edu new Python API."""

    def directory_access(self) -> dict:
        return self._get('/api/v1/directory_access/')

    def project_access(self) -> dict:
        return self._get('/api/v1/project_access/')

    def fstree_access(self) -> dict:
        return self._get('/api/v1/fstree_access/')
