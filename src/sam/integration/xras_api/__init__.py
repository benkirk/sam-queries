"""Outbound client for the XRAS Allocations API — ``https://api.xras.org/v1/…``.

**Read-only, GET-only, fail-closed.** See ``client.py`` for why that is
structural rather than a convention, and ``docs/xras/outgoing/`` for the live
probe this is built on.

Do not confuse this with the *inbound* XRAS surface (``src/sam/xras/``,
``src/webapp/api/xras/``), which is XRAS calling SAM, nor with
``sam/integration/xras.py``, which is the ORM for the inbound audit tables.
"""

from sam.integration.xras_api.base import (
    XrasApiNotConfigured,
    XrasSourceUnavailable,
)
from sam.integration.xras_api.client import XrasApiClient
from sam.integration.xras_api.config import XrasApiConfig, xras_api_configured
from sam.integration.xras_api.people import (
    get_person,
    get_resources,
    resource_repository_keys,
)

__all__ = [
    'XrasApiClient',
    'XrasApiConfig',
    'XrasApiNotConfigured',
    'XrasSourceUnavailable',
    'get_person',
    'get_resources',
    'resource_repository_keys',
    'xras_api_configured',
]
