# fstree / pace-chart latency investigation

Status: **open** — instruments landed (this PR); conclusion pending in-prod data.

## Problem

Prod watch caught `GET /api/v1/fstree_access/Casper` (and
`/allocations/htmx/pace-chart/Derecho`) running **~9.7s wall** across consecutive
ticks under load. These are known-expensive endpoints, but the size of the tail
and its appearance under traffic bursts warranted attribution: is the time in the
**database** (sam-sql) or in the **app tier** (sam.hpc)?

## Findings so far

**The database is healthy; the query is genuinely ~3s.** A read-only probe ran
the real `get_fstree_data('Casper')` code path against sam-sql (`hpc-reader`,
`SELECT`-only):

- run 1 (cold) 3.34s, run 2 (warm) 2.94s
- during: `Threads_running` 2, `Slow_queries` Δ0, `Innodb_buffer_pool_reads` Δ0
  (served from the buffer pool, no disk I/O)

The measurement was taken from a laptop over the VPN, and the build is
multi-query, so each round-trip adds VPN latency — i.e. ~3s is an **upper bound**
on the app's in-cluster DB time. So of the ~9.7s app-observed wall, at most ~3s
is DB, and **~6–7s is app-tier**.

**Sizing (from code):**

| | value |
|---|---|
| gunicorn (gthread) | 9 workers × 8 threads = 72 slots/pod; 2 pods; no HPA |
| main MySQL pool (fstree uses it) | `pool_size=10 + max_overflow=20` = 30 conns/worker |
| `pool_timeout` | unset → 30s default |
| `preload_app` | True |
| mem limit | 12G (still sized for the retired 33-process model) |

With 30 connections per worker for only 8 threads, **connection-pool exhaustion
is unlikely** to be the cause. The stronger remaining hypothesis is **GIL/CPU
contention** during the Python-heavy phases (fstree's multi-query aggregation,
pace-chart's matplotlib render) when a worker's 8 threads overlap under load. The
one "pods idle at 2–4 millicores" reading was taken at a quiet moment, **not**
during a slow window, so it is not yet evidence.

## What the instruments in this PR add

- **Per-request `db=Xms q=N`** on the app log line (`webapp/request_timing.py`) —
  the DB-vs-app split on every request, including the exact fstree/pace-chart
  requests. `db=/q=` is also appended to the `Slow request:` warning line.
- **Watch `load:` line** (`scripts/cirrus_watch.sh`) — `dbload:` (Threads_running,
  Threads_connected, Slow_queries Δ) + `podcpu:` (sum/max millicores). The
  pod-CPU-during-window signal that was missing, plus a `db split` note computed
  from the slow-request lines.

## Data-collection plan (once this PR is live in prod)

1. Watch a slow window; read the slow-request `↳ db split` note — it gives
   `db≈Xms / total≈Yms`. If `total ≫ db`, the gap is app-side.
2. Read the `podcpu:` on the same tick:
   - **high CPU** → GIL/app-CPU contention (the lead hypothesis).
   - **idle CPU** → queueing / connection wait.
3. Rule the pool in/out from the **existing** admin config card
   (`webapp/utils/config_inspect.py:94-140`, `checked_out / pool_size / overflow /
   utilization_pct`) during a slow window — no new code needed.
4. Confirm `preload_app=True` pools are per-worker: SQLAlchemy QueuePool opens no
   connection until checkout, so nothing is shared at fork; verify via per-pod
   `checked_out` on the pool card.

## Recommendation (to be filled from the data above)

Candidate levers, in the order the data should be consulted before choosing:

- **query/render cost** — reduce the fstree date-group fan-out or the pace-chart
  cold-cache cost (a real ~3s DB cost is worth trimming regardless).
- **worker/thread mix** — if GIL-bound, more processes (fewer threads) or more
  pods helps; if I/O-bound, the current gthread mix is right.
- **explicit `pool_timeout`** — only if the pool card shows saturation.
- **HPA** — none exists today; 2 static pods may be the real ceiling under bursts.

Any of these is a **separate deploy PR** (deploy mechanics are owned separately),
made on the data, not on this investigation alone.

## Related

- `docs/plans/implemented/K8S_DEPLOYMENT_HARDENING.md` — the gthread sizing model
  and its own deferred follow-ups (memory right-sizing, cpu request/limit
  coherence). This DB-pool-vs-worker question is not covered there.
- pace-chart also has a separate daily-cold usage-cache tail (its own follow-up).
