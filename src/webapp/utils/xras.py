"""The XRAS plumbing both dashboards share: audit-write sessions, the interactive clients,
the published request index. Route-free; the Allocations ``xras/_shared`` re-exports it."""

from webapp.extensions import db


def session_factory():
    """Private sessions for the XRAS services' audit writes.

    Deliberately **not** ``db.session``: an audit row that a request rollback could erase
    would be a record of an irreversible act that can be un-said.

    WARNING: the services take the factory, not a session: pass ``session_factory``,
    never ``session_factory()``. The first production merge (2026-08-25) shipped the call
    form and wrote no audit row; the service logs and proceeds by design, so nothing
    else fails.
    """
    from sqlalchemy.orm import Session
    return Session(db.engine)


def interactive_config():
    """The XRAS config on the interactive budget: an outage degrades a page, never blocks it."""
    from sam.integration.xras_api.config import XrasApiConfig
    return XrasApiConfig.from_environment().interactive()


def read_client(config=None):
    from sam.integration.xras_api import XrasApiClient
    return XrasApiClient.from_environment(config or interactive_config())


def admin_client(config=None):
    from sam.integration.xras_api.admin_client import XrasAdminClient
    return XrasAdminClient.from_environment(config or interactive_config())


def resource_keys(config=None):
    """XRAS ``resourceId`` -> repository key from the catalog, or ``None`` when XRAS is not answering."""
    from sam.integration.xras_api import XrasSourceUnavailable
    from sam.integration.xras_api.people import resource_key_map
    try:
        catalog = read_client(config).get_resources()
    except XrasSourceUnavailable:
        return None
    return resource_key_map(catalog) if catalog else None


def requests_index():
    """The published request index, or ``None`` if no sweep has written one."""
    from sam.integration.xras_api.cache import load_requests_index
    return load_requests_index()
