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
| Limits | dev limiter tiers 100000/min; Ingress `limit-rps` 100 (burst ×5) and `limit-connections` 200 per client IP |
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

**A. Every API-key request costs ~260 ms of CPU before route code runs.**
`webapp/utils/api_auth.py` caches the credential rows but runs `bcrypt.checkpw`
(cost 12) on each request. A warm cache hit with no DB work still shows
`cpu=260ms`. Fix: cache the verification result (e.g. SHA-256 of the presented key
→ ok, short TTL) or a cheaper cost for M2M keys.

**C. API-key throughput per dev pod saturates at ~13 req/s at the 4-core limit**, all
of it bcrypt; latency then grows linearly with client count (pure queueing). Prod's
16-core pods and two replicas put the same ceiling near 100 req/s of API-key calls.
Same fix as A.

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
`time_appconnect` 0, DNS bypassed). The same run showed prod's hostname stalling
while dev, www.ucar.edu and the Postgres LB answered, and vice versa; the Postgres LB
never stalled. Platform-side (nginx-external / its LB), not SAM; prod users would see
it as a sporadic first-load hang. Hand to the platform team with this evidence.

**Migration blockers already known, confirmed here:** the webapp's theoretical pool
ceiling (9 workers × 30 × 2 pods = 540) exceeds `max_connections` 300, and the
cluster sets no `statement_timeout`.

## Tooling notes

- `cirrus_watch.sh --env dev` reports "kubectl logs unreachable" on an empty window
  and prints only the web section for dev.
- Readiness and health paths are limiter-exempt; never use them to test tiers.
- macOS `awk` lacks `match(s, re, arr)`; the `python` in `conda-env/` is the driver.
