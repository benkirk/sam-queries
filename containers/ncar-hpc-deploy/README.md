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
  images/*.sif        pulled by digest; 3 kept (NCAR_HPC_DEPLOY_KEEP), always current + previous
  current previous    symlinks into images/; swapped by rename, so a running job is never left without one
  state/              locks, last-run.<job>.<host>, last-tick.<cadence>.<host>, last-digest,
                      update-history, disk stamps, extract/<image>/ (host-side files
                      cached from the image), spool/<host>/
  logs/<job>/<host>-YYYY-MM-DD.log   one per job; logs/tick/ holds one line per tick
                                     pruned after NCAR_HPC_DEPLOY_LOG_DAYS (90)
```

Overlays exist because SAM accounting and `jobhist-sync` read the same
variable names (`CIRRUS_PG_USER`, ...) under different roles. Only the job
that writes job_history gets the DML writer role, `jobhist_writer` (OpenBao
`csg/jobhist-writer`; grants in `docs/plans/JOBHIST_WRITER_ROLE.md`).

## Commands

```bash
ncar-hpc-deploy tick --lane prod hourly                      # what cron calls
ncar-hpc-deploy tick --lane dev daily --list                 # steps for this host and lane
ncar-hpc-deploy run --lane prod accounting-comp --last 2d   # one job: flock + log + exit code
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

The smoke gate is read-only. It checks imports, entry points and
`etc/schedule` (every row names a shipped job), runs a SAM query, and dry-runs `accounting-comp`, `jobhist-sync` and `collectors` against
the lane's real databases and PBS hosts. The `jobhist-sync` step runs only in
a lane with `env.jobhist-sync`. `NCAR_HPC_DEPLOY_SMOKE_STEPS` overrides the
list. Each step runs in its own container, so the job's env overlay applies.

## Cadences

Cron fires a **cadence**, not a job: one line per (cadence, host), calling
`tick --lane L CADENCE`. What each cadence runs is `etc/schedule`, one row per
step (`cadence hosts lanes job [args]`), in file order. The schedule ships in
the image and is read from `current`, so adding a step is a code change that
the `schedule` smoke check gates like any other. A new cadence (say `monthly`)
takes schedule rows plus one cron line per host.

| cadence | prod steps | dev steps |
|---|---|---|
| `rapid` (*/5) | collectors, `jobhist-sync rapid` | collectors |
| `hourly` | `accounting-comp --last 2d` | same |
| `daily` (01:07) | `jobhist-sync daily`, `accounting-comp --last 7d`, `accounting-disk` | the last two |
| `weekly` (Sat) | `jobhist-sync weekly` | none |

- A failed step does not stop the tick. The tick exits with the worst step's
  code, and each failed step prints one stderr line, so cron sends one mail per
  tick listing every failure.
- Each step keeps its own job lock, log and `last-run`. A step whose job lock is
  held (the same job under another cadence) is skipped in `rapid` and `hourly`
  and waited for elsewhere (`NCAR_HPC_DEPLOY_STEP_WAIT`, 1800 s). So daily
  `jobhist-sync` is never dropped because a rapid run held the lock.
- The tick has its own lock, so an overrun skips the next tick of that cadence.
- A cadence with no rows for this host and lane exits 1 with a stderr line: a
  cron line that runs nothing is a misconfiguration.
- `update` stays a separate cron line, because it replaces the image the ticks
  run in.

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
and cached under `lanes/<lane>/state/extract/`) runs it into
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
- `jobhist-sync` only checks the schema; a sync or `--dry-run` against a
  schema that is behind exits 2 with "run `jobhist-sync --init-db`". Run that
  once per schema change as the DB owner (`postgres`), outside the lanes.
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
