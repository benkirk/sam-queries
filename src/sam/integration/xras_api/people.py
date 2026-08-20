"""Cached wrappers over the client's lookups.

The split from ``client.py`` is the same one ``awards/`` makes: the client
knows about HTTP and nothing about memoisation, so its retry and
three-outcome semantics stay testable without a cache in the way. These are
what application code calls.

Each builds its own client. That is cheap (a ``requests.Session``
constructor) and it is what keeps config read **per call** — the webapp
reloads config without a restart, and the scheduled task reads its
environment once per run.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sam.integration.xras_api.cache import cached_person, cached_resources
from sam.integration.xras_api.client import XrasApiClient


def get_person(username: str) -> Optional[Dict[str, Any]]:
    """One XRAS person, memoised. ``None`` means XRAS has no such username.

    Raises:
        XrasApiNotConfigured: the outgoing lever is off or no key is set.
        XrasSourceUnavailable: XRAS could not be reached.
    """
    if not username or not str(username).strip():
        return None
    return cached_person(
        username, lambda: XrasApiClient.from_environment().get_person(username))


def get_resources() -> Optional[List[Dict[str, Any]]]:
    """The XRAS resource catalog, memoised.

    Raises:
        XrasApiNotConfigured: the outgoing lever is off or no key is set.
        XrasSourceUnavailable: XRAS could not be reached.
    """
    return cached_resources(
        lambda: XrasApiClient.from_environment().get_resources())


def resource_repository_keys() -> List[int]:
    """Just the ``resourceRepositoryKey`` values, as ints.

    The shape ``audit_resource_mapping(session, xras_keys=...)`` wants — it
    compares against ``xras_resource_repository_key_resource``, whose key
    column is an integer.
    """
    keys: List[int] = []
    for resource in get_resources() or []:
        if not isinstance(resource, dict):
            continue
        raw = resource.get('resourceRepositoryKey')
        try:
            keys.append(int(raw))
        except (TypeError, ValueError):
            # A non-numeric key cannot join to the integer column at all;
            # dropping it here keeps the audit's report honest rather than
            # reporting a key that could never have matched.
            continue
    return sorted(set(keys))
