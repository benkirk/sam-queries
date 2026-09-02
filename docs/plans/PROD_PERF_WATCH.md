# Production performance watch — baseline and follow-ups

A read-only characterization of how the public SAM webapp performs in
production, captured 2026-09-02 (week 2 of the XRAS cutover, while traffic was
light and the deployment still new). It records the baseline, the read-only
signals available, and the slow-endpoint follow-ups worth investigating.

## Baseline (16h window, 5,457 requests, ~341/hr)

Measured from `kubectl logs` of the `samuel` pods (`sha-b1f0efa`).

| Signal | Value |
|---|---|
| 5xx | **0** |
| 4xx | 67 (1.2%) — mostly XRAS people-lookup 404s for placeholder/guest ids, plus `/robots.txt` and `/wp-login.php` scanner noise |
| static : non-static | **0.3 : 1** (the static-asset caching in CLAUDE.md §11 is holding; the pre-cache baseline was ~7:1) |
| Bytes served | ~1.3 GB |
| Latency (non-static) | p50 **179 ms**, p90 1.6 s, p95 **2.95 s**, p99 **6.7 s**, max 24 s |

The median is healthy; the story is the **tail**, and the tail is almost
entirely the legacy-compat integration endpoints serving large JSON to the
`ssg` consumer (and other machine clients — `collector`, `XRAS`).

| Endpoint | calls/16h | avg latency | MB/16h |
|---|---|---|---|
| `/api/v1/directory_access/` | 66 | **6,885 ms** | 170 |
| `/api/v1/fstree_access/Casper` | 320 | **1,734 ms** | 218 |
| `/api/v1/fstree_access/Derecho` | 320 | 1,115 ms | 215 |
| `/api/v1/fstree_access/*GPU` | ~960 | 150–350 ms | ~440 |

Every `Slow request:` warning (>5 s, emitted by
[`src/webapp/run.py`](../../src/webapp/run.py)) in the window is one of
`directory_access` or `fstree_access/Casper`.

## Read-only signals available in prod

There is no Prometheus / `/metrics` / statsd endpoint and no `X-Response-Time`
header. What exists, all read-only:

- **`kubectl logs -l app=samuel --tail=-1`** — two line formats on one stdout:
  - gunicorn access line
    ([`containers/webapp/gunicorn_config.py`](../../containers/webapp/gunicorn_config.py)):
    carries `%(D)s` response time (microseconds, `µs`-suffixed), response size,
    and a trailing `xff="…"`. Successful `/health` lines are filtered out at the
    source.
  - app request line ([`src/webapp/run.py`](../../src/webapp/run.py),
    [`src/webapp/logging_config.py`](../../src/webapp/logging_config.py)):
    `METHOD path → status (N.N ms) rid=…`, plus a `Slow request: N ms` warning
    above 5,000 ms.
- **Redis** — chart cache hit/miss counters in DB 0 (`chart:hits:<name>` /
  `chart:misses:<name>`); rate-limit events in DB 1 (`ratelimit:events`).
- **`GET /api/v1/health/`** (public JSON, per-DB `latency_ms` + schema drift)
  and **`/api/v1/health/db-pool`** (admin, connection-pool stats) —
  [`src/webapp/api/v1/health.py`](../../src/webapp/api/v1/health.py).
- **`tests/perf/baselines.json`** — per-route SQL query-count baselines, naming
  the heavy routes (`/admin/htmx/institutions-fragment`, `/user/`,
  `/allocations/`, `/api/v1/fstree_access/`).

Cache and rate-limit **stats** are HTML/HTMX admin pages only, no JSON
equivalent.

## Tooling trap fixed here

`kubectl logs -l <selector>` defaults to **`--tail=10` per pod**, and `--since`
does not lift that cap. [`scripts/cirrus_weblog_audit.sh`](../../scripts/cirrus_weblog_audit.sh)
omitted `--tail`, so it harvested ~10 lines/pod and undercounted every section
(a 6h run reported ~10 requests instead of ~3,000). Fixed by adding `--tail=-1`
to the single harvest, with a comment naming the trap.

**Follow-up:** [`scripts/cirrus_healthcheck.sh`](../../scripts/cirrus_healthcheck.sh)
§10 reads "the last 500 webapp lines" through the same selector and may share
the cap — verify and fix separately.

## Slow-endpoint follow-ups

Both offenders are legacy-compat API blueprints that mirror the legacy Java
response shape for systems-integration consumers (see CLAUDE.md § API —
"legacy-compat blueprints, DO NOT REFACTOR"). The response bytes must not
change, so any remedy is **caching or query-shape**, never a response redesign.

1. **`fstree_access/Casper` averages 1.7 s despite a documented 5-minute
   cache.** `tests/perf/baselines.json` notes `fstree_api_route` is "cached 5min
   in prod." Investigate whether the cache is missing (short TTL vs request
   spacing, per-resource key cardinality) or whether the cost is JSON
   serialization of a ~200 MB/16h payload after the cache hit —
   [`src/webapp/api/v1/fstree_access.py`](../../src/webapp/api/v1/fstree_access.py).
2. **`directory_access/` averages 6.9 s for ~2.7 MB per call**, uncached —
   [`src/webapp/api/v1/directory_access.py`](../../src/webapp/api/v1/directory_access.py).
   Assess an additive cache (the consumer polls it hourly) and the query shape
   behind the payload.

Neither is urgent — 0 5xx, the callers are batch integrations, and the median
user request is fast. This note tracks them so the tail is watched rather than
forgotten.
