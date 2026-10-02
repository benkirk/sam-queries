# SAMuel presentation series — a multi-part Quarto deck

**Status:** Parts 1–5 drafted on framework `samuel` (Part 5, "Deployment & Operations", 2026-10-01); awaiting Ben's review (§8). On 2026-09-30 Part 2 Concepts was inserted and Pieces split by axis into Part 4 (systems and data flow) and Part 5 (hosting, GitOps, deployment). This doc is the handoff: each session
picks up the next unchecked phase in §8, ticks it, and appends to the session log (§10).
**Goal:** replace the stale `docs/presentations/overview/` with a comprehensive, multi-part
SAMuel presentation, authored in the standalone `~/Documents/quarto-docs-framework` repo and
linked into this worktree.

## 1. Why

`docs/presentations/overview/overview.qmd` (352 lines, last touched 2026-04-25) is out of date:

- **Its numbers are wrong:** it says 97 tables and ~1,400 tests. The suite is now 9,296 collected
  tests (`docs/TESTING.md`).
- **Whole areas are missing:** it has nothing on plugins, system_status, job history, `fs-scans`,
  CIRRUS/Argo, RBAC, scheduled tasks, notifications, account registration or `ncar-hpc-deploy`.
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
| Where the decks live | A long-lived **`samuel` branch** of quarto-docs-framework, content in `docs/samuel/`. The template's `main` stays clean for cloners. Framework-generic improvements go to `main` by PR, and `samuel` is **rebased** onto `main` after each lands (since 2026-09-30; before that, `main` was merged in). |
| This repo | **Retire `docs/presentations/`**, leaving a README pointer to the framework repo. Add a **local-only, gitignored** symlink `docs/presentations/samuel -> ~/Documents/quarto-docs-framework/docs/samuel`; an absolute symlink would dangle for CI and everyone else. Delete the stale `presentation` branch, local and remote, after confirming at that step. |
| Format | **Mixed, chosen per part** (§13). Part 1 Overview: pptx + ncar-beamer PDF, both branded. Parts 2–4 and details: revealjs HTML where interactivity earns its keep, plus companion interactive pages where slides run out; pptx/PDF builds remain as handouts. Revised 2026-09-29, replacing "pptx + HTML equally". |
| Audience | **CISL management / stakeholders:** Part 1 must stand alone as a non-technical briefing. **Incoming developers / handoff:** Parts 2–4 go deep, with code paths, gotchas and war stories. |
| Voice | Direct, unapologetically technical, playful; calibrated on Ben's own decks (§11). |
| Planning vehicle | This doc, on branch `samuel-presentation-plan`, as a docs-only draft PR against `staging`. It matures over several sessions. |

## 3. Deck architecture (`quarto-docs-framework/docs/samuel/`)

One directory holds every part. Each part is a thin wrapper around an underscore body file;
Quarto skips `_*.qmd` when rendering a project. The full deck includes every body:

```
docs/samuel/
  Makefile            DECKS := samuel 1-overview 2-concepts 3-databases 4-systems 5-deployment A-peers
  _quarto.yml -> ../common/_quarto.yml   (auto-symlinked by Make.common)
  _variables.yml      facts: counts, hosts, dates, each with a source + as-of comment
  samuel.qmd          full deck: frontmatter + {{< include >}} of every body
  1-overview.qmd      frontmatter + {{< include _1-overview.qmd >}}
  _1-overview.qmd     body: `#` section-header slides, `##` slides (slide-level 2)
  2-concepts.qmd  / _2-concepts.qmd
  3-databases.qmd / _3-databases.qmd
  4-systems.qmd   / _4-systems.qmd
  5-deployment.qmd / _5-deployment.qmd
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

**Tooling: which repo carries what** (decided 2026-09-29):

| Tool | Home | Why |
|---|---|---|
| Mermaid, Graphviz `{dot}` | the framework (Quarto bundles both, so nothing to install) | generic deck craft; conventions in the framework's CLAUDE.md (#12) |
| ER generator (ORM → Graphviz) | sam-queries `scripts/er_diagram.py` (#680) | it imports SAM's models; replaces `eralchemy2` with no dependency |
| Charts, `refresh_data.sh` | the framework's `docs/samuel/`, run with the sam-queries Python | refresh time only; it already needs the sam-queries checkout |
| `quarto`, `eralchemy2`, `pydeps` | removed from sam-queries' `conda-env.yaml` (#680) | the decks left this repo |

**Authoring rules:**
- **Diagrams:** mermaid for simple flows. Use Graphviz `{dot}` when grouping or ranking
  matters: its clusters keep their layout where mermaid subgraphs fail. Both render to PNG in
  pptx and PDF, and live in HTML. Keep one idea per diagram; large graphs are the main pptx
  friction.
- **ER diagrams** are generated, never drawn: `refresh_data.sh` runs
  `$SAMUEL_REPO/scripts/er_diagram.py <tables…>` into `_er_*.qmd` fragments, one per domain
  (about 6 tables each), so they cannot drift from the models.
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
    - 14 modules in `src/webapp/api/v1/` (15 `.py` files, one of them `__init__.py`);
    - 5 legacy-compat blueprints frozen byte-for-byte;
    - the XRAS server side under `/api/xras/v1`: 9 routes plus 2 catch-alls that log anything
      unmapped (legacy's own mapped surface is 8). Say "XRAS's handoff endpoints", not a count;
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
  - the LDAP mirror (`sam-ldap-syncd`);
  - PBS → collectors → the status API;
  - job_history → the accounting ingest;
  - the peer DBs;
  - consumers (`hpc-scheduling-tools`, LDAP provisioning, the legacy-compat API callers).
  - Sources: `docs/apis/SYSTEMS_INTEGRATION_APIs.md`, `docs/apis/CHARGING_INTEGRATION.md`.
- **The plugin approach:**
  - **Registry:** `src/sam/plugins.py`. A `Plugin(name, package, install_hint)` has `.load()`,
    which raises `PluginUnavailableError`, and `.available`. It defines three plugins, all from
    the `[hpc]` extra:
    - `HPC_USAGE_QUERIES` → `job_history`;
    - `FS_SCANS` → `fs_scans`, which ships in the same `hpc-usage-queries` wheel;
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

### Part 2 — Concepts (added 2026-09-30)

The domain model in words and pictures, before any table appears. It borrows the concepts from
`sam_and_pbs` (§12) with less of its math. Ben's framing rules:
- Call them **concepts**, outright. There is no "cast", and no banking analogy.
- **Accounts:** a project has a list of accounts, one per resource, and each account has its own
  (potentially different) list of authorized users. The data model permits that, but it is
  usually more flexibility than we want. The mental model is "users on a project"; the details
  are more complicated.
- **Charge adjustments** are `†` footnotes where they matter, not a slide of their own.

Outline (40 slides as drafted). Blocks A–C are the natural 2a, and D–F the 2b, if it splits:

| Block | Slides | Sources |
|---|---|---|
| A. The core concepts | one sentence; the concept map (Graphviz); users (mirrored from LDAP, never created); projects; resources and units; facility → panel → allocation type | `src/sam/enums.py`, `src/sam/resources/facilities.py`, `docs/xras/PROJECT_AND_ACCOUNT_LIFECYCLE.md` |
| B. Users on a project | mostly; through resources (no user↔project table); SCSG0001's ten accounts + roster (frozen); membership dates; PSA, the rowless lead; live accounts | `src/sam/accounting/accounts.py`, `Project.users` / `live_accounts`, `ACCOUNT_USER_SOFT_DELETE.md`, `LEAD_ADMIN_MEMBERSHIP.md` |
| C. Allocations and the ledger | amount × window; remaining = allocated − used; SAM used to keep every job; jobs live with their machine; the ledger; replay (frozen SCSG0001); the CESM0002 926; PSA, supplement ≠ total; the verbs | `src/sam/accounting/allocations.py` (`replay_amount`), `src/sam/manage/allocations.py`, `tests/unit/manage/test_accounting_models.py`, `CHARGING_INGEST.md` |
| D. Access branches and groups | branches; derived, never stored; two kinds of groups (project groups: SAM is the truth; adhoc: upstream is); the fine print; who reads it | `src/sam/security/access.py`, `src/sam/queries/{directory,project}_access.py`, `docs/apis/SYSTEMS_INTEGRATION_APIs.md` § Group Pipeline, `SAM_LDAP_SYNCD_REFERENCE.md` |
| E. Trees | projects are trees; usage rolls uphill; allocations are trees too; two conventions; award and pool (SAM amounts, frozen); as PBS sees it; detach ≠ independence; the tree audit (frozen) | `project_tree_charging.md`, `ALLOCATION_TREE_EDITING.md`, `src/sam/queries/tree_audit.py` |
| F. Wrap | follow one job's hours; TL;DR | — |

**Frozen data:** `docs/samuel/concepts_data.py`, run by `refresh_data.sh` (test DB on 3307
only; the CLIs get explicit `SAM_DB_*`, which win over the `.env` they re-load), writes
`_out_accounts`, `_out_users`, `_out_replay`, `_out_audit`, `_tree_sam_award` and
`_tree_sam_pool`. Only SCSG0001 (Ben's own) and projcodes are read; no `--verbose`, no
`directory_access` payloads. The replay is asserted, so the refresh fails loudly if it drifts.

### Part 3 — The Databases

The tree slides moved up to Part 2 on 2026-09-30; the two ER slides stay here as "Part 2's
concepts, as tables" and "The balance, as tables".

- **One-slide map:** built from `src/webapp/utils/engine_inventory.py` (`EngineSource`,
  `engine_sources()`). The same inventory drives the Admin Configuration card,
  `/api/v1/health/db-pool` and `/database`.

  | DB | Engine | Prod | Dev | Owning code | Writers → readers |
  |---|---|---|---|---|---|
  | `sam` | MySQL (prod); Postgres dual-backend | `sam-sql.ucar.edu` (the VM) | `sam_dev` on CNPG (samuel-dev); compose MySQL; test DBs on :3307 (MySQL) and :5434 (Postgres) | `src/sam/`, `sam.session`, `sam.sqlcompat` | webapp, sam-admin, XRAS, charge ingest, tasks → everything |
  | `system_status` | Postgres (prod), MySQL (local) | CNPG `csg-postgres`, DB `system_status` | `system_status_dev` | `src/system_status/`, Alembic 0001–0007 | collectors via the status API, task ledger, login sightings → status dashboard, admin |
  | `job_history` | Postgres (read-only from SAM) | `csg-postgres-ro`, DBs `derecho_jobs` / `casper_jobs` | same | `hpc-usage-queries` (`job_history`) | `jobhist-sync` → My Jobs, drill-downs, `sam-admin accounting --comp` |
  | `fs_scans` | Postgres (CNPG) | `csg-postgres-ro`, DBs `campaign` / `destor`, one schema per collection | same | `hpc-usage-queries` (`fs_scans`) | scanners/importers → disk-scan tabs |

- **SAM:**
  - domain tour: users / projects / accounts / allocations (a tree) / resources / charging;
  - the balance calculation: `remaining = allocated − (charges + adjustments)`;
  - the charge-summary tables: **five**, one per `activity_type` (comp, dav, hpc, disk, archive;
    `src/sam/accounting/calculator.py`), not the four CLAUDE.md names;
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
    - Symptom: a 3.5-minute outage when a `csg-postgres` roll failed the system_status readiness
      check.
    - Lesson: never add a secondary bind to `/ready`'s required set.
- **job history:**
  - connection settings: 60 s statement timeout, pool of 5 + 10 overflow (`src/webapp/config.py`);
  - Source: `docs/plans/implemented/JOB_HISTORY_DASHBOARD.md`.
- **`fs-scans`:** 100 s statement timeout; one engine per database × collection schema.
  Source: `FS_SCANS_PLUGIN-part1.md`.
- **`/database` browser:** read-only rows from every engine, found by reflection; it replaced
  Flask-Admin. Source: `docs/plans/implemented/DB_BROWSER.md`.
- **ER diagrams:** per-domain fragments generated by `scripts/er_diagram.py` (§3).

### Part 4 — Who Talks to Whom (systems and data flow; added 2026-09-30, first draft done)

Ben split the old "Pieces" by axis on 2026-09-30. Part 4 covers what talks to what and which
way the data moves. Part 5 covers where it runs and how a commit gets there.

**Ben's framing:**
- **Legacy SAM is drawn as grey boxes throughout.** The Java webapp (Tomcat on `sam-tomcat`,
  behind `sam.ucar.edu`) and the `sam-app` helpers are still running, and they will overlap
  SAMuel for a while.
- **The key message is that we do not yet fully own the SQL schema.**
- Diagram colors: grey legacy (`#E5E7EB` / `#6B7280` / `#374151`), SAMuel in the Part 2 blue,
  and the shared database in orange.

**Outline as drafted** (framework `de99dce`, 31 slides):
- **A. The map:**
  - the whole neighborhood (a pinned `neato` grid);
  - four flows, one database;
  - two apps, one database;
  - **we don't own the schema (yet)**;
  - the PSA for the 2026-08-10 legacy V37 migration, which dropped `users` columns the ORM
    still selected;
  - who's still on legacy.
- **B. Identity in:**
  - the directory to `users` chain (UCAR directory, the replica, the transformer,
    `sam-ldap-syncd`, legacy's ldapsync API, MySQL);
  - what the synchronizer writes;
  - taking over the ldapsync endpoints (planned);
  - signing in.
- **C. Allocations (XRAS):** ARC to XRAS to SAMuel; incoming, with six verbs and the Steve
  Peckins 200/OK quote; outgoing, with the sweep and the writes.
- **D. Usage in:** data in / data out; collectors; job history and charges, "ORM, not REST";
  the real `etc/schedule`.
- **E. Access out:** four API families (route counts); same bytes, new address (legacy
  paths); the LDAP loop closing; SAMuel to PBS, in one slide.
- **F. Everything else:** outbound side-channels; the overlap, mapped; TL;DR.

**Facts, as verified 2026-09-30** against `origin/staging` `886c2f09`:
- **Schema:**
  - Legacy's Flyway history runs to `V38__AddDeactivateToUsersTable` (2026-08-09), applied by
    hand with `tools/dbmigrate.sh`, not at startup.
  - Every `CREATE TABLE` in legacy's migrations, matched against the ORM: **92** of the 108
    modeled tables are legacy's, and **16** are SAMuel's own: `account_*`, `notification_*`,
    `samuel_role*`, `external_ticket`, `xras_*` ledgers.
  - The incident is pinned in `tests/api/test_health_endpoints.py` (`TestSchemaDriftContract`).
- **Identity:** the source map is `docs/plans/SAM_LDAP_SYNCD_REFERENCE.md` §1.2 and §2.4–2.5,
  plus `docs/plans/LDAP_SYNC_API.md`. The Appendix B census gives the legacy callers:
  ldapsync is live, AMIE is idle, HEUV is not ported, and XRAS has made 0 legacy calls since
  08-28.
- **API:** decorator counts give core 39 routes (7 modules), legacy shapes 21 (6), fairshare 1,
  and XRAS 11 (5). Nothing is mounted at `/api/protected`.
- **Collector key:** the `api_collector` role holds `MANAGE_SYSTEM_STATUS` and
  `MANAGE_CHARGE_SUMMARIES` (`src/sam/security/rbac_defaults.py`).
- **Lanes:** prod's daily step is `jobhist-sync daily`. `accounting-disk` runs on casper only.
  Daily fires at 01:07 on casper and 01:12 on derecho. The prod lane went live with its first
  daily run on 2026-09-30.
- **Public-repo hygiene:** no IP addresses, OpenBao paths, or `pg_hba`/superuser details on
  any slide or in the notes.

**Open for review:**
- the working title ("Who Talks to Whom");
- whether the scheduler hosts and the LDAP provisioner have actually repointed from legacy.
  The census only says "ported; both serve them".

### Part 5 — Deployment & Operations (hosting, GitOps, deployment; first draft done)

**Status:** first draft on framework `samuel` (`f49e8ac` + `679b129`, 2026-10-01): 27 content
slides plus 5 dividers. The outline below was verified on 2026-09-30 (`886c2f09`) and rechecked on 2026-10-01
(`1b624fc3`: no change under `helm/`, `.github/` or `compose.yaml`).

**Ben's calls (2026-10-01):**
- **Title:** "Deployment & Operations", replacing "Some Assembly Required".
- **Length:** the full section.
- **No PSA slides.** The GHCR prune and the squash trap stay out of Part 5; the prune is one
  notes line.
- **HPC deployment is downplayed.** Some readers may see the host-lane automation as a
  code-injection risk, so the deck highlights GitOps to k8s. The HPC hosts get one slide that
  says only that the same image runs there and that "HPC deployments from staging and main are
  also straightforward". There are no lane mechanics on slides or in notes.
- **CNPG backups:** one line, "snapshots configured; scheduled backups to Boreas planned".
- **A slide on the Claude watch skills:** `watch-prod`, `watch-dev`, `profile-dev`, and the peer
  `watch-cnpg`.
- **The companion pipeline page is built** (§13).

- **A. Commit to image:**
  - **Branch flow:** feature to `staging` pins `cirrus-dev` and opens or refreshes the
    promotion PR. Merging that pushes `main`, which pins `cirrus`, and `sync-staging-to-main`
    then resets staging.
  - **Fourteen workflows** (table):

    | Workflow | Purpose | Trigger |
    |---|---|---|
    | `sam-ci-docker` | `pytest-mysql` (coverage), `pytest-postgres`, `perf`; shared `test-stack` action | PR to main/staging/integration, push main |
    | `ci-staging` | TruffleHog, MegaLinter, Terraform fmt + validate, Helm lint/renders | PR to staging |
    | `browser-smoke` | Chromium sweep of every dashboard | as sam-ci-docker |
    | `sam-ci-conda_make` | conda/pip install path + CLI smoke | as sam-ci-docker |
    | `test-install` | `install.sh`: first run, update run, LFS recovery | as sam-ci-docker |
    | `mega-linter` | MegaLinter (cupcake) | PR to main |
    | `build-images-cirrus-deploy` | build `samuel` to GHCR, pin `cirrus` / `cirrus-dev` | push main/staging, `v*` tags, dispatch |
    | `open-staging-promotion` | keeps one staging-to-main PR open | push staging |
    | `sync-staging-to-main` | resets staging to main after promotion | PR closed on main |
    | `clean-ghcr` | GHCR prune; keeps any manifest a kept index references (#670) | Sundays 03:15 UTC |
    | `_prune-workflow-runs`, `cron-clean-action-log`, `manually-clean-action-log` | delete old workflow runs | reusable; 11th monthly; dispatch |
    | `deploy-staging` | the retired AWS ECS deploy | dispatch only |

  - **The three-way test split** landed in #677/#678 (`docs/plans/implemented/CI_PARALLEL_SPLIT.md`).
  - **One `samuel` image:**
    - built on native amd64 and arm64 runners, pushed by digest, and merged with `imagetools`;
    - peer plugins are pinned to SHAs via `git ls-remote`;
    - size went from 2.07 GB to 679 MB, and build time from 8 to about 3 min
      (`docs/plans/implemented/JOBS_IMAGE.md`).
  - **PSAs:**
    - the GHCR prune (#670): the 2026-09-27 run left 12 of 47 `webapp` tags and all 15 `mysql`
      tags unpullable;
    - the squash trap (#406/#408): write the token broken.
- **B. GitOps on CIRRUS:**
  - **The image line:**
    - CI's only edit is the `image:` line;
    - `update-helm` resets the cirrus branch to the ref's tree, `sed`s the line, and
      force-pushes one commit.
  - **Push lock:** the GitHub App `cirrus-benkirk-deployer` is the only bypass on the ruleset
    "Lock cirrus to deploy workflow".
  - **The publish pipeline:** redraw the ASCII diagram in `docs/CIRRUS_PUBLISHING.md` (lines
    9–22) as Graphviz.
  - **Two Argo apps:**

    | | prod | dev |
    |---|---|---|
    | App and namespace | `sam-query`, `sam-queries` | `sam-query-dev`, `sam-queries-dev` |
    | Values | `values.yaml` | plus `values-dev.yaml` |
    | Database | MySQL VM | CNPG `sam_dev` |
    | Replicas | 2 | 1 |
    | Mail, XRAS, Jira | on | off |
    | Tasks disabled | 2 | 5 |

- **C. Runtime on nwc1:**
  - **Pods:** 2 replicas, `maxUnavailable 0`, `maxSurge 1`, PDB `minAvailable 1`, a hostname
    spread; `gunicorn` `gthread` with 8 threads.
  - **Ingress:** one InCommon multi-SAN cert on `nginx-external` for two hosts.
  - **Secrets:** 9 ExternalSecrets through SecretStore `csg-ro`; dev drops xras and jira,
    leaving 7.
  - **Redis:** 192 MB, `allkeys-lru`, no persistence. DB 0 is the cache and DB 1 the limiter.
  - **The `samuel-tasks` CronJob:**
    - runs at `7,22,37,52 * * * *` UTC with `Forbid`;
    - `SAM_TASKS_DISABLED` is fail-open;
    - the lease must outlive `activeDeadlineSeconds`.
- **D. The companion CNPG cluster** (2–3 slides). The source is `hpc-usage-queries` `helm/`,
  which Argo renders, so `helm list` is empty.
  - **What it hosts** (a table: database, writer, reader):
    - `derecho_jobs` and `casper_jobs`;
    - `campaign` and `destor`;
    - `system_status`, the only data that can't be rebuilt;
    - `system_status_dev` and `sam_dev`.
  - **How it runs:**
    - PG 18.3, 2 instances, 512Gi each;
    - rw and ro LoadBalancers;
    - switchover rolls (`switchoverDelay 60`);
    - limits of 4 CPU and 64Gi;
    - `scripts/cnpg_watch.sh`.
  - **The 2026-09-13 roll:** Part 3 owns the PSA, so Part 5 only points back to it. Measured
    since: a switchover gap under 1 s, and a failover of about 16 s.
  - **Backups:** no `ScheduledBackup` exists yet; the plan is barman to Boreas
    (`CNPG_BACKUPS.md` in that repo). Phrase it as "planned", and confirm the phrasing with Ben.
- **E. Dev vs prod:**
  - **Compose:**

    | Service | Profile | Host port |
    |---|---|---|
    | `samuel` | default | 7050 |
    | `samuel-dev` | default | 5050 |
    | `cache` | default | 6379 |
    | `mysql` | default | 3306 |
    | `mysql-test` | test | 3307 |
    | `postgres-test` | test | 5434 |
    | `postgres` | pg | 5433 |

  - **samuel-dev:** `make refresh-dev`; `helm/tests/test-dev-render.sh`.
  - **The four `scripts/cirrus_*.sh`:** healthcheck, watch, weblog audit, and redis purge, the
    only one that mutates, and only with `--yes`.
  - **The Claude skills** (a table): skill, when to load it, what it drives, and what it
    reports.
- **F. The HPC hosts:** reduced to one broad slide at the end of B (Ben, 2026-10-01; see above).

### Appendix A — Peer repos ("The Neighbors"; drafted 2026-10-01)

Ben's ask: 1–2 slides per peer repo, 3 for `hpc-usage-queries` (both plugins), 2 for
`hpc-scheduling-tools` (the PBS rules kept apart, then packaged and served by the API), legacy
SAM and its container zoo, private repos named but not linked, and one undetailed slide for
what is retired or retiring.

| Repo | Visibility | What it is | Coupling to SAMuel |
|---|---|---|---|
| `hpc-usage-queries` (`~/codes/hpc-usage-queries/devel`) | public, `benkirk/` | `job_history` + `fs_scans`, ~34K Python lines, ~920 tests; owns the `csg-postgres` CNPG chart | Two plugins (`[hpc]` extra); `sam-admin accounting --comp` reads `daily_summary`; sync runs in the `ncar-hpc-deploy` lane |
| `hpc-scheduling-tools` (`~/codes/hpc-scheduling-tools`) | private, NCAR | `fsparsetree-mr` (`resource_group`); reference copies of HSG's hooks (`sample_hooks/`) and `samuel2sql` (`ncar_accounting.db`), iterated on since; `hpc-sched-refresh` (playground) | `HPC_SCHEDULING_TOOLS` plugin behind `/api/v1/fairshare/<machine>`; reads `fstree_access`, `queue`, `wallclock_exemption` |
| `pbsparse` | public, NCAR | PBS accounting-log parser | Transitive, through `hpc-usage-queries` |
| `PBS_hooks` | private, NCAR | HSG's deployed `accounting` and `wallclock` hooks (branch `production`) | HSG's own `samuel2sql` builds their database from SAMuel's legacy-shaped APIs |
| legacy SAM (`~/codes/sam`, symlinked as `legacy_sam`) | private, NCAR | Java/Spring/JSF/Hibernate WAR on Tomcat; ~2,900 `.java` files, 229 `.xhtml` | Same MySQL DB; SAMuel is porting its API families |
| container zoo (`legacy_sam/container_zoo/`) | mostly private (`amie-sam-mediator`, `amiemediator` public) | LDAP chain, AMIE chain, `sweet` base images, `sam-app` host config | Only `sam-ldap-syncd` and `amie-sam-mediator` call legacy SAM |

- **Slides (`_A-peers.qmd`, 12 with title and section):** Who's who; `hpc-usage-queries` x3
  (overview, `job_history` logs to charges, `fs_scans` and the CNPG cluster); `hpc-scheduling-tools`
  x2 (kept apart; packaged once, served by the API); legacy SAM; the container zoo; retired or
  soon to be; TL;DR.
- **Left out:** `qhist`, `csg-utils`, `fs_usage`, `sam_usage_data`, `parse_quotas` (no
  dependency).
- **Repo naming, resolved:** `NCAR/sam-queries` does not exist; slides use
  `github.com/benkirk/sam-queries`.
- **The hooks are HSG's** (Ben, 2026-10-02): HSG runs and maintains their own hooks and
  `samuel2sql`. The copies in `hpc-scheduling-tools` are references Ben has iterated on since.
  Only the fairshare tree is this repo's, and only the tree is served by SAMuel's API.
- **To confirm with Ben:** nothing in `hpc-scheduling-tools` calls `/api/v1/fairshare`;
  `refresh.py` (the playground) builds both artifacts from the raw endpoints. That HSG's
  production cron downloads the tree is Ben's statement (2026-10-01), not something in a repo.
- **Further reading:** `docs/plans/LDAP_SYNC_API.md` Appendix A has the fuller zoo survey.

### Appendix B — Two Dialects (the APIs; drafted 2026-10-02)

Ben's ask: 8–10 slides with code, for API consumers and future SAMuel developers, including
cache refreshing. The growth pitch is for HSG and groups like it, tied to old endpoints or
wanting more but not yet asking.

- **Slides (`_B-apis.qmd`, 12 with title and section):**
  - One front door;
  - The old dialect: no client change;
  - Hand-built, on purpose (the `queue_access.py` dict);
  - Proving it (the parity harness, 590 of 590, the phone `MIN()`);
  - The bridge: legacy shape, schema inside (`DiskQuotaSchema`, `data_key`);
  - The new dialect (the projects list route and its JSON envelope);
  - Writes follow the web app's rules (`HTMX_API_READINESS.md`: about 87 of 160 writes are
    cheap, 5 have API twins);
  - Cached, and how to refresh (5 minutes in Redis, `POST …/refresh`, `sam-admin cache
    --refresh`);
  - Need more? Ask;
  - TL;DR.
- **Facts:**
  - 72 routes on 15 blueprints (core 39, legacy shapes 21, fairshare 1, XRAS 11);
  - keys held per route under `RBAC_SOURCE=db`;
  - `RATELIMIT_M2M` 120 per minute;
  - `CACHE_DEFAULT_TIMEOUT` 300, Redis in production;
  - `fsparsetree_mr` refreshes `fstree_access` before it reads.
- **Code is trimmed:** the `DiskQuotaSchema` snippet drops `allow_none=True`, and the projects
  route shows only its last lines.

### Appendix C — Keeping It Snappy (performance; placeholder, 2026-10-01)

- **Measure first:**
  - request `db=`/`cpu=`/`q=` (#531);
  - `tests/perf/baselines.json`;
  - `docs/plans/implemented/PROD_PERF_WATCH.md`.
- **The read model:** `docs/plans/implemented/READ_MODEL.md`, live (`READ_MODEL_ENABLED=1`,
  #534/#543/#546), and `FSTREE_LATENCY_INVESTIGATION.md`. The slide needs before and after
  numbers.
- **The caches:**
  - CLAUDE.md §11, static assets: 87.5% of requests were 304s;
  - `docs/plans/REDIS_CACHE_PREFIXES.md`;
  - `GLOBAL_CACHE_REFRESH_API.md`;
  - charts keyed by input data.
- **Query shape:**
  - `directory_access` split-and-assemble (~6.9 s before; after is still to fill in);
  - the jobs end-composite indexes;
  - the CNPG temp-spill fix.

## 5. Facts to resolve before they go on a slide

- **The name:** the app, and Ben's March deck, say "Systems Accounting Manager". `CLAUDE.md`'s
  "System for Allocation Management" is the outlier; the slides use the app's name.
- **The LDAP identity mirror** (`sam-ldap-syncd`) still talks to **legacy** SAM on the shared
  database (`docs/plans/LDAP_SYNC_API.md`). The Part 1 diagram shows identities flowing into
  the SAM database, and the notes say legacy still owns that edge.
- **Legacy stack facts** (Spring/JSF, ~250K LOC, ~15 years) come only from Ben's March deck,
  not from anything in this repo. `_variables.yml` cites the slide.

- **SAM table count:** the sources disagree.
  - `CLAUDE.md` says "~100"; the old deck says 97; `docs/LOCAL_SETUP.md` says ~107 tables and
    7 views; there are 117 `__tablename__` entries under `src/sam`.
  - **Resolved 2026-09-30** by `docs/samuel/count_tables.py` (run from `refresh_data.sh`, port
    3307 only): the test DB holds **114 tables + 7 views = 121** (the `/database` browser agrees);
    the ORM maps **108 + 7**; the 6 unmapped are legacy (`schema_version`, `EXPORT_TABLE`,
    `TIME_DIM`, `stage_hpc_job`, `tables_dictionary`, one scratch table); nothing is modeled but
    missing. (The 117 above was a text grep for `__tablename__`, not the metadata.) The slide
    quotes the script.
- **Chart count:** `CLAUDE.md` says 16 charts; there are 14 `BaseChart` subclasses in
  `src/webapp/dashboards/charts/`. Decide which count means what.
- **Route counts:** ~82 API route decorators and ~444 `.route(` calls webapp-wide. These are
  rough greps; recount with a stated method, or say "hundreds".
- **Test counts:** 9,296 collected / 9,239 default, ~90 s (`docs/TESTING.md`, measured
  2026-09-18). Refresh at render time.

## 6. Screenshots and tooling

**How the Part 1 shots were taken (reuse this):**
- The local dev DB (3306) is **real data** by Ben's choice, and `samuel-dev` reads it. Never
  screenshot it for this deck.
- Instead, run a throwaway instance on the obfuscated test DB:
  - `docker compose run -d --rm --no-deps --name samuel-shots -p 5051:5050`, with the env
    overrides `SAM_DB_SERVER=mysql-test` and `SAM_DB_PORT=3306`;
  - its own Redis DBs, `CACHE_REDIS_URL=redis://cache:6379/5` and `RATELIMIT_STORAGE_URI=…/6`,
    so it cannot share cache keys with `samuel-dev`;
  - the three plugins off (`JOB_HISTORY_MACHINES=`, `FS_SCANS_ENABLED=0`,
    `HPC_SCHEDULING_TOOLS_ENABLED=0`), because their Postgres data is real;
  - `NOTIFY_ENABLED=0`.
- Log in with Quick Login as `benkirk` (Ben's own identity is fine). Everyone else shows up as
  `user_xxxxxxxx`, with synthetic names and titles.
- Shots are 1440×810. Light mode needs `emulateMedia({colorScheme: 'light'})` plus the
  `sam_theme=light` cookie. The dark mobile shot is 390×844 with `sam_layout=mobile`.
- The status page's "no updates in 26 hours" banner comes from idle local collectors. It is
  removed from the DOM before capture, and the slide notes say so.
- ⚠️ Don't run `docker compose config` in a session transcript: it prints the `.env` secrets.
- Found while shooting: on mobile, the Rolling Consumption Rate gauge collapses and its axis
  labels overlap ("200%0%"). It's a webapp bug, not yet filed; the deck's shot crops it out.

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

- [x] Should Part 4 be split into 4a CI/GitOps + 4b HPC data gathering? Superseded 2026-09-30: Ben split it by axis instead, into Part 4 (systems and data flow) and Part 5 (hosting, GitOps, deployment).
- [ ] Should Part 2 (Concepts, 40 slides) be split into 2a (blocks A–C) + 2b (D–F)? Decide at review.
- [x] SAM table count: all three, stated (§5); the slide leads with the live 114 + 7.
- [x] ER diagrams: not eralchemy2. sam-queries' `scripts/er_diagram.py` (#680) emits Graphviz
  from the ORM metadata, with no DB and no dependency. The deck freezes its output into
  fragments (§3).
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
  for the long haul, and maybe merge to `main` much later. Rebase `samuel` onto `main` whenever
  framework fixes land (`--force-with-lease`; the branch holds only `docs/samuel/` commits). (`sam_and_pbs` and `new_user_samples` landed on `main`; SAMuel
  deliberately does not.)
- [x] **Does the docs gate follow the local symlink?** No. `test_docs.py` builds its corpus from
  `git ls-files` and skips symlinks, so an ignored symlink is never read.
- [x] How much of `sam_and_pbs` (§12) to include by reference vs. summarize? Answered by Part 2 (2026-09-30): its concepts are retold lighter on math, with one PBS-tree slide included by reference.

## 8. Phases (one session each; tick as they land)

- [x] **Phase 0 — Scaffold.** Changes by repo:
  - [x] Framework `main`: branch `make-common-multi-deck` (commit `dae87f0`), with the
    `Make.common` `DECKS` generalization, README, CLAUDE.md and `docs/.gitignore`. `sample` (all
    three formats) and `sam_and_pbs` build unchanged. Merged as framework PR #10.
  - [x] Framework `samuel` branch, cut from that branch (commit `95f495c`): `docs/samuel/` with
    the wrappers, `_variables.yml`, and one divider + placeholder slide per part.
    `make -C docs/samuel all` builds all 15 outputs. `main` (with #10) merged in, `e5f5338`.
  - [x] This repo: `docs/presentations/` reduced to a README pointer plus a `.gitignore` for
    the local `samuel` symlink; `docs/INDEX.md` entry updated.
  - [x] Deleted the stale `presentation` branch, local + origin (was `8623665b`).
- [ ] **Phase 1 — Part 1 Overview:** content, the architecture diagram and the screenshot tour.
  Then revisit the format decision.
  - [x] First draft on framework `samuel` (commit `d7f2b77`, local): 18 slides, the
    big-picture mermaid, a six-shot tour, and the Progression chart. Checked in pptx
    (LibreOffice), beamer and revealjs.
  - [ ] Ben reviews the tone, especially The Good / The Bad / The Murky, which are drafted
    opinions. Then push `samuel`: it is the first push that carries screenshots to the public
    repo.
  - [ ] Revisit the format decision (§13). The revealjs output is branded since framework #13.
- [ ] **Phase 2 — Part 2 Concepts** (added 2026-09-30, §4):
  - [x] Framework `samuel` rebased onto `main` (#17/#18, theme 2.3) with a plain rebase; parts
    renumbered (Databases → 3, Pieces → 4).
  - [x] First draft (framework commit `a1e9e0f`, local): 40 slides, the frozen fragments from
    `concepts_data.py`; the tree slides moved up from Databases. Checked in pptx (layouts plus
    LibreOffice), beamer and revealjs.
  - [ ] Ben reviews (framing, facts, the 2a/2b split), then push `samuel` and refresh framework
    #16's body.
- [ ] **Phase 3 — Part 3 Databases:** resolve the §5 facts first.
  - Tooling: `er_diagram.py` (#680), and Graphviz for the databases map (Quarto bundles it; conventions in framework #12).
  - [x] Settle the table count with a stated method (§5): `count_tables.py`, 2026-09-30.
  - [x] First draft on framework `samuel` (commits `47b2f7a`, `69cee17`, local): 16 slides, the
    Graphviz map, two generated ER fragments, the CNPG PSA and a `/database` shot. Checked in
    pptx (LibreOffice), beamer and revealjs.
  - [x] Screenshots link to the live page (`b653b71`): a linked image in every format plus a
    footer line in HTML/PDF, via the deck filter `screenshots.lua`.
  - [x] Pushed as `samuel` (`6038638`) with the living draft PR **framework #16**; review happens there.
  - [ ] Companion page (the interactive database map, §13): decide now the slides exist.
- [ ] **Phase 4 — Part 4 Who Talks to Whom** (systems and data flow, §4):
  - [x] Framework `samuel` rebased onto `main` after #19 squash-merged (`622d413`); #681
    rebased onto `staging`.
  - [x] Pieces split into Parts 4 and 5 (framework `f4622aa`).
  - [x] First draft (framework `de99dce`, pushed): 31 slides, grey legacy boxes, the schema
    slides and PSA, pinned `neato` diagrams, the frozen `etc/schedule`. Checked in the
    ncar-beamer PDF (page by page), pptx (layout scan plus LibreOffice) and revealjs (spot
    checks at 1280×720).
  - [ ] Ben reviews: the title, the grey-box convention, and the repoint question in §4.
- [ ] **Phase 5 — Part 5 Deployment & Operations** (hosting, GitOps, deployment, the companion
  CNPG cluster, §4):
  - [x] Ben answered the handoff's four questions (§4, Ben's calls).
  - [x] First draft (framework `f49e8ac`, local): 26 content slides plus 5 dividers, four
    Graphviz diagrams, and new `_variables.yml` facts. Checked in the ncar-beamer PDF (page by
    page), pptx (layout scan plus LibreOffice), and all 21 outputs build.
  - [x] Companion page published and made public by Ben: the pipeline walkthrough (§13),
    linked from its own slide (`679b129`).
  - [ ] Ben reviews, including whether Part 4's `ncar-hpc-deploy` box and notes should be
    softened to match.
- [ ] **Phase 6 — Appendix + full-deck polish:** a consistent diagram style, a fact-refresh pass on
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
- **2026-09-29 (Phase 1 first draft):**
  - Voice calibrated on the Vibe Coder deck: dated narrative milestones, a `†` footnote on
    the metric, and a Good / Bad / Murky close.
  - Facts gathered with sources, correcting §4 (14 API modules; XRAS route count) and §5 (the
    name; the LDAP mirror; the legacy-fact source).
  - The Progression chart:
    - `docs/samuel/refresh_data.sh` counts every text line on `origin/main`, month by month,
      reproducing the March talk's milestones exactly;
    - today's figure is 444K lines, stacked as source 186K + tests 153K + docs and other
      105K. Source alone is under legacy's ~250K; the footnote calls that comparison "a
      landmark, not a race".
  - Screenshot revision (Ben): the usage chart is WYOM0247 on Derecho (dense, 14–16 users).
    CESM0002 has too little charge data in the obfuscated sample. The allocations shot is the
    Derecho facility pie. Projcodes are not obfuscated, only titles and users.
  - Screenshots came from the obfuscated test DB (§6).
  - Format lessons, recorded in the qmd:
    - a lead paragraph before a table demotes the pptx slide to Content with Caption, so put
      the lead in the title;
    - in beamer, full-width 16:9 screenshots need `{height="72%" fig-align="center"}`;
    - an `LR` diagram with 5 outputs came out 1.4:1 and clipped its labels; `TB` with short
      labels is 2.66:1 and clean;
    - `{.smaller}` on the dense table slides keeps revealjs from overflowing.
  - Framework PR #10 opened; `samuel` pushed; the `presentation` branch deleted.
  - #10 merged; `main` merged into `samuel`.
  - **Next:** Phase 1 (Part 1 Overview) starts with the title slide
    as the tone test (§11).
- **2026-09-30 (Phase 2 start):**
  - Framework `samuel` rebased onto `main` (#12 Graphviz, #13 branded HTML): the four deck
    commits cherry-picked onto `origin/main` (a plain `rebase --onto` replays `main`'s own
    commits through the old merges), `docs/samuel/` byte-identical, force-pushed as `5e4ba5b`.
  - Deleted the squash-merged framework branches (`make-common-images-and-gotchas`,
    `graphviz-diagrams`, `ncar-revealjs`); merging each into `main` was a no-op tree.
  - This repo: Part 2 tracked on `samuel-presentation-part2` (from `staging`).
  - Part 1 in the branded HTML: the title, dividers and tables are right; every screenshot slide
    overflowed (a `%` height resolves against the whole slide), now fixed deck-side.
  - Table count settled (§5); ER fragments and the databases map in Graphviz; Part 2 drafted
    (§8). Facts re-read from their sources: charges route by `activity_type` over **five**
    summary tables, not the four CLAUDE.md describes.
  - Screenshots link to sam.hpc.ucar.edu (Ben's ask): pandoc turns `[![](x.png)](url)` into a
    clickable pptx picture (`a:hlinkClick`) with no slide split.
  - A first `make all` hung 29 min in headless Chrome on `samuel.pptx`; the rerun took 36 s.
  - Retrospective (Ben's call): the deck's workarounds moved upstream as theme #4 and
    framework #14/#15 (§14), with mermaid and Graphviz as first-class theme samples. Ben's pptx
    link cue is `link_captions.py`. `samuel-next` proves the deck on top of them.

- **2026-09-30 (Part 2 Concepts):**
  - Ben's call: insert a new Part 2 "Concepts" (the domain model, before any table); Databases
    becomes Part 3, Pieces Part 4; the phases renumber to match (§8). Plan reviewed in-session:
    plain naming, no banking analogy, Ben's own framing of accounts, charge adjustments as
    footnotes, a slide on the two kinds of groups, and job-history history (SAM used to keep
    every job; now one database per machine and roll-ups in SAM).
  - Framework `samuel` rebased onto `main` (#17/#18, theme 2.3) with a plain `git rebase`: no
    merges left, so the cherry-pick workaround is no longer needed. `samuel-next` deleted.
  - Part 2 drafted (framework `a1e9e0f`, local, not pushed): 40 slides, `concepts_data.py`
    fragments from the test DB. The test DB has the right shapes: SCSG0001's Derecho ledger
    (NEW 100M, then "100M was crazy.", replaying to 25M), CESM0002 as a subdivided award (15
    children carve 442M of 465M) and NMMM0003 as a pool with a detached NMMM0083.
  - QA traps hit: Poppins has no `→`/`↔` (PDF boxes; write words); a Graphviz image in a column
    is forced to the column, so keep its aspect near 1.2 (a snake layout for long chains); bullets
    before a table or image demote the pptx slide to Content with Caption, so tables stand alone
    (lead in the title, or a column); pandoc pipe tables wider than 72 characters take their
    column widths from the dashes. LibreOffice shows code-block lines as bullets; the pptx has
    `buNone`, so that is a render artifact.
  - Framework builds need the conda env *activated* (`conda-env/etc/conda/activate.d/*.sh` sets
    `QUARTO_DENO` etc.); a bare PATH fails with a missing `deno`.

- **2026-09-30 (Part 4 Who Talks to Whom):**
  - **Rebases:** framework #19 squash-merged, so `samuel` went `rebase --onto origin/main e87b1e2`
    (only the two theme Lua files changed) and was pushed as `622d413`. #681 was rebased onto
    `staging`.
  - **Ben's call:** split Pieces by axis. Part 4 is systems and data flow; Part 5 is hosting,
    GitOps and deployment, plus a couple of CNPG slides. Legacy SAM is drawn as grey boxes,
    because `sam-tomcat` and `sam-app` still run. The key message is that we don't yet own the SQL
    schema.
  - **Facts re-verified** against staging `886c2f09` with five read-only agents (§4 holds the
    corrections). The schema count is new: 92 of the 108 modeled tables come from legacy
    migrations, and 16 are SAMuel's own.
  - **Draft:** 31 slides (framework `de99dce`).
  - **QA traps:**
    - A 20:1 Graphviz chain is a sliver in pptx and stretched in the beamer PDF. Reshape it,
      with pinned `layout=neato` positions for snakes and rings (Quarto's WASM Graphviz
      renders them; clusters are lost). Ben: a diagram's aspect may flex to fill the page.
    - A `†` footnote after a two-column block or a table splits the pptx slide. Put it inside
      the last column, or in the notes.
    - zsh reads `"$ref:c…"` as a history modifier; write `${ref}`. Bash scripts are unaffected.

- **2026-10-01 (slide layout controls):**
  - Ben asked for a "center + scale by xx" control for short slides, then chose separate
    controls: `.hcenter`, `.vcenter`, `.center`, `scale=` and `.fill`.
  - **Theme #8 (v2.5.0):**
    - both filters wrap the body in a Div before the footnote hoist, and turn it into raw
      markup after;
    - HTML: the autofit script starts at S, grows for `.fill`, and measures `.vcenter`;
    - PDF: a `\fontsize` group, frame option `c`, and `center` + `varwidth`.
  - **Framework #20:** re-vendors the theme. The pptx path is `slide-layout.lua`, which writes
    a notes line (pandoc merges notes Divs), plus `utils/slide_layout.py`.
  - **`samuel` stacks on #20;** two Part 4 slides use it.
  - **Bug found:** Quarto wraps plain code and caps its height with an inner scroll, so
    `.fill` grew the schedule to 3× in folded lines. Fixed: no wrap on `.fill` slides, and an
    inner scroll counts as overflow.

- **2026-10-01 (layout pass, merged):**
  - Theme #8 merged, with Ben's review commit: `.hcenter` leaves a captioned figure alone in
    the PDF. Framework #20 merged with the theme's `main` vendored.
  - `samuel` was rebased onto `main` (13 deck commits).
  - The layout pass covers 22 slides, chosen by measuring each slide's free height in HTML.
    Note that column slides read about 95% free, which is an artifact of the measure.
  - Every PDF page was checked for overflow. All 21 outputs build, slide counts are unchanged,
    and there are no untitled pptx slides.
- **2026-10-01 (Part 5 draft):**
  - Ben's answers to the handoff questions: the title "Deployment & Operations", the full
    length, backups as "planned", and the companion page now. Then, at plan review: no PSA
    slides, a slide on the Claude watch skills, and HPC deployment downplayed (§4).
  - **Draft:** framework `f49e8ac` (local).
    - The six skeleton sections are now five, plus one HPC slide.
    - Four Graphviz diagrams: the pipeline ring, the branch flow, the publish pipeline and the
      namespace.
    - The workflows table is condensed to eight rows covering the 14 files.
  - **Companion page:**
    - built as a claude.ai artifact;
    - each stop now links its workflow YAML and docs on `main`;
    - Ben made it public;
    - linked from a new slide (a linked screenshot, all three formats).
  - **QA:**
    - every beamer page read;
    - pptx: 32 slides, all titled;
    - LibreOffice contact sheet checked;
    - `make -C docs/samuel all` green;
    - sources and the page grepped for IPs, secret paths, lane mechanics and the skip-ci
      tokens.
    - HTML not yet spot-checked in a browser.
- **2026-10-01 (visual format pass, Parts 1–5; framework `b6af2d2`):**
  - **Ben's call:** short slides are centered with one fixed bump, `{.vcenter scale="1.15"}`.
    Every short slide gets the same size, so text stays steady across them; dense slides keep
    the deck's size. "Short" was measured, not eyeballed: bullet-only slides used at most 54%
    of the HTML body, and text-only column slides used 31–64%.
  - **Applied to 61 slides.** "SAM in one slide" and "`is_active`" keep `{.vcenter}` alone,
    because at 1.15 the PDF overran the page number and wrapped the code.
  - **`.smaller` without `.fill` shrank HTML text to about 60%.** Resources, two kinds of groups,
    the fine print and fourteen ways now use the short-slide recipe instead.
  - **Overflows fixed:**
    - the Resources table and the four-API-families table ran off the beamer page. They now
      have explicit pipe-table dash widths, whose total must pass 72 columns to take effect;
    - the tree-audit output got `scale="0.92"`;
    - the replay table's dates broke at their hyphens. It got dash widths and a nowrap span
      (`concepts_data.py`), which HTML honors and beamer and pptx ignore.
  - **Diagrams reshaped to fill the slide:**
    - "Follow one job's hours", from a 7-wide mermaid row to a pinned neato snake;
    - "The verbs", to a two-rank dot tree;
    - "Now: jobs live with their machine", to a pinned two-column snake.
  - **Monospace for tool, package and database names** on slides, in the companion page and in
    this doc: `sam-ldap-syncd`, `hpc-usage-queries`, `hpc-scheduling-tools`, `fs-scans`,
    `csg-postgres`, `gunicorn`, `amie-sam-mediator`, `jobhist-sync`, `sam` / `system_status` /
    `job_history` / `fs_scans`. Repo names used as the project's name (sam-queries) and
    environment names (samuel-dev) stay plain. Diagram labels stay in one sans face.
  - **Companion page:** inline code no longer breaks at hyphens. It has no overflow at 360 px.
    The screenshot was refreshed, and the page was republished to the same URL (version 3).
  - **QA:** every HTML slide screenshotted (1600×900) and every PDF page rasterized, before and
    after. pptx and PDF slide counts match for every part (19/40/16/31/33, 138 combined), and
    the LibreOffice sheet shows no split slides.
- **2026-10-01 (deck QA tooling):** framework #21 adds `make qa` (`deck_qa.py`) and a `deck-polish`
  skill, so the next format pass starts from a report.
  - Run against the deck before the format pass, it flags every defect that pass found by hand.
  - It found two the pass missed, both fixed on `samuel` (`815b3b2`): Part 3's PSA slide never
    got the short-slide recipe (its title is the same as Part 2's), and the borrowed-databases
    table ran past the PDF margin at scale 1.2.
  - `docs/samuel/qa-names.txt` drives the name scan.
  - The skill's deferred list holds the follow-ups: an ignore list, the sample deck's three
    overflows, CI, and pptx overflow.
- **2026-10-01 (Ben's review, batch 1: Part 1):** Ben edited Part 1 directly. Claude swept the
  rest of the deck for the same treatment (§11 records the tone call).
  - **Part 1:** bold key terms; plain titles ("Deep roots", "Mobile and dark mode"); users and
    support staff rather than PIs and CISL staff; more on organizational and grant metadata,
    Events, and why plugins exist.
  - **Milestones** are added to `_variables.yml` and "How we got here":
    - charge ingest, May 2026 (Ben);
    - web app read/write, June 2026 (#320, 2026-06-18);
    - FY27 renewals, September 2026.
  - **Parts 2–5:** row labels bolded in eight tables. No bolding of bullet lists outside Part 1.
  - **Part 2:**
    - a new slide, "Who, where, and who pays": organizations, institutions, contracts and area of
      interest, with the external-contacts plan as a footnote (Ben);
    - an Events bullet on Users.
  - **QA:** `make qa` passes on all parts. Plugins, "How we got here" and the new slide dropped
    to `{.vcenter}` to fit the PDF.
  - **Follow-ups the same day:**
    - the shared-pool example moved to Casper (NMMM0080, 75K of a 750K pool). Derecho's NMMM0083
      held a copy of the pool's amount, and Ben relinked it in prod;
    - closing slides bold their key phrases;
    - the PSA titles became warning triangles;
    - Part 3 gained "Every date is a timestamp" (the reason for DATETIME is undocumented; the
      slide labels its guesses) and footnotes on CI covering both engines and on reading from
      the replica.
  - **Trap:** a footnote after a bare table splits the pptx slide. Put the table and footnote
    in one 100% column.
  - **Trap:** `concepts_data.py` reads "now". Run after 2026-10-01, it draws the FY27 windows
    at 0% used. `_out_accounts.qmd` and `_out_audit.qmd` stay at their FY26 versions until it
    takes an as-of date.

- **2026-10-01, Appendix A drafted.** The placeholder became 12 slides (see §4). Facts from
  read-only surveys of `hpc-usage-queries`, `hpc-scheduling-tools`, `~/codes/sam` and its zoo,
  plus `gh repo view` for visibility. `_variables.yml` gained `peers.huq_lines` and
  `peers.huq_tests`. `make qa DECKS="A-peers samuel"` passes.
  - **Trap:** a wide `rankdir=LR` chain in a 100% column renders squashed in the PDF. A vertical
    chain in a 40% column, beside the bullets, renders cleanly in all three formats.

- **2026-10-02, Part 3 sizes.** `dataset_activity` (6.6 GB) retires with the per-job tables
  (Ben), so "Four databases, two engines" footnotes 58 GB of `sam`'s 60 GB as legacy-only.
  SAMuel models the table but never writes it.

- **2026-10-02, Appendix B drafted.** The placeholder became 12 slides (see §4), from two
  read-only surveys of the API. `make qa DECKS="B-apis samuel"` passes.
  - **Trap:** pptx moves anything after a `.columns` block to a new slide. Put the bullets
    inside the columns.

- **2026-10-02, Appendix B endpoint tables.** Two summary tables follow the two dialects'
  introductions: "The legacy-shaped endpoints" and "The new endpoints" (Ben).
  - **Trap:** a bare `<code>` in a speaker note is a real HTML tag in revealjs and swallows the
    slides after it. Backtick every `<placeholder>` in notes; Parts 3 and 5 had three.

- **2026-10-02, framework fixes** (the retrospective's three; framework branch
  `framework-fixes` `2ac87c8`, theme branch `glyph-fallback` `b5393d7`, both local).
  - `docs/common/single-body.lua` wraps a pptx slide's body in one 100% column when content
    follows a table or diagram. Code blocks never split. The deck's 7 hand wrappers came out,
    and the pptx is byte-identical.
  - Theme 2.6.0 borrows ⚠ and arrows from DejaVu Sans. The 4 triangle titles are now plain
    `## ⚠︎ Title`.
  - `deck_qa` fails on a bare `<word>` in the sources, with `file:line`.
  - `samuel` merged `framework-fixes` (`d7dba41`), then the cleanup (`b8b69fa`). `make qa`
    passes on all 9 decks.
  - Still a rule: content after a `.columns` block splits pptx; put it inside the columns.

## 11. Voice, tone and the fun

**SAMuel = SAM, updated for extended lifecycle.** That backronym is the deck's premise and its
running gag: legacy SAM served ~15 years, and SAMuel is its extended lifecycle.

**Ben's call, 2026-10-01 (his Part 1 edit):** say it plainly. SAMuel is "a modernization and
refactoring effort to support another generation", not a wink at an obituary. Part 1 drops the
cute titles and asides ("A long and honorable career" became "Deep roots"). The audience is
users and support staff, not just PIs and CISL staff. Part 1 also bolds the key term of each
bullet and the first column of each table. Parts 2–5 bold table row labels only.

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
- Part 1 is stakeholder-facing: plain, with no winks; the fun lives in Parts 2–5.
- Parts 2–4 can be as nerdy as the material.
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
- **Part titles with personality:** Part 1 "The Lay of the Land"; Part 2 "Concepts" (plain, by
  Ben's call); Part 3 "Where State Lives" (or "Four Databases Walk Into a Bar"); Part 4 "Who
  Talks to Whom"; Part 5 "Deployment & Operations" (plain, by Ben's call); the Appendix "The
  Neighbors".
- **Recurring "⚠ Don't let this happen to you…" slides** for the war stories: the CNPG roll
  (Part 3) and the schema drop (Part 4). Part 5 has none, by Ben's call.
  - "PSA - " became a warning triangle on 2026-10-01 (Ben).
  - Write the title as `## ⚠︎ Title`. Theme 2.6 draws the ⚠ (and → ← ↔ ⇒) from DejaVu Sans
    in the PDF.
- **Live CLI output:** real `sam-search` / `sam-admin` / `jobhist` output on slides.
  - Use the §3 frozen-data pattern: a `refresh_data.sh` writes `data/*.txt` from the obfuscated
    local DB, or from Ben's own records (e.g. `sam-search user benkirk`,
    `sam-search project SCSG0001`).
  - Show it through `{bash}` `cat` cells.
  - Never execute against a DB at render time.
- **Hand-drawn diagrams:** mermaid's `look: handDrawn` for the Part 1 big-picture diagram, if it
  survives the pptx PNG render. Keep the crisp look for Parts 2–4.

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
| "SAM Projects & Allocations" (Facility → Allocation type → Project → per-resource Allocation + Users; CPU/GPU resource pairs) and its mermaid | Part 1's plain-language domain slide; Part 2's facility → panel → allocation type slide |
| "SAM Project Trees": two conventions stored identically (**shared pool** `NMMM0003` vs **subdivided award** `CESM0002`); a root's amount already is its subtree total; the API never deduplicates | Part 2, block E: the two-conventions table, the SAM-side award and pool trees, and the CESM tree included by reference |
| "The SAM API — SAMuel": basic auth via env, server-side cache, the `fstree_access` / `queue` / `wallclock_exemption` / cache-refresh endpoints | Part 1 surfaces (the API as consumers see it); Part 4 |
| "The Big Picture": SAM API → `samuel2sql.py` / `fsparsetree_mr.py` → `ncar_accounting.db` + `resource_group` → hooks / scheduler → PBS | Part 1's big-picture diagram (SAMuel's outbound edge); the `hpc-scheduling-tools` row in Appendix A |
| "Tooling — SAM to PBS Pipeline" (5 slides), below | Part 4, data gathering, as the **outbound** half |
| "When Something Looks Wrong" symptom→meaning table | A format to copy for Part 4's ops slides |

Details of the SAM to PBS pipeline:
- HSG's cron processes download from SAMuel's API for the accounting hooks and `fairshare`; they do not run our Python. `fsparsetree_mr` is embedded in the API (`/api/v1/fairshare/<machine>`), so nobody runs it on a scheduler host. The 12-minute cron on
  `cron.hpc.ucar.edu` is Ben's internal playground, not production; keep it off the slides
  (Ben, 2026-10-01);
- content-gated publishing, with asymmetric digests (bytes vs `.dump`);
- a churn guard;
- `.prev` rollback;
- CSG builds and HSG deploys (`make status/deploy/rollback-*`); a DB swap is free, a tree swap
  costs a scheduler pause.

**Framing it enables.** Part 4's data gathering becomes **data in / data out**:
- *in* is PBS → collectors and `jobhist-sync` → SAMuel (`ncar-hpc-deploy`);
- *out* is SAMuel → `hpc-scheduling-tools` → PBS (the SAM to PBS pipeline).

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
| **revealjs HTML** (framework #13: `--to ncar-revealjs`, NCAR web theme) | `make html` | Live mermaid/SVG; autofit that shrinks an overflowing slide; `{.scrollable}`; code-line highlighting and fragments; speaker view; `{.smaller}`; iframes; OJS interactivity | Mermaid/Graphviz draw live, so they can differ from the baked pptx/PDF PNGs; check both | Parts 2–4 when presenting live |
| **Companion interactive pages** (claude.ai Artifacts, HTML/React) | published separately and linked from a slide | Anything too rich for a slide, e.g. a schema or ER explorer, the CI → GHCR → Argo pipeline walk-through, the `ncar-hpc-deploy` cadence timeline, and an allocation-tree explorer on the `tree2mermaid` data | Outside the deck build; private by default; links go stale | Deep-dive details, as needed |

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

**Centering and scaling a short slide** (2026-10-01; theme #8 v2.5.0 and framework #20, both merged).
- **Five independent controls on a `##` heading:** `.hcenter`, `.vcenter`, `.center` (both;
  the theme's, not Quarto's), `scale="S"` and `.fill`.
- **Formats:** they work in HTML and PDF. pptx gets them through `slide_layout.py`, which
  can't do `hcenter` or `fill`.
- **Recipe for a short slide:** `{.center .fill scale="S"}`, with S sized so the *PDF* fits.
  HTML fills to the floor, and PDF/pptx use S. In Part 4, 1.4 and 1.5 overflowed the beamer
  page; 1.15 and 1.25 fit.
- **Applied to Parts 1–4** (framework `ca66e38`), on 22 slides:
  - standalone tables use `{.center .fill scale="S"}`;
  - bullet plus CLI output uses `{.vcenter .fill}`;
  - short diagrams use `{.vcenter}`.

  Two tables keep scale 1 because their PDF overflowed even at 1.1: "five audiences" and
  "same bytes".
- **Short bullet and column slides:** `{.vcenter scale="1.15"}`, one size for all of them
  (Ben, 2026-10-01). A slide too dense for it at 1.15 in the PDF keeps `{.vcenter}` alone.
- **A table too wide for the beamer page:** give the pipe table explicit dash widths whose total
  passes 72 columns. Pandoc then wraps the cells to those proportions.

**Candidate companion pages** (decide per phase; each needs a slide that earns the link):
- Part 2: an allocation-tree explorer.
- Part 3: an interactive map of SAMuel's databases and engines (from `engine_inventory`).
- Part 4: the systems map, clickable per box (who writes what, legacy vs SAMuel).
- Part 5: **built 2026-10-01**, the CI → image → Argo → pods pipeline, clickable per stage with
  a prod/dev toggle.
  - **Where:** public claude.ai artifact `Tp4XaBDH7BmvUbPT6UUbtS`.
  - **Linked from:** the slide "Walk the pipeline yourself", as a linked screenshot.
  - **Each stop** links its workflow YAML and docs on sam-queries `main`.
  - **Source:** vendored in framework `docs/samuel/companion/` (`7683b21`). The README there
    covers the build, the republish step and the screenshot command. That copy is canonical.
  - **Dropped:** the lanes timeline, because HPC deployment stays downplayed.
- Appendix: a peer-repo dependency graph.

## 14. Found along the way: fix later, on another branch

A running list. Deck work surfaces these, but none belongs on this branch. Each gets its own
branch and PR against `staging`. Tick an item once its fix merges, and note the PR.

- [ ] **The new API dialect isn't uniform yet** (found 2026-10-02, Appendix B).
  - There is no shared pagination helper; only projects and users page.
  - `users.py` `list_users`: `total` is the count on the current page, and `?search=` skips
    pagination.
  - `projects.py` `list_projects`: the `?facility=` path skips pagination, and `?active=` uses
    a raw `Project.active ==` comparison (CLAUDE.md §5).
  - There is no OpenAPI.
  - `HTMX_API_READINESS.md:180` still says keys skip the permission check, which is false
    under `RBAC_SOURCE=db`.
- [ ] **Mobile gauge collapse.** On Resource Usage Details at phone width, the Rolling
  Consumption Rate gauge collapses to a sliver, and its axis labels overlap ("200%0%").
  Template: `src/webapp/templates/dashboards/user/fragments/rolling_rate_htmx.html`. Found
  2026-09-29 on the obfuscated DB, 390×844, dark.
- [ ] **Mobile table headers wrap mid-word.** On the same page at phone width, the daily
  history table breaks headers and values across lines ("USE RS", "JO BS", "6,80 6"). It needs
  `white-space: nowrap` plus horizontal scroll, or fewer columns on mobile.
- [ ] **Deprecate `sam-status`.** The third CLI (`pyproject.toml`: `sam-status =
  "system_status:main"`, `src/system_status/cli.py`) is slated for removal (Ben, 2026-09-29).
  Once it goes, Part 1's "3 command-line tools" becomes 2: update `clis.count` in
  `_variables.yml`.
- [ ] **The name in `CLAUDE.md`.** It says "System for Allocation Management"; the app header and
  Ben's decks say "Systems Accounting Manager".
- [ ] **`helm/README.md` is stale.** It calls the task dispatcher "hourly" (it runs every 15
  minutes) and lists "7 ExternalSecrets" (prod has 9).
- [ ] **`docs/apis/HPC_DATA_COLLECTORS_GUIDE.md` is stale.** Its base URL is `sam.ucar.edu`, and
  it shows Slurm commands.
- [ ] **`docs/STAGING.md`** describes the retired AWS ECS/RDS staging; mark it retired or
  fold it into history.
- [x] **Framework: `Make.common` doesn't track `images/*`.** #10 made `_*.qmd` and `data/*`
  prerequisites, but a replaced image still skipped the render until a `touch`. Fixed in
  **framework PR #11** (merged 2026-09-30), which also adds this session's gotchas to the framework's CLAUDE.md.
- [x] **Framework: beamer divider subtitles.** Beamer rendered the paragraph after a `#`
  divider as its own slide. Fixed in two PRs:
  - **benkirk/NCAR_beamer_template#2** adds `\sectionsubtitle`, and its Lua filter declares
    each subtitle in the preamble by section number (a raw block before the heading opened an
    empty frame);
  - **framework #11** re-vendors the theme and demos it in `sample`.
  Both merged 2026-09-30. `main` is merged into `samuel` and the fences are gone; a re-vendor
  from the theme's `main` is a no-op.
- [x] **Framework: an NCAR revealjs theme** (§13): brand colors, Poppins and the logo lockup.
  Landed as **framework #13** (`make html` renders `--to ncar-revealjs`); `samuel` rebased onto
  it 2026-09-30.
- [x] **Framework: the `sample` mermaid slide split into an orphan** (its explanation followed
  the diagram), and the Graphviz conventions. That commit landed after #11 had merged, so it
  moved to **framework #12**, merged 2026-09-30; `samuel` rebased onto it.
- [x] **Framework retrospective, 2026-09-30** (the deck's workarounds moved upstream; all three merged
  2026-09-30):
  - **Theme, benkirk/NCAR_beamer_template#4 (2.2.0):** `{height="72%"}` fits HTML; Graphviz
    fills the column; mermaid labels no longer clip. Root cause of the last one: quarto renders
    each diagram inside its slide, which reveal has scaled to the window, so mermaid measured
    labels at 0.8 (at 1280 px). Not fonts. Mermaid and Graphviz are now first-class samples in
    both the LaTeX and Quarto examples. Review added: a `%` height scales with the percentage
    (72% is the full cap), keeping the image's own style.
  - **Framework #14:** vendors #4's branch (re-vendor from the theme's `main` before merging);
    `notes-last.lua`. A probe deck showed the only notes trap is notes *before* columns;
    notes after images and tables never split, and tables or code inside a column are fine
    (the earlier entry here was wrong). Deck `*.lua` files are make prerequisites. CLAUDE.md
    lessons: Chrome Headless Shell (the 29-minute hang), the QA walk, Poppins arrows.
  - **Framework #15 (stacked on #14):** linked images. `[![](x.png)](url)` is the whole
    syntax: an HTML footer and PDF foot line from `linked-images.lua`, a pptx caption box
    (the link) from `link_captions.py`. Ben's pptx cue took about 50 lines of python-pptx.
  - **SAMuel:** local branch `samuel-next` (framework `main` + the eight deck commits, the last
    dropping `screenshots.lua` and the `shot-link` divs), rebased after the merges. It builds
    19/18/41 slides with 6 pptx captions, no HTML overflow, and exact mermaid labels. Pushed as
    `samuel`; framework #16 is the living draft PR.
- [ ] **CLAUDE.md: the charge-summary description is stale.** It names four tables and routes
  HPC/DAV to comp + dav; `calculator.py` routes by `activity_type` over five (hpc included).
- [ ] **Access-branch nuances to verify** (found researching Part 2): `directory_access`'s SQL
  has no `al.deleted` filter while `project_access` filters it, and both use `end_date > cutoff`,
  so an open-ended (NULL `end_date`) allocation never qualifies. Check against legacy before
  calling either a bug.
- [ ] **`src/webapp/disk_scans/session.py:71` docstring** says Destor maps to `desc1`; config
  and helm say `destor`.
- [ ] **`scripts/cron/accounting/` and `collectors/cron_scripts/`** still exist, unmarked; the
  `ncar-hpc-deploy` lanes replaced them (prod's install removes the crontab entries).
- [ ] **`docs/plans/implemented/CHARGING_INGEST.md`** still says job data arrives "via a SQLite
  database".
- [ ] **`docs/CIRRUS_PUBLISHING.md:28`** says the workflow "has four jobs"; its table lists five.
- [ ] **`docs/xras/incoming/XRAS_CUTOVER_RUNBOOK.md:176`** still says XRAS posts to legacy's URL
  (repointed 2026-08-24).
- [ ] **helm comments** call `account_requests_reconcile` "hourly"; it runs every 15 minutes.
- [ ] **`docs/plans/implemented/POSTGRES_MIGRATION.md`** still marks Stage 5 (samuel-dev on
  `sam_dev`) "IN PROGRESS"; it shipped.
- [ ] **`docs/plans/implemented/CNPG_ROLL_RESILIENCE.md`** says #115's 65Gi is kept; #118 set
  64Gi back.
- [ ] **`JUPYTERHUB_API_URL`** is set in helm, but nothing in `src/` reads it.
- [ ] **`_variables.yml` `api.legacy_compat` is "5"**: five frozen blueprints, plus
  `disk_quota` as a sixth legacy-shaped one. Part 1's wording is right; Part 4 says six shapes.
