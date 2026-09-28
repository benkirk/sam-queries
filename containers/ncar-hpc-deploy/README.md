# ncar-hpc-deploy

Runs the HPC-host scheduled jobs (job_history ingest, SAM charge summaries,
status collectors) from a **blessed container image** instead of live git
checkouts and conda envs. It uses the published `webapp` image, which already
carries the whole repo, `sam-admin`, `jobhist-sync` and the collectors.
Apptainer runs it on casper and derecho (`module load apptainer`).

The container stays out of sight: `bin/sam-admin`, `bin/sam-search` and
`bin/jobhist-sync` are symlinks to `libexec/wrap.sh`, which dispatches on its
own name. `bin/ncar-hpc-deploy` is the scheduler-facing CLI.

## Lanes

| lane | tracks | writes | cron |
|---|---|---|---|
| `prod` | `:main` | prod SAM, job_history, samuel status | `etc/crontab.prod` |
| `dev` | `:staging` | `sam_dev`, samuel-dev status (no job_history ingest) | `etc/crontab.dev` |

Each lane is a directory under `$NCAR_HPC_DEPLOY_ROOT/lanes/` (by default
this directory; `lanes/` is git-ignored):

```
lanes/<lane>/
  env                 0600, apptainer --env-file (template: etc/env.example)
  env.<job>           optional overlay for one job (etc/env.jobhist-sync.example)
  images/*.sif        pulled by digest; current + previous + 3 newest kept
  current previous    symlinks into images/; swapped by rename, so a running job is never left without one
  state/              locks, last-run.<job>.<host>, last-digest, update-history, disk stamps,
                      run-manifest.<image>.sh (extracted from the image), spool/<host>/
  logs/<job>/<host>-YYYY-MM-DD.log   pruned after NCAR_HPC_DEPLOY_LOG_DAYS (90)
```

Overlays exist because SAM accounting and `jobhist-sync` read the same
variable names (`CIRRUS_PG_USER`, ...) under different roles. Only the job
that writes job_history gets the writer role.

## Commands

```bash
ncar-hpc-deploy run --lane prod accounting-comp --last 2d   # flock + log + exit code
ncar-hpc-deploy run --lane dev collectors --dry-run
ncar-hpc-deploy update --lane prod --smoke-hosts derecho.hpc.ucar.edu
ncar-hpc-deploy status --lane prod
ncar-hpc-deploy rollback --lane prod                         # swap current and previous
ncar-hpc-deploy promote --lane prod lanes/prod/images/X.sif  # manual bless
NCAR_HPC_DEPLOY_LANE=dev bin/sam-search project SCSG0001     # tools through the wrapper
```

`update` resolves the tag's digest anonymously from GHCR and returns if it is
unchanged. Otherwise it pulls to `lanes/<lane>/images/`, points `candidate` at
it, and runs `smoke/smoke.sh` inside it, locally and then on each
`--smoke-hosts` host over ssh. Only a pass moves `current`; `previous` keeps the
image that was current (a `--force` re-smoke of an unchanged digest leaves it
alone). A failure leaves `current` alone, writes
`lanes/<lane>/state/last-update.FAILED`, and exits 2 so cron mails.

The smoke gate is read-only. It checks imports and entry points, runs a SAM
query, and dry-runs `accounting-comp`, `jobhist-sync` and `collectors` against
the lane's real databases and PBS hosts. The `jobhist-sync` step runs only in
a lane with `env.jobhist-sync`. `NCAR_HPC_DEPLOY_SMOKE_STEPS` overrides the
list. Each step runs in its own container, so the job's env overlay applies.

## Jobs

`jobs/<job>.sh` runs **inside** the image, so a swap moves code and job logic
together. The host side (`libexec/`) only chooses the image, the binds
(`/glade`, plus whichever PBS accounting directory exists on this host) and
the env.

| job | replaces |
|---|---|
| `jobhist-sync rapid\|daily\|weekly` | hpc-usage-queries `job_history/cron_scripts/*.sh` |
| `accounting-comp [args]` | `scripts/cron/accounting/jobs/run_ncar_accounting.sh` |
| `accounting-disk [args]` | `scripts/cron/accounting/disk/Makefile` |
| `collectors [args]` (host scrape, then container parse) | `collectors/cron_scripts/run_ncar_collectors.sh` |

The collectors job has a host half. The container can't ssh between hosts
(host-based auth needs setuid `ssh-keysign`) or run the PBS client, so
`prejob_collectors` first has the image print each collector's manifest, and
`collectors/bin/run-manifest.sh` (bash only, extracted from that same image
and cached per image under `lanes/<lane>/state/`) runs it into
`state/spool/<host>/<system>/`. The container then parses that spool with
`--spool`. Each host collects only itself: casper does `casper` and
`jupyterhub`, derecho does `derecho`. Nothing on the host side reads the git
checkout except `libexec/` itself.

To try a change before its image exists, set `NCAR_HPC_DEPLOY_SRC=<checkout>`.
This binds that checkout's `containers/ncar-hpc-deploy` and `collectors` over
the image's copies (testing only).

## Install

1. As csgteam, clone the repo and write `lanes/<lane>/env` (plus
   `env.jobhist-sync` for prod), all 0600.
2. `bin/ncar-hpc-deploy update --lane <lane> --smoke-hosts derecho.hpc.ucar.edu`
3. Set `NHD=` in `etc/crontab.<lane>`, then merge it into csgteam's crontab
   on `cron`. Remove each host-checkout entry it replaces in the same edit.

Ordering: a lane's image must carry this directory. Until the change that adds
it has reached the lane's tag and CI has published that image, `update` fails
its `jobs-shipped` smoke step, and only `NCAR_HPC_DEPLOY_SRC=<checkout>` can
run the jobs. Dev (`:staging`) therefore goes live one promotion before prod.

Gotchas:
- `--cleanenv` means only the lane env and what `nhd_exec` names reach the
  job: `NCAR_HOST`, `TZ` (default `America/Denver`), the `NHD_*` paths and
  `MPLCONFIGDIR`. The image's own clock is UTC.
- `jobhist-sync --dry-run` still runs `init_db()` DDL, so it needs the writer
  role.
- A skipped run is normal for an overrun and is only logged; a lock held longer
  than `NCAR_HPC_DEPLOY_STALE_MIN` (60) minutes is reported on stderr every
  tick, so a hung job mails rather than silently starving its successors.
- `accounting-disk` stamps a usage file once `sam-admin` has consumed it, even
  when rows were skipped (exit 2 still mails once). Reloading the same file
  would repeat the same rows; to rerun one, delete
  `lanes/<lane>/state/disk-<key>.stamp`.
- The `accounting-comp` smoke step runs the job's fallback ladder with
  `--dry-run`, and `--skip-errors` still exits 2 when rows were skipped. A bad
  data day therefore fails every candidate, but the same data is already
  failing prod's hourly job, so fix the data rather than the gate.
- The collectors spool lives under `lanes/<lane>/state/` on shared `/glade`, deliberately:
  node-local disk on a shared login node is scarce. Each derecho capture is about
  43 MB (mostly `qstat -f -F json`); two are kept, and a new one is written every
  5 minutes. Possible future change: a node-local spool, since the scrape and the
  parse always run on the same host.
