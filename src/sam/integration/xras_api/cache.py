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

#: The Feed-B account worklist. The sweep publishes a single whole-process
#: snapshot under this key.
_PENDING_KEY = 'worklist'

#: The Remediations card's request index — a **second key in the same bucket**,
#: written by the same sweep run.
#:
#: Two keys rather than one payload, deliberately: the ``worklist`` value keeps
#: its exact shape, so a deploy in either direction stays compatible. An old
#: webapp reading a new sweep's output sees the worklist it expects and simply
#: never asks for this key; a new webapp reading an old sweep's output finds no
#: index and renders its "no sweep has published one yet" state — which is a
#: real state during the first hour after a deploy, and one the card names
#: rather than showing an empty table.
_REQUESTS_KEY = 'requests_index'

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


def invalidate_person(username: str) -> None:
    """Forget one cached person lookup.

    ⚠️ **Load-bearing after a merge.** A merge deletes the source username in
    XRAS, but this bucket holds it for four hours — so without this the very
    card the operator just fixed keeps rendering the placeholder it merged
    away, and re-merging it 404s. The service calls this for **both** the
    source and the target: the source because it no longer exists, the target
    because merge folds roles into it and its detail sheet is now different.

    Casefolds the same way :func:`cached_person` does, or it would miss.
    Absent keys are not an error — a merge from a card that never rendered the
    person is perfectly ordinary.
    """
    adapter = _CACHE.adapter('people')
    if adapter is None:
        return
    with adapter.lock:
        adapter.pop(username.strip().casefold(), None)


def cached_resources(compute: Callable[[], Any]) -> Optional[Any]:
    """Memoise the resource catalog. One key — it takes no arguments."""
    return _CACHE.get_or_compute('resources', 'catalog', compute)


def store_pending_worklist(payload: Any) -> str:
    """Publish the sweep's Feed-B result for the dashboard to read.

    Returns **where it landed**, not merely whether a write succeeded:
    ``'redis'`` (shared, the dashboard will see it), ``'local'`` (a per-worker
    in-process cache), or ``'disabled'`` / ``'full'``.

    ⚠️ **Why this is not a bool.** `BucketedTTLCache.adapter()` falls back to a
    per-worker `TTLCacheAdapter` when `CACHE_REDIS_URL` is unset or Redis is
    unreachable, and that fallback is load-bearing — an unreachable Redis must
    never take the webapp down. But `xras_sweep` runs in a **one-shot CronJob
    pod**: a process-local write succeeds, the pod exits, and the cache dies
    with it. The producer reports success, the consumer sees nothing, and
    nothing anywhere errors.

    That is not hypothetical — it is what the first production run did, because
    `cronjob-tasks.yaml` did not carry `CACHE_REDIS_URL`. A bool could not tell
    the two apart, so the caller could not report the difference.
    """
    # 'full' back from the helper means the bucket had nothing expired to
    # evict — skipped rather than failing the sweep, which has already done the
    # useful work.
    return _store('pending', _PENDING_KEY, payload)


def is_shared_backend(adapter: Any) -> bool:
    """Is this adapter visible to other processes?

    The one question a producer in a short-lived pod actually needs answered.
    """
    from sam.caching.redis_ttl import RedisTTLAdapter
    return isinstance(adapter, RedisTTLAdapter)


def store_requests_index(payload: Any) -> str:
    """Publish the sweep's Remediations index. Same contract as
    :func:`store_pending_worklist` — including returning **where it landed**,
    because this is written from the same one-shot CronJob pod and a
    process-local write there succeeds and then dies with the process.
    """
    return _store('pending', _REQUESTS_KEY, payload)


def load_requests_index() -> Optional[Any]:
    """Read the last published request index, or ``None``.

    ``None`` is a distinct and meaningful answer: no sweep has published one
    yet. That is the state for the first hour after this ships, and for as long
    as the sweep stays disabled, and the card says so rather than rendering an
    empty table that looks like "nothing to remediate".
    """
    return _load('pending', _REQUESTS_KEY)


def patch_requests_index(request_number: str, entry: Optional[Any]) -> bool:
    """Replace one entry in the published index, in place. ``True`` if it stuck.

    ⚠️ **This is what makes a write visible in the same interaction.** The card
    renders from an hourly snapshot, but an operator who withdraws an action
    must not keep reading "Approved" for another fifty minutes, and re-running
    the 60-90s enumeration per click is not on the table. So the service
    re-fetches the one request it just changed and patches its entry here.

    Read-modify-write under the adapter's lock. The lock matters more than it
    looks: the bucket may be Redis shared across every webapp worker, and two
    operators acting on different requests in the same second would otherwise
    race to write back two whole payloads, one of which would lose an edit.

    *entry* of ``None`` removes the row. Callers should prefer patching a row
    into its new state over dropping it — a row that vanishes on click reads as
    a bug, while a row that changes reads as the effect.

    ``False`` means there was nothing to patch (no index published, or the
    request is not in it), which is not an error: the write itself already
    happened and was verified, and the next sweep will pick the request up.
    """
    adapter = _CACHE.adapter('pending')
    if adapter is None:
        return False
    with adapter.lock:
        if _REQUESTS_KEY not in adapter:
            return False
        payload = adapter[_REQUESTS_KEY]
        if not isinstance(payload, dict) or not isinstance(payload.get('rows'), list):
            return False

        wanted = str(request_number).strip()
        rows = payload['rows']
        index = next((i for i, row in enumerate(rows)
                      if isinstance(row, dict)
                      and str(row.get('request_number') or '').strip() == wanted),
                     None)
        if index is None:
            return False

        if entry is None:
            rows.pop(index)
        else:
            rows[index] = entry

        # Re-store rather than mutate in place: the Redis adapter serializes on
        # assignment, so an in-place edit of the object we read back would
        # change nothing on a shared backend and everything on a local one —
        # the worst kind of difference to carry between dev and production.
        adapter.pop(_REQUESTS_KEY, None)
        try:
            adapter[_REQUESTS_KEY] = payload
        except ValueError:
            return False
    return True


def _store(bucket: str, key: str, payload: Any) -> str:
    adapter = _CACHE.adapter(bucket)
    if adapter is None:
        return 'disabled'
    with adapter.lock:
        adapter.pop(key, None)
        try:
            adapter[key] = payload
        except ValueError:
            return 'full'
    return 'redis' if is_shared_backend(adapter) else 'local'


def _load(bucket: str, key: str) -> Optional[Any]:
    adapter = _CACHE.adapter(bucket)
    if adapter is None:
        return None
    with adapter.lock:
        if key not in adapter:
            return None
        return adapter[key]


def load_pending_worklist() -> Optional[Any]:
    """Read the sweep's last published Feed-B result, or ``None``.

    ``None`` means "no sweep has published yet" — which is the shipped state
    until the task is enabled, and is what the tab's empty state describes. It
    is deliberately distinguishable from a published-but-empty result (a real
    sweep that found nothing), which comes back as a payload with zero rows.
    """
    return _load('pending', _PENDING_KEY)
