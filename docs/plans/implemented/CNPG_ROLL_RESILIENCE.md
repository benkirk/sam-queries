# Surviving a csg-postgres roll

**Status: IMPLEMENTED both sides and VERIFIED 2026-09-13** (SAM #556 deployed
14:00Z; hpc-usage-queries #114 merged, #115 drove the verifying roll at 14:26Z).
Record of the 2026-09-13 outage, why a secondary database took the whole site down,
what changed on both sides, and what a roll costs now.

## What happened

| UTC | Event |
|---|---|
| 03:11 | hpc-usage-queries #113 merged: CNPG `resources.limits.cpu` 8 → 4. A pod-spec change; CNPG recreates both instances. |
| 03:22:06 | `csg-postgres-2` (replica) recreated. |
| 03:23:57 | `csg-postgres-1` (primary) shut down for its in-place restart. `csg-postgres-service` (the LB, selector `cnpg.io/instanceRole: primary`) has no endpoint from here. |
| 03:23:57–03:27:26 | 27 × `GET /api/v1/health/ready → 503` across both samuel pods, every one with `sam=4.5ms/1q` (MySQL fine) — the `system_status` ping refused. Readiness `10 s × 3` → both pods NotReady → the samuel Service had no endpoints. |
| 03:26:51 | new primary pod started (image pull 6 s; the rest is PVC reattach + recovery). |
| 03:27:26 | last 503; readiness back within one period. |

~3.5 minutes with the site unreachable, caused by a database that only the status
dashboards read, on a night when the primary was healthy.

## Why

Two defaults compounded:

1. **CNPG `primaryUpdateMethod` defaults to `restart`.** The chart set no update
   strategy at all. Replica first, then the primary restarted *in place* — a full
   stop/recreate/recover of the primary pod with no endpoint behind the LB.
2. **SAM's `/ready` treated every bind as required.** `_collect_health` pinged
   `sam` and `system_status` in one loop and either failure was a 503. Both
   replicas resolve the same `csg-postgres.k8s.ucar.edu`, so a cluster-wide
   Postgres event fails every pod's probe at once, and readiness — which exists to
   pull *one bad pod* — emptied the Service. The same file already argued this for
   schema drift; fs-scans and job_history (the other two csg-postgres consumers)
   were already fail-soft and absent from `/ready`. `system_status` was the odd one
   out.

A third, latent: no engine set `connect_timeout` or keepalives, so a probe hung in
`engine.connect()` for the OS SYN timeout while the kubelet had given up at 5 s —
one gthread per probe, every 10 s, per pod.

## What changed

**SAM (this repo, PR from `cnpg-roll-resilience`):**

- `/api/v1/health/ready` gates on the primary `sam` bind only. A secondary bind
  failing is reported (`checks.system_status.status: unhealthy`, `required:
  false`), the body says `degraded`, the status is 200, and a WARNING is logged
  (P1-55). `?strict=1` restores 503-on-any-bind; compose healthchecks use it for
  startup ordering. `/` (monitoring) is unchanged: 503 on any bind or drift.
- `sam.session.connect_args` — every SAM engine's one funnel (webapp `sam` +
  `system_status` binds, `create_sam_engine`, `create_status_engine`) — sets
  `connect_timeout` (`SAM_DB_CONNECT_TIMEOUT`, default 10 s) on both drivers and,
  on Postgres, TCP keepalives (30/10/3) and `target_session_attrs=read-write`, so a
  dead or demoted primary fails fast instead of hanging.
- Tests: `tests/api/test_health_endpoints.py::TestSecondaryBindContract`,
  `tests/unit/test_sam_session_url.py`.

**CNPG (hpc-usage-queries, `helm/templates/postgres_cluster.yaml`):**
`primaryUpdateStrategy: unsupervised`, `primaryUpdateMethod: switchover`,
`switchoverDelay: 60`, `smartShutdownTimeout: 30`, and the watch-cnpg skill
describes what a roll now looks like.

## Can a roll be non-disruptive?

Nearly. With switchover, the write path drops for the seconds a promotion takes
(the LB IP is stable; its endpoint flips with the `instanceRole` label), in-flight
requests on the old primary fail once, and `pool_pre_ping` reconnects everything
after. Readiness no longer notices. The `-ro` LB still empties while the single
replica rebuilds (2 instances); its consumers degrade per panel.

Invisible needs a CNPG `Pooler` (pgbouncer) holding client connections across the
switch, or `instances: 3` to keep `-ro` populated. Both are cluster-side
decisions for Ben; neither is needed for the status dashboards.

**Horizon 2 implication** (`docs/plans/implemented/POSTGRES_MIGRATION.md`): once
the SAM database itself lives on csg-postgres, that seconds-long blip is on the
*required* bind. Readiness will (correctly) 503 for those seconds; with
`failureThreshold: 3 × 10 s` it will usually not even trip. The levers above are
what make that acceptable; a `Pooler` is what would make it invisible.

## Verified (2026-09-13, both changes live)

| Experiment | Exercised | Primary gap | SAM `/ready` | Whole roll |
|---|---|---|---|---|
| 14:15Z Ben deleted the primary pod (#114 had applied in place, no roll) | CNPG **failover** | ~16 s | `degraded` ≤16 s, 0 × 503 | 65 s |
| 14:26Z hpc-usage-queries #115 (`memory` 64→65Gi, a pod-spec change) | CNPG **switchover**, the #114 path | <1 s | never left `healthy` at a 15 s poll | 59 s |

The switchover roll, from a 15 s monitor on the pod labels and `/ready`: `-1`
(replica) recreated 14:26:04 → promoted ~14:26:20 (SAM logged two `readiness
degraded (still serving)` WARNINGs one second apart) → `-2` recreated 14:26:37 →
replica by 14:27:03. Pod logs for the window: 0 readiness 503s, 0 5xx; no
`Unhealthy` event on the samuel pods. Overnight the same class of change was 3.5
minutes of full-site outage.

Deleting a pod is a **failover**; only an operator-driven roll (image, `resources`,
restart-class parameters) takes the `primaryUpdateMethod` path. The primary now
alternates between `csg-postgres-1` and `-2` after each roll (on `-1` since
14:26Z); the peer repo's `cnpg_watch.sh` prints that as a `FAILOVER` line, which
during a known roll is the switchover, not an incident. #115's 65Gi is kept.
