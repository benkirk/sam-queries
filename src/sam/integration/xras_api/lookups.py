"""Cached wrappers over the client's reference lookups — FoS and opportunities.

The sibling of :mod:`sam.integration.xras_api.people`: the client knows HTTP,
these know memoisation, and these are what application code calls. Both back
the request/opportunity modals, which resolve *names* the ``reports``
enumeration only ever carries as ids.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sam.integration.xras_api.cache import cached_fos_types, cached_opportunity
from sam.integration.xras_api.client import XrasApiClient


def get_fos_types() -> Optional[List[Dict[str, Any]]]:
    """The XRAS FoS catalog, memoised (~39 rows, near-static).

    Raises:
        XrasApiNotConfigured: the outgoing lever is off or no key is set.
        XrasSourceUnavailable: XRAS could not be reached.
    """
    return cached_fos_types(
        lambda: XrasApiClient.from_environment().get_fos_types())


def fos_name_map() -> Dict[int, str]:
    """``{fosTypeId: fosName}`` for resolving a request's id-only ``fos[]``.

    Best-effort by design: an outage or an unconfigured lever yields an **empty
    map**, and the modal falls back to the id — a field-of-science name is a
    nicety, never worth failing the whole request view for. Prefers ``fosName``,
    falls back to ``fosAbbr``.
    """
    try:
        rows = get_fos_types() or []
    except Exception:
        return {}
    out: Dict[int, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        fid = row.get('fosTypeId')
        name = row.get('fosName') or row.get('fosAbbr')
        if fid is not None and name:
            out[int(fid)] = str(name)
    return out


def get_opportunity(opportunity_id) -> Optional[Dict[str, Any]]:
    """One opportunity's full detail, memoised. ``None`` if XRAS has no such id.

    Definite negatives cache too (a 404 is a real answer); an
    ``XrasSourceUnavailable`` propagates before the store, so a transient outage
    is never remembered.

    Raises:
        XrasApiNotConfigured: the outgoing lever is off or no key is set.
        XrasSourceUnavailable: XRAS could not be reached.
    """
    if opportunity_id is None:
        return None
    return cached_opportunity(
        opportunity_id,
        lambda: XrasApiClient.from_environment().get_opportunity(opportunity_id))
