# k8s Deployment Hardening — gunicorn worker model + fs-scans query bound

**Branch:** `k8s_deployment_hardening` · **PR base:** `staging` · **Deploy:** manual
`workflow_dispatch` of the CIRRUS deploy on this branch → deploy-probe-test iterate.

## Why

The fs-scans Phase 2/3 UI (PR #322) added the first **interactive, long-running,
connection-holding** routes. The deployment config was tuned for an era of
sub-second DB reads, so the numbers were inherited/arbitrary and a poor fit:

- `gunicorn_config.py`: `worker_class=sync` — one slow request pins a whole
  process. `workers` defaulted to `multiprocessing.cpu_count()*2+1`, where
  `cpu_count()` returns the **node's 64 cores**, so the helm template pinned it
  to `2×limits.cpu+1 = 33`. But that tracks the **burst limit (16)** while only
  **4 cores are guaranteed** (requests) → 33 processes contend for 4 cores.
- The fs-scans CNPG engine had **`statement_timeout = 0`** — a runaway scope
  rides a worker to the 120s gunicorn SIGKILL with no clean failure.

## Measurement that grounded the worker model

In-pod probe (build c9250db) of NRAL0002 whole-parent scans (on-the-fly path,
collections ncar+ral, 10 path_prefixes), splitting Postgres `execute` time from
Python time via SQLAlchemy cursor events:

| Scan | Wall | DB (Postgres) | Python |
|---|---|---|---|
| file_size_histogram | 61.4s | **52.3s (85%)** | 9.1s (15%) |
| access_history | 70.6s | **53.5s (76%)** | 17.2s (24%) |

→ **76–85% DB-bound.** psycopg2 releases the GIL during `execute`, so threads
overlap those waits → **gthread is the right model.** (Both authoritative numbers
are in-cluster — the in-pod probe and the gunicorn `%(D)s` request duration —
pod↔CNPG colocated, so the local laptop+VPN hop does not inflate them.)

Cutting the 52s of DB work itself is **plugin-side** (precompute more collection
roots / push aggregation into SQL) and is explicitly out of scope here.

## Changes in this PR (all SAM-side, no peer/plugin dependency)

1. **`containers/webapp/gunicorn_config.py`**
   - `worker_class` default `sync` → **`gthread`**; add `threads` (env `GUNICORN_THREADS`, default 4).
   - New `_effective_cpus()` reads the **cgroup** CPU quota (v2 `cpu.max`, then
     v1) instead of the node core count, so the default worker count is safe
     even if `GUNICORN_WORKERS` is unset.
2. **`helm/templates/deployment.yaml`** + **`helm/values.yaml`**
   - `GUNICORN_WORKER_CLASS=gthread`, `GUNICORN_THREADS=8`.
   - `GUNICORN_WORKERS` now derives from **`requests.cpu` (guaranteed)**:
     `2×4+1 = 9` (was 33). Tunable via `webapp.gunicorn.{workerClass,workers,threads}`.
   - Net: **9 processes × 8 threads = 72 concurrency** (was 33 sync / 33 procs)
     → lower memory, smaller CNPG connection fan-out, more concurrency.
3. **`src/webapp/config.py`** + **`src/webapp/disk_scans/session.py`**
   - `FS_SCAN_STATEMENT_TIMEOUT_MS` (default 100000 = 100s; below the 120s
     gunicorn `timeout`, above the ~70s legit-scan worst case). 0 disables.
   - The existing connect listener (`_attach_application_name` →
     `_apply_connection_settings`) now also issues `SET statement_timeout`. Value
     read once at init (warm pool runs in threads where `current_app` isn't bound).

`helm lint` clean; template renders class=gthread workers=9 threads=8.

## Deploy-probe-test checklist (iterate on this branch)

- [ ] `cirrus_healthcheck.sh` — 0 FAIL; confirm pod env shows class/workers/threads.
- [ ] `ps -e -o cmd | grep -c gunicorn` in-pod ≈ 9 workers (+ master), not 33.
- [ ] Re-walk fs-scans tabs for NMMM0003 (fast) + NRAL0002 (on-the-fly); confirm
      no thread-safety regressions (gthread is the main risk — watch for
      cross-request state bleed, session/engine sharing).
- [ ] Concurrency: fire a few simultaneous NRAL0002 scans; fast routes must stay
      responsive while a thread is parked on the 60s query.
- [ ] Verify a deliberately huge scope now fails at ~100s with a clean
      statement_timeout error (catchable), not a 120s SIGKill.
- [ ] Observe per-pod memory under gthread → then right-size `limits.memory`
      (currently 12G, sized for the old 33-process model) in a follow-up commit.

## Open follow-ups (NOT this PR)

- Plugin-side: precompute more collection roots so whole-lab-parent scopes hit
  the fast path instead of the 52s on-the-fly aggregation.
- Memory limit right-sizing (pending the observation above).
- Revisit cpu requests/limits coherence once worker count is validated.
