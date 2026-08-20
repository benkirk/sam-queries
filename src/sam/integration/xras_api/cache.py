"""TTL cache for outbound XRAS lookups.

Two buckets with deliberately different horizons:

``xras_people`` (4 h)
    ``isReconciled`` is the **closure signal** for the account-creation
    worklist — an item closes itself when the flag flips — so this must not
    go stale. Four hours means an operator who creates an account in the
    morning sees the row clear the same day without touching SAM.

``xras_resources`` (1 day)
    A 13-row catalog that changes on the order of once a year.

Registered with the webapp caching facade via ``_BUCKETED_CACHE_MODULES`` in
``webapp/caching/__init__.py``, which is the one line that buys the Admin card
row, ``stats()``, ``clear()`` and
``sam-admin cache --refresh --category xras_api``.

⚠️ ``BucketSpec.name`` is a **global Redis key prefix**, hence the ``xras_``
prefix on both.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from sam.caching import BucketedTTLCache, BucketSpec

_CACHE = BucketedTTLCache('xras_api', 'xras_api', {
    'people': BucketSpec(
        name='xras_people',
        ttl_key='XRAS_PEOPLE_CACHE_TTL', ttl_default=14400,      # 4 hours
        size_key='XRAS_PEOPLE_CACHE_SIZE', size_default=512,
    ),
    'resources': BucketSpec(
        name='xras_resources',
        ttl_key='XRAS_RESOURCES_CACHE_TTL', ttl_default=86400,   # 1 day
        size_key='XRAS_RESOURCES_CACHE_SIZE', size_default=8,
    ),
})

#: Test seam, matching the awards / fs-scans / jobs idiom: ``_adapters`` IS
#: the cache's memo dict, so clearing it re-initialises the cache.
_adapters = _CACHE._adapters


def cached_person(username: str, compute: Callable[[], Any]) -> Optional[Any]:
    """Memoise one person lookup.

    Successes **and definite negatives** are cached — a 404 for a username is
    a real answer and re-asking costs a round trip per card render. An
    ``XrasSourceUnavailable`` propagates out of *compute* before the store, so
    a transient outage is never remembered.

    The username is casefolded into the key: XRAS usernames are matched
    case-insensitively at the API, and without this ``Smith`` and ``smith``
    would occupy two entries.
    """
    return _CACHE.get_or_compute('people', username.strip().casefold(), compute)


def cached_resources(compute: Callable[[], Any]) -> Optional[Any]:
    """Memoise the resource catalog. One key — it takes no arguments."""
    return _CACHE.get_or_compute('resources', 'catalog', compute)
