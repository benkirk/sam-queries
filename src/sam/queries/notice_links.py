"""URLs that project notices link to, built without Flask so the scheduled
tasks can use them. Paths are gated against the route map by
tests/unit/gates/test_lifecycle_notice_links.py."""

from __future__ import annotations

from typing import Dict, Optional
from urllib.parse import quote

#: Where mail links land when the caller has no request to read a root from.
DEFAULT_SITE_URL = 'https://sam.hpc.ucar.edu/'

#: Pages the onboarding block links to, relative to the site root.
ONBOARDING_PATHS = {
    'accounts': 'user/accounts',
    'jobs': 'user/jobs',
    'data': 'user/data',
    'status': 'status/derecho',
}
RESOURCE_DETAILS_PATH = 'user/resource-details'
MANAGE_PROJECT_PATH = 'admin/project/{projcode}/edit'


def site_root(site_url: Optional[str]) -> str:
    return (site_url or DEFAULT_SITE_URL).rstrip('/') + '/'


def landing_links(site_url: Optional[str]) -> Dict[str, str]:
    root = site_root(site_url)
    return {key: root + path for key, path in ONBOARDING_PATHS.items()}


def resource_details_url(site_url: Optional[str], projcode: str,
                         resource_name: str) -> str:
    return (f'{site_root(site_url)}{RESOURCE_DETAILS_PATH}/{projcode}'
            f'?resource={quote(resource_name)}')


def manage_project_url(site_url: Optional[str], projcode: str) -> str:
    return site_root(site_url) + MANAGE_PROJECT_PATH.format(projcode=projcode)
