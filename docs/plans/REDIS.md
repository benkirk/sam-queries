# Centralized Redis for shared cache + rate-limit storage

## Context

PR #238 (`caching_unified`) is merged. The empirical findings in `docs/plans/SHARED_CACHE_FINDINGS.md` prove that the unified cache machinery works correctly but is structurally limited: with `preload_app=True` and 33 gunicorn workers per pod across 2 pods, every worker holds its own private `OrderedDict` / `TTLCache` / `SimpleCache`. That gives **66 independent caches per deployment** and a best-case single-user hit rate of `~1/33`. Verified: an admin probe of `/allocations` showed 1 fast load (3.1s, warm worker) out of 8 attempts (16-19s on cold workers).

The fix is to replace the per-worker stores with a **shared Redis backend**. This unblocks:
- chart-cache hit rates approaching 100% across all workers
- consistent allocation-usage-cache behavior across workers
- the rate-limiting work (`docs/plans/RATE_LIMITING.md`) moving from per-worker `memory://` directly to Redis instead of MySQL/Postgres on `system_status` (per user decision)
- introspection of Flask-Cache groups (`directory_access`, `fstree_access`, `project_access`) becoming meaningful at deployment scope, not per-worker

Per user decision: **centralized**, **Redis** (not memcached — rate limiting motivates it), **single small Deployment + Service**, **no persistence, no HA** for now.

The `Caching` facade (`webapp.caching.Caching`) and the `CacheBase` contract (`sam.caching.base`) introduced by #238 are exactly the seams we need; this PR adds Redis-backed adapters that conform to the same interfaces, plus orchestration plumbing in Helm and compose.

## Decision summary

| Aspect | Choice |
|---|---|
| Topology | One Redis Deployment + ClusterIP Service in `sam-queries` namespace |
| Image | `redis:7-alpine` |
| Replicas | 1 (no HA) |
| Persistence | none (`--save ""`) |
| Eviction | `--maxmemory 64mb --maxmemory-policy allkeys-lru` (Redis-wide; per-cache `maxsize` bounds dropped) |
| Used for | chart cache, allocation usage cache, Flask-Cache HTTP layer, `RATELIMIT_STORAGE_URI` |
| Fallback | when `CACHE_REDIS_URL` unset/unreachable, all caches fall back to current per-worker behavior (per-worker `OrderedDict`, `TTLCache`, `SimpleCache`); webapp keeps serving |
| Compose parity | new `cache` service alongside `mysql`, used by both `webapp` and `webdev` |

## Files to modify

### Helm (k8s deployment)

1. **`helm/templates/redis-deployment.yaml`** (NEW) — `Deployment` with 1 replica, `redis:7-alpine`, args `--save "" --maxmemory <values>mb --maxmemory-policy allkeys-lru`, requests `32Mi/50m`, limits `96Mi/500m`. Labels `app: {{ .Values.cache.name }}`.

2. **`helm/templates/redis-service.yaml`** (NEW) — `ClusterIP` Service on port 6379, selector `app: {{ .Values.cache.name }}`. Pattern matches `helm/templates/service.yaml:1-13`.

3. **`helm/values.yaml`** — new top-level `cache:` block:
   ```yaml
   cache:
     enabled: true
     name: samuel-redis
     image: redis:7-alpine
     port: 6379
     maxmemoryMB: 64
     requests: { memory: 32Mi, cpu: 50m }
     limits:   { memory: 96Mi, cpu: 500m }
   ```

4. **`helm/values-local.yaml`** — scale-down override (`maxmemoryMB: 32`, smaller cpu requests).

5. **`helm/templates/deployment.yaml`** (modify line 28-29 area) — inject two env vars when `cache.enabled`:
   ```yaml
   {{- if .Values.cache.enabled }}
   - name: CACHE_REDIS_URL
     value: "redis://{{ .Values.cache.name }}.{{ .Release.Namespace }}.svc.cluster.local:{{ .Values.cache.port }}/0"
   - name: RATELIMIT_STORAGE_URI
     value: "redis://{{ .Values.cache.name }}.{{ .Release.Namespace }}.svc.cluster.local:{{ .Values.cache.port }}/1"
   {{- end }}
   ```
   Note the **DB number split**: cache on `/0`, rate-limit counters on `/1`. Keeps the two namespaces separately FLUSHable.

### docker-compose (local dev parity)

6. **`compose.yaml`** — add a `cache` service (mirrors mysql pattern at line 104-128):
   ```yaml
   cache:
     container_name: samuel-cache
     image: redis:7-alpine
     command: ["redis-server", "--save", "", "--maxmemory", "64mb", "--maxmemory-policy", "allkeys-lru"]
     networks: [sam-network]
     healthcheck:
       test: ["CMD", "redis-cli", "ping"]
       interval: 5s
       timeout: 3s
       retries: 5
     ports: ["6379:6379"]
   ```
   Update `webapp` and `webdev` `environment:` blocks to add:
   ```yaml
   - CACHE_REDIS_URL=${CACHE_REDIS_URL:-redis://cache:6379/0}
   - RATELIMIT_STORAGE_URI=${RATELIMIT_STORAGE_URI:-redis://cache:6379/1}
   ```
   And add `cache: { condition: service_healthy }` to both `depends_on:` blocks.

### Python — adapters

7. **`pyproject.toml`** — add `redis` to `dependencies` (line 22-46 area).

8. **`src/sam/caching/redis_ttl.py`** (NEW) — `RedisTTLAdapter(CacheBase)` exposing the same dict-like API that `usage_cache.py:138-163` already uses (`__contains__`, `__getitem__`, `__setitem__`, `pop`, `.lock`). The lock becomes a no-op context manager — Redis ops are atomic, and per-process locking can't coordinate across workers anyway. Keys are tuple-typed so we serialize with `pickle` and prefix with `usage:` for namespace isolation. Per-key TTL set on `__setitem__`. `info()` reports `currsize` via `SCAN` count of the prefix (not via `DBSIZE` — that would conflate with chart entries on the same DB).

9. **`src/webapp/caching/redis_chart.py`** (NEW) — `RedisChartCache(CacheBase)` exposing `get`/`put` like `ChartCache` (`src/webapp/caching/chart.py:33-103`). SVG strings stored under `chart:{name}:{key}` with per-cache TTL (default 600s, overridable per call). Per-cache `maxsize` bounds dropped — Redis-wide `allkeys-lru` policy handles eviction. `hits`/`misses` incremented via Redis `INCR` on `chart:hits:{name}` / `chart:misses:{name}` so counters survive worker rotation.

10. **`src/sam/caching/__init__.py`** — export `RedisTTLAdapter` alongside the existing `TTLCacheAdapter`.

11. **`src/webapp/caching/__init__.py`** (modify the `Caching` facade) — at construction, check `os.environ.get('CACHE_REDIS_URL')`:
    - if set → `self.flask = Cache(config={'CACHE_TYPE': 'RedisCache', 'CACHE_REDIS_URL': ...})`, register a Redis-backed flask adapter, and `chart_cached()` returns `RedisChartCache` instances
    - if not → current behavior (per-worker `Cache()`, `ChartCache(OrderedDict)`)
    - both branches keep the `CacheBase` contract; `adapters()` and `stats()` and `clear()` are unchanged
    - Wrap Redis client construction in a try/except: if `redis.ConnectionError` at startup, log a warning and fall through to in-process. **Graceful fallback is load-bearing.**

12. **`src/webapp/caching/flask_adapter.py`** — add a `_redis_introspect()` path: when `self._flask.cache` is a `RedisCache`, use `SCAN` (not `KEYS` — `KEYS` blocks Redis) to count entries by group prefix (`directory_access`, `fstree_access`, `project_access`), aggregate sizes via `MEMORY USAGE` on a sampled subset (full scan would be expensive). Falls back to the existing "not introspectable" placeholder if the client doesn't support `MEMORY USAGE`.

13. **`src/sam/queries/usage_cache.py`** — modify `get_cache_adapter()` (line ~58 area) to choose between `TTLCacheAdapter` and `RedisTTLAdapter` based on `os.environ.get('CACHE_REDIS_URL')`. The dict-like call sites at lines 138-163 are unchanged — both adapters expose the same API.

14. **`src/webapp/run.py`** (modify lines 103-109) — pick `CACHE_TYPE` from env:
    ```python
    if app.config.get('TESTING') or os.environ.get('FLASK_ENV') == 'testing':
        app.config['CACHE_TYPE'] = 'NullCache'
    elif os.environ.get('CACHE_REDIS_URL'):
        app.config.setdefault('CACHE_TYPE', 'RedisCache')
        app.config.setdefault('CACHE_REDIS_URL', os.environ['CACHE_REDIS_URL'])
    else:
        app.config.setdefault('CACHE_TYPE', 'SimpleCache')
    ```

### Tests

15. **`tests/unit/test_redis_cache.py`** (NEW) — fakeredis-backed tests for the new adapters: `RedisTTLAdapter` round-trips, TTL expiry, `info()` shape; `RedisChartCache` get/put + hit/miss counters; the `Caching` facade switches backends based on env.

16. **`tests/unit/test_allocations_performance.py`** — extend the existing `_reset_usage_cache_globals` autouse fixture to also reset the Redis adapter cache when env is set in test (use `monkeypatch.delenv('CACHE_REDIS_URL', raising=False)` for the bulk of tests; add a focused subset that sets it to a fakeredis URL).

17. **`pyproject.toml`** — add `fakeredis` to test dependencies.

### Docs

18. **`docs/plans/RATE_LIMITING.md`** — update the "Storage decision" paragraph (line 15) to:
    - Phase 1: `memory://` (unchanged)
    - Phase 2: **Redis** at `redis://samuel-redis:6379/1` (was: MySQL/Postgres on `system_status`). Reasoning: Redis is now provisioned for cache; rate-limit counters fit naturally; avoids write amplification on every-request DB hits. Update `RATELIMIT_STORAGE_URI` default in the plan's config snippet (line 59).

19. **`docs/plans/SHARED_CACHE_FINDINGS.md`** — annotate "Option A: shared backend" with a forward link to this PR; close it as "in progress / shipped" once merged.

### Audit step (per user request)

20. **Audit `src/webapp/api/v1/fstree_access.py`, `src/webapp/api/v1/directory_access.py`, and `src/webapp/api/v1/project_access.py`** — confirm they use `@cache.cached(...)` (or `@caching.flask.cached(...)`) so they pick up the Redis backend automatically when `CACHE_TYPE=RedisCache`. If any uses a bespoke caching pattern, migrate it to Flask-Caching for consistency. Verify the cache key includes user/IP scoping where appropriate (the existing `user_aware_cache_key` helper at `src/webapp/extensions.py:20-59`) so we don't leak cross-user data via shared keys.

## Implementation order (suggested commits)

1. **`deps + helm + compose`** — add `redis` to pyproject, create `helm/templates/redis-{deployment,service}.yaml`, update `helm/values.yaml` + `helm/values-local.yaml`, add `cache` service to `compose.yaml`. No Python changes yet — Redis exists but unused.

2. **`python adapters + tests`** — create `RedisTTLAdapter`, `RedisChartCache`, modify `Caching` facade and `usage_cache.get_cache_adapter()` to switch on env. Add fakeredis-based tests.

3. **`env injection + run.py wiring`** — add `CACHE_REDIS_URL` and `RATELIMIT_STORAGE_URI` env injection in `helm/templates/deployment.yaml` and `compose.yaml`; update `webapp/run.py` `CACHE_TYPE` selection. After this commit, the deployment uses Redis end-to-end.

4. **`audit + flask-cache call sites`** — review `fstree_access.py`, `directory_access.py`, `project_access.py` and any other `@cache.cached` users; migrate bespoke cachers if found; verify keying.

5. **`docs`** — update `RATE_LIMITING.md` Phase 2 + `SHARED_CACHE_FINDINGS.md` status.

## Verification

### Local (docker compose)

```bash
docker compose up cache webdev -d
# Verify Redis is reachable
docker compose exec cache redis-cli ping  # → PONG
# Verify webdev sees the env var
docker compose exec webdev env | grep CACHE_REDIS_URL
# Drive /allocations → expect Redis-cached SVGs after first hit
# Verify cache populated:
docker compose exec cache redis-cli --scan --pattern 'chart:*' | head
```

Admin Configuration card (`http://127.0.0.1:5050/admin/?tab=configuration`):
- Caching card shows `Flask-Cache backend: RedisCache`
- Chart SVG caches show non-zero entries after a `/allocations` page load
- Per-chart breakdown stats are stable across browser refreshes (no longer per-worker)

### Helm template render (no apply)

```bash
helm template helm -f helm/values.yaml | grep -A2 'kind: Service' | grep redis
helm template helm -f helm/values.yaml | grep -A1 CACHE_REDIS_URL
# expect: value: "redis://samuel-redis.sam-queries.svc.cluster.local:6379/0"
helm template helm -f helm/values.yaml | grep -A1 RATELIMIT_STORAGE_URI
# expect: value: "redis://samuel-redis.sam-queries.svc.cluster.local:6379/1"
```

### Production (after deploy to samuel.k8s.ucar.edu)

```bash
CTX=nwc1; NS=sam-queries
kubectl --context $CTX -n $NS get deploy samuel-redis
kubectl --context $CTX -n $NS get svc samuel-redis
kubectl --context $CTX -n $NS exec deploy/samuel-redis -- redis-cli info memory | grep used_memory_human
```

Then drive 8 cold `/allocations` loads via the established playwright probe:
- Expect: load 1 ~17s (cold cache), loads 2-8 ~2-5s (Redis hits across all workers)
- Admin Caching card hits/misses counters should accumulate consistently across Refresh-button rotations to different workers
- Memory: pods stay well under 12 GiB; Redis pod ~10 MiB resident

### Tests

```bash
source etc/config_env.sh && pytest
# expect: existing tests pass unchanged (no env var set → in-process fallback)
# expect: new tests in test_redis_cache.py pass via fakeredis
```

## Out of scope for this PR

- Redis HA / Sentinel / Cluster — single replica is intentional. Revisit when pod count grows or uptime SLA tightens.
- Persistence (RDB/AOF) — `--save ""` keeps Redis in-memory only. A pod restart loses cache; acceptable (cache misses, not data loss).
- Redis password auth — ClusterIP Service is namespace-internal only. If we expose Redis externally later, add auth then.
- Migration of Flask sessions to Redis — sessions stay client-side cookie-signed for now.
- Refactoring `usage_cache.py:138-163` away from the dict-like API — keeps both adapters interchangeable; cleaner refactor can come later.
- Admin "Clear cache by category" UI button — `caching.clear()` already supports it; route + button is a small follow-up.
- The unaddressed `redis-py` retry/timeout tuning — defaults are fine for pod-local network.
