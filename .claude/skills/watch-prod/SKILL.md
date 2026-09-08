---
name: watch-prod
description: >-
  Watching the production SAM (samuel) release on nwc1 for anomalies on a
  recurring tick. Load before starting or resuming a prod-watch loop to run
  scripts/cirrus_watch.sh, read and classify what it reports (XRAS action_log
  rows, web/latency/cache/CronJob signals), and (re)start the Claude-side tick
  timer without spawning a duplicate — so a real regression is caught and a
  quiet hour stays one line.
---

# Watch production

An ordered procedure for keeping an eye on the public `samuel` release
(namespace `sam-queries`, cluster `nwc1`) between deploys. The mechanics live in
`scripts/cirrus_watch.sh` — a read-only *delta* tick. This skill carries the
judgment the script can't: what a line means, what to flag vs let ride, and how
to run the recurring wake without piling up duplicate timers.

Work top to bottom. Step 1 runs a tick; steps 2–5 read it; step 6 schedules the
recurring wake; step 7 is the traps.

## 1. Run a tick

```bash
scripts/cirrus_watch.sh                 # one tick; prints only what changed
scripts/cirrus_watch.sh --reset-baseline   # seed a fresh baseline, no report
```

Prerequisites: the VPN is up (the script TCP-probes the prod DB first and exits
cleanly if it can't reach it — VPN down is not a prod fault), `kubectl` targets
the `nwc1` context (pass `--context nwc1` if it isn't your current one), and a
read-only prod DB credential is available (mysql reads `~/.my.cnf`
automatically, else set `SAM_DB_USERNAME`/`SAM_DB_PASSWORD`). Exit code is
`0` quiet / `1` warn / `2` fail, so a scheduler can alert on it. State (the
last-seen XRAS id, image sha, and Redis counters) lives outside the repo under
`$XDG_STATE_HOME/sam-watch/`.

**Report only what changed since the last tick.** A quiet tick is one terse
line per section; escalate a *trend across ticks*, not a single outlier.

## 2. The web line

Steady state is one line. Raise it only on a real signal:

- **5xx > 0** — server errors (the script FAILs).
- **p95 > 4000ms** — latency regression.
- **4xx > 40%** of requests — heavy probing/scanning.
- **a probe-path hit** (`/.env`, `/wp-login`, `.php`, `/actuator`, …).
- **a NEW slow (>5s) endpoint.**

**Known-slow — do NOT re-flag:** `directory_access` ~6.9s, `fstree/Casper`
~3s DB plus an app-side tail that amplifies under load (measured; the old "~1.7s"
was optimistic). Both are **under active investigation** —
`docs/plans/FSTREE_LATENCY_INVESTIGATION.md` — so don't re-diagnose from scratch;
flag only a *material, sustained* worsening (running >5s across several
consecutive ticks, not one spike). The script prints this reminder under the slow
line for exactly this reason.

Also flag a **pod restart** or an **image-sha change** (a deploy) — the script
warns on both, comparing the sha to the last tick.

**The `load:` line + `split` (attribution).** Each tick prints a
`load:` line — `dbload:` (`threads_running` / `conns` / `slow_q` Δ from the
read-only `hpc-reader` `SHOW GLOBAL STATUS`) and `podcpu:` (`kubectl top`,
sum/max millicores across the webapp pods). On a slow (>5s) window the script also
prints a `↳ split` computed from the per-request `db=`/`cpu=` figures the app logs:
`total ~= cpu (compute/GIL) + db (DB wait) + rest (GIL/pool wait)`. Read it:

- **large `db` share** → DB-bound (the query itself).
- **large `cpu` share** → app compute — matplotlib render / Python aggregation
  (the GIL-bound path); fix is warming / render-trim.
- **`total` ≫ `db + cpu`** → the request was *waiting* (GIL contention or pool
  checkout), not computing → the sizing lever (workers/pool/HPA).

⚠️ `db=`/`cpu=` on the `↳ split` are **per-request accurate**; `dbload:`/`podcpu:`
are **point-in-time snapshots at tick time** and may not coincide with the slow
request's moment — trust the `split` for attribution, treat the snapshots as
ambient context. (`db=`/`cpu=` overlap slightly — result-parsing CPU counts in
both — so the split is approximate.)

## 3. The XRAS action_log line

`xras: quiet (still #N)` is the normal case. On new rows the script prints the
table and flags any non-2xx. Classify each non-200 by its `err`/`status`:

| Signature | Classification |
|---|---|
| a mnemonic-code error | tie-break / data — an org→mnemonic ambiguity |
| "no current institution/organization" | upstream affiliation gap (George's area) |
| "Cannot find contract" | missing contract |
| "Username … is missing" (`-user-<hex>` placeholder) | accounts-needed / unreconciled identity |
| a `rechecked` row | would-succeed now — **"ready for XRAS re-push"** |

Processed (2xx) rows → one line. Carry a short "known-open, don't re-flag" list
across ticks so the same unresolved rows don't re-alert every wake.

## 4. The daily full sweep is expected

`xras_sweep` runs a cheap `active` pass hourly and one heavy `full` rebuild a
day at the **10:07 UTC (04:00 MDT)** slot. A `[full]` pass there taking ~35–100s
is EXPECTED — flag it only if it *fails* or doesn't switch back to `active`
afterward. This is a Python task the `samuel-tasks` CronJob dispatches, not a
separate CronJob.

## 5. The samuel-tasks CronJob — report, never touch

The `tasks:` line reports the dispatcher's liveness: `suspend`,
`lastScheduleTime` age (stale past ~70 min = it has stopped waking), and failed
Job count. On a wedged state the script FAILs/WARNs and **prints** the manual
remedy — it never runs it:

```bash
kubectl -n sam-queries create job --from=cronjob/samuel-tasks samuel-tasks-manual
```

Re-triggering is a human decision (Ben owns deploy mechanics). It is *safe*
where it matters — the `task_run` ledger's UNIQUE `(task_name, occurrence_key)`
dedups a duplicate dispatch to a no-op — but **deleting a running mail task**
(`expiration_notices`, `xras_notices`) is UNSAFE: killing it mid-send lets the
next dispatch reclaim the lease and double-mail every PI. So: report it, hand it
off, don't automate it.

## 6. Scheduling the recurring wake ("restart only if necessary")

The tick repeats on a **Claude-side timer** (a scheduled cron, or a `/loop`),
NOT a cluster CronJob — the remote cron is hands-off (step 5). Cadence ~30 min.

**Before starting a watch timer, check one isn't already running, and only
(re)start if none is live** — never spawn a duplicate:

1. `CronList` (or check `/tasks`) for an existing watch cron/loop.
2. If one is already scheduled and healthy, leave it — do nothing.
3. If none exists (or it died), start one: a `CronCreate` cron at ~30-min
   cadence (or `/loop` in dynamic mode) whose prompt runs `scripts/cirrus_watch.sh`
   and reports only the deltas per steps 2–5.

A dropped VPN makes a tick a clean no-op (step 1), so the timer survives an
overnight VPN outage without alarms.

## 7. Traps

- **VPN preflight is load-bearing.** A fully-down VPN blackholes DNS/SYN and the
  mysql/kubectl connect timeouts don't cover it (a bare run once took ~1000s).
  The script probes TCP first and exits 0 with `OFFLINE:` — that is not a prod
  fault.
- **`kubectl logs -l <sel>` defaults to `--tail=10` per pod.** The script passes
  `--tail=-1`; without it the web section silently undercounts to ~10 lines/pod.
- **The Redis eviction delta is the real cache signal**, not the memory percent.
  With `allkeys-lru`, `evicted` rising between ticks means live entries are being
  dropped — raise `cache.maxmemoryMB`. A missing INFO field (db1 is absent when
  the ratelimit DB is empty) must not abort the run.
- **The prod DB is a read-only replica.** The XRAS read is a `SELECT`; never add
  a write path here.
- **State lives outside the repo.** Don't commit a state file; `--reset-baseline`
  re-seeds it after a long gap so historical rows aren't reported as new.
