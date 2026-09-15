# samuel-dev load campaign, 2026-09-13

**Status: DONE (measurements). Findings A–F below are open hardening items.** First
laptop-driven load campaign against samuel-dev, watched on three sides at once
(`scripts/cirrus_watch.sh --env dev`, the prod tick, and hpc-usage-queries'
`cnpg_watch.sh`), ending with a CNPG switchover under load. Driver and raw CSVs
were throwaway (scratchpad); the numbers are here.

## Setup

| | |
|---|---|
| SAM | samuel-dev rev 2, image sha-8e3b48c, 1 replica, gunicorn gthread 3 workers × 8 threads (24 slots), pod requests 1 CPU / 1 GiB, limits 4 CPU / 4 GiB, no HPA |
| DB | `sam_dev` (407 MB obfuscated clone) + `system_status_dev` on the shared CNPG `csg-postgres` (2 instances, no Pooler, `max_connections` 300, ~21 in use at start, `work_mem` 64 MB, no `statement_timeout`) |
| Pool | `sam` engine `pool_size` 10 + `max_overflow` 20 per worker, `pool_pre_ping`, `connect_timeout` 10 s, keepalives, `target_session_attrs=read-write` |
| Limits | dev limiter tiers 100000/min; Ingress annotations declare `limit-rps` 100 (burst ×5) and `limit-connections` 200 — but see the note below the P1 rerun: 180 req/s from one IP was served without a single rejection |
| Client | one laptop, API key `collector` (Basic auth), Python `requests` driver, 8–32 threads |

## P0 — single client, cold (after `POST …/refresh`) then warm

| Endpoint | cold client / server | warm server | `cpu=` | `sam=` on the miss | prod MySQL reference |
|---|---|---|---|---|---|
| `/users/benkirk` | — | ~290 ms | 275–300 ms | 4.7 ms / 18 q | — |
| `/queue/Derecho` | 4402 / 279 ms | ~262 ms | ~265 ms | 3.6 ms / 1 q | — |
| `/disk_quota/` | 1520 / 1306 ms | ~265 ms | 1176 ms | 143 ms / 10 q | — |
| `/fstree_access/Casper` | 1193 / 573 ms | ~262 ms | 427 ms | 148 ms / 6 q | ~3.3 s db, 3.5–4.3 s wall |
| `/directory_access/` | 2089 / 1005 ms | ~270 ms | 596 ms | 415 ms / 12 q | ~7 s wall |

The 4.4 s on the first `queue` GET was outside the app (server 279 ms); see F.

## P1 — app-tier ceiling through the Ingress

| Run | Result |
|---|---|
| `/health/live`, 16 clients, capped 80 req/s, 30 s | 2413 req, 0 errors, p50 33 / p95 95 / p99 124 ms |
| `/users/<u>`, 8 clients, 45 s | 13.5 req/s, p50 586 ms |
| `/users/<u>`, 16 clients | 12.8 req/s, p50 1216 ms |
| `/users/<u>`, 32 clients | 12.5 req/s, p50 2004 ms, p99 4104 ms, 0 errors |

Pod CPU sat at 3.9–4.0 cores (the limit) for the whole `/users` ladder; memory
stayed under 500 MiB. `kubectl port-forward` was tried first and rejected as a
transport: 36 ms median but an 8–9 s tail and failed requests at 32 clients.

### P1 repeated after the fix (#561, image sha-1dd1516, 21:0xZ)

Same ladder, same client, same endpoints, with `API_KEY_VERIFY_TTL` at its 300 s
default:

| Run | Before | After | Change |
|---|---|---|---|
| `/users/<u>`, 8 clients | 13.5 req/s, p50 586 ms | 121.0 req/s, p50 60 ms | 9.0× |
| `/users/<u>`, 16 clients | 12.8 req/s, p50 1216 ms | 122.1 req/s, p50 81 ms | 9.5× |
| `/users/<u>`, 32 clients | 12.5 req/s, p50 2004 ms | 179.7 req/s, p50 164 ms, p99 943 ms | 14.4× |

Single-client repeat calls: first request 456 ms server-side (`cpu=442`, cold
worker plus the one bcrypt), every later one **15 ms** server-side with `cpu=12`,
`sam=4.0 ms/18q` — against ~290 ms and `cpu=275–300` before.

The bottleneck moved off the app. Across the 20 897 requests of the ladder the
median server time was 37 ms = `cpu` 7.5 + `sam` 26.9, so a request is now
database-dominated, and the pod drew 1.4 cores instead of pinning at 4. One
request in the 16-client run hit the 60 s client timeout: that is the Ingress
connect stall of finding F, not the app.

**Correction to an earlier claim.** Before the fix the app could not exceed 13 req/s,
so the Ingress `limit-rps: 100` annotation was never tested and was written up as the
real single-client ceiling. The rerun sustained 121–180 req/s from one IP with zero
429s and zero 503s, so that annotation does not bite the way the number suggests —
nginx applies `limit_req` per worker process, and the burst multiplier absorbs the
rest. Treat the declared limits as a floor on what is allowed, not a measured cap.

## P3 — write path, `POST /api/v1/status/derecho`, 5/s × 5 min

1415 POSTs, all 201, p50 309 / p95 416 / p99 508 ms; no server-side POST over 1 s;
CNPG: 36 connections peak, no temp spill, replication in sync. Two client-side
stalls of ~9 s (see F).

## P2 — cold-miss herd (refresh, then N callers at once)

| Endpoint | N | slowest caller | vs single cold | `pg_stat_statements` Δ calls |
|---|---|---|---|---|
| fstree/Casper | 4 / 8 / 16 | 1.75 / 2.37 / 4.34 s | 1.5× / 2.0× / 3.6× | +29 for 28 callers |
| directory_access | 4 / 8 | 2.75 / 4.43 s | 1.3× / 2.1× | +12 for 12 callers |

Every caller rebuilt (no dogpile lock). Server median under the 16-herd was 2164 ms =
cpu 470 + sam 247 + ~1.4 s waiting on the 4-core limit; the DB side stayed cheap
(3.3 s total for the 12 heaviest directory_access executions). `sam_dev` connections
peaked at 17 and stayed pooled afterwards; CNPG temp spill +69 MB, no long queries.

## P4 — CNPG switchover under load

Mixed load (users ×3, queue, fstree warm, status POST) at 8 clients, ~5 req/s, for
12 min. hpc-usage-queries #118 (memory 65Gi → 64Gi, a pure pod-spec change) was
merged at 19:24:24Z after two minutes of clean traffic; Argo applied it about three
minutes later.

| UTC | Event |
|---|---|
| 19:27:34 | `csg-postgres-2` (replica) recreated at 64Gi |
| 19:28:04.2 | first client 500; dev `/ready` 503 from 19:28:06 |
| 19:28:13.3 | last client 500 (30 in 9.1 s); `/ready` 200 again at 19:28:14 |
| 19:28:21 | `csg-postgres-1` recreated, back as replica; primary is now `-2` |

- 3438 requests, 30 × HTTP 500, p50 316 / p95 862 ms, **zero errors after 19:28:13**.
- The 500s were fast (~300 ms), never hung: `pool_pre_ping` invalidated the pool and
  the reconnect failed with "the database system is shutting down" (4), "server
  closed the connection" (5), "Connection refused" (6), or "Operation not permitted"
  (9, the primary LB with no endpoint). Longest request in the window 4.9 s.
- Dev readiness: two 503 polls, one kubelet Unhealthy event, under `failureThreshold`
  3, so the pod was never pulled from the Service.
- Prod pods logged two `readiness degraded` WARNINGs (their status bind) and no 503.
- CNPG afterwards: healthy, replication in sync, no temp spill, no ERROR/FATAL.
- The peer `cnpg_watch.sh` tick that started at 19:26:44 blocked for ~2.5 min while
  the cluster switched (its exec/psql calls wait); note for that skill.

## Findings

**A. Every API-key request cost ~260 ms of CPU before route code ran. FIXED (#561).**
`webapp/utils/api_auth.py` cached the credential rows but ran `bcrypt.checkpw`
(cost 12) on each request, so a warm cache hit with no DB work still showed
`cpu=260ms`. `API_KEY_VERIFY_TTL` (default 300 s) now remembers a successful
verification per worker, keyed by username, stored hash and a SHA-256 of the
presented key; failures are never cached, so brute-force cost is unchanged.
Measured after: `cpu` 12 ms on a repeat call.

**C. API-key throughput per dev pod saturated at ~13 req/s at the 4-core limit**,
all of it bcrypt, with latency growing linearly with client count. **FIXED with A**:
121–180 req/s on the same pod, drawing 1.4 cores. The remaining cost is the query
itself (18 statements for one user), so the next lever, if one is ever wanted, is
that route rather than the auth layer.

**B. Postgres beats prod MySQL on the legacy heavy queries** (obfuscated clone caveat):
fstree db 148 ms vs ~3.3 s, directory_access 415 ms vs ~7 s wall. A migration datum,
not a hardening item.

**D. The cache herd is real but app-tier.** N cold callers = N rebuilds; the cost
multiplier comes from CPU contention, not the DB. A dogpile lock caps it at one
rebuild. Lower priority than A/C.

**E. A CNPG roll on the required bind costs a ~9 s window of fast 500s** with
switchover + the readiness/connect hardening. No hangs, no stuck pool, no pod
eviction. Invisible needs a CNPG `Pooler` (holds client connections across the
switch) and/or a one-shot retry on `OperationalError` at connect time.

**F. ~8 s connect stalls at the shared Ingress, with or without load.** Roughly every
1–2 min a new TCP connection to 128.117.41.126 gets no SYN answer for 8 s (curl
`time_appconnect` 0, DNS bypassed). A 5-minute discriminator (150 rounds, 2 s apart,
four targets per round) counted 11 stalls on the Ingress hostnames (7 dev, 4 prod,
never both in the same round) and 0 on www.ucar.edu and 0 on the Postgres LB
(`csg-postgres.k8s.ucar.edu:5432`, a different LB IP on the same cluster).
Platform-side (nginx-external / its LB), not SAM; prod users would see it as a
sporadic first-load hang. Hand to the platform team with this evidence.

**Migration blockers already known, confirmed here:** the webapp's theoretical pool
ceiling (9 workers × 30 × 2 pods = 540) exceeds `max_connections` 300, and the
cluster sets no `statement_timeout`.

## Next steps

### The session round — what a logged-in browser adds

The `collector` key reaches only the legacy blueprints, so the whole `@login_required`
surface went untested: the allocation and charge rollups, every matplotlib chart, and
the dashboards themselves. That is the CPU/GIL-bound half of the app — the half where
the pod's core limit still binds now that the auth cost is gone. The prod tick on the
afternoon of this campaign made the point: the one new slow endpoint it flagged,
`/user/resource-details/<projcode>` at 7.9 s and 98% database time, is a route nothing
here could reach.

Playwright is not a load generator; it drives one browser at human pace. Its two uses
are **fidelity** — the real asset, htmx and chart path a user sees — and
**authentication**: log in once, save the storage state, let the driver replay that
cookie at concurrency. Ranked by what it would teach us:

1. **Charts, and the dashboards carrying them.** `/allocations/projects` is the
   heaviest single page: a full-page GET rendering pies inline plus one pace-chart
   fragment per resource. Server-rendered SVG under the GIL is the "large cpu share"
   regime, and on three workers concurrent page loads are its pathological case.
2. **Allocation-usage and charge rollups.** The summary routes dump one usage schema
   per account in a Python loop. Finding B compares Postgres to MySQL on the legacy
   blueprints only; these are the queries Horizon 2 actually turns on.
3. **The read-model fallbacks.** Every request here logged `rm=served`. Those two
   rollup routes are the token's main producers, six distinct reasons force the `live`
   path, and none of their costs are measured.
4. **Per-user cache cardinality.** Rendered HTML is keyed by user first, then path,
   query string, facility scope, layout and theme, so N users are N entries. The chart
   SVGs are the opposite, keyed on data content and shared across users — the per-user
   cost is the HTML, not the pictures. One browser as one user therefore measures only
   the warm path; sizing `cache.maxmemoryMB` needs distinct users or query strings.
   This campaign saw `evicted 0` throughout under a single identity, which proves
   nothing about the real key space.
5. **Real-browser request counts.** A warm browser issues no request at all for a
   `?v=`-tagged asset and a conditional one for the rest. The measurement behind the
   static-asset work — 87.5% of requests to `/static`, 96% of those answered 304 — is
   a browser phenomenon, as is the true gunicorn `max_requests` recycle rate; curl
   measures neither. A session spanning a deploy also surfaces the documented window
   where cached HTML keeps emitting a stale `?v=`.
6. **CSRF and htmx swaps under load** — a correctness question rather than a
   throughput one. Every POST here was a `@csrf.exempt` API route.

The tooling for this round now lives in `scripts/` (graduated from the throwaway
driver):

- `scripts/dev_capture_session.py` — headed Playwright; log in + 2FA once, it
  writes a `storage_state.json` outside the repo (a credential).
- `scripts/dev_session_load.py` — replays that cookie at concurrency against a
  ranked session-only target list (`--list`), client latency/throughput/status
  only, `X-Request-ID` recorded for pod-log correlation. Refuses a non-dev base.
- `e2e/conftest.py` honors `SAM_E2E_STORAGE_STATE`, so the single-browser
  fidelity sweep runs against samuel-dev:
  `make e2e SAM_E2E_BASE_URL=https://samuel-dev.k8s.ucar.edu SAM_E2E_STORAGE_STATE=<file>`.

Watched on three sides during a run: the driver, SAM's `scripts/cirrus_watch.sh
--env dev` (app/logs), and the CNPG `cnpg_watch.sh --context nwc1 --database
sam_dev` from `hpc-usage-queries` (its `watch-cnpg` skill).

Rules that still hold:

- **Do not load-test the login path.** `RATELIMIT_AUTH_LOGIN` keeps the prod default
  on dev deliberately, and the dev render test pins its absence from the overlay. It
  covers the login POST and the OIDC callback, per client IP, so the sixth callback in
  a minute is a 429 and every worker behind one egress IP shares that budget. It is
  also real traffic to the Entra tenant. Log in once.
- The session cookie is a credential: environment only, never the repo.
- Interpretation caveat: dev is one replica of four cores against prod's two of
  sixteen, on an obfuscated clone, so GIL-bound chart numbers are indicative only.

### Open items

| Item | Next move |
|---|---|
| F, Ingress connect stalls | Platform ticket carrying the discriminator evidence |
| D, no cache dogpile lock | Decide before Horizon 2; cheap, and the herd is app-tier |
| Pool ceiling 540 > `max_connections` 300 | A CNPG `Pooler` or a webapp pool resize, before Horizon 2 |
| No `statement_timeout` on the cluster | Cluster-side decision |
| E, ~9 s of fast 500s on a roll | A `Pooler` and/or a one-shot retry on connect |
| `/user/resource-details/<projcode>` at 7.9 s | First target of the session round; one occurrence so far |

## Tooling notes

- `cirrus_watch.sh --env dev` reports "kubectl logs unreachable" on an empty window
  and prints only the web section for dev.
- Readiness and health paths are limiter-exempt; never use them to test tiers.
- macOS `awk` lacks `match(s, re, arr)`; the `python` in `conda-env/` is the driver.
