# Shared cache findings

Investigation date: 2026-05-06
Branches probed: `main` (sha-fda98ab), `gunicorn_worker_fix` (sha-cb2ef93, since merged as PR #240), `caching_unified` (sha-8008045, draft PR #238 rebased onto post-#240 staging).
Site: samuel.k8s.ucar.edu (cluster `nwc1`, namespace `sam-queries`).

## TL;DR

The unified `Caching` facade introduced in `caching_unified` is functionally correct and safely deployable — it doesn't regress memory, doesn't OOM, and surfaces useful admin observability. **But the chart caches it manages don't materially speed up `/allocations` in production.**

The reason isn't the new code — it's a structural property of the deployment that pre-existed the PR: **every gunicorn worker holds its own private `OrderedDict` cache.** With `preload_app = True` and 33 workers per pod × 2 pods = **66 independent caches per deployment**, the best a single user can hope for is a `1/33` hit rate on the warm pod and `0/33` on the cold one.

The PR is mergeable as-is for the operability/visibility benefits. The performance win for `/allocations` requires a separate change: either a shared cache backend (Redis) or a different render strategy (per-chart HTMX fragments cached at the HTTP layer).

## What we measured

Identical playwright probe (8 cold `/allocations` loads, sequential, signed in as `benkirk`) against three deployments, with `kubectl top pods` sampled every 3 seconds.

| Metric | sha-fda98ab (pre-fix) | sha-cb2ef93 (gunicorn fix) | sha-8008045 (+ caching_unified) |
|---|---|---|---|
| Workers per pod | 132 | 33 + 1 master | 33 + 1 master |
| Baseline RSS (fresh boot) | ~3 GiB | ~520 MiB | ~520 MiB |
| RSS after 8 cold loads | OOMKilled at load 2 | ~1.6 GiB | 1.7–1.9 GiB |
| Pod restarts during probe | 1 (OOMKilled at 11.4 GiB) | 0 | 0 |
| Wall time, cold render | 16–19 s | 16–19 s | 16–26 s |
| Wall time, warm-worker hit | n/a | n/a | **3.1 s** (1 of 8) |
| Admin Caching card hits/misses | n/a (no card) | n/a | 0 / 0 (on the queried worker) |
| Admin Caching card entries | n/a | n/a | 0 / N for all 8 chart caches |

The single 3.1 s response on the caching deploy is the load that happened to land on a worker that had served the same request earlier in the probe. Every other request landed on a worker whose cache was still cold.

## Root cause

`containers/webapp/gunicorn_config.py` sets `preload_app = True`. The webapp is imported once in the master, then forked. Every module-level singleton — including `caching = Caching()` in `webapp/caching/__init__.py`, the `flask_caching.Cache()` SimpleCache backing dict, and each `ChartCache(name=…, maxsize=…)` registered via `caching.chart_cached(...)` — is a copy-on-write inheritance from the master into each worker.

The first time a worker calls `cache.put(key, svg)`, the COW page is broken and the OrderedDict becomes uniquely the property of that worker. A second worker serving the next request has its own pristine OrderedDict; it pays full matplotlib cost, dirties its own COW page, and stores the result locally.

There is no IPC between workers. There is no shared backend. SimpleCache is `cachelib.simple.SimpleCache`, which is in-process. cachetools.TTLCache likewise.

So the effective cache behaviour at the deployment level:
- 66 independent caches (2 pods × 33 workers)
- Each user's repeated requests are spread across workers by the load balancer / gunicorn's request distribution
- A cache entry warmed in worker N is invisible to workers ≠ N
- The admin Caching card queries whichever single worker happens to handle the request — its `0 / 0` is correct for that worker, just not representative of the deployment

## Per-worker memory evidence

A `kubectl exec ... ps -eo pid,rss,cmd | grep gunicorn` snapshot during the probe on the caching deploy showed most workers around 170–190 MiB RSS, with a single outlier at ~388 MiB. That outlier is the worker that warmed its chart cache. The COW divergence is real and observable.

## Why `/allocations` rendering is expensive in the first place

A single `/allocations` page renders ~30 SVGs synchronously inside the template:
- 10 facility pies (Allocated tab) — `facility_pie_chart` cache
- 10 facility pies (Usage tab)
- 10 pace charts (Pace tab) — `pace_chart` cache
- plus assorted timeseries, stacked-area, allocation-type pies

Each SVG is a matplotlib figure → `savefig(format='svg')` round-trip. matplotlib first-render on a cold worker is dominated by font discovery, locator setup, and axes layout. That's where the 16–19 s comes from. Caching at the SVG-string level (what `ChartCache` does) is exactly the right shape — but only useful if the cache is shared.

## Recommendations

### Option A: Move to a shared backend (the proper fix) — **in progress**

> **Status (2026-05-06):** implemented on branch `add_redis` per `docs/plans/REDIS.md`.
> A single-replica Redis Deployment (`samuel-redis`, `redis:7-alpine`, no
> persistence, `allkeys-lru` eviction at 64 MiB) is provisioned in the
> `sam-queries` namespace. New adapters `RedisTTLAdapter` (allocation
> usage) and `RedisChartCache` (matplotlib SVGs) sit behind the existing
> `CacheBase` contract; the `Caching` facade switches backends based on
> `CACHE_REDIS_URL`. Flask-Caching switches to `RedisCache`. Graceful
> fallback to per-worker behavior when Redis is unreachable. Same Redis
> (DB `/1`) is now the planned Phase-2 target for `RATE_LIMITING.md`.

Configure Flask-Caching with a Redis or Memcached backend, and route the chart caches through it. With Helm we can add a Redis sidecar or use an existing cluster Redis.

- Pros: One cache shared across all 66 workers. First user warms; everyone else benefits. Hit rate goes from `1/66` to ~`100%` for stable URLs.
- Cons: Adds an infrastructure dependency. Need to handle Redis being unreachable (fallback to per-worker).

The unified `Caching` facade in this PR is the right place to plumb this. `webapp/caching/flask_adapter.py` already abstracts the backend; we'd swap `CACHE_TYPE=SimpleCache` for `CACHE_TYPE=RedisCache` plus a `CACHE_REDIS_URL`. The `ChartCache` instances would need to be re-implemented as Redis-backed (or replaced by a `flask-caching` decorator). The `CacheBase` contract already gives us the abstraction layer.

**Audit note (verify-only):** `src/webapp/api/v1/{fstree,directory,project}_access.py` use
`@cache.cached(query_string=True)` and `@cache.memoize()`. Cache keys are URL/argument-only,
not user-scoped. Response bodies for these routes are deterministic functions of URL params
and DB state — no `current_user` dependency — so sharing them across all VIEW_PROJECTS /
VIEW_USERS holders via Redis is safe. (Adopting `user_aware_cache_key` is a follow-up if a
future route is found to vary by user.)

### Option B: Per-chart HTMX fragments cached at the HTTP layer

Instead of inlining 30 SVGs in the `/allocations` HTML, return a skeleton page that loads each chart via an HTMX endpoint. Apply `@cache.cached(timeout=N, query_string=True)` per endpoint.

- Pros: Initial page load returns instantly (the 30 SVGs load progressively). Cache works at the URL level — same caveat about per-worker SimpleCache, but with Redis (option A) becomes truly shared. Each chart cacheable independently with its own TTL.
- Cons: Bigger refactor. Changes user-visible behaviour (progressive rendering vs single-shot).

### Option C: Accept the limitation; document it

Merge `caching_unified` for the observability benefits. Accept that chart caching only helps power users who hit `/allocations` frequently enough to keep multiple workers warm. Document in the admin card that the displayed stats are per-worker.

- Pros: Zero additional work. The gunicorn fix already ships the big production win.
- Cons: Doesn't solve the underlying problem. Future "why is /allocations still slow?" debugging will rediscover this.

### Suggested ordering

1. Merge `caching_unified` (PR #238) — it's the foundation, regardless of which path we take next.
2. Open a follow-up issue tracking option A (Redis-backed shared cache) as the medium-term fix.
3. Lightly clarify the admin Caching card to say "per-worker (this process only)" so future readers don't repeat this confusion.

## Reproducing the per-worker effect

```bash
CTX=nwc1; NS=sam-queries
POD=$(kubectl --context $CTX -n $NS get pods -l app=samuel -o jsonpath='{.items[0].metadata.name}')

# Worker RSS distribution during traffic — look for outlier (warmed worker).
kubectl --context $CTX -n $NS exec $POD -- \
  sh -c 'ps -eo pid,rss,cmd | grep "gunicorn -c" | sort -k2 -n -r | head'
```

If you see most workers within ~20% of each other and one or two ~2× larger, those outliers are warmed. That's the structural shape of the per-worker cache problem.

## Out of scope for this writeup

- Choice of shared backend (Redis vs Memcached vs disk-cache) — should be decided alongside the rate-limiting plan in `docs/plans/RATE_LIMITING.md`, since the same shared backend can serve both.
- Hit-rate measurement: `cachetools.TTLCache` doesn't natively count hits/misses, so the admin card surfaces `None` for those today. Worth fixing in the per-worker counters so we can validate any future shared-cache work has the expected effect.
