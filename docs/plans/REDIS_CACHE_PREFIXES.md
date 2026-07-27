# Redis cache prefixes — retire the shared `'usage:'` namespace

**Status: IMPLEMENTED (2026-07-27), branch `redis_and_ux_tweaks`.** Item 6
deferred from `JOB_HISTORY_DASHBOARD.md` / `JOB_HISTORY_FOLLOWUPS.md`. No
plugin (hpc-usage-queries) changes.

## The problem

`RedisTTLAdapter` (`src/sam/caching/redis_ttl.py`) namespaces keys — and its
`clear()` / `info()` SCANs — by a constructor `prefix` that defaulted to
`_DEFAULT_PREFIX = 'usage:'`. Three of the five constructions never passed one:

| Adapter (name) | Constructed in | Redis prefix (before) | TTL |
|---|---|---|---|
| `allocation_usage` | `sam/queries/usage_cache.py` | `usage:` (default) | 1 h |
| `fs_scans` | `webapp/disk_scans/cache.py` | `usage:` (default) | **8 d** |
| `fs_scans_filtered` | `webapp/disk_scans/cache.py` | `usage:` (default) | 30 min |
| `jobs` | `webapp/jobs/cache.py` | `jobs:` (explicit, round 1) | 6 h |
| `jobs_recent` | `webapp/jobs/cache.py` | `jobs_recent:` (explicit) | 15 min |

Consequences (all live with `CACHE_REDIS_URL` set):

1. **Cross-wipe**: `caching.clear('usage')` and `clear('scans')` each
   SCAN-deleted `usage:*` — clearing cheap 1-hour usage rollups silently
   discarded 8-day fs-scan aggregations and vice versa.
2. **Blended stats**: `usage_cache_info()` and `fs_scans_cache_info()` both
   counted the same `usage:*` superset.
3. **Cross-TTL keyspace**: `fs_scans` (8 d) and `fs_scans_filtered` (30 min)
   shared one namespace — a same-shaped key cached by one bucket would satisfy
   lookups from the other under the wrong TTL policy.
4. `FlaskCacheAdapter._redis_introspect()` skipped foreign keys via a
   hardcoded `chart:`/`usage:` pair — the round-1 `jobs:`/`jobs_recent:`
   prefixes were never added, so jobs keys miscounted into the flask card's
   `other` group.
5. The explicit jobs `prefix=` kwargs had no unit pin.

## What shipped

- **Name-derived default prefix** (`redis_ttl.py`): `prefix: Optional[str] =
  None` → `self._prefix = prefix if prefix is not None else f'{name}:'`;
  `_DEFAULT_PREFIX` deleted. Every construction gets a distinct namespace with
  no call-site edits; the explicit jobs kwargs became redundant and were
  dropped (the per-bucket-keyspace rationale moved to the adapter docstring).
  Resulting prefixes: `allocation_usage:`, `fs_scans:`, `fs_scans_filtered:`,
  `jobs:`, `jobs_recent:`. (Glob safety: `fs_scans:*` does not match
  `fs_scans_filtered:…` — the colon terminates the shorter prefix.)
- **flask_adapter skip list**: module-level `_FOREIGN_PREFIXES` (+ `_B` bytes
  form) = `('chart:',)` + the five adapter prefixes. **No legacy `'usage:'`
  entry** — the deploy-time FLUSHDB (below) removes the orphan window
  entirely. `tests/unit/test_flask_cache_adapter.py` cross-checks the tuple
  against every live non-flask adapter's `_prefix` via `caching.adapters()`
  so a future sixth cache cannot silently regress it.
- **Unit pins** (`tests/unit/test_redis_cache.py::TestDerivedPrefixes`):
  per-factory prefix assertions (fakeredis) + cross-wipe regression.
- **No category changes**: `flask|chart|usage|scans|jobs` remains the
  `_VALID_CATEGORIES` set in `api/v1/admin.py`, the CLI, and the Admin card —
  the categories were always right; only their keyspaces overlapped.
- **`scripts/cirrus_redis_purge.sh`**: deploy-time cache flush for nwc1
  (dry-run by default, `--yes` to execute FLUSHDB on DB 0; `--pattern GLOB`
  for targeted SCAN→DEL cutovers). First Redis-mutating script in `scripts/`
  — healthcheck and weblog-audit stay read-only by design.

## Deploy

Cutover = flush, not migration: after this lands, old `usage:*` entries are
never read again. At the deploy window run

    scripts/cirrus_redis_purge.sh          # dry-run: shows DBSIZE + per-prefix counts
    scripts/cirrus_redis_purge.sh --yes    # FLUSHDB on DB 0

(house pattern from the CSP rollout; the app rebuilds caches on demand by
design). Rate-limit data lives in Redis DB 1 and is untouched. Without the
flush, orphaned `usage:*` keys would persist up to the fs_scans 8-day TTL and
count into the flask card's `other` group — cosmetic, but avoidable.

## Out of scope

- Plugin repo changes (none needed).
- Chart cache (`chart:`) and Flask-Caching (`flask_cache_`) keyspaces —
  already namespaced.
- Any change to cache TTL/size config keys or categories.
