"""TTL cache for award lookups.

Award records are near-immutable — an NSF award's title, dates, and program
officer change at most once a year — so this sits at the long end of the
range, alongside ``FS_SCANS_CACHE_TTL`` (8 days) rather than the 30-minute
jobs TTL. Its real job is to make the *operator's* workflow cheap: typing a
number, fetching, editing a field, and re-fetching should cost one request,
not three.

Registered with the webapp caching facade via ``_BUCKETED_CACHE_MODULES``
in ``webapp/caching/__init__.py``, which is what puts it on the Admin
Configuration card and behind ``sam-admin cache --refresh --category awards``.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from sam.caching import BucketedTTLCache, BucketSpec

_CACHE = BucketedTTLCache('awards', 'awards', {
    'default': BucketSpec(
        name='awards',
        ttl_key='AWARD_LOOKUP_CACHE_TTL', ttl_default=691200,    # 8 days
        size_key='AWARD_LOOKUP_CACHE_SIZE', size_default=256,
    ),
})

#: Test seams, matching the fs-scans / jobs idiom: ``_adapters`` IS the
#: cache's memo dict, so clearing it re-initialises the cache.
_BUCKETS = _CACHE.buckets
_adapters = _CACHE._adapters


def cached_lookup(provider_name: str, contract_number: str,
                  compute: Callable[[], Any]) -> Optional[Any]:
    """Memoise one provider's answer for one award number.

    Only successful answers are cached, including a definite "no such
    award" (``None``) — an ``AwardSourceUnavailable`` propagates out of
    *compute* before the store, so a transient outage is never remembered.
    """
    return _CACHE.get_or_compute(
        'default', (provider_name, contract_number), compute)


def purge() -> int:
    """Drop every cached award record. Returns the number of entries cleared."""
    return _CACHE.purge()
