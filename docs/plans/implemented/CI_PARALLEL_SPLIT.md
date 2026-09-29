# Parallel SAM test jobs; staging duplicates dropped (PR #678)

As-built record, 2026-09-29.

## Problem

The per-PR critical path was **SAM Test Suite** (`sam-ci-docker.yaml`), 13–15 min. Measured on run
36619889663 (PR into staging):

| job | setup | work | total |
|---|---|---|---|
| sam-ci-docker `pytest` (MySQL) | 143 s | pytest+cov 429 s, then perf 289 s (serial, `if: always()`) | 896 s |
| sam-ci-docker `pytest-postgres` | 133 s | clone-pg 56 s + pytest 284 s | 508 s |
| ci-staging `test` (MySQL) | 137 s | pytest, no cov, 357 s | 525 s |
| ci-staging `test-postgres` | 133 s | clone-pg 61 s + pytest 304 s | 529 s |

1. One job ran two unrelated tiers. A red check meant opening the log to learn which one had failed,
   and perf added ~5 min to the critical path.
2. `sam-ci-docker` triggers on PRs into `[main, staging, integration]`, so the `ci-staging` test
   jobs repeated both suites on every staging PR, about 17 runner-minutes of duplicate work. No
   ruleset or branch protection requires any of these check names.
3. The browser sweep (`browser-smoke.yaml`) was already its own workflow and runner, and was not on
   the critical path.

## What shipped

- `sam-ci-docker.yaml` runs three independent jobs on three runners:
  - **Test Suite (MySQL, coverage)**
  - **Test Suite (Postgres)**
  - **Perf Tier (MySQL)**
- Composite action `.github/actions/test-stack` does the shared setup. It installs the latest Docker,
  fails hard on an LFS pointer, and runs `docker compose --profile test up --detach --wait --build
  <services>`. That replaced three divergent copies of the setup steps and the wait scripts.
- `ci-staging.yaml` keeps secret scan, MegaLinter, Terraform and Helm. Its test jobs are gone.
- `docs/TESTING.md` § CI Integration describes the new shape.

Result, from run 36623716948: the workflow's wall clock went from 15 min to **9 m 45 s**. Per job:
MySQL+coverage 578 s, Postgres 496 s, perf 416 s. Coverage still gates at 85% (88.18% on that run).

## Declined: build the images once and share them across jobs

Setup is ~148 s per job, split as follows (run 36623716948; all three jobs agree to within ~3 s):

| phase | time | would image reuse remove it? |
|---|---|---|
| install latest Docker | ~11 s | no |
| image build (samuel ~60 s; the mysql images take ~13 s, in parallel) | ~64 s | yes |
| start → healthy (concurrent restore of `mysql` and `mysql-test` from the 20 MB dump, plus `ANALYZE TABLE`) | ~72 s | no |

- **A build job plus a `docker save` artifact is slower.** Each job's build runs in parallel today,
  so the build costs 64 s of wall clock once. Fan-out adds a serial stage: build plus save/upload,
  then ~20–30 s of download and load per job. The critical path grows by ~40–60 s, and the only
  saving is runner-minutes, which are free on a public repo.
- **A per-job BuildKit GHA layer cache saves at most ~30 s** (~5%). Cached layers still have to be
  downloaded and loaded, and exporting layers alone takes ~11 s. It also brings a staleness trap:
  `containers/samuel/Dockerfile` installs `hpc-usage-queries@main`, so a layer keyed on the literal
  `main` keeps serving an old plugin unless CI resolves the peer to a SHA the way
  `build-images-cirrus-deploy.yaml` does. On top of that, CI needs its own cache scope (no deploy
  key, no `GIT_SHA`), which competes with the deploy caches for the repo's 10 GB quota.

## If CI speed matters again

The time is in pytest, not setup. The coverage leg's pytest step takes 412 s of the 578 s critical
path.
- **Shard the coverage leg.** Two shards would put the critical path on the Postgres leg (~15%
  faster); sharding both legs would reach ~30%. That needs `pytest-split` (or a directory split),
  a `coverage combine` job carrying `fail_under`, and a check against the free plan's ceiling of
  20 concurrent jobs.
- **Restore (~72 s), unmeasured.** Stop restoring the main `mysql` in test jobs: `samuel` depends
  on it only for its healthcheck, but first check that the webapp never writes to its bind at
  startup. The other idea is a pre-restored datadir image keyed by the dump hash; a 1–2 GB pull
  eats much of that gain.
