# fstree / pace-chart latency investigation

Status: **instruments live in prod** (#531, merged to staging→main). Live
per-request data collected 2026-09-08. **Verdict: the cold-miss tail is DB-bound
(~86%), not app-tier/GIL.** Provisional call: **accept-as-is is viable while it
holds ~4s cold; warming / read-model are the levers if it regresses.**

## Problem

Prod watch caught `GET /api/v1/fstree_access/Casper` (and
`/allocations/htmx/pace-chart/Derecho`) running **~9.7s wall** across consecutive
ticks under load. These are known-expensive endpoints, but the size of the tail
and its appearance under traffic bursts warranted attribution: is the time in the
**database** (sam-sql) or in the **app tier** (sam.hpc)?

## Verdict from live instruments (2026-09-08) — DB-bound

Once #531 shipped, the per-request `db=`/`cpu=` split settled the attribution.
Live prod (fresh pods, cold caches):

| fstree variant | cold-miss total | db | cpu |
|---|---|---|---|
| Derecho / Casper (base) | ~3.5–4.3s | ~3.3s (**~86%**) | ~0.6s |
| GPU variants, Gust | <0.8s | small | small |
| warm hits (all) | 70–180ms | 0–90ms | ~70ms |

The cold miss is **~86% database** — the live charge rollup over the allocation
tree — with only ~0.6s of Python/CPU. This **overturns the app-tier / GIL
hypothesis** in the section below (inferred from a VPN-inflated upper bound before
the instruments existed). The GIL/render frame is real for **pace-chart** (whose
same split reads CPU-heavy), not for fstree.

**The overnight ~9s creep** is this same cold miss with its **db step swollen
under diurnal load** on the shared MySQL VM (another team's box) — the ~3.3s DB
portion stretches when the VM is busy. The variability is DB-side, not ours.

**Cache sharing confirmed:** the fstree cache is shared across pods (RedisCache,
`flask_cache_` prefix, URL-keyed), so with many PBS pollers only the *first* after
each TTL expiry pays the miss. But the poll cadence (~5 min) ≈ the TTL, so that
first-miss recurs ~every cycle.

## Findings before the instruments (historical; hypothesis above overturns this)

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
is unlikely** to be the cause. The working hypothesis here was **GIL/CPU
contention** during the Python-heavy phases — **now overturned for fstree** by the
live `db=`/`cpu=` split (see the verdict at the top: the tail is DB, ~0.6s CPU).
It remains the right frame for pace-chart's CPU-heavy render.

## What the instruments in this PR add

- **Per-request `db=Xms cpu=Yms pgdb=Zms q=N pq=M`** on the app log line
  (`webapp/request_timing.py` + `run.py`) — partitions every request:
  `total ~= cpu (compute/GIL, via time.thread_time) + db (SAM/MySQL wait) +
  pgdb (plugin CNPG wait: job-history, fs-scans) + rest (GIL/pool wait)`.
  Per-request accurate. Also appended to the `Slow request:` warning line.
  `pgdb=` exists because a 19 s jobs drill-down once logged `db=8ms`: the
  plugin engines were uninstrumented and their time hid in `rest`.
- **Watch `load:` line** (`scripts/cirrus_watch.sh`) — `dbload:` (Threads_running,
  Threads_connected, Slow_queries Δ) + `podcpu:` (sum/max millicores), plus a
  `↳ split` note (db/cpu/total) computed from the slow-request lines. The
  `dbload:`/`podcpu:` are tick-time snapshots (ambient context); the per-request
  `↳ split` is the trustworthy attribution.

## Data-collection plan (DONE — answered by the verdict at top)

The steps below were the plan; the live `db=`/`cpu=` split (verdict section)
executed step 1 and settled the attribution as DB-bound. Retained as the method.

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

## Recommendation

The tail is DB-bound and currently holds ~4s cold; **accept-as-is is a legitimate
call** while it stays there — the shared cache means only one poller per cycle
waits, nobody is erroring. Revisit only on a sustained regression (cold misses
>5s across many cycles, or the 9s creep becoming routine). The lever is **not**
gunicorn sizing (the request is waiting on the DB, not queued on workers) — it is
to move the rollup **off the request path**, two rungs:

### The two levers: warming vs read-model

- **Warming (cheapest, reversible).** A scheduled job recomputes the existing
  Redis entry just before its TTL, so live requests never hit a cold miss. Same
  code, same cache; the ~3.3s work moves to a task pod. It **reshapes who waits,
  it does not reduce DB work** — the query still runs each interval.
- **Read-model (durable).** Project the rollup into a table we own (ideally the
  CNPG Postgres), read by a trivial indexed `SELECT`: compute-once-read-many, so
  it **is** strictly less DB load. Rung 2 = scheduled ETL into CNPG (consumers
  already tolerate ~5-min staleness); rung 3 = the full CNPG cutover. **Avoid**
  live MySQL→PG CDC — a stateful mirror is the fragile option.

Selection: goal is user latency → warming suffices. Goal is to spare the shared
MySQL VM itself → **read-model**, since warming adds steady load.

### Cadence / contention caveat (before building warming)

The `samuel-tasks` dispatcher fires ~hourly and the schedule vocabulary has no
minute-granularity type; warming wants a ~2–4 min interval. So: (a) add a
short-interval `Schedule` + fire a dispatcher every minute, **or** a dedicated
fast warm CronJob (`*/2`) separate from the hourly suite (smaller blast radius);
(b) `concurrencyPolicy: Forbid` + a short `expected_runtime` lease so a slow run
cannot overlap (the ledger's `UNIQUE(task_name, occurrence_key)` already dedups a
double-dispatch); (c) Job churn — a per-minute dispatcher is ~1,440 pods/day, so
tighten history limits or prefer the dedicated CronJob. And warming *faster* than
the current effective miss rate adds net DB load — another nudge toward
read-model if the DB is the constraint.

Any chosen fix is a **separate deploy PR** (deploy mechanics owned separately).

## Tactical options (smaller than the two levers above)

- **Raise the fstree TTL above the poll cadence** — one line, `timeout=900` at
  `src/webapp/api/v1/fstree_access.py:54`. Byte-safe. Tradeoff: fairshare up to
  15 min stale — an SSG-owner call. Narrows the miss rate cheaply, but the first
  poll each TTL still pays full cost: a stopgap, not a fix.
- **Push the fstree rollup into SQL** — it does a live MPTT rollup over the tree,
  not a pre-aggregated summary table; this is the DB-side form of the read-model
  and the highest-leverage query change now that the tail is confirmed DB-bound.
- **Drop `bbox_inches='tight'`** (`src/webapp/dashboards/charts/base.py:53`) —
  **demoted**: fstree's CPU is trivial and pace-chart's is ~0.6s, so render-trim
  is not material for this problem. Keep only as a general chart-cost cleanup
  (needs `CHART_FINGERPRINT_REGEN=1` + a visual check).

Each chosen fix is its own PR.

## Related

- `docs/plans/implemented/K8S_DEPLOYMENT_HARDENING.md` — the gthread sizing model
  and its own deferred follow-ups (memory right-sizing, cpu request/limit
  coherence). This DB-pool-vs-worker question is not covered there.
- pace-chart also has a separate daily-cold usage-cache tail (its own follow-up).
