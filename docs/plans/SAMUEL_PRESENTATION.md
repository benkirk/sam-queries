# SAMuel presentation series — a multi-part Quarto deck

**Status:** Phase 0 (scaffold) done, 2026-09-29, pending the merge of framework PR #10. This doc is the handoff: each session
picks up the next unchecked phase in §8, ticks it, and appends to the session log (§10).
**Goal:** replace the stale `docs/presentations/overview/` with a comprehensive, multi-part
SAMuel presentation, authored in the standalone `~/Documents/quarto-docs-framework` repo and
linked into this worktree.

## 1. Why

`docs/presentations/overview/overview.qmd` (352 lines, last touched 2026-04-25) is out of date:

- **Its numbers are wrong:** it says 97 tables and ~1,400 tests. The suite is now 9,296 collected
  tests (`docs/TESTING.md`).
- **Whole areas are missing:** it has nothing on plugins, system_status, job history, fs-scans,
  CIRRUS/Argo, RBAC, scheduled tasks, notifications, account registration or ncar-hpc-deploy.
- **Its deployment diagram is wrong.** The "Production Deployment" mermaid (around line 278) shows
  MariaDB on a PersistentVolume and a manual `helm upgrade`. Neither is how production runs.

The `presentation` branch (11 commits, merge-base 2026-04-21) holds only the same scaffolding
plus a 28-line system_status placeholder, so there is nothing to salvage.

The build infra has since matured into `~/Documents/quarto-docs-framework`
(`github.com/benkirk/quarto-docs-framework`, **PUBLIC, a GitHub template repo**). It has:
- Quarto 1.9.38 in `./conda-env`;
- `docs/Make.common` and the NCAR `docs/common/branding/ncar/template.pptx`;
- `enable_autofit.py` and `embed_poppins.py`;
- a CI workflow that builds the sample deck in all three formats. The workflow also fails any
  `docs/*/*.qmd` whose line 1 is not `---` (it skips `_*` fragments) and any pptx carrying a
  literal `last-modified` date (#9).

That repo already hosts content decks as sibling dirs: `new_user_samples` on main, and
`monthly_report` on its own branch. The in-repo `docs/presentations/common/` is an older copy
of the same infra.

## 2. Decisions (confirmed with Ben, 2026-09-29)

| Decision | Choice |
|---|---|
| Where the decks live | A long-lived **`samuel` branch** of quarto-docs-framework, content in `docs/samuel/`. The template's `main` stays clean for cloners. Framework-generic improvements go to `main` by PR and are merged into `samuel`. |
| This repo | **Retire `docs/presentations/`**, leaving a README pointer to the framework repo. Add a **local-only, gitignored** symlink `docs/presentations/samuel -> ~/Documents/quarto-docs-framework/docs/samuel`; an absolute symlink would dangle for CI and everyone else. Delete the stale `presentation` branch, local and remote, after confirming at that step. |
| Format | **Mixed, chosen per part** (§13). Part 1 Overview: pptx + ncar-beamer PDF, both branded. Parts 2–3 and details: revealjs HTML where interactivity earns its keep, plus companion interactive pages where slides run out; pptx/PDF builds remain as handouts. Revised 2026-09-29, replacing "pptx + HTML equally". |
| Audience | **CISL management / stakeholders:** Part 1 must stand alone as a non-technical briefing. **Incoming developers / handoff:** Parts 2–3 go deep, with code paths, gotchas and war stories. |
| Voice | Direct, unapologetically technical, playful; calibrated on Ben's own decks (§11). |
| Planning vehicle | This doc, on branch `samuel-presentation-plan`, as a docs-only draft PR against `staging`. It matures over several sessions. |

## 3. Deck architecture (`quarto-docs-framework/docs/samuel/`)

One directory holds every part. Each part is a thin wrapper around an underscore body file;
Quarto skips `_*.qmd` when rendering a project. The full deck includes every body:

```
docs/samuel/
  Makefile            DECKS := samuel 1-overview 2-databases 3-pieces A-peers
  _quarto.yml -> ../common/_quarto.yml   (auto-symlinked by Make.common)
  _variables.yml      facts: counts, hosts, dates, each with a source + as-of comment
  samuel.qmd          full deck: frontmatter + {{< include >}} of every body
  1-overview.qmd      frontmatter + {{< include _1-overview.qmd >}}
  _1-overview.qmd     body: `#` section-header slides, `##` slides (slide-level 2)
  2-databases.qmd / _2-databases.qmd
  3-pieces.qmd    / _3-pieces.qmd
  A-peers.qmd     / _A-peers.qmd
  images/             screenshots + generated diagrams
```

**Constraints behind that layout:**
- **Keep the deck at depth 1** (`docs/<deck>/`). The shared `_quarto.yml` sets
  `reference-doc: ../common/branding/ncar/template.pptx`, which is resolved relative to the
  `_quarto.yml` as the deck sees it. A deck nested deeper breaks that path.
- **Use one `images/` dir for every deck.** `{{< include >}}` is textual, so image paths resolve
  against the *including* file. With every wrapper and the full deck in the same directory,
  `images/x.png` resolves identically everywhere.
- **Body files carry no YAML frontmatter.** Only the wrappers have it.

**Read first:** the framework's own `CLAUDE.md` (on its `main`). It records the hard-won rules this
deck must follow, all learned on the `sam_and_pbs` deck (§12):
- **Slide splitting:** content after a table or image splits the slide into an untitled
  continuation, so put bullets *before* tables and notes *before* full-bleed images.
- **Layout demotion:** `strip-raw-figure.lua` stops columns slides being demoted to the Comparison
  layout.
- **Autofit:** the 0.8 line-spacing trap.
- **Layout diagnosis:** read `ppt/slides/_rels/slideN.xml.rels`, where layout4 is Title and
  Content, layout6 Two Content and layout7 Comparison.
- **Mermaid:**
  - size with `%%| fig-width` (≈10 full-bleed, ≈5 in a column), and keep the aspect ratio near
    2.4:1;
  - prefer `classDef` lanes over subgraphs;
  - never use reversed arrows;
  - always look at the rendered PNG, because every failure is silent.

**Divider subtitles and footnotes are built in.** `Make.common`'s pptx recipe now runs
`section_subtitle.py` → `enable_autofit.py` → `embed_poppins.py` → `style_footnotes.py`:
- a paragraph right after a `#` divider becomes that divider's subtitle — a natural home for a
  part's punchline;
- a paragraph starting with `†` renders as a muted footnote — the house version of Ben's asterisk
  jokes.

**Frozen-data pattern (`sam_and_pbs` precedent):**
- Real output lives in `data/*.txt`, and generated mermaid in `_*.qmd` fragments.
- Both are regenerated by a hand-run `refresh_data.sh`; **rendering never needs SAM access**.
- Slides show the frozen output with deterministic `{bash}` cells (`cat data/…`, `head -8 …`), so
  the frontmatter needs `engine: jupyter` + `jupyter: bash`.
- The refresh script queries **only the author's own username**, so nobody else's data is
  committed. Keep that discipline; the repo is public.

**Framework change (PR to the framework's `main`, branch `make-common-multi-deck`):**
- `docs/Make.common` built exactly one `OUT` per directory. It becomes:
  - `OUT ?= $(notdir $(CURDIR))`. The README always claimed a 1-line `include ../Make.common`
    worked, but nothing defaulted `OUT`.
  - `DECKS ?= $(OUT)`, with pattern rules `%.pptx / %.html / %.pdf : %.qmd $(DEPS)`. The pptx
    rule keeps all four post-steps, in order.
  - `QFILE` survives as a single-deck override, via explicit rules emitted only when it differs
    from `$(OUT).qmd`.
  - `.NOTPARALLEL`, because every deck in a directory shares one `.quarto/` cache;
    `.DELETE_ON_ERROR`, so a failed post-step leaves no "finished" output behind.
- **The stale-fragment gotcha is fixed** in the same PR. `DEPS` includes
  `$(wildcard _*.qmd) $(wildcard data/*)`; the wrapper + body design makes every edit a
  fragment edit. `sam_and_pbs` benefits too.
- Existing 3-line deck Makefiles keep working unchanged, as `make -C docs/sample` and
  `make -C docs/sam_and_pbs` prove.
- `docs/.gitignore` gains anchored `/*/*.{pptx,pdf,html}`. Outputs had been ignored
  per deck, and inconsistently.
- The README's "Adding a new deck" section and the framework's CLAUDE.md describe `DECKS`.
- **Commit the deck's `_quarto.yml` / `_extensions` symlinks** (mode 120000), as `sample/` and
  `sam_and_pbs/` do.

**Facts file:**
- Put every number that goes stale in `_variables.yml`: tests, tables, charts, tasks, endpoints,
  replicas, ExternalSecrets.
- Cite it with `{{< var tests.collected >}}`, and give each entry a source path and as-of date in
  a comment.
- A refresh then means editing one file, never grepping slides.

**Authoring rules:**
- **Diagrams:** mermaid, which renders to PNG in pptx and live in HTML. Keep one idea per diagram;
  large graphs are the main pptx friction.
- **Dense slides:** tag them `{.smaller}`, which only HTML honors, and split walls of text by hand
  for pptx.
- **Columns:** `:::: {.columns}` works in both formats.
- **Speaker notes:** use `::: {.notes}` for talk-track detail, so the handoff audience gets depth
  without crowding the slides.

## 4. Outline and source map

All paths are relative to this repo unless noted. ⚠️ marks a doc that is **stale**; don't lift
from it onto slides.

### Part 1 — Overview (stakeholder-safe, minimal jargon)

- **What SAM is:**
  - the system of record for who may compute, on what, and how much;
  - who uses it: users, PIs, CISL staff, systems integrations;
  - that it replaces the Java/Tomcat legacy SAM on the same MySQL database.
  - Sources: `README.md`, `docs/xras/PROJECT_AND_ACCOUNT_LIFECYCLE.md` (ARC → XRAS → SAM; "SAM
    never creates users", since users are mirrored from LDAP).
- **The surfaces:**
  - web dashboards (user / admin / allocations / status / gallery);
  - REST API:
    - 15 modules in `src/webapp/api/v1/`;
    - 5 legacy-compat blueprints frozen byte-for-byte;
    - the XRAS server side (7 endpoints);
  - the `sam-search` / `sam-admin` / `sam-status` CLIs;
  - 8 scheduled tasks, run from one CronJob every 15 min;
  - notifications;
  - account registration and invitations;
  - RBAC.
  - Sources:
    - `CLAUDE.md`, `src/webapp/README.md`, `src/cli/README.md`;
    - under `docs/plans/implemented/`: `SCHEDULED_TASKS.md`, `NOTIFICATION_FRAMEWORK.md`,
      `ACCOUNT_REGISTRATION.md`, `RBAC_DB_ROLES.md`;
    - `docs/AUTHENTICATION.md`: the sign-in walkthrough plus an OIDC mermaid sequence at around
      line 75.
- **Big-picture diagram:** a refreshed version of the `overview.qmd` architecture mermaid. Show:
  - XRAS in and out;
  - the LDAP mirror (sam-ldap-syncd);
  - PBS → collectors → the status API;
  - job_history → the accounting ingest;
  - the peer DBs;
  - consumers (hpc-scheduling-tools, LDAP provisioning, the legacy-compat API callers).
  - Sources: `docs/apis/SYSTEMS_INTEGRATION_APIs.md`, `docs/apis/CHARGING_INTEGRATION.md`.
- **The plugin approach:**
  - **Registry:** `src/sam/plugins.py`. A `Plugin(name, package, install_hint)` has `.load()`,
    which raises `PluginUnavailableError`, and `.available`. It defines three plugins, all from
    the `[hpc]` extra:
    - `HPC_USAGE_QUERIES` → `job_history`;
    - `FS_SCANS` → `fs_scans`, which ships in the same hpc-usage-queries wheel;
    - `HPC_SCHEDULING_TOOLS` → `hpc_scheduling_tools`, a private repo installed via a deploy key.
  - **CLI:** `BaseCommand.require_plugin()` in `src/cli/core/base.py`. On a missing plugin it
    prints the install hint and exits 2.
  - **Webapp:** `PluginExtension` in `src/webapp/plugins/base.py`:
    - loads the plugin once in `create_app` and warms its engines;
    - sets Postgres `application_name` + `statement_timeout`;
    - stores its state on `app.extensions`;
    - on a missing plugin, logs a warning and boots anyway.
    - Subclasses: `src/webapp/jobs/session.py` and `src/webapp/disk_scans/session.py`.
  - **Kill switches:** `FS_SCANS_ENABLED`, `HPC_SCHEDULING_TOOLS_ENABLED`, and an empty
    `JOB_HISTORY_MACHINES`.
  - **Degradation:** nav hides the tabs (`src/webapp/utils/nav.py`), and `/api/v1/fairshare`
    returns 503.
  - Source: `docs/plans/implemented/FS_SCANS_PLUGIN-part1.md`.
- **Screenshot tour:** 4–6 hero shots — the user dashboard, a project page with allocation
  charts, admin, the status page, and dark mode on mobile.

### Part 2 — The Databases

- **One-slide map:** built from `src/webapp/utils/engine_inventory.py` (`EngineSource`,
  `engine_sources()`). The same inventory drives the Admin Configuration card,
  `/api/v1/health/db-pool` and `/database`.

  | DB | Engine | Prod | Dev | Owning code | Writers → readers |
  |---|---|---|---|---|---|
  | sam | MySQL (prod); Postgres dual-backend | `sam-sql.ucar.edu` (the VM) | `sam_dev` on CNPG (samuel-dev); compose MySQL; test DBs on :3307 (MySQL) and :5434 (Postgres) | `src/sam/`, `sam.session`, `sam.sqlcompat` | webapp, sam-admin, XRAS, charge ingest, tasks → everything |
  | system_status | Postgres (prod), MySQL (local) | CNPG `csg-postgres`, DB `system_status` | `system_status_dev` | `src/system_status/`, Alembic 0001–0007 | collectors via the status API, task ledger, login sightings → status dashboard, admin |
  | job_history | Postgres (read-only from SAM) | `csg-postgres-ro`, DBs `derecho_jobs` / `casper_jobs` | same | hpc-usage-queries (`job_history`) | `jobhist-sync` → My Jobs, drill-downs, `sam-admin accounting --comp` |
  | fs_scans | Postgres (CNPG) | `csg-postgres-ro`, DBs `campaign` / `destor`, one schema per collection | same | hpc-usage-queries (`fs_scans`) | scanners/importers → disk-scan tabs |

- **SAM:**
  - domain tour: users / projects / accounts / allocations (a tree) / resources / charging;
  - the balance calculation: `remaining = allocated − (charges + adjustments)`;
  - the four charge-summary tables;
  - the universal `is_active` hybrid.
  - **Postgres:** Horizon 1 is done (PRs #549–551, 2026-09-12); all-PG prod is still open.
  - Sources: `CLAUDE.md`, `docs/plans/implemented/POSTGRES_MIGRATION.md` (14 MySQL→PG gotchas),
    `docs/DATABASE_SWITCHING.md`, `docs/LOCAL_SETUP.md`.
- **system_status:** 19 tables, grouped as:
  - snapshots: `derecho_status`, `casper_status`, `casper_node_type_status`, `queue_status`,
    `filesystem_status`, `login_node_status`, `jupyterhub_status`;
  - lookups: `systems`, `queues`, `filesystems`, `login_nodes`, `status_users`, `project_codes`;
  - outages: `system_outages`, `resource_reservations`;
  - other: `user_proj_queue_status`, `task_run`, `access_sources`, `user_last_seen`.
  - Sources: `migrations/README.md` (one Alembic env per DB),
    `docs/plans/implemented/ADD_ALEMBRIC_and_SYSTEM_STATUS_REFACTOR.md`, `USER_LAST_SEEN.md`
    (prod backfill: 22,015 rows, 10,122 users, 13 systems, back to 2011).
  - **War story:** `CNPG_ROLL_RESILIENCE.md`, the 2026-09-13 outage.
    - Symptom: a 3.5-minute outage when a csg-postgres roll failed the system_status readiness
      check.
    - Lesson: never add a secondary bind to `/ready`'s required set.
- **job history:**
  - connection settings: 60 s statement timeout, pool of 5 + 10 overflow (`src/webapp/config.py`);
  - Source: `docs/plans/implemented/JOB_HISTORY_DASHBOARD.md`.
- **fs-scans:** 100 s statement timeout; one engine per database × collection schema.
  Source: `FS_SCANS_PLUGIN-part1.md`.
- **`/database` browser:** read-only rows from every engine, found by reflection; it replaced
  Flask-Admin. Source: `docs/plans/implemented/DB_BROWSER.md`.
- **Optional:** an ER diagram of the core SAM tables via eralchemy2 (see §7).

### Part 3 — The Pieces (may split into 3a CI/GitOps and 3b HPC data gathering past ~30 slides)

- **Repos + CI:**
  - **Workflows:**

    | Workflow | Purpose | Trigger |
    |---|---|---|
    | `sam-ci-docker` | pytest | PR→main/staging/integration, push main |
    | `ci-staging` | TruffleHog, MegaLinter, Terraform fmt, Helm renders | PR→staging |
    | `browser-smoke` | Chromium sweep of every dashboard | PR→main/staging/integration, push main |
    | `sam-ci-conda_make` | conda/pip install path + CLI smoke | same |
    | `test-install` | `install.sh`: first run, update run, LFS recovery | same |
    | `mega-linter` | MegaLinter (cupcake flavor) | PR→main |
    | `build-images-cirrus-deploy` | build `samuel` → GHCR, pin `cirrus` / `cirrus-dev` | push main/staging, `v*` tags, dispatch |
    | `open-staging-promotion` | keeps one staging→main PR open | push staging |
    | `sync-staging-to-main` | resets staging to main after promotion | PR closed on main |
    | `clean-ghcr` | GHCR prune; never deletes a manifest a kept tag references (#670) | Sundays 03:15 UTC |
    | run-cleanup workflows | delete old workflow runs | monthly / dispatch |
    | `deploy-staging` | the retired AWS ECS deploy | dispatch only |

  - **Branch flow:**
    - feature → PR to `staging`;
    - the merge pins `cirrus-dev` (dev deploy) and opens/refreshes the promotion PR;
    - merging the promotion PR pushes `main`, which pins `cirrus` (prod), then sync resets
      `staging`.
  - **The unified `samuel` image:**
    - built natively on amd64 and arm64 runners (no QEMU);
    - each platform is pushed by digest, then merged into one multi-arch index;
    - peer plugin repos are pinned to SHAs at build time.
    - Source: `docs/plans/implemented/JOBS_IMAGE.md`.
  - **LFS test blob:** `containers/sam-sql-dev/backups/sam-obfuscated.sql.xz`, the only LFS file.
  - **In flight:** the three-job test split (`pytest-mysql` + coverage / `pytest-postgres` /
    `perf`) is on branch `ci-parallel-test-jobs` (commit 65e8a582), not yet on staging. Confirm
    its state before quoting it.
  - Sources: `docs/CIRRUS_PUBLISHING.md`, the `.github/workflows/*.yaml` header comments,
    `docs/TESTING.md`.
  - **Then vs. now:** `docs/nrit-review-2026-05/06_platform.md` has findings CI1–CI11 and
    D1–D11, many since fixed. It works as "then vs. now" material.
- **GitOps on CIRRUS (cluster nwc1):**
  - CI's only change on a cirrus branch is the `image:` line in `helm/values.yaml`. There is no
    manual `helm upgrade`.
  - **Argo CD apps:**
    - `sam-query` → namespace `sam-queries` (Capsule-managed);
    - `sam-query-dev` (AppProject `csg`) → `sam-queries-dev`, using `values.yaml` +
      `values-dev.yaml`.
  - **Push lock:** only the GitHub App `cirrus-benkirk-deployer` can push the cirrus branches,
    enforced by the ruleset "Lock cirrus to deploy workflow".
  - Convert the ASCII pipeline in `docs/CIRRUS_PUBLISHING.md` (around lines 9–22) to mermaid.
- **Runtime on k8s:**
  - **Prod shape:** 2 replicas, `maxUnavailable 0`, a PDB and topology spread; gunicorn gthread.
  - **Hosts:** `sam.hpc.ucar.edu` (a CNAME) and `samuel.k8s.ucar.edu`, with one multi-SAN
    InCommon cert via cert-manager behind `nginx-external`.
  - **Secrets:** ESO + OpenBao through SecretStore `csg-ro` — 9 ExternalSecrets in prod, 7 in dev.
  - **Redis:** one `redis:7-alpine` pod, 192 MB, `allkeys-lru`. DB 0 holds the cache and DB 1 the
    limiter; no persistence.
  - **Tasks CronJob:** `samuel-tasks` at `7,22,37,52 * * * *` UTC. The `task_run` ledger dedupes.
    The `SAM_TASKS_DISABLED` kill switch is fail-open.
  - **Peer Postgres:** the CNPG `csg-postgres` cluster (namespace `pg-testing`), whose chart lives
    in hpc-usage-queries.
  - Sources: `docs/README-k8s.md`, `helm/`, `docs/plans/implemented/K8S_DEV_ENVIRONMENT.md`,
    `docs/plans/implemented/REDIS.md`, `docs/plans/implemented/SCHEDULED_TASKS.md` (task_run state
    machine at around line 386), `docs/k8s.md`.
  - ⚠️ `helm/README.md` is stale: it says the dispatcher is "hourly" and lists "7 ExternalSecrets".
- **Dev vs prod:**
  - **Local compose:**
    - `samuel` :7050 and `samuel-dev` :5050;
    - `cache` (Redis) and `mysql` (the obfuscated LFS dump);
    - profile `test`: `mysql-test` :3307 and `postgres-test`;
    - profile `pg`: `postgres`.
  - **samuel-dev on k8s:**
    - `samuel-dev.k8s.ucar.edu`, 1 replica;
    - no mail and no XRAS key;
    - `make refresh-dev` rebuilds it;
    - `helm/tests/test-dev-render.sh` proves it shares nothing with prod.
  - **Watch tooling** (`scripts/cirrus_*.sh`, each taking `--env dev`): healthcheck, watch,
    weblog audit, and `redis_purge` (which only mutates when given `--yes`).
  - ⚠️ `docs/STAGING.md` describes the retired AWS ECS/RDS staging. It is worth one history line at
    most.
- **Data gathering on NCAR HPC:**
  - **ncar-hpc-deploy** (`containers/ncar-hpc-deploy/`, inside this repo):
    - the same `samuel` image runs under Apptainer on casper + derecho;
    - lane `prod` tracks `:main` and lane `dev` tracks `:staging`;
    - cron runs as csgteam on the `cron` host and reaches the nodes over ssh;
    - `update` pulls by digest, smoke-tests both hosts, then swaps, keeping the previous image
      for rollback.
  - **Cadences** (`etc/schedule`):
    - `rapid` every 5 min: collectors, plus `jobhist-sync rapid` on prod;
    - `hourly`: `accounting-comp --last 2d`;
    - `daily` at 01:07: `jobhist-sync`, `accounting-comp --last 7d`, `accounting-disk`;
    - `weekly` on Saturday: `jobhist-sync weekly`.
  - **Collectors:** the host side scrapes PBS into a spool; the container parses it and POSTs
    `/api/v1/status/{derecho,casper,jupyterhub}` (needs `MANAGE_SYSTEM_STATUS`).
  - **Charges don't go over REST in practice:** `sam-admin accounting --comp` reads job_history
    and writes SAM via the ORM. The routes `POST /api/v1/charge-summaries/*` also exist.
  - Sources:
    - `containers/ncar-hpc-deploy/README.md` (the best single doc) + `etc/schedule`,
      `etc/crontab.prod`;
    - `collectors/README.md`;
    - `docs/apis/CHARGING_INTEGRATION.md`, `docs/plans/implemented/CHARGING_INGEST.md`.
  - ⚠️ `docs/apis/HPC_DATA_COLLECTORS_GUIDE.md` is stale: its base URL is `sam.ucar.edu` and it
    uses Slurm commands. Its ASCII diagram (around lines 13–28) is still a usable starting shape.
  - **Retired:** `scripts/cron/accounting/` and `collectors/cron_scripts/` are the old per-user
    crontabs that ncar-hpc-deploy replaced.
- **Optional war stories:**
  - the CNPG roll outage;
  - the skip-ci squash trap (#406/#408; see CLAUDE.md, and never write the bare token);
  - the GHCR prune that deleted referenced multi-arch children (#670).

### Appendix A — Peer repos

| Repo | Remote | What it is | Coupling to SAMuel |
|---|---|---|---|
| hpc-usage-queries (`~/codes/hpc-usage-queries/devel`) | `github.com/benkirk/hpc-usage-queries` | `job_history` (PBS job history, charging) + `fs_scans`; also the csg-postgres CNPG chart (`helm/`) and `scripts/cnpg_watch.sh` | Two plugins; shared Postgres; `jobhist-sync` runs in the ncar-hpc-deploy lane |
| hpc-scheduling-tools (`~/codes/hpc-scheduling-tools`) | `github.com/NCAR/hpc-scheduling-tools` (private) | Fairshare tree + PBS accounting DB (`fsparsetree-mr`, `samuel2sql`, `hpc-sched-refresh`) | Calls the SAMuel API; also the `HPC_SCHEDULING_TOOLS` plugin behind `/api/v1/fairshare` |
| legacy SAM (`~/codes/sam`, symlinked as `legacy_sam`) | `github.com/NCAR/sam` | Java/Tomcat original | Same MySQL DB; SAMuel is porting its API families |
| sam-ldap-syncd | `github.com/NCAR/sam-ldap-syncd` | Perl daemon: IDMS/LDAP ↔ SAM | Legacy `/api/protected/admin/...` endpoints, not yet ported (`docs/plans/LDAP_SYNC_API.md`) |
| amie-sam-mediator | `github.com/NCAR/amie-sam-mediator` | ACCESS AMIE packets via SAM + LDAP | Legacy `/api/protected/amie/v1/*`, not ported, consumer idle |
| pbsparse | `github.com/NCAR/pbsparse` | PBS log parser | Transitive, through hpc-usage-queries |
| XRAS broker, HEUV portal | (no local repos) | External consumers | XRAS is ported (`/api/xras/v1/*`); HEUV is not |

- **Not separate repos:** fs-scans, ncar-hpc-deploy, collectors and the GitOps config all live in
  a repo above. There is no separate helm-values repo.
- **Repo naming:** this repo's remote is `github.com/benkirk/sam-queries`, but docs link PRs at
  `NCAR/sam-queries`. Pick one for the slides.
- **Further reading:** `docs/plans/LDAP_SYNC_API.md` Appendix A has a fuller table of the legacy
  container zoo.

## 5. Facts to resolve before they go on a slide

- **SAM table count:** the sources disagree.
  - `CLAUDE.md` says "~100"; the old deck says 97; `docs/LOCAL_SETUP.md` says ~107 tables and
    7 views; there are 117 `__tablename__` entries under `src/sam`.
  - Resolution: count live from `information_schema` on the test DB, state what is counted
    (tables vs views vs ORM-mapped), and record it in `_variables.yml`.
- **Chart count:** `CLAUDE.md` says 16 charts; there are 14 `BaseChart` subclasses in
  `src/webapp/dashboards/charts/`. Decide which count means what.
- **Route counts:** ~82 API route decorators and ~444 `.route(` calls webapp-wide. These are
  rough greps; recount with a stated method, or say "hundreds".
- **Test counts:** 9,296 collected / 9,239 default, ~90 s (`docs/TESTING.md`, measured
  2026-09-18). Refresh at render time.

## 6. Screenshots and tooling

- **Playwright MCP** drives the local `samuel-dev` (:5050, `docker compose up samuel-dev --watch`)
  through stub Quick Login on obfuscated data. It captures desktop / mobile and light / dark
  variants into `docs/samuel/images/`.
  - `.playwright-mcp/` already holds ~1,089 PNGs from past UI work. Check there before
    re-shooting.
  - ⚠️ **The framework repo is PUBLIC.** Check each shot for real names before committing; a dev
    DB may be unobfuscated. Never screenshot prod.
- **claude-in-chrome skill** uses Ben's browser session for SSO-gated UIs: Argo CD, GitHub
  Actions, the rulesets page, samuel-dev on k8s.
- **Google Workspace MCP** can mine existing CISL/NCAR Drive decks for framing.
- **pptx visual QA goes through LibreOffice** (installed 2026-09-29, §7): run
  `soffice --headless --convert-to pdf --outdir <scratch> deck.pptx`, then read the PDF pages.
  Fonts fall back to a serif, so this checks layout only. The ncar-beamer PDF is read directly,
  and the revealjs HTML is reviewed with Playwright screenshots.

## 7. Open questions

- [ ] Should Part 3 be split into 3a CI/GitOps + 3b HPC data gathering? Decide after drafting it.
- [ ] SAM table count: which definition (§5)?
- [ ] Is the eralchemy2 ER diagram worth the dependency? It would go in the framework's
  `conda-env.yaml` and needs the test DB at render time, so it may be better as a committed PNG
  regenerated by hand. `docs/presentations/overview/NEXT_STEPS.md` sketches eralchemy2 + pydeps
  targets.
- [ ] Publish the combined HTML deck: a claude.ai artifact, GitHub Pages on the framework repo,
  or neither?
- [x] Format strategy: superseded by the mixed, per-part strategy in §13 (2026-09-29).
- [x] LibreOffice installed 2026-09-29 (26.8.0, `brew install --cask --appdir=~/Applications libreoffice`; the `--appdir` avoids the `sudo` prompt that fails under `!`). Verified on `sam_and_pbs.pptx`: 17 s to PDF. Caveats:
  - LibreOffice ignores the theme-font mapping, so slides render in a serif fallback, not Poppins. Treat it as a check for overflow, splits and diagrams, not for exact wrapping.
  - It surfaced a real bug: every slide's date footer reads the literal text `last-modified`, because `date: last-modified` reaches pandoc's footer unresolved. Root cause: the leading Emacs mode-line comment above the front matter. Filed as quarto-docs-framework#7 and fixed by framework PR #8 (merged 2026-09-29). SAMuel deck files must start with `---`. The ncar-beamer PDF shows the same bug on its title slide ("LAST-MODIFIED"), so the fix belongs in the shared `date:` handling, not in one format.
  Original path note: `brew install --cask libreoffice`, which puts an `soffice` wrapper on PATH (conda-forge has no package). QA loop: `soffice --headless --convert-to pdf --outdir <scratch> deck.pptx`, then read the PDF pages. Use a throwaway `-env:UserInstallation=file:///<scratch>/lo-profile` so a running GUI instance doesn't block headless mode.
- [x] The retirement of `docs/presentations/` rides this PR (#679), decided 2026-09-29.
- [x] Branding: resolved 2026-09-29. The framework's `template.pptx` has already been reworked (framework PR #2); use it as is.
- [ ] Pick the §11 devices and part titles; draft the title slide first as the tone test.
- [x] **Branch vs `main` in the framework:** resolved 2026-09-29. Work on the `samuel` branch
  for the long haul, and maybe merge to `main` much later. Merge `main` into `samuel` whenever
  framework fixes land. (`sam_and_pbs` and `new_user_samples` landed on `main`; SAMuel
  deliberately does not.)
- [x] **Does the docs gate follow the local symlink?** No. `test_docs.py` builds its corpus from
  `git ls-files` and skips symlinks, so an ignored symlink is never read.
- [ ] How much of `sam_and_pbs` (§12) to include by reference vs. summarize?

## 8. Phases (one session each; tick as they land)

- [x] **Phase 0 — Scaffold.** Changes by repo:
  - [x] Framework `main`: branch `make-common-multi-deck` (commit `dae87f0`), with the
    `Make.common` `DECKS` generalization, README, CLAUDE.md and `docs/.gitignore`. `sample` (all
    three formats) and `sam_and_pbs` build unchanged. Framework PR #10, awaiting merge.
  - [x] Framework `samuel` branch, cut from that branch (commit `95f495c`): `docs/samuel/` with
    the wrappers, `_variables.yml`, and one divider + placeholder slide per part.
    `make -C docs/samuel all` builds all 15 outputs. Merge `main` in once the PR lands.
  - [x] This repo: `docs/presentations/` reduced to a README pointer plus a `.gitignore` for
    the local `samuel` symlink; `docs/INDEX.md` entry updated.
  - [x] Deleted the stale `presentation` branch, local + origin (was `8623665b`).
- [ ] **Phase 1 — Part 1 Overview:** content, the architecture diagram and the screenshot tour.
  Then revisit the format decision.
- [ ] **Phase 2 — Part 2 Databases:** resolve the §5 facts first.
- [ ] **Phase 3 — Part 3 Pieces:** split if it runs long.
- [ ] **Phase 4 — Appendix + full-deck polish:** a consistent diagram style, a fact-refresh pass on
  `_variables.yml`, and a decision on publishing.

Each phase closes with render → visual review → commit on the framework's `samuel` branch, and
an update here (tick boxes, session log).

## 9. Verification

- **Framework builds:**
  - `make -C docs/samuel pptx html` builds with no `Couldn't find layout named` warnings and no
    missing-image errors;
  - `make -C docs/sample` still builds (back-compat).
- **pptx:** `samuel.pptx` opens in PowerPoint with no "Repair?" prompt, and Poppins is embedded.
- **Visual review:** screenshot every slide of the HTML, plus the PDF-converted pptx. Look for
  overflow, mermaid legibility and broken images.
- **This repo, after retirement:**
  - `pytest tests/unit/gates/test_docs.py` passes.
    - `tests/unit/gates/test_docs.py:30` lists `docs/presentations/` in `RECORD_PREFIXES`. Keep the
      prefix while the README pointer lives there.
    - The gate never sees the local symlink: its corpus is `git ls-files` (§7).
  - Leave the historical mentions in `docs/nrit-review-2026-05/` and
    `migrations/system_status/implemented/2026-05-04-update-prod.md` alone.

## 10. Session log

- **2026-09-29:**
  - Research + planning.
  - Surveyed the framework repo, the stale deck and `presentation` branch, and every source doc
    above; mapped the peer repos.
  - Decisions in §2 confirmed with Ben.
  - No decks built.
  - Voice calibrated from two of Ben's Google Slides decks (§11).
  - Mined the framework's `sam_and_pbs` deck and its CLAUDE.md for reuse and build lessons (§3, §12).
  - Reviewed the framework's new ncar-beamer PDF layer (#4); adopted a mixed, per-part format strategy (§13).
  - LibreOffice installed and verified. The `last-modified` date bug was root-caused and fixed upstream (framework #7/#8).
  - **Handoff complete.** The next session starts at §8 Phase 0 and should first:
    - cut `samuel` from the framework's current `main`, which now includes the beamer layer and the date fix;
    - read the framework's `CLAUDE.md`;
    - take the `Make.common` `DECKS` PR first.
    - Still open in §7: splitting Part 3, the table-count definition, eralchemy2, publishing, the §11 devices, and reuse of `sam_and_pbs`.
- **2026-09-29 (Phase 0):**
  - Refined this doc: stale LibreOffice note, the contradictory §7 branch item, the retirement
    decision (this PR), and the docs-gate question (it reads `git ls-files`).
  - Framework: `Make.common` gained `DECKS`, fragment prerequisites, `OUT ?=`, `.NOTPARALLEL`
    and `.DELETE_ON_ERROR`, plus a deck-output `.gitignore`. The SAMuel scaffold is on `samuel`.
    All 15 outputs build, with no layout warnings or literal dates; the pptx was checked via
    LibreOffice and the beamer PDF read directly.
  - Found the beamer divider-subtitle gap (§13), and answered the `when-format` question.
  - This repo: `docs/presentations/` retired to a pointer.
  - Framework PR #10 opened; `samuel` pushed; the `presentation` branch deleted.
  - **Next:** after #10 merges, merge `main` into `samuel`. Phase 1 (Part 1 Overview) starts with the title slide
    as the tone test (§11).

## 11. Voice, tone and the fun

**SAMuel = SAM, updated for extended lifecycle.** That backronym is the deck's premise and its
running gag: legacy SAM served ~15 years, and SAMuel is its extended lifecycle, not its
obituary.

**Calibration sources.** These are Ben's own decks; read them through the Google Workspace MCP
(`get_presentation`, `get_page_thumbnail` with `inline=true`) before writing any slide text:
- *Confessions of a Vibe Coder* (SEA, 2026-03-11), ID `18U5fchHOiNZRKdmHd4uDolNIw8QhgRbY1OENWDJk16Y`.
  - Slides 3 and 25 cover legacy SAM: ~250K LOC; Java Spring / Hibernate / JSF-PrimeFaces /
    Flyway; cron and shell glue.
  - Slides 8–9 are "Project SAMuel Progression": LOC milestones from Oct 2025 to Mar 2026
    (~8.8K → ~75.4K).
- *Enabling CI/CD Workflows with NCAR-ish HPC Containers* (ISS 2026), ID
  `1xeH-o1eoCb2XfASJfK4aBAc_Jx-cptR4ev0jCXyPcFQ`. It is the technical register: a color-coded
  stack table, real terminal output, and CIRRUS runners.

**Voice rules distilled from those decks:**
- **Direct and unapologetically technical** when the audience warrants it. Give concrete
  numbers ("Over 50 valid combinations", "~1,300 line monstrosity"), show real commands and
  real output, and use a "TL;DR -" line.
- **Self-deprecating candor:**
  - "Full disclosure: I'm a mediocre python programmer…";
  - "Neither of those two repo names make much sense in retrospect";
  - "Is it an improvement…? I'm not so sure, but seems to be 'the way…'".
  - Name the ugly parts plainly; war stories are features.
- **Playful punctuation and asides:**
  - `?!?!`, trailing ellipses, parenthetical wit ("thanks Kevin!");
  - asterisk footnotes that land a joke ("* Friends don't let friends use xAI/Grok.");
  - ♥ / + / ★ legend symbols on tables.
- **Narrative chains:** "This led to… This led to… …Which leads to today."
- **Titles with a wink:** "Down the Rabbit Hole…", "Taming The Beast", "Getting Beyond '-ish'",
  "PSA - Don't let this happen to you…".
- **Credit colleagues by name** where they helped (CIRRUS, NUSD, George, Kevin …).
- **Close with The Good / The Bad / The Murky.** Keep a Backup section after the close.

**Where the fun goes, and where it doesn't:**
- Titles, section dividers, asides, footnotes and speaker notes get the personality. Facts,
  tables and diagrams stay precise; a joke never replaces a number.
- Part 1 is stakeholder-facing: lighter touch, and the fun is in the framing.
- Parts 2–3 can be as nerdy as the material.
- Never punch at legacy SAM or its developers. "Extended lifecycle" is the honorific.

**Candidate devices** (pick, don't use all):
- **Title slide:** the backronym reveal, **S**AM **u**pdated for **e**xtended **l**ifecycle,
  with the letters highlighted.
- **Then vs. now callouts:** SAM (2011–) vs SAMuel. Java/Spring/JSF vs Python/SQLAlchemy/htmx;
  cron + shell vs ledger-backed tasks and gitops; one server vs CIRRUS.
- **A continuation of the Vibe Coder "Progression" slide:** LOC and commits over time from
  `git log`, rendered with the dataviz skill rather than a GitHub screenshot. It picks up where
  the March talk stopped, and "~75K LOC in Mar 2026 → today" is the sequel beat. Define the LOC
  method once and record it in `_variables.yml`.
- **Part titles with personality:** Part 1 "The Lay of the Land"; Part 2 "Where State Lives"
  (or "Four Databases Walk Into a Bar"); Part 3 "Some Assembly Required"; the Appendix "The
  Neighbors".
- **Recurring "PSA - Don't let this happen to you…" slides** for the war stories: the CNPG roll,
  the skip-ci squash trap, the GHCR prune.
- **Live CLI output:** real `sam-search` / `sam-admin` / `jobhist` output on slides.
  - Use the §3 frozen-data pattern: a `refresh_data.sh` writes `data/*.txt` from the obfuscated
    local DB, or from Ben's own records (e.g. `sam-search user benkirk`,
    `sam-search project SCSG0001`).
  - Show it through `{bash}` `cat` cells.
  - Never execute against a DB at render time.
- **Hand-drawn diagrams:** mermaid's `look: handDrawn` for the Part 1 big-picture diagram, if it
  survives the pptx PNG render. Keep the crisp look for Parts 2–3.

**Branding check:**
- Ben's 2026 Google decks use the NSF NCAR template: an orange accent bar left of the title,
  NSF + NCAR "operated by UCAR" logos top right, and the talk title in the footer.
- The framework's `template.pptx` uses the NCAR wave-line artwork. Compare the two.
- If they differ, export one of the Google decks to `.pptx` (Drive export) and harvest its
  master into a pandoc-compliant reference doc. That is a framework `main` PR, following the
  template constraints in the framework README.

## 12. Prior art: the `sam_and_pbs` deck (framework `main`, PRs #2/#3)

The framework repo's `docs/sam_and_pbs/sam_and_pbs.qmd` (~960 lines) is "SAM & PBS —
Accounting & Scheduler Integration". It was written for sysadmins and is the closest existing
work to this deck, in both content and craft. **Reuse it; don't duplicate it.**

**Content to lift or cross-reference:**

| `sam_and_pbs` slide | Use in the SAMuel deck |
|---|---|
| "SAM Projects & Allocations" (Facility → Allocation type → Project → per-resource Allocation + Users; CPU/GPU resource pairs) and its mermaid | Part 1's plain-language domain slide; Part 2's SAM domain tour |
| "SAM Project Trees": two conventions stored identically (**shared pool** `NMMM0003` vs **subdivided award** `CESM0002`); a root's amount already is its subtree total; the API never deduplicates | Part 2, SAM: the allocation-tree slide |
| "The SAM API — SAMuel": basic auth via env, server-side cache, the `fstree_access` / `queue` / `wallclock_exemption` / cache-refresh endpoints | Part 1 surfaces (the API as consumers see it); Part 3 |
| "The Big Picture": SAM API → `samuel2sql.py` / `fsparsetree_mr.py` → `ncar_accounting.db` + `resource_group` → hooks / scheduler → PBS | Part 1's big-picture diagram (SAMuel's outbound edge); the hpc-scheduling-tools row in Appendix A |
| "Tooling — SAM to PBS Pipeline" (5 slides), below | Part 3, data gathering, as the **outbound** half |
| "When Something Looks Wrong" symptom→meaning table | A format to copy for Part 3's ops slides |

Details of the SAM to PBS pipeline:
- cron on `cron.hpc.ucar.edu` every 12 min, ssh to Casper with Derecho as fallback;
- content-gated publishing, with asymmetric digests (bytes vs `.dump`);
- a churn guard;
- `.prev` rollback;
- CSG builds and HSG deploys (`make status/deploy/rollback-*`); a DB swap is free, a tree swap
  costs a scheduler pause.

**Framing it enables.** Part 3's data gathering becomes **data in / data out**:
- *in* is PBS → collectors and `jobhist-sync` → SAMuel (ncar-hpc-deploy);
- *out* is SAMuel → hpc-scheduling-tools → PBS (the SAM to PBS pipeline).

Summarize *out* in 2–3 slides and point to the `sam_and_pbs` deck for the full story.

**Anecdotes it already carries** (voice-compatible, reusable as asides):
- First day in production: ~70 cycles; the DB changed a handful of times and the tree twice.
- A transient SAM connect timeout left Casper on its last good artifacts while Derecho
  published. See the SAM client connect-timeout work.
- The subgraph lesson "took four renders to learn".

**Mechanics to reuse:**
- `tree2mermaid.py` turns real fairshare trees into styled mermaid via `_tree_*.qmd` fragments.
  Consider the same trick for SAMuel's allocation trees.
- `refresh_data.sh` is the model for §3's frozen data.
- **Includes across decks:** `{{< include ../sam_and_pbs/_tree_overview.qmd >}}` works from a
  sibling deck once `samuel` has merged framework `main`.

## 13. Formats: mixed, per part

The framework now has three branded or brandable outputs. Use each where it is strongest, from
one source.

| Format | Build | Strengths | Weak spots | Where we use it |
|---|---|---|---|---|
| **pptx** (NCAR template) | `make pptx` | Editable, shareable, the CISL default; four post-render fixes | Mermaid is baked PNG; content after tables/images splits slides; autofit traps | Part 1 (primary); handout builds of everything |
| **ncar-beamer PDF** (framework #4; theme vendored from `benkirk/NCAR_beamer_template`) | `make pdf` (`--to ncar-beamer`, XeLaTeX, bundled Poppins) | Crisp typography; real monospace code; 2026 blue brand; readable directly by Claude, so it is the easiest format to QA | **No autofit:** dense slides overflow silently; fix with trimming or `{.shrink}` on the heading | Part 1 (primary, alongside pptx); archival and emailable copies |
| **revealjs HTML** | `make html` | Live mermaid/SVG; `{.scrollable}`; code-line highlighting and fragments; speaker view; `{.smaller}`; iframes; OJS interactivity | **No NCAR theme yet:** needs a small SCSS theme (brand colors, Poppins, logo lockup) as a framework PR | Parts 2–3 when presenting live |
| **Companion interactive pages** (claude.ai Artifacts, HTML/React) | published separately and linked from a slide | Anything too rich for a slide, e.g. a schema or ER explorer, the CI → GHCR → Argo pipeline walk-through, the ncar-hpc-deploy cadence timeline, and an allocation-tree explorer on the `tree2mermaid` data | Outside the deck build; private by default; links go stale | Deep-dive details, as needed |

**One source, conditional content:**
- **Interactive vs. static:** put the interactive element in
  `::: {.content-visible when-format="revealjs"}` and the static PNG/table fallback in
  `::: {.content-visible unless-format="revealjs"}`.
  - **Verified in Phase 0:** `when-format="beamer"` matches the custom `ncar-beamer` format;
    `when-format="ncar-beamer"` matches nothing. Always write `beamer`.
- **Classes each format honors:**
  - ncar-beamer: `{.feature background-image="images/x.jpg"}` gives a full-bleed photo slide, and
    `background-image=` doubles as the revealjs background. It also honors `{.closing}`,
    `{.example}` / `{.alert}` blocks, `[text]{.alert}` and `{.shrink}`.
  - pptx ignores those classes.
  - revealjs honors `{.smaller}` and `{.scrollable}`.
- **The pptx gotchas still bind:** a slide must survive pptx even when HTML is primary for its
  part, because the handout builds depend on it.

**Divider subtitles in beamer (found in Phase 0).** Beamer renders text after a `#` divider
as its own slide, titled after the section. `section_subtitle.py` fixes only pptx, and no other
deck had exercised this in a PDF. For now the bodies fence each subtitle in
`::: {.content-visible unless-format="beamer"}`. The proper fix is a framework Lua filter that
puts the paragraph on the beamer section page; it is a framework `main` follow-up.

**Build implications for Phase 0:**
- The `Make.common` `DECKS` generalization must keep the pdf recipe's `_extensions` symlink
  prerequisite.
- Build every format for every deck by default. A part can opt out of a format, rather than
  opting in.
- CI already installs TinyTeX for `sample.pdf`, so a SAMuel build job can reuse that setup.

**QA per format:**
- pptx: LibreOffice → PDF, then read it. Fonts fall back to a serif, so check layout only.
- ncar-beamer: read the PDF directly; the fonts are real. Look hardest for overflow, since
  beamer has no autofit.
- revealjs: Playwright screenshots at 1280×720, plus one narrow viewport.
- Companion pages: the artifact-design checklist (both themes, phone width).

**Candidate companion pages** (decide per phase; each needs a slide that earns the link):
- Part 2: an interactive map of SAMuel's databases and engines (from `engine_inventory`); an
  allocation-tree explorer.
- Part 3: the CI → image → Argo → pods pipeline, clickable per stage; the ncar-hpc-deploy lanes and
  cadences as a timeline.
- Appendix: a peer-repo dependency graph.
