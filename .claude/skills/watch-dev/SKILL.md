---
name: watch-dev
description: >-
  Checking the samuel-dev release (Argo app sam-query-dev, namespace
  sam-queries-dev on nwc1) after a staging merge or while working on dev. Load
  before running scripts/cirrus_watch.sh --env dev to confirm the cirrus-dev pin
  rolled and dev is healthy, read what the dev tick can and cannot see, and
  (only if asked) start a dev timer without duplicating the prod one.
---

# Watch dev

The dev counterpart of `watch-prod`, and deliberately thin: how to read a web,
split, cache or tasks line is the same, so read `watch-prod` §2, §5 and §7 for
that. This skill carries only what differs on dev. Dev pages nobody; the usual
job is a check after a deploy, not a standing watch.

## 1. Run a tick

```bash
scripts/cirrus_watch.sh --context nwc1 --env dev
```

`--env dev` selects namespace `sam-queries-dev`, release `samuel-dev`, host
`samuel-dev.k8s.ucar.edu` and its own state file (`sam-watch/state-dev`). The
preflight probes the ingress on 443, not a DB.

## 2. What the tick can see

| Line | On dev |
|---|---|
| `http: ready …` | **The outside-in health signal.** `/api/v1/health/ready` over HTTPS: `sam` must be healthy (FAIL otherwise); `system_status` down reads `degraded` (WARN). |
| `web:` / `pods:` / `cache:` / `tasks:` | As on prod (kubectl RBAC in `sam-queries-dev` landed 2026-09-26). One replica, one Redis, the dev dispatcher. |
| `k8s: no RBAC in sam-queries-dev` | Printed only for a user without kubectl access there: the four pod-log sections are skipped and `http:` is the one live line. Not a fault on dev. |
| `xras: skipped` / `dbload: skipped` | By design: XRAS never posts to dev, and dev SAM is Postgres. |

For the database side, run the peer repo's `cnpg_watch.sh --database sam_dev`
(recipe in `profile-dev` §3). nwc1 reports a namespace you have no RBAC in as
**NotFound**, so without access `cirrus_healthcheck.sh --env dev` fails at its
first check.

## 3. Did the pin roll?

A staging merge builds an image and pins `cirrus-dev` about 10 minutes later
(`git log -1 origin/cirrus-dev` names the sha); Argo then syncs `sam-query-dev`.
The tick's `pods: sha=` line names the served image; compare it to the pin.
Without kubectl, the Argo UI `sam-query-dev` app shows the synced revision, or
probe a route the change added or removed (for example `/dev/gallery/`, mounted
by #603: 302 to login when live, 404 when not).

`cirrus_healthcheck.sh --env dev` reads three WARNs on a healthy dev: the kill
switch (values-dev disables five tasks on purpose) and, during a roll, a startup
probe "connection refused" event. A PodDisruptionBudget is not expected at one
replica and the check says so.

## 4. What is normal on dev and not on prod

- Mail is off (`NOTIFY_ENABLED=0`) and XRAS is capture-only, so a task "sending
  nothing" is correct.
- The limiter tiers are effectively off, so a load test (`profile-dev`) looks like
  real load. Check who is driving before calling a spike a fault.
- `make refresh-dev` / `sync-dev` swap the databases and evict the pods'
  sessions. A burst of errors or a `degraded` `/ready` during a refresh is
  expected.
- A single slow `/ready` (7 s once, 2026-09-23, then 100 ms) is noise. Flag it
  only when it repeats across ticks.

## 5. Timer (only when asked)

Default to one tick after each deploy. If Ben asks for a standing dev watch,
follow `watch-prod` §6: `CronList` first and never duplicate. Use about 60
minutes, a prompt that names `--env dev`, and an off-minute distinct from the
prod timer's.
