---
name: profile-dev
description: >-
  Profiling the samuel-dev interactive surface under a real OIDC session on a
  laptop. Load before running an interactive load/latency pass to capture a
  session once, drive scripts/dev_session_load.py against the right ranked
  targets, and read the three-sided watch (driver + cirrus_watch --env dev +
  the peer cnpg_watch) — so a cost lands on the right axis (DB, plugin, GIL) and
  a healthy surface stays one line.
---

# Profile dev interactively

An ordered procedure for characterizing the `samuel-dev` `@login_required`
surface (namespace `sam-queries-dev`, cluster `nwc1`, host `samuel-dev.k8s.ucar.edu`)
with one captured browser session replayed at concurrency. The mechanics live in
two scripts — `scripts/dev_capture_session.py` and `scripts/dev_session_load.py`
— plus the two existing watchers. This skill carries the judgment they can't:
which targets matter, how to read the per-request split, and the caveats already
measured (recorded in `docs/plans/DEV_LOAD_CAMPAIGN.md`).

Work top to bottom. Step 1 captures a session; step 2 drives load; step 3 watches
three sides and attributes cost; step 4 is the shape the numbers already take;
step 5 is the traps.

## 1. Capture a session once

```bash
python scripts/dev_capture_session.py    # headed browser; log in + 2FA; writes storage_state.json
export SAM_E2E_STORAGE_STATE=<the path it prints>
```

Or reuse a live Playwright browser: after a real login, `context.storageState({path})`
into the scratchpad. **Log in ONCE** — `RATELIMIT_AUTH_LOGIN` keeps the prod
default on dev deliberately (per-IP, covers the OIDC callback, is real Entra
traffic). The cookie is a credential: it lives in the scratchpad or an env var,
**never the repo, never a log**. If a request starts returning 302→`/auth/login`
the session expired — recapture.

## 2. Drive load

```bash
python scripts/dev_session_load.py --list          # the target catalog
python scripts/dev_session_load.py --target allocations_projects --clients 16 --duration 20
```

It replays the cookie, records client latency / throughput / status and each
`X-Request-ID`, and **refuses a non-samuel-dev base** unless `--allow-nondev`.
Flags: `--clients/--duration|--count`, `--target …|--all`, `--vary`, and the
substitutions `--projcode/--resource/--username/--machine/--days/--date`.

The ranked catalog, by what it exercises:

- **Cached surface** — `allocations_projects` (per-user HTML + shared chart-SVG
  cache), `charges_summary`, `project_allocations` (read-model rollups). Steady
  state; the GIL only shows with a cold chart cache (step 4).
- **Uncached tree routes** — the `rd_*` group (`resource_details`,
  `rd_usage_chart`, `rd_user_pie`, `rd_disk_chart`, `rd_user_subtree`,
  `rd_day_subtree`, `user_tree`). No route cache, so single-request latency *is*
  the interactive cost. Point them at a **wide tree** — `SCSG0001` is a leaf; find
  a broad one with a SQL query on `sam_dev` (a `project` whose
  `tree_right - tree_left > 1` joined through `account`→`resources` to the
  resource, e.g. `NCGD0006` ~54 descendants, `NMMM0003` ~30). Disk group:
  `--resource Campaign_Store` (fsscans plugin via `scan_overview`).
- **Plugin charts** — `jobs_card` / `jobs_by_user` (the jobhistory plugin,
  embedded lazily on the HPC resource-details page). `--days 365` over a wide tree
  is the single heaviest interactive call.

## 3. Watch three sides, read the split

Run alongside the driver, both on their own baselines:

```bash
scripts/cirrus_watch.sh --env dev                                   # app / pod logs
( cd ../../hpc-usage-queries/devel && scripts/cnpg_watch.sh --context nwc1 \
      --database sam_dev --reset-baseline )                         # DB, seed first
```

`cirrus_watch --env dev` skips the XRAS/db-load reads (dev SAM is Postgres) and
prints the web line + a `↳ split` on any slow (>5 s) request. Those pod-log
sections need kubectl RBAC in `sam-queries-dev`; without it the tick says
`k8s: no RBAC` and only the `http:` line is live (see the `watch-dev` skill). **Most profiling
targets are sub-second and never trip that split** — grep the pod log directly
for one request's tokens by its `rid` (= the driver's `X-Request-ID`):

```bash
kubectl -n sam-queries-dev logs deploy/samuel-dev --tail=1500 | grep '<rid or route>'
```

Read `total ~= cpu + Σ <db>=Xms/Nq + pool + rest`, where the DB labels are
`sam` / `status` / `jobhistory` / `fsscans`:

- **a named DB dominates** → that store is the cost. `jobhistory` / `fsscans` are
  the plugin CNPG databases (on `csg-postgres-ro`), `sam` the primary app DB.
- **large `cpu`** → app compute (matplotlib render / Python aggregation), the
  GIL-bound path.
- **`rest` dominates** → the request was *waiting* (GIL contention / pool
  checkout), not computing → the sizing lever.

For the DB side, `cnpg_watch` reports cluster phase, connections vs
`max_connections`, temp spill, replication, slow (≥10 s) logs. Before looping it
on a Claude-side timer, `CronList` / check `/tasks` and don't spawn a duplicate
(its own `watch-cnpg` skill covers this). Its per-DB probes read `csg-postgres`,
which hosts `sam_dev`, `system_status_dev` **and** the plugin stores.

## 4. The shape the numbers already take (measured)

`docs/plans/DEV_LOAD_CAMPAIGN.md` is the record; the standing conclusions:

- **The cached surface is well designed.** Warm: `rm=served` dominates the
  rollups, ~42 req/s on `allocations_projects` at 32 clients, DB conns bounded,
  no 5xx. Don't re-litigate this.
- **The GIL wall is a COLD-cache phenomenon (finding D).** The chart-SVG cache is
  content-keyed and shared, so `--vary` alone can't force a cold render — it still
  serves the cached SVG. Flush first (`sam-admin cache --refresh --category chart`)
  then `--vary`: 32 concurrent cold renders → p50 16.5 s, `rest≈86 %` (GIL wait),
  still zero 5xx. The one prod trigger is a post-deploy cache-refresh under load.
- **The uncached tree routes are DB-light.** Even on the 54-node tree they stay
  under ~320 ms p50; the fsscans disk path is sub-second. No route-level cache is
  warranted — they are already cheap.
- **The one heavy interactive plugin call is the jobs chart at a wide window.**
  `jobs_by_user --days 365` over a broad tree ≈ 2.6 s cold, **92 % of it the
  `jobhistory` plugin query**, cached to ~15 ms after; `--days 30` ≈ 160 ms.
  Plugin-DB-bound, not GIL — a different axis. Narrowing the window or tree scope
  removes it.
- **dev is 1 replica × 4 cores vs prod 2 × 16, on an obfuscated clone** — every
  absolute here is indicative; trends and attributions carry, magnitudes don't.

## 5. Traps

- **Never drive the login path.** `RATELIMIT_AUTH_LOGIN` is on by design, per-IP,
  and hits the real Entra tenant. One capture per session.
- **The session cookie is a credential** — scratchpad/env only, never the repo,
  never a log. Recapture on a 302→login.
- **`--vary` only defeats an HTML-response cache.** For a cold chart render, flush
  `--category chart`; for the uncached tree routes it changes nothing (they always
  pay the query). Vary `--days` / `--resource` / the projcode to move the *data*.
- **Fast routes get no `↳ split`** — grep the pod log by `rid` for the
  `cpu=/sam=/jobhistory=/fsscans=/rest=` tokens; the driver prints each `rid`.
- **`require_project_access(include_ancestors=True)` gates the tree routes.** The
  captured user needs membership/lead/admin of the projcode's tree **or**
  `VIEW_PROJECTS` (an admin like `benkirk` reaches any; a scoped user won't — a
  302/403 there is authorization, not a bug).
- **Pick a real wide tree.** `SCSG0001` is a leaf and hides the subtree-walk cost;
  query `sam_dev` for a broad one (§2).

Dev-hammering constraints and the deploy/refresh mechanics:
`docs/plans/K8S_DEV_ENVIRONMENT.md` § 6.5.
