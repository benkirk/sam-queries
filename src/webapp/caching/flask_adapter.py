"""
FlaskCacheAdapter — wraps a Flask-Caching instance to conform to CacheBase.

Flask-Caching itself is just an orchestrator for a pluggable backend
(SimpleCache, Redis, Memcached, …). Stats introspection only works for
SimpleCache today (in-process backing dict); other backends fall back to a
"not introspectable" placeholder rather than crashing the admin card.
"""

from typing import Optional

from sam.caching import CacheBase, approx_bytes


# Cache-key prefixes used by the API endpoints we want to break out in the
# admin card. Flask-Caching with @cache.cached(query_string=True) builds
# keys from the request path, so a simple prefix bucket gives operators
# the "where is our memory going" view at a glance.
_KEY_GROUPS = (
    'directory_access',
    'fstree_access',
    'project_access',
)


class FlaskCacheAdapter(CacheBase):
    """Best-effort introspection of a Flask-Caching instance."""

    name = 'flask_cache'

    def __init__(self, flask_cache, *, redis_client=None):
        self._flask = flask_cache
        # Optional Redis client — when present, info() can introspect the
        # shared cache by SCANning the Flask-Caching key prefix.
        self._redis_client = redis_client

    # ── Try to reach the SimpleCache backing dict ───────────────────────

    def _backing_dict(self) -> Optional[dict]:
        """Return the SimpleCache backing dict, or None for other backends."""
        try:
            backend = self._flask.cache
        except (AttributeError, RuntimeError):
            return None
        # cachelib.simple.SimpleCache stores entries under `_cache` as
        # {key: (expires_at, value)}. NullCache has no entries.
        backing = getattr(backend, '_cache', None)
        if isinstance(backing, dict):
            return backing
        return None

    @staticmethod
    def _bucket_for(key: str) -> str:
        """Map a flask-cache key to one of the known API groups, else 'other'."""
        for prefix in _KEY_GROUPS:
            if prefix in key:
                return prefix
        return 'other'

    # ── Redis introspection (used when Flask-Caching backend is Redis) ──

    def _redis_introspect(self) -> Optional[dict]:
        """Aggregate per-group counts/bytes by SCANning the Redis keyspace.

        Flask-Caching's RedisCache stores keys under the key_prefix
        configured for the extension (default ``flask_cache_``). We use
        SCAN (not KEYS — KEYS would block Redis) to walk entries and
        bucket them by API group based on substring match. Bytes are
        approximated via a sampled `MEMORY USAGE` to avoid an O(N)
        roundtrip on every entry.
        """
        client = self._redis_client
        if client is None:
            return None
        try:
            # Flask-Caching prefixes keys; we scan everything not owned
            # by our chart/usage adapters (those have their own prefixes).
            groups: dict[str, dict] = {
                g: {'entries': 0, 'bytes_approx': 0}
                for g in (*_KEY_GROUPS, 'other')
            }
            sampled_total = 0
            sample_count = 0
            total_entries = 0
            for raw_key in client.scan_iter(match='*', count=200):
                # Skip keys owned by our other adapters before decoding —
                # usage:* values are pickle-suffixed bytes that aren't
                # valid UTF-8, so checking on raw bytes avoids a
                # UnicodeDecodeError on the next line.
                if isinstance(raw_key, bytes):
                    if raw_key.startswith(b'chart:') or raw_key.startswith(b'usage:'):
                        continue
                    key = raw_key.decode('utf-8', errors='replace')
                else:
                    if raw_key.startswith('chart:') or raw_key.startswith('usage:'):
                        continue
                    key = raw_key
                total_entries += 1
                bucket = self._bucket_for(key)
                groups[bucket]['entries'] += 1
                if sample_count < 50:
                    try:
                        used = client.memory_usage(raw_key) or 0
                        sampled_total += int(used)
                        sample_count += 1
                    except Exception:
                        # MEMORY USAGE missing on some Redis builds — skip.
                        pass
            avg = (sampled_total / sample_count) if sample_count else 0
            for g in groups.values():
                g['bytes_approx'] = int(avg * g['entries'])
            return {
                'name':         self.name,
                'enabled':      True,
                'currsize':     total_entries,
                'maxsize':      None,
                'ttl':          None,
                'hits':         None,
                'misses':       None,
                'bytes_approx': int(avg * total_entries),
                'extras':       {'backend': 'redis', 'groups': groups},
            }
        except Exception:
            return None

    # ── CacheBase ───────────────────────────────────────────────────────

    def info(self) -> dict:
        backing = self._backing_dict()
        if backing is None:
            # SimpleCache not in play. Try Redis introspection if a
            # client is wired in; otherwise fall back to placeholder.
            redis_info = self._redis_introspect()
            if redis_info is not None:
                return redis_info
            return {
                'name':         self.name,
                'enabled':      True,
                'currsize':     None,
                'maxsize':      None,
                'ttl':          None,
                'hits':         None,
                'misses':       None,
                'bytes_approx': None,
                'extras':       {'message': 'not introspectable for this backend'},
            }

        # Build a per-group breakdown alongside the totals. Each entry
        # value is (expires_at, value); we measure the value only.
        groups: dict[str, dict] = {
            g: {'entries': 0, 'bytes_approx': 0}
            for g in (*_KEY_GROUPS, 'other')
        }
        total_bytes = 0
        # Snapshot keys first to avoid issues with concurrent mutation.
        snapshot = list(backing.items())
        for key, entry in snapshot:
            try:
                _, value = entry
            except (TypeError, ValueError):
                value = entry
            sz = approx_bytes(value)
            total_bytes += sz
            bucket = self._bucket_for(key if isinstance(key, str) else str(key))
            groups[bucket]['entries'] += 1
            groups[bucket]['bytes_approx'] += sz

        return {
            'name':         self.name,
            'enabled':      True,
            'currsize':     len(snapshot),
            'maxsize':      None,
            'ttl':          None,
            'hits':         None,
            'misses':       None,
            'bytes_approx': total_bytes,
            'extras':       {'groups': groups},
        }

    def clear(self) -> int:
        """Wipe the entire flask-cache backend. Returns prior entry count
        when known, else 0 (some backends don't expose count).
        """
        backing = self._backing_dict()
        prior = len(backing) if backing is not None else 0
        try:
            self._flask.clear()
        except Exception:
            pass
        return prior
