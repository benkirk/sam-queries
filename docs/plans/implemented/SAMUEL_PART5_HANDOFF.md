# Handoff: SAMuel Part 5, "Some Assembly Required"

Untracked notes, 2026-10-01, written at the end of the Part 4 and layout-controls session.
Read with `SAMUEL_HANDOFF.md` (build, QA traps) and `SAMUEL_PRESENTATION.md` §4 Part 5 (the
outline, as committed on #681).

Facts below were verified on 2026-09-30 against sam-queries `origin/staging` `886c2f09`,
unless marked otherwise. Re-check anything marked (recheck) before quoting it.

## Scope and boundaries

- **Part 5 is hosting, GitOps and deployment:** where SAMuel runs and how a commit gets
  there. Plus 2–3 slides on the companion CNPG cluster, at Ben's request.
- **Part 4 already owns the data flows:** identity, XRAS, usage in, API access out, and the
  legacy overlap. Part 5 shows GitHub/CIRRUS/HPC as *machinery*; don't re-tell the flows.
- **Part 3 already owns the 2026-09-13 CNPG roll PSA** and the databases map. Part 5 points
  back with "see Part 3"; don't repeat it. Part 3's "MySQL today, Postgres tomorrow" also
  covers Horizon 1.
- **Legacy SAM stays grey** in any diagram, per Ben's convention (fill `#E5E7EB`, stroke
  `#6B7280`, text `#374151`).
- **The framework repo is PUBLIC.** No IP addresses, OpenBao secret paths (`csg/...`),
  `pg_hba` rules, superuser details or credential names on slides *or in notes*. Phrase the
  CNPG backup gap as "planned", and ask Ben how much to say.

## Where it lives

- **Deck:** `~/Documents/quarto-docs-framework/docs/samuel/_5-deployment.qmd`, which is a
  six-section placeholder skeleton. The wrapper is `5-deployment.qmd` (subtitle "Some
  Assembly Required"), and `samuel.qmd` already includes it.
- **Branch:** framework `samuel` (`ca66e38`, 13 deck commits on `main`), living draft PR
  framework #16.
- **Plan doc:** sam-queries `docs/plans/SAMUEL_PRESENTATION.md` on `samuel-presentation-part2`
  (PR #681). It covers §4 Part 5, §8 Phase 5, §13 formats and companion candidates, and the
  §14 fix-later list.

## Outline (as in plan §4), with the verified facts

### A. From commit to image
- **Branch flow:**
  - feature to `staging`;
  - the merge pins `cirrus-dev` (dev deploy) and opens or refreshes the promotion PR
    (`open-staging-promotion`);
  - merging the promotion pushes `main`, which pins `cirrus` (prod);
  - `sync-staging-to-main` resets staging.
- **14 workflow files** (`.github/workflows/`):

  | File | Purpose | Trigger |
  |---|---|---|
  | `sam-ci-docker` | `pytest-mysql` (coverage), `pytest-postgres`, `perf` | PR to main/staging/integration, push main |
  | `ci-staging` | TruffleHog, MegaLinter, Terraform fmt+validate, Helm lint/renders | PR to staging |
  | `browser-smoke` | Chromium sweep of every dashboard | as sam-ci-docker |
  | `sam-ci-conda_make` | conda install path + CLI smoke | as sam-ci-docker |
  | `test-install` | `install.sh` first run, update run, LFS recovery | as sam-ci-docker |
  | `mega-linter` | MegaLinter (cupcake) | PR to main |
  | `build-images-cirrus-deploy` | build `samuel`, pin `cirrus`/`cirrus-dev` | push main/staging, `v*` tags, dispatch |
  | `open-staging-promotion` | keeps one promotion PR open | push staging |
  | `sync-staging-to-main` | resets staging | PR closed on main |
  | `clean-ghcr` | GHCR prune | Sundays 03:15 UTC |
  | `_prune-workflow-runs`, `cron-clean-action-log`, `manually-clean-action-log` | old run cleanup | reusable / 11th monthly 03:43 UTC / dispatch |
  | `deploy-staging` | retired AWS ECS | dispatch only (push trigger removed 2026-09-04) |

- **The three-way test split** is live: #677/#678, `.github/actions/test-stack/action.yml`
  shared, record `docs/plans/implemented/CI_PARALLEL_SPLIT.md`. Test count
  `{{< var tests.collected >}}` (recheck `docs/TESTING.md`).
- **One `samuel` image** (`docs/plans/implemented/JOBS_IMAGE.md`, PRs
  #660/#664/#665/#666/#668/#669/#670/#672):
  - **Runners and digests:** native runners
    (`PLATFORMS='amd64:ubuntu-24.04 arm64:ubuntu-24.04-arm'`, no QEMU); `push-by-digest=true`,
    then a `merge` job running `docker buildx imagetools create`, which fails unless it gets
    exactly 2 digests.
  - **Peer pins:** `setup` resolves the `hpc-usage-queries` and `NCAR/hpc-scheduling-tools`
    `main` to SHAs via `git ls-remote`; on failure it warns and falls back to `main`.
  - **Before and after:**

    | | Before | After |
    |---|---|---|
    | Size (uncompressed, arm64) | 2.07 GB | 679 MB |
    | Build time | 8.0 min | ~3 min |

  - **Other images:** only `samuel` (target `production`) builds by default; `mysql` and
    `samuel-dev` are dispatch-only.
- **PSA, the GHCR prune (#670):**
  - in a multi-arch index, the per-platform and attestation manifests are untagged, so the
    prune deleted them;
  - the 2026-09-27 run left 12 of 47 `webapp` tags and all 15 `mysql` tags unpullable;
  - the fix keeps anything a kept index references, keeps everything if a manifest can't be
    read, and dry-runs off `main`;
  - source: `JOBS_IMAGE.md` lines 84–88, plus Claude memory `reference_ghcr_prune_multiarch`.
- **PSA, the squash trap (#406/#408)** (CLAUDE.md "Skipping CI"):
  - GitHub scans the whole squash message, title *and* body, for the skip tokens, and then
    creates no runs at all;
  - #408 also skipped `deploy-staging` and suppressed CI on the promotion PR;
  - the tell: `workflow_dispatch` still works;
  - the fix: an empty commit with a clean message.
  - **Never write the bare token anywhere, slides included.** Write `[skip&nbsp;ci]` or
    "skip-ci token".
  - The `update-helm` commit on the cirrus branches carries the token on purpose.

### B. GitOps on CIRRUS
- **The image line:**
  - CI changes only the `image:` line in `helm/values.yaml`
    (`docs/CIRRUS_PUBLISHING.md:7`);
  - `update-helm` resets the cirrus branch to the triggering ref's tree, `sed`s the line, and
    force-pushes one commit (line 63).
- **Push lock:** the GitHub App `cirrus-benkirk-deployer` (line 71) is the only bypass on
  ruleset "Lock cirrus to deploy workflow" (line 84), which covers `cirrus` and `cirrus-dev`.
- **The publish-pipeline diagram:** redraw the ASCII at `docs/CIRRUS_PUBLISHING.md` lines
  9–22 as Graphviz.
  - The shape: three triggers (main push or tag; staging push; dispatch with `target=dev`
    default or `prod` explicit) feed the workflow, which tags GHCR with `sha-<short>`,
    `latest`, branch and semver, then feeds `cirrus` (prod) or `cirrus-dev`.
  - Doc bug: line 28 says "four jobs", but there are five (setup, build, merge, summary,
    update-helm).
- **Trap:** a `workflow_dispatch` from any branch force-pushes *that branch's*
  `helm/values.yaml` onto `cirrus`. Keep dispatch branches rebased on staging. (Claude
  memory `project_xras_cutover_state`; it bit #367.)
- **Two Argo apps:**

  | | prod | dev |
  |---|---|---|
  | App, namespace | `sam-query`, `sam-queries` (Capsule) | `sam-query-dev` (AppProject `csg`), `sam-queries-dev` |
  | Values | `values.yaml` | + `values-dev.yaml` |
  | SAM DB | MySQL VM `sam-sql` | CNPG `sam_dev` (+ `system_status_dev`) |
  | Replicas, PDB | 2, PDB on | 1, PDB off |
  | Mail, XRAS, Jira | on | off (`NOTIFY_ENABLED 0`, XRAS outgoing/write 0, capture-only 1) |
  | `SAM_TASKS_DISABLED` | `xras_notices,account_queue_digest` | + `expiration_notices,xras_sweep,account_requests_reconcile` |
  | Redis | 192 MB | 64 MB |

  Sources: `docs/plans/implemented/K8S_DEV_ENVIRONMENT.md` (prod app at lines 51–52),
  `docs/README-k8s.md:410-412`, `helm/README.md:16`. The guarantee that dev shares nothing
  with prod is `helm/tests/test-dev-render.sh`.

### C. Runtime on nwc1 (`helm/values.yaml`, `helm/templates/`)
- **Pods:**
  - `replicaCount: 2`, `maxUnavailable: 0`, `maxSurge: 1`;
  - PDB `minAvailable: 1`;
  - topology spread maxSkew 1 on hostname (`ScheduleAnyway`);
  - gunicorn `gthread` with 8 threads; workers default to 2×CPU+1, so 9 at the 4-CPU request.
- **Ingress:**
  - fqdn `samuel.k8s.ucar.edu` plus `sam.hpc.ucar.edu` (a CNAME);
  - one multi-SAN InCommon cert (`incommon-cert-samuel`, issuer `incommon`);
  - `ingressClassName: nginx-external`.
- **Secrets:** 9 ExternalSecrets (`templates/external_secret.yaml`), all through SecretStore
  `csg-ro` (OpenBao): db, samDb, jhDb, fsDb, jupyterhub, xrasApi, jira, oidc, humanCheck. Dev
  drops xras and jira, leaving 7.
  - Names on a slide are fine; paths are not.
- **Redis:**
  - `redis:7-alpine`, `maxmemoryMB 192` (limit 320Mi), `--maxmemory-policy allkeys-lru`,
    `--save ""` (no persistence), single replica;
  - DB 0 holds the Flask-Caching / chart cache, DB 1 the limiter.
  - `scripts/cirrus_redis_purge.sh` is the only mutating watch script, and only with `--yes`.
- **Tasks CronJob:**
  - `samuel-tasks` at `7,22,37,52 * * * *` (`Etc/UTC`), `concurrencyPolicy: Forbid`;
  - the `task_run` ledger dedupes (state machine: `docs/plans/implemented/SCHEDULED_TASKS.md`
    around line 386).
- **Traps worth a slide** (CLAUDE.md "The account-request tasks" and around it):
  - `SAM_TASKS_DISABLED` is **fail-open**: a new task goes live unless named in
    `values.yaml` in the same change.
  - `NOTIFY_*` must reach the CronJob explicitly; when it doesn't, it fails silent and green.
  - `expected_runtime` is a lease knob: the lease `max(3×, 900 s)` must exceed
    `activeDeadlineSeconds`, or a killed send is reclaimed and mails twice.
- **The `/ready` rule** (CLAUDE.md, Part 3 PSA): only the `sam` bind gates readiness;
  secondary binds report `degraded` with a 200.

### D. The companion CNPG cluster (2–3 slides; source: peer repo `~/codes/hpc-usage-queries/devel/helm/`)
- **Spec:**
  - chart `cirrus-csg-postgres`;
  - Cluster `csg-postgres`, namespace `pg-testing` (still named that, with prod data in it);
  - CNPG operator 1.28.0, image `postgresql:18.3`, **2 instances** (primary + replica);
  - 512Gi PVC each, Ceph RBD snapshot class;
  - limits `cpu 4` (8 to 4 in #113), `memory 64Gi`; no requests.
- **Parameters:** `max_connections 300`, `shared_buffers 32GB`, `idle_session_timeout` and
  `idle_in_transaction_session_timeout` 10 min, `pg_stat_statements`,
  `log_min_duration_statement 2s`, TLS 1.3, timezone America/Denver.
- **Rolls:** `primaryUpdateMethod: switchover`, `switchoverDelay 60`,
  `smartShutdownTimeout 30` (hpc-usage-queries #114).
  - Measured: switchover gap under 1 s (whole roll ~59 s); failover ~16 s.
  - There was another failover on 2026-09-29 22:12Z.
- **Services:** LoadBalancers `csg-postgres.k8s.ucar.edu` (rw, the primary) and
  `csg-postgres-ro.k8s.ucar.edu` (the replica), via external-dns; TLS from a cert-manager
  self-signed issuer.
- **Deployment:**
  - Argo renders the chart, so `helm list` is empty and a merge to the chart rolls the cluster
    (#113 merged at 03:11Z; the pods were recreated at 03:22Z);
  - Ben can't read the Cluster CR (RBAC), so pod labels are the fallback;
  - the Argo app name isn't documented (recheck).
- **What it hosts** (slide table):

  | DB | Written by | Read by |
  |---|---|---|
  | `derecho_jobs`, `casper_jobs` | `jobhist-sync` (prod lane), plus a legacy cron | SAMuel job_history plugin via `-ro` |
  | `campaign`, `destor` | weekly fs-scans rebuild | SAMuel fs_scans plugin via `-ro` |
  | `system_status` | SAMuel + collectors | SAMuel: **the only data here that can't be rebuilt** |
  | `system_status_dev`, `sam_dev` | samuel-dev; `make clone-pg` rebuilds `sam_dev` | samuel-dev |

- **Roles** (name only on a slide, if at all): `pguser` (app), `jobhist_writer` (DML-only,
  created 2026-09-28; the prod lane's swap is pending the image, so recheck), `sam_dev`.
- **Operations:**
  - `scripts/cnpg_watch.sh` (peer repo, read-only, ~30 min, 8 sections) and
    `scripts/cirrus_healthcheck.sh`;
  - the `watch-cnpg` skill lives in the peer repo.
- **Murky / ask Ben:**
  - **No `ScheduledBackup` exists.** Snapshots are configured, and barman to Boreas is wired
    but off. Plan: `docs/plans/CNPG_BACKUPS.md` in the peer repo, triggered when SAM goes
    CNPG-prod. Phrase it as "planned".
  - Monitoring: there's no PodMonitor.
  - **Horizon 2** (prod SAM on csg-postgres) is open and gated on retiring legacy SAM (Part 4
    "The overlap, mapped"). Listed blockers: the pool ceiling (540) exceeds
    `max_connections` (300); per-request bcrypt; no dogpile lock; no `statement_timeout`; the
    backups. Source: `docs/plans/implemented/POSTGRES_MIGRATION.md`, whose Stage 5 line is
    stale (it shipped).
  - **Hardening items:** keep off the slides entirely.

### E. Dev vs prod
- **Three places:** laptop compose, samuel-dev (`samuel-dev.k8s.ucar.edu`, 1 replica,
  `make refresh-dev`), prod.
- **Compose** (`compose.yaml`):

  | Service | Profile | Host port |
  |---|---|---|
  | `samuel` (target production) | default | 7050 |
  | `samuel-dev` (target development) | default | 5050 |
  | `cache` (Redis) | default | 6379 |
  | `mysql` (obfuscated LFS dump) | default | 3306 |
  | `mysql-test` | test | 3307 |
  | `postgres-test` | test | 5434 |
  | `postgres` | pg | 5433 |

- **Watch scripts** (`scripts/cirrus_*.sh`, all take `--env dev`):
  - `cirrus_healthcheck.sh` is the read-only probe;
  - `cirrus_watch.sh` is the change-only tick, with state file and `hosts:` line;
  - `cirrus_weblog_audit.sh` covers traffic, rate limits and security failures;
  - `cirrus_redis_purge.sh` dry-runs, or mutates with `--yes`.
- **Skills:** `watch-prod`, `watch-dev`, `profile-dev`.
- **History, one line at most:** `docs/STAGING.md` is the retired AWS ECS/RDS staging.

### F. ncar-hpc-deploy as deployment (`containers/ncar-hpc-deploy/README.md`, the best single doc)
- **Same image, different host:** the same `samuel` image runs under Apptainer on casper and
  derecho.
  - Lane `prod` tracks `:main`; lane `dev` tracks `:staging`.
  - Dev writes `sam_dev`/status-dev and does no job_history ingest.
- **Cron:** runs as csgteam on the `cron` host, ssh to the nodes, a flock on each line.
  Crontabs: `etc/crontab.{prod,dev}`.
- **`update`:**
  - resolves the digest and pulls;
  - smoke-tests locally and on derecho (`--smoke-hosts derecho`);
  - swaps only on a pass;
  - keeps `previous`, with a `rollback` command.
  - Prod updates daily at 06:32 and dev hourly at :47.
- **Lane status:** prod is live; its first daily run was 2026-09-30 (from the watch state,
  not a repo doc). `ncar-hpc-deploy status` prints `lane=X not installed` otherwise
  (`libexec/ncar-hpc-deploy:340`).
- **Watch:** a `hosts:` line in `cirrus_watch.sh` via `scripts/lib/nhd_lane_summary.sh`
  (#658/#659).
- **Already in Part 4:** the cadence table (`etc/schedule`, frozen as `_out_schedule.qmd`).
  Part 5 shows the deployment mechanics only.
- **Then vs now:**
  - per-user crontabs in a home checkout (`scripts/cron/accounting/`,
    `collectors/cron_scripts/`; they still exist, unmarked; see §14) versus lanes, digests
    and rollback;
  - prod's install removes the host-checkout entries it replaces.

## Craft notes that apply

- **Short slides:** use `{.center .fill scale="S"}` for a standalone table (HTML fills; S,
  1.1–1.25, sized so the beamer PDF fits). Use `{.vcenter}` for a short diagram. Leave bullet
  slides alone, for a steady text size. Theme 2.5.0; README "Centering and scaling a short
  slide".
- **Diagrams:**
  - Graphviz in the Part 2/4 palette;
  - wide chains as snakes or rings with `layout=neato` and pinned `pos="x,y!"` (clusters are
    lost under neato);
  - aim for 2–3:1; the aspect can flex to fill.
  - The CIRRUS pipeline and the Argo/pods picture are the two big ones.
- **Footnotes:** a `†` after a columns block or a table splits the pptx slide. Put it in the
  last column or in the notes. "PSA - Don't let this happen to you…" is the war-story title
  pattern; Part 4 used "…again".
- **Poppins has no arrows:** write "to".
- **Cross-references:** credit people and teams by name: CIRRUS, George, Steve Peckins. Never
  punch at legacy.
- **Facts:** put numbers in `_variables.yml` with source and as-of. Candidates: `ci.workflows`
  14, `image.size_before/after`, `image.build_min_before/after`, `k8s.replicas_prod/dev`,
  `k8s.secrets_prod/dev` 9/7, `redis.mb` 192, `cnpg.instances` 2, `cnpg.pg` 18.3.
- **QA:** `make -C docs/samuel all`, then:
  - the pptx layout scan (no untitled slides);
  - LibreOffice to PDF;
  - read the beamer PDF page by page;
  - HTML at 1280×720, served by `python3 -m http.server`;
  - grep the sources for IPs and `csg/` before pushing.
- **Companion page candidates** (§13): the CI to image to Argo to pods pipeline, clickable per
  stage; the ncar-hpc-deploy lanes and cadences as a timeline.

## Open questions for Ben

1. **CNPG backups:** how much to say publicly ("planned" vs omit)?
2. **Section length:** Part 5 is likely 25–30 slides. Is that OK, or should we trim C or E?
3. **Companion page:** build the pipeline walkthrough now, or later?
4. **Title:** keep "Some Assembly Required"?

## Stale docs met along the way (already on the plan's §14 list)

- `helm/README.md`: "hourly" dispatcher, "7 ExternalSecrets".
- `docs/CIRRUS_PUBLISHING.md:28`: says four jobs.
- `docs/plans/implemented/POSTGRES_MIGRATION.md`: Stage 5 marked in progress.
- `docs/plans/implemented/CNPG_ROLL_RESILIENCE.md`: says 65Gi is kept.
- `docs/STAGING.md`: retired AWS.
- helm comments calling the reconcile "hourly".
