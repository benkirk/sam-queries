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
    # The Feed-B handoff. Unlike the two above this is NOT a memo of an
    # expensive read — it is a **producer/consumer mailbox**: `xras_sweep`
    # writes the classified pending-request worklist, and the dashboard tab
    # reads it. The enumeration behind it is 21 pages and 60-90s, which no
    # htmx round-trip can afford, so the tab cannot compute this itself.
    #
    # ⚠️ **TTL spans the overnight gap, on purpose.** The sweep runs on
    # business hours (08:00-17:00 Mountain), so the longest interval between
    # writes is 17:00 -> 08:00, about 15 hours. A TTL tuned to the *hourly*
    # cadence would expire around 21:00 and leave the tab blank every morning
    # until the first sweep of the day — the exact moment an operator looks.
    # 24h covers the gap with room for a missed run; the data is only ever as
    # stale as the last successful sweep, and the tab renders that timestamp.
    'pending': BucketSpec(
        name='xras_pending',
        ttl_key='XRAS_PENDING_CACHE_TTL', ttl_default=86400,     # 1 day
        size_key='XRAS_PENDING_CACHE_SIZE', size_default=4,
    ),
})

#: One key: the sweep publishes a single whole-process snapshot.
_PENDING_KEY = 'worklist'

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


def store_pending_worklist(payload: Any) -> bool:
    """Publish the sweep's Feed-B result for the dashboard to read.

    Returns True if it was stored. A disabled bucket (TTL or size <= 0, or
    caching switched off in this worker) is **not** an error: the sweep still
    reports its findings in the ledger row, and the tab renders its
    "no sweep data yet" state.
    """
    adapter = _CACHE.adapter('pending')
    if adapter is None:
        return False
    with adapter.lock:
        adapter.pop(_PENDING_KEY, None)
        try:
            adapter[_PENDING_KEY] = payload
        except ValueError:
            # Full with nothing expired to evict — skip rather than fail the
            # sweep, which has already done the useful work.
            return False
    return True


def load_pending_worklist() -> Optional[Any]:
    """Read the sweep's last published Feed-B result, or ``None``.

    ``None`` means "no sweep has published yet" — which is the shipped state
    until the task is enabled, and is what the tab's empty state describes. It
    is deliberately distinguishable from a published-but-empty result (a real
    sweep that found nothing), which comes back as a payload with zero rows.
    """
    adapter = _CACHE.adapter('pending')
    if adapter is None:
        return None
    with adapter.lock:
        if _PENDING_KEY not in adapter:
            return None
        return adapter[_PENDING_KEY]
