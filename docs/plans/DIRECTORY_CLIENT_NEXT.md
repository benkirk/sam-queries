# Directory client, next: groups in the UX, then the CLI and the nightly report

**Status:** in progress on `directory-client` (PR #841), 2026-10-16. Steps 1 to 3 of
`DIRECTORY_CLIENT.md` are on the branch; this is what comes after, in the order Ben wants it:
groups first, the CLI and the nightly report second. Decisions (Ben, 2026-10-16, § 3.3):
managers are flagged as drift (report-only); the project panel lives on the admin project card;
the absent-group count stays in the report and the CLI, not the Configuration block.

- [x] § 3.1 read model: `GroupRecord.managers` (a second search in the same connection),
  `project_group` = `project_groups(session, project_id=...)` (the SQL scoped, the loop shared),
  `GroupSnapshot.managers` (`None` for adhoc groups: not compared), the two manager categories
- [x] § 3.2 panels: one template for both cards, ids per entity, the report-only note per kind
- [x] § 4 CLI and the nightly report: `sam-admin directory report` is the CLI's first verb group
  (`CLI_VERBS.md` plans the rest); the task is named in prod's `SAM_TASKS_DISABLED`, dev runs it

Deviations from the plan below, and why: the panel module moved to `sam/manage/directory_panel.py`
(Flask-free, so the CLI shares it); `project_group` is `project_groups(session, project_id=...)`
with the SQL scoped rather than a lifted loop body; `GroupSnapshot.managers` is `None` for an
adhoc group (not compared); one panel template serves both cards; the report-only note is per
kind (a project group flows SAM to directory, an adhoc group Google to directory to SAM); the
report exits 0 with findings (a report, not an audit); `--limit` narrows SAM's side to the
sample; `sam-admin directory report` nests (Ben, 2026-10-16) instead of `--report`.

## 1. Where things stand

- **Built and on samuel-dev since 2026-10-16 11:54Z** (`sha-fde9df2`): the reader contract and
  the UCAR adapter (`src/sam/integration/directory/`), the report-only check after each ldapsync
  PUT (`sam/queries/directory.py::run_check`, hook in `webapp/api/ldapsync/sync.py`), the
  per-kind apply levers all off (`sam/manage/directory.py`), and the operator's view: the
  Configuration block, the user-card panel, the organization and institution dialogs
  (`sam/manage/directory_panel.py`, `dashboards/admin/directory_routes.py`). Prod reads too
  from the next prod deploy (`helm/values.yaml`, commit `21fec670`); its check is armed and fires
  only once the daemon's `SAM_URL` flips.
- **Review of `fde9df23`:** F1 (the check holds the PUT reply) is recorded as a soak-gated
  follow-up in `DIRECTORY_CLIENT.md` § 10; F2 (deploy during the soak's deciding days) is
  recorded in `implemented/LDAP_SYNC_API.md` § 10.7 on the #777 branch.
- **Watch:** the session timer runs prod, dev and legacy ticks at :13 and :43; the dev tick
  tallies the daemon's PUTs and the `directory check` lines. No PUT has reached dev since the
  roll, so no check line yet.
- **Smoke recipe for a worktree:** devel's `scripts/dev_server_alt.sh <worktree> 5051` with
  `SAM_PYTHON=<worktree>/conda-env/bin/python` (devel's env lacks ldap3); devel `.env` carries
  the `DIRECTORY_*` keys; Quick Login as benkirk.

## 2. The groups finding (census 2026-10-16, anonymous reads, counts only)

Measured from the local prod snapshot (`127.0.0.1:3306`) against the live directory with
`project_groups` from `sam/queries/ldapsync.py` as the sync's own derivation.

| | |
|---|---|
| Directory groups | 12,181: `source` PDB 10,743, AD 1,349, FI 54, GOOGLE 35. Created 2026-07 (10,802) and 2026-08 (1,356); 23 more in September and 12 in October. 59 entries ever modified by Fischer, none by the daemon's bind. |
| Active SAM projects with a gid | 1,506. **Present in the directory 1,360, absent 146**, and every absent one was created on or after **2026-07-10**; the newest present project was created 2026-07-08. Absent by gid and by name. |
| Members (150 present, sampled) | directory == the sync's derivation on 100; on 45 the directory lacks members SAM has; on 10 it has members SAM lacks. |
| SAM's own two derivations | the sync's `project_groups` == `Project.users` on 149 of 150. |
| Managers (the same 150) | people whose `x-ucar-adminFor` names the gid == SAM's lead plus admin on 131; a subset on 10; none on 7; different on 2. |
| Adhoc groups | SAM's 310 all present by gid, 242 by name, all `source PDB`; the 35 GOOGLE-sourced groups are not SAM's. |

What it means:

- **The deviation is explainable, and it is not SAM's.** `project_groups` and `Project.users`
  agree; the gap is between SAM and the directory, and it dates from the July load: the
  daemon's `ProjectGroupUpdater` (SAM to staging to directory, `SAM_LDAP_SYNCD_REFERENCE.md`
  § 3.6) has **created no project group since 2026-07-10** and has left membership behind on
  a third of the sampled groups. `LDAPSYNC_DIRECTORY_AFFILIATIONS.md` § 2 says "project groups
  work"; for creates it does not. Candidate cause: the same cron-child death that kills
  `LifecycleUpdater` (`implemented/LDAP_SYNC_API.md` § 2.4, reference § 3.6 and the `cronDispatcher` note
  at reference L1014); George's 376b1a6/5f216b2 cron rework may bear on it. **Action for Ben:**
  an upstream issue on NCAR/sam-ldap-syncd with the dates above, and a correction to
  AFFILIATIONS § 2.
- **Managers were loaded, never maintained.** The sync sends no lead or admin (schemas
  `ProjectGroupSyncSchema`, `GroupSyncSchema`), so the 131 matches are PeopleDB's July copy of
  SAM's lead and admin. The 10 subsets and 2 differences are changes since July that the
  directory never saw. On a **group** entry `x-ucar-adminFor` points at other *groups* it
  administers (csg 1564 -> 75040, 75465; 16 entries carry it); on a **person** it lists the
  groups they administer. The adapter today reads only the group-side attribute
  (`GroupRecord.manager_gids`).
- **Adhoc groups** (Ben, 2026-10-16): authored in Google Groups, flowing to LDAP, read by SAM
  from there; that Google-to-LDAP sync is broken today. So the adhoc manager would be the
  Google group's owner, surfaced on the person entries' `adminFor` once that path works. SAM's
  adhoc groups are still the PeopleDB set.

## 3. Groups in the UX (build first)

Report-only: SAM to directory is the daemon's job, so a group panel shows and never applies.

### 3.1 Read model

- `UCARDirectory.group(gid)` adds a second search in the same connection:
  `ou=allPeople`, scope LEVEL, `(x-ucar-adminFor=gidNumber=<gid>,ou=allGroups,<base>)`,
  attributes `x-ucar-upid`, `uid`, giving `GroupRecord.managers: tuple[int, ...]` (upids) beside
  the existing `manager_gids` (managing groups). `FakeDirectory` and `make_group_record` follow.
- `snapshot_from_group` (`sam/queries/directory.py`) takes its project members and tags from
  the sync's own rule: `project_groups` accepts `project_id=` (its four statements take an
  optional one-project scope; the loop is unchanged) and `project_group(session, project)` is
  that one row, so the sync list and the snapshot share one derivation. The row also carries
  `managers` (the lead's and admin's upids; the sync schema dumps declared fields only). An
  adhoc group's snapshot has `managers=None` and is not compared on managers until Google
  owners exist.
- `compare_group`: add `manager_missing_in_directory` and `manager_missing_in_sam` to
  `GROUP_CATEGORIES`; keep `missing_in_directory` (the 146 land here). Delete the no-op line
  `record_tags &= set(snap.tags) | record_tags` (`compare.py` ~L404); the condition below it
  already does the work.
- `reconcile_group` stays report-only (every discrepancy withheld), and the
  `DIRECTORY_APPLY_GROUP` lever stays meaningless by design; say so in the panel's lever line.

### 3.2 Panels

- **Project**: a Directory section in the admin project card
  (`dashboards/user/partials/project_card.html::render_project_card`, rendered by
  `blueprint.project_card` with `admin_origin=true`; the lead, admin and GID sit in
  `render_project_info` L25-38). The wrapper passes `directory=directory_configured()`; the
  section renders under `{% if admin_origin and directory %}`. "Check directory" ->
  `GET /htmx/project/<projcode>/directory` (`@login_required`, then the admin card's own gate:
  `VIEW_PROJECTS` plus the facility-scope check the card route does, or
  `@require_project_access` which admits members too; pick the card's gate) ->
  `fragments/project_directory_panel.html`: the group's facts (name, active, source, tags),
  **managers** as usernames resolved from upids with lead/admin marks, the member diff in
  both directions with usernames, and the drift table from `compare_group`. An absent group
  gets one line naming the cause: no project created since 2026-07-10 has a directory group.
  `project_panel(session, project, ...)` in `directory_panel.py` beside the others.
- **Adhoc group**: the same section on the group card
  (`dashboards/user/partials/group_card.html::render_group_card`, wrapper
  `admin/fragments/group_card_wrapper.html`, route `blueprint.group_card`, `VIEW_GROUPS`):
  `GET /htmx/group/<group_name>/directory`. Managers shown from the directory only.
- Both panels are inline (no modal shell), like the user card. Gates: route-map regen,
  CSP lint, action cells, the modal ratchet does not apply. Tests in
  `tests/unit/webapp/test_directory_panels.py`: `TestProjectPanel` and `TestGroupPanel` with
  `FakeDirectory` and a `GroupRecord` carrying `managers`; `active_project` fixture; the
  absent-group line.
- `sam-search project <p> --directory` reuses `project_panel` without the template (step 4).

### 3.3 Open questions for Ben (decide before building 3.2)

1. Project managers: display only, or also flag the 10 subset and 2 differ cases as drift? Flagging
   is cheap (the categories above); fixing needs the daemon to send lead and admin, an upstream
   change.
2. The project panel's home: the admin project card (recommended; it already shows lead, admin
   and GID) or the edit-project Members tab (`edit_project.html`, `EDIT_PROJECTS`).
3. Whether to show the 146 absent projects as a Configuration-block count (one cheap LDAP
   search by gid set) or leave it to the nightly report.

## 4. Step 4: the CLI and the nightly report

Anchors: `src/cli/cmds/admin.py` (`user` options L65-84, `tasks` L766-831, `last-seen` L834-846
as the smallest wiring example), `src/cli/user/commands.py` (`UserSearchCommand.execute` L60-84,
`_UNCONFIGURED` L34, `_restore_deactivation` L221-242 for a write with `--dry-run` and the JSON
refusal), `src/cli/user/builders.py::build_user_provisioning` L91 and
`display.py::display_user_provisioning` L113, `src/cli/core/options.py::provisioning_option` L19,
`src/cli/README.md` L174-209 "Adding New Commands", package trios `src/cli/xras/`, `src/cli/tasks/`.

- `sam-search user <u> --directory`: `data['directory'] = build_user_directory(...)` from
  `person_panel` (absent key when unconfigured via the sentinel pattern; `{'available': False}`
  when down); `display_user_directory` renders the facts, the positions and collaborations, and
  the drift as an `issues_table`. The JSON envelope gains one optional key.
- `sam-search project <p> --directory`: the group view from `project_panel` (after § 3).
- `sam-admin user <u> --directory-apply [--dry-run]`: `reconcile_from_directory` inside
  `management_transaction`; JSON refused for a live write.
- `sam-admin directory report [--kind K] [--limit N]` (a verb group): the stage-1 sweep (AFFILIATIONS § 6).
  One `reader.session()`; `ldap3` paged search (`paged_size=1000`, the cookie loop) per subtree;
  bulk SAM projections (one query per table keyed by upid, organization id, institution id, gid;
  never one per record); counts by category with capped id lists; the 146 absent project groups
  as `missing_in_directory` under `group`. New package `src/cli/directory/{commands,builders,display}.py`,
  wired as a top-level `sam-admin directory` group. `--format json` supported.
- Task `directory_report` (`src/scheduling/tasks/directory_report.py`, nightly 03:00 Mountain,
  `needs=('sam',)`, `expected_runtime` ~ 5 min, registered in `tasks/__init__.py`): the same
  sweep; `TaskResult.detail` carries the counts and `message` the headline. **Named in
  `helm/values.yaml` `SAM_TASKS_DISABLED` in the same commit** (the list is fail-open), with a
  values-grep test like `test_task_xras_notices.py::test_it_ships_switched_off`. The CronJob
  forwards keys by name: add `DIRECTORY_PROVIDER`, `DIRECTORY_URL`, `DIRECTORY_BASE_DN`,
  `DIRECTORY_TIMEOUT_SECONDS` to `helm/templates/cronjob-tasks.yaml` (the `XRAS_API_BASE` block
  at L206 is the model) and assert them in `helm/tests/test-cronjob-render.sh`.
- `.claude/rules/directory.md` gains the CLI and task lines; `cli.md`'s command list gains the
  three commands.

## 4.1 The first full report (2026-10-16, local prod snapshot against the live directory, 34 s)

| Kind | Directory | SAM | Drift, largest first |
|---|---|---|---|
| person | 28,605 | 28,571 | twin 974, history_open 601, phone 385, start_day 365, no_directory_start 317, finished_vs_open 173, end_day 162, planned_end 105, active 61, missing_in_sam 59, name 36, employer 35, academic_status 33, email 25, missing_in_directory 25, no_sam_row 21, token_type 19, username 2 |
| organization | 401 | 402 | parent 15, missing_in_directory 1 |
| institution | 1,412 | 1,410 | state 34, name 28, address 20, acronym 12, missing_in_sam 2 |
| group (SAM's active ones compared) | 12,181 | 1,777 | missing_in_sam 6,101 (Google-sourced and groups SAM never had), member_missing_in_directory 1,689 (adhoc 791, projects 898), member_missing_in_sam 309, manager_missing_in_directory 205, **missing_in_directory 146** (the census's number), name 76 (case), tags 27, manager_missing_in_sam 20, active 1 |

The sweep compares SAM's active groups only; an inactive one is known (not `missing_in_sam`)
but not compared: the directory strips a retired project's members while the sync keeps its
lead and admin, so the 4,410 retired projects produced 6,430 manager and 4,681 member rows that
are not drift anyone will act on. The panel still compares any group on request. The person categories are the AFFILIATIONS § 3.4 shapes at full scale (twins, open
history, the Workday winter shift), all withheld.

## 5. Verification

1. `pytest tests/unit/directory tests/unit/queries/test_directory_snapshots.py tests/unit/manage/test_directory_reconcile.py tests/unit/webapp/test_directory_panels.py tests/unit/cli tests/unit/tasks -n 4`, the structural gates (modal shell contract, CSP lint, action cells, route map, docs, Font Awesome names), then the full suite.
2. Worktree server on 5051 (§ 1 recipe): the project card for SCSG0001 and a project created after 2026-07-10 (absent line), an adhoc group card; `sam-search project SCSG0001 --directory`; `sam-admin directory report --limit 200` against the live directory (counts should reproduce § 2 within the day's drift).
3. Dev after deploy: the same panels, `sam-admin tasks --run directory_report --dry-run` from a pod.

## 6. Out of scope here

Applying anything to groups (SAM to directory is the daemon's path; its creates are the
upstream issue in § 2); the Google-to-LDAP adhoc path; the F1 move of the check after the
reply (soak-gated); any `DIRECTORY_APPLY_*` lever in a values file.
