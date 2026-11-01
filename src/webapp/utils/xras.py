"""The XRAS plumbing both dashboards share: audit-write sessions, the interactive clients,
the published request index, the request types' cards. Route-free; the Allocations
``xras/_shared`` re-exports it."""

import logging

from webapp.extensions import db

logger = logging.getLogger(__name__)


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
    """XRAS ``resourceId`` -> repository key from the catalog (cached a day, as
    ``people.get_resources``), or ``None`` when XRAS is not answering."""
    from sam.integration.xras_api import XrasSourceUnavailable
    from sam.integration.xras_api.cache import cached_resources
    from sam.integration.xras_api.people import resource_key_map
    try:
        catalog = cached_resources(lambda: read_client(config).get_resources())
    except XrasSourceUnavailable:
        return None
    return resource_key_map(catalog) if catalog else None


def requests_index():
    """The published request index, or ``None`` if no sweep has written one."""
    from sam.integration.xras_api.cache import load_requests_index
    return load_requests_index()


def report_opportunities(config=None):
    """``(views, outage)``: the open New opportunities read with no user (a visitor, an admin)."""
    from sam.integration.xras_api import XrasSourceUnavailable
    try:
        return admin_client(config).new_request_opportunities(None), False
    except XrasSourceUnavailable as exc:
        logger.warning('XRAS opportunities (report context): not answering: %s', exc)
        return [], True


def type_cards(opportunities, config=None, *, audiences, include_unguided=False):
    """The request types' cards (``sam/xras/allocation_overview.py``), each open type with
    its New rule book; the reader and the resource catalog are read only when XRAS answered."""
    from sam.integration.xras_api.rule_book import rule_book
    from sam.xras.allocation_overview import allocation_type_overview
    reader = read_client(config) if opportunities else None
    return allocation_type_overview(
        db.session, opportunities, audiences=audiences, include_unguided=include_unguided,
        rules_for=lambda type_id, name: rule_book(reader, type_id, 'New',
                                                  allocation_type_name=name),
        resource_keys=resource_keys(config) if opportunities else None)
