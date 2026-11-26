# Durable logs: an in-namespace log archiver

Shipped to samuel-dev first (`logArchiver.enabled` in `helm/values-dev.yaml`).
Production stays off until Ben turns it on; see "Enabling on prod".

## The problem

Pod stdout is the only log sink SAMuel can read, and it lives exactly as long as
the pod. Every deploy empties it: on 2026-11-26 the prod pods were 42 hours old,
so a planned one-week rate-limit measurement could cover 48 hours. CIRRUS keeps
cluster logs, but that store is outside our RBAC. Also lost: a task Job's output
once the CronJob's history limit (3 successful, 5 failed) deletes its pod, and
any proof of "no calls from X" over weeks.

A tenant on nwc1 cannot run a DaemonSet or mount a hostPath, so a node-level
shipper (Fluent Bit, Vector) is out. What the tenant can do is read its own pods'
logs through the Kubernetes API and write to a volume. That is the whole design.

## The design

One small always-on pod per environment copies every samuel pod's log to a
persistent volume, keeps it 90 days, and answers `cat` and `status`.

| Piece | Where | What it does |
|---|---|---|
| The package | `src/logarchive/` | stdlib plus `requests`; imports nothing from `sam`, `webapp`, `system_status` or `cli` (gated by `tests/unit/gates/test_layer_imports.py`) |
| `kube.py` | | two API calls: list pods by label, follow one container's log. Reads the mounted service-account token and re-reads it on a 401 |
| `store.py` | | the files: `<root>/<app label>/<pod>__<container>.log`, each line as the API gave it (RFC3339Nano timestamp first). A `.log.gz` means "finished, never fetch again" |
| `follow.py` | | the loop: every 10 s list the pods, keep one follower thread per started container, finalize files whose pod is gone, prune hourly, touch `.heartbeat` |
| `read.py` | | merges the files overlapping a window in time order, plain and gzipped alike |
| The chart | `helm/templates/log-archiver.yaml` | ServiceAccount, Role (`get`, `list` on pods; `get` on pods/log), RoleBinding, and a one-replica StatefulSet with a `volumeClaimTemplates` claim |
| The reader | `scripts/cirrus_logs.sh` | `kubectl exec <archiver pod> -- python -m logarchive cat ...`, optionally through grep |
| The watch | `scripts/cirrus_watch.sh` | the `logs:` line and its size guard |

**The archive is its own state.** There is no cursor file to drift: on every
(re)connect a follower reads the last timestamp from its file's tail and asks
the API for everything since.

**Isolation.** The webapp never talks to the archiver. It is not in `/ready`,
not in the Redis NetworkPolicy, and holds no database credential or app secret.
If it dies we lose archive, not traffic. It is the only pod in the chart that
mounts a service-account token.

**Why a StatefulSet.** A claim made from `volumeClaimTemplates` is not pruned
when the flag goes off or Argo prunes, and the retention policy is `Retain`.
Turning the archiver off never deletes the archive.

## Traps the code honors

- **Follow, do not poll.** A webapp pod is deleted seconds after it terminates
  on a rollout. Only an open follow stream delivers its last lines, and a roll is
  when they matter. Task pods linger about 45 minutes, so they are safe either way.
- **Timestamps do not compare as strings.** Go's RFC3339Nano trims trailing zeros
  (`...:01.5Z` sorts after `...:01.50001Z`). Everything compares
  `(epoch seconds, nanoseconds)`.
- **`sinceTime` has second granularity**, so a resume re-delivers the cursor's
  second. The follower drops lines older than the cursor and, of the lines equal
  to it, the first N, where N is how many the file already holds at that stamp.
- **A dropped stream proves nothing.** A file is finalized only after a stream
  that began on an already-finished container ran to a clean end, or the API
  answered 404 and the pod listing agrees. A read timeout (a quiet pod, 300 s)
  or a reset reconnects.
- **A container that will restart is not finished.** A terminated container in
  a Deployment pod runs again; only a pod in `Succeeded`/`Failed`, a
  `restartPolicy: Never` container, or a pod that is gone is final.
- **Log timestamps are UTC from the API** and are stored as they arrive. The
  naive-Mountain rule for SAM data does not apply here.
- **A torn last line** (a kill or a full disk mid-write) is cut back to the last
  newline before the cursor is read; the stream re-delivers it.

## The loss window

Lines are lost only when a pod starts **and is deleted** while the archiver
itself is down, or when the archiver is down longer than the kubelet keeps a
container's log (10 Mi per container by default). An archiver restart alone
loses nothing: it resumes from each file's tail. The window is stated, not
closed; the watch flags a down archiver so it stays short.

## Size and the guard

About 22k lines and 5.5 MB a day raw on prod (estimated 2026-11-26), so 90 days
is under 1 GB before gzip on a 5 Gi volume. Measured on dev: recorded here after
the dev soak.

An accidentally verbose logger must not fill the disk unnoticed:

- `status --json` reports bytes, volume size and free space, the oldest file's
  date and the largest live file.
- The watch prints growth as a per-day rate between ticks and WARNs above
  50 MB a day (about ten times the estimate), naming the largest live file: that
  is the verbose pod. It WARNs at 60% full and FAILs at 85%
  (`WATCH_LOGS_*` overrides).
- The archiver protects itself: at 90% full it deletes the oldest finished files
  ahead of retention until under 80%, one WARNING per pass. A write that fails
  (ENOSPC or otherwise) is logged and retried every 30 s; it never crash-loops
  and never deletes a live file. Shortened retention shows as the watch line's
  `oldest:` date moving forward.

## Reading it

```bash
scripts/cirrus_logs.sh --env dev --since 7d -- -F rate_limit_exceeded
scripts/cirrus_logs.sh --env dev --app tasks --since 3d --timestamps
scripts/cirrus_weblog_audit.sh --env dev --archive --since 7d
```

`cat` prints lines exactly as `kubectl logs` would (the timestamp is stripped
unless `--timestamps`), so the existing awk parsers read it unchanged. With the
archiver absent, `cirrus_logs.sh` says so and exits 0.

## Enabling on prod

One line, Ben's change: `logArchiver.enabled: true` in `helm/values.yaml` (the
name `samuel-logs` and the 5 Gi `ceph-kubepv` claim are already there). Update
`helm/tests/test-dev-render.sh`, which pins prod off, in the same change. The
claim's size and class are immutable once the StatefulSet exists.

## What it is not

- Not backed up: one RWO volume. It is evidence for weeks, not a system of record.
- Not a search UI and not parsed: grep over `cat`. Loki, Grafana, or parsed rows
  in Postgres would be a later layer on top of the archive.
- Not the model audit log. `/var/log/sam/model_audit.log` stays a file in the
  pod. Sending audit lines to stdout, where the archiver would keep them, is a
  separate small PR after a look at what the lines contain.
