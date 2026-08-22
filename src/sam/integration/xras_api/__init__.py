"""Outbound clients for the XRAS Allocations API — ``https://api.xras.org/v1/…``.

**Two clients, one config, fail-closed twice.**

``XrasApiClient`` (``client.py``)
    Read-only and GET-only *by construction* — the sole transport primitive is
    an internal ``_get``, and a test pins that no write verb exists on the
    class. Hardcoded to ``XA-CONTEXT: report``. Governed by
    ``XRAS_OUTGOING_ENABLED``.

``XrasAdminClient`` (``admin_client.py``)
    The deliberate **sibling** — never a subclass, never a relaxation of the
    pin above. Hardcoded to ``XA-CONTEXT: submit``, one attempt per write, and
    every write verified by a re-read because a 200 from this API proves only
    that the call was allowed. Governed by a **second** lever,
    ``XRAS_WRITE_ENABLED``, which defaults off and is webapp-only.

The split is not merely stylistic: the Reports family answers under ``report``
and 401s under ``submit``, and the write routes do the reverse, so one class
genuinely cannot serve both. See ``docs/xras/outgoing/XRAS_WRITE_PROBES.md``
for the measurements behind every verb.

Do not confuse either with the *inbound* XRAS surface (``src/sam/xras/``,
``src/webapp/api/xras/``), which is XRAS calling SAM, nor with
``sam/integration/xras.py``, which is the ORM for the inbound audit tables.
"""

from sam.integration.xras_api.admin_client import (
    PI_ROLE_TYPE_ID,
    ROLE_TYPES,
    RoleType,
    XrasAdminClient,
    XrasWriteResult,
    role_type,
)
from sam.integration.xras_api.base import (
    XrasApiNotConfigured,
    XrasSourceUnavailable,
    XrasWriteNotConfigured,
    XrasWriteRejected,
)
from sam.integration.xras_api.cache import invalidate_person
from sam.integration.xras_api.client import XrasApiClient
from sam.integration.xras_api.config import (
    XrasApiConfig,
    xras_admin_context_available,
    xras_api_configured,
    xras_write_configured,
)
from sam.integration.xras_api.lookups import (
    fos_name_map,
    get_fos_types,
    get_opportunity,
)
from sam.integration.xras_api.people import (
    get_person,
    get_resources,
    resource_repository_keys,
)

__all__ = [
    'PI_ROLE_TYPE_ID',
    'ROLE_TYPES',
    'RoleType',
    'XrasAdminClient',
    'XrasApiClient',
    'XrasApiConfig',
    'XrasApiNotConfigured',
    'XrasSourceUnavailable',
    'XrasWriteNotConfigured',
    'XrasWriteRejected',
    'XrasWriteResult',
    'fos_name_map',
    'get_fos_types',
    'get_opportunity',
    'get_person',
    'get_resources',
    'invalidate_person',
    'resource_repository_keys',
    'role_type',
    'xras_admin_context_available',
    'xras_api_configured',
    'xras_write_configured',
]
