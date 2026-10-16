# Directory client: an optional, read-first integration with the UCAR LDAP directory

**Status: steps 1 to 4 built 2026-10-16; steps 1 to 3 on samuel-dev** (`directory-client`, draft PR #841 against `staging`). Groups and step 4: [`DIRECTORY_CLIENT_NEXT.md`](DIRECTORY_CLIENT_NEXT.md).
Long-lived: each step lands as its own commit series and deploys to samuel-dev from the branch
(`gh workflow run "Publish Images and CIRRUS Deploy" --ref directory-client`). The PR delivers
steps 1 and 2; steps 3 and 4 are scoped here and land on the same PR or as follow-ups.
Implements stage 1 and stage 2 of
[`LDAPSYNC_DIRECTORY_AFFILIATIONS.md`](LDAPSYNC_DIRECTORY_AFFILIATIONS.md) (AFFILIATIONS below).

## Progress

- [x] Step 1a: package `sam.integration.directory` (protocol, records, config, registry, outage latch, UCAR adapter, compare), `ldap3` dependency, test pins and the LDAP socket guard, import-graph and helm gates
- [x] Step 1b: `sam/queries/directory.py` snapshots and `run_check`; the after-commit hook in `webapp/api/ldapsync/sync.py`; API hook tests
- [x] Step 1c (part): dev values (`ucar`, URL, check on); pod egress to `ldap.ucar.edu:389` confirmed 2026-10-15 (anonymous bind from the webapp pod)
- [ ] Step 1c: first `directory check` lines read on dev after the branch deploys
- [x] Step 2a: `sam/manage/directory.py` reconcile per kind behind `DIRECTORY_APPLY_<KIND>`, all off; reconcile tests
- [x] Step 2b (code): the hook applies the enabled kinds in a second transaction
- [ ] Step 2b (dev): levers on one kind at a time after clean step-1 days
- [x] Step 3: Configuration card block; user-card Directory panel (check, apply); organization and institution row actions (`sam/manage/directory_panel.py`, `dashboards/admin/directory_routes.py`)
- [x] Step 4: `sam-search user|project --directory`, `sam-admin user --directory-apply`, `sam-admin directory report` (a verb group), the `directory_report` task named in prod's `SAM_TASKS_DISABLED`
- [x] Docs: `.claude/rules/directory.md` + `CLAUDE.md` row and tripwire; `.env.example`; `SYSTEMS_INTEGRATION_APIs.md` § 8; AFFILIATIONS status line and Appendix D order

## 1. Context

The Perl daemon pushes increments (one PUT per changed person, organization, institution or
group) into `/api/protected/admin/ldapsync/*`. SAMuel applies them faithfully and never reads the
directory itself. AFFILIATIONS establishes that the daemon's record is not the directory's
(planned end dates erased, affiliations never ended), that the directory is an anonymous-readable,
current-state picture keyed by ids SAM already holds, and that SAMuel should read each changed
record itself and reconcile in trust stages.

This PR adds the client as a general capability (`DirectoryReader` protocol, null reader when
unconfigured) with one concrete adapter (`UCARDirectory`), switched on in steps:

1. **Check on sync (report only).** After each incoming PUT commits, read that record from the
   directory and log how it differs from the increment and from SAM. Usernames and row ids in
   the line, as the rest of the ldapsync logger does; personal names, email addresses and phone
   numbers are counted, never printed.
2. **Apply on sync.** Patch SAM from the full record, per record kind, each behind its own lever
   shipped off. Identity fields plus affiliation rows the directory ties to by id. SAM-only
   history, twins and undated-inactive entries are reported, never written (AFFILIATIONS D-A,
   D-B, D-C).
3. **UX.** A Directory block on Admin > Configuration; a per-user drift panel with a
   "Check directory" button and, when a kind's lever is on, "Apply"; the same per organization.
4. **CLI and the nightly report.** `sam-search user|project --directory`, `sam-admin user --directory-apply`,
   `sam-admin directory report` (a verb group), and a `directory_report` task named in prod's `SAM_TASKS_DISABLED`.

Decisions (Ben, 2026-10-15): step 2 covers identity fields and id-linked affiliations; `ldap3`
is a core dependency; the incoming-API check is the first consumer and the CLI the last.

## 2. Patterns to copy

| Need | Copy from |
|---|---|
| Protocol, registry, `''` = off, import-light rule | `src/sam/integration/tickets/base.py`, `registry.py`, `jira.py`; gate `tests/unit/gates/test_notify_import_graph.py` |
| Config read per call (Flask config then env), frozen dataclass, cheap predicates | `src/sam/integration/_config.py`; `src/sam/integration/xras_api/config.py` |
| Hook seam after the PUT commits | `src/webapp/api/ldapsync/sync.py` `_apply` and `put_user`; `on_reactivate` in `src/sam/manage/ldapsync.py` as the tested-callback model |
| Fail-open rule | the blueprint `Exception` handler in `src/webapp/api/protected.py` makes any escape a 500, and the daemon drops a rejected record until its next full reload |
| Test pins and socket guards | `tests/conftest.py` (`pytest_configure` pins, `_no_outbound_http`); `tests/unit/models/test_outbound_guards.py` |
| Fake provider | `tests/factories/tickets.py` `FakeTicketProvider` |
| Card block | `src/webapp/utils/config_inspect.py` `_tickets_block`; `dashboards/admin/fragments/configuration_card.html` "Help-desk tickets" |
| Single-button htmx POST that re-renders a fragment | the access-grid reconcile route in `src/webapp/dashboards/admin/projects_routes.py` |
| Per-user card reload | `_AddExemptionHandler` triggers `reloadUserCard` (`src/webapp/dashboards/admin/blueprint.py`); `static/js/htmx-config.js` |
| Per-user CLI sub-view | `--provisioning`: `src/cli/core/options.py`, `src/cli/user/commands.py`, `builders.py`, `display.py`; the `_UNCONFIGURED` sentinel in `commands.py` |
| Admin write with `--dry-run` | `UserAdminCommand._restore_deactivation` in `src/cli/user/commands.py` |
| Task shape | `src/scheduling/tasks/deactivate_expired.py`; registration list `src/scheduling/tasks/__init__.py` |
| Helm lever gate | `tests/unit/gates/test_helm_jira_levers.py`; the `LDAPSYNC_*` blocks in `helm/values.yaml` and `helm/values-dev.yaml` |
| Dates | `src/sam/dates.py` `SERVER_TZ`, `from_epoch_millis`, `end_of_day` |

## 3. Configuration

All read per call through `sam.integration._config`; declared in `src/webapp/config.py` beside
the `LDAPSYNC_*` block with a one-line constraint comment each; `TestingConfig` pins provider and
URL empty and the check lever False.

| Name | Values | Prod `values.yaml` | Dev `values-dev.yaml` |
|---|---|---|---|
| `DIRECTORY_PROVIDER` | `''` (off) or `ucar` | `ucar` | `ucar` |
| `DIRECTORY_URL` | `''` = off | `ldap://ldap.ucar.edu` | `ldap://ldap.ucar.edu` |
| `DIRECTORY_BASE_DN` | default `dc=ucar,dc=edu` | | |
| `DIRECTORY_TIMEOUT_SECONDS` | connect and receive, default `5` | | |
| `DIRECTORY_CHECK_ON_SYNC` | `0`/`1`: the after-commit hook | `"1"` (armed; nothing PUTs to prod until the daemon's `SAM_URL` flips) | `"1"` |
| `DIRECTORY_APPLY_<KIND>` | `0`/`1` per `KINDS` entry: the trust stage for that kind, whichever path triggers the write | absent | absent until step 2 soaks |

`KINDS = ('person', 'position', 'collaboration', 'organization', 'institution', 'group')`.
The CronJob template (`helm/templates/cronjob-tasks.yaml`) forwards keys by name: the
`DIRECTORY_*` keys are added there in step 4. Gate: `tests/unit/gates/test_helm_directory_levers.py`.

## 4. Step 1: adapter and the report-only check on sync

### 4.1 Package `src/sam/integration/directory/`

- `__init__.py` exports only `base` names (records, `DirectoryReader`, `NullDirectory`, `KINDS`,
  exceptions). `registry.py` and `ucar.py` are imported by path inside functions; `ldap3` is
  imported lazily inside `ucar.py`. The import-graph gate asserts that importing `sam` and
  `sam.queries` loads neither `ldap3`, `directory.ucar` nor `directory.registry`.
- `base.py`: `DirectoryUnavailable` (could not ask), `DirectoryNotConfigured(DirectoryUnavailable)`.
  Frozen records, datetimes naive-Mountain so they compare with ORM columns directly:
  `PersonRecord(upid, username, unix_uid, active, first_name, last_name, middle_name, nickname,
  name_suffix, emails: tuple[EmailRecord], phones: tuple[PhoneRecord], academic_status (raw),
  academic_status_code (mapped or None), token_type, source, positions, collaborations, modified,
  extra)`; `PositionRecord(position_id, organization_id, start, end, active, source, modifier)`;
  `CollaborationRecord(institution_id, start, end, active, admin_org_id, source, modifier)`
  (`modifier` = `modifiersName`, the later hook that tells a SAM-written collaboration end from
  PeopleDB's); `OrganizationRecord`, `InstitutionRecord`, `GroupRecord(gid, name, tags,
  member_upids, active, manager)` per AFFILIATIONS Appendix C.2. Wired-not-shown attributes
  (supervisor, auth type, GitHub user, office) ride on `PersonRecord.extra: Mapping[str, str]`.
  `DirectoryReader(ABC)`: `name`, `from_environment()` (never raises), `configured`, `summary()`,
  `check() -> (bool, str)`, `person(upid)`, `organization(id)`, `institution(id)`, `group(gid)`,
  `search(kind, query, *, limit=20)`, `session()` context manager (connection reuse for sweeps),
  `guard_read()`. `NullDirectory`: `name='none'`, `configured=False`, every read raises
  `DirectoryNotConfigured`.
- `config.py`: frozen `DirectoryConfig.from_environment()` (`provider, url, base_dn, timeout,
  check_on_sync, apply_kinds: frozenset`), `configured = bool(provider and url)`,
  `apply_enabled(kind)`, `summary()`; module predicates `directory_configured()`,
  `directory_check_on_sync()`.
- `registry.py`: `READERS = {'ucar': UCARDirectory}`; `build_reader(name)` (`''`/`none` ->
  `NullDirectory`, unknown -> `DirectoryNotConfigured` listing the names);
  `reader_from_environment()` returns `NullDirectory` whenever the URL is empty.
- `outage.py`: process-local latch on `time.monotonic()`: `mark_down(seconds=60)`, `is_down()`,
  `reset()`. A 1,300-PUT reset burst pays at most one timeout per worker per minute.
- `ucar.py` `UCARDirectory(config)`, `name='ucar'`: subtree constants (`ou=allPeople`,
  `ou=allOrganizations`, `ou=externalOrgs`, `ou=allGroups`), per-class attribute tuples,
  `ACADEMIC_STATUS_LABELS` (Workday label -> SAM code; a 1-2 character value is already a code;
  the one place AFFILIATIONS § 6 names). `_connection()`: `ldap3.Server(url, connect_timeout,
  get_info=NONE)`, anonymous `Connection(read_only=True, receive_timeout, raise_exceptions=True)`,
  closed on exit; `LDAPException`/`OSError` -> `DirectoryUnavailable`. `person(upid)`: one subtree
  search on `ou=allPeople` with `(x-ucar-upid=N)` returns the person and its position and
  collaboration children (all carry the upid), split by `objectClass`. The others filter on
  `x-ucar-organizationId`, `x-ucar-externalOrgId`, `gidNumber`. `check()`: a base-scope read of
  the base DN. Pure parsers over `Mapping[str, list[str]]` (ldap3's `attributes` shape) so tests
  need no ldap3: `parse_person`, `parse_position`, `parse_collaboration`, `parse_organization`,
  `parse_institution`, `parse_group`; helpers `_flag` (`'TRUE'`), `_phone` (`"Label: number"`),
  `_dn_id` (a parent under `ou=externalOrgs` -> None), `_time` -> `sam.dates.from_generalized_time`
  (new: `YYYYMMDDHHMMSS[.f][Z|±hhmm]` -> aware -> `SERVER_TZ` -> naive; None when empty or malformed).
- `compare.py` (pure, no ORM): `PERSON_CATEGORIES = (missing_in_sam, missing_in_directory,
  username, upid, unix_uid, active, academic_status, academic_status_unmapped, token_type, name,
  email, phone)`; `AFFILIATION_CATEGORIES = (planned_end, finished_vs_open, employer, no_sam_row,
  twin, history_open, end_day, start_day, no_directory_start)`; snapshots `PersonSnapshot`,
  `AffiliationSnapshot(row_id, employer_id, start, end, directory_id)`;
  `Discrepancy(kind, category, field, sam_id, directory_id, sam_value, directory_value)`, whose
  values are filled for ids, dates, flags and usernames and left empty for personal names, email
  addresses and phone numbers (counted, never valued; the UX panel and the CLI, where the operator
  holds `VIEW_USERS`, re-read both sides from the record and the ORM);
  `DriftReport(kind, key, side, found_in_directory, discrepancies)` with `counts()`, `ids()`
  (`uo:123`, `ui:45`, `pos:P17…`, `inst:77`) and `fragment()` (`drift=3[employer:1,planned_end:2] ids=…`);
  `compare_person(directory, sam, *, key, side, now)`, `compare_organization`,
  `compare_institution`, `compare_group`.

  Rules (AFFILIATIONS § 3.4, § 6, C.2): positions match by `directory_id == position_id`; an
  unmatched directory position is `no_sam_row`, or `twin` when the person holds another directory
  id for the same organization; SAM rows with no directory id, or an id the directory lacks, that
  are still open are `history_open`; `planned_end` when the directory end is in the future and
  SAM's row has none; `finished_vs_open` on open-ness disagreement (active AND end > now) with one
  `now` per check; `end_day` when both ends are past and differ by Mountain day; `employer` when
  the organization ids differ and both are set. Collaborations key on `institution_id` to the
  latest SAM row (max start); `no_directory_start` when the entry has no start; no `end_day`
  (stored as sent).

### 4.2 ORM side `src/sam/queries/directory.py`

`sam/queries/__init__.py` stays empty. `snapshot_from_user(user)`, `snapshot_from_increment(data)`
(the `UserSyncInput.load()` dict), `check_person(session, reader, data, *, now=None) ->
(increment_report, sam_report) | None` (`upid` None is a role login: None; one `reader.person(upid)`;
SAM side `User.get_by_upid`), `check_organization` / `check_institution` / `check_group`, and
`run_check(session, kind, data, key)`: latch check, `reader_from_environment()`, `configured`
check, read, compare, one log line
`ldapsync: directory check person %s upid=%s found=%s increment=%s sam=%s`
(username, upid, then `fragment()` per side); on `DirectoryUnavailable`: `mark_down()` plus one
warning.

### 4.3 Hook `src/webapp/api/ldapsync/sync.py`

`_directory_check(kind, data, key)`: returns unless `DIRECTORY_CHECK_ON_SYNC`; wraps `run_check`
in `try/except Exception` -> one warning (`type(exc).__name__`, no message text). Called after
the `with` block in `_apply(schema_cls, sync_fn, kind)` (institution, organization, group; gid
passes `None`) and in `put_user` (`'person'`). `sync_user` keeps returning `unix_uid`;
`check_person` loads by `data['upid']`. The hook never writes and never raises past itself.

### 4.4 Dependency and gates

- `pyproject.toml` dependencies: `ldap3>=2.9.1,<3` (pure Python; the slim image needs no system
  LDAP client). The Dockerfile installs from pyproject and is unchanged.
- `tests/conftest.py`: `pytest_configure` assigns `DIRECTORY_PROVIDER=''`, `DIRECTORY_URL=''`,
  `DIRECTORY_CHECK_ON_SYNC='0'` beside the XRAS pins; an autouse `_no_outbound_ldap` wraps
  `ldap3.Connection.open` and `.bind` to raise unless the server host is in the localhost
  allowlist; `_UNIT_DOMAIN_MARKERS['directory']` plus a `directory` marker in `pytest.ini`.
- `tests/unit/models/test_outbound_guards.py`: the pins (`reader_from_environment()` is a
  `NullDirectory`; `monkeypatch.setenv` flips `directory_configured()`) and the socket guard
  (`ldap3.Connection(Server('ldap://ldap.ucar.edu'), auto_bind=True)` raises the guard's error; a
  localhost server fails with ldap3's own error).
- `tests/factories/directory.py`: `FakeDirectory(DirectoryReader)` (dicts by upid/id/gid,
  `calls`, `raise_with`, `configured`), `make_person_record`, `make_position_record`,
  `make_collaboration_record`, `entry(**attrs)`.
- Tests: `tests/unit/directory/test_{base,config,outage,ucar_parsing,compare}.py` (one test per
  category; Generalized Time `20260715235959Z` -> `2026-07-15 17:59:59` and `20260115060000Z` ->
  `2026-01-14 23:00:00`; `fragment()` carries no `@` and no personal name);
  `tests/unit/queries/test_directory_snapshots.py` (factories `make_user`,
  `make_user_organization(idms_unique_name=...)`, `make_user_institution`; `check_person` makes
  exactly one read); `tests/api/test_ldapsync_directory_hook.py` (`ldapsync_client`;
  `sync.sync_user` monkeypatched so nothing commits: lever off -> no read and 200; lever on -> one
  `('person', upid)` call; `run_check` raising -> still 200 and one WARNING;
  `DirectoryUnavailable` -> latch set and the second PUT makes no call; an organization PUT reaches
  the hook with kind `organization`).
- Dev pre-flight: a samuel-dev pod reaches `ldap.ucar.edu:389` (confirmed 2026-10-16; the
  chart has no egress policy). Prod is the same cluster; the Configuration block's latch line
  is the first signal if its route differs.

## 5. Step 2: apply on sync, per kind

### 5.1 `src/sam/manage/directory.py`

`reconcile_person` / `reconcile_organization` / `reconcile_institution` / `reconcile_group` take
SAM's object and the record; `reconcile_record(session, kind, key, record, ...)` loads SAM's side
by key (the hook, which already holds the record from the check) and
`reconcile_from_directory(session, kind, key, *, reader, ...)` reads first (the panel and the
CLI). The rules consume the compare categories, so the report and the apply cannot drift.
`ReconcileResult(kind, key, report, applied: tuple[Change, ...], withheld: tuple[Discrepancy, ...],
dry_run)` with `fragment()` (`applied=2[name:1,planned_end:1] withheld=1[twin:1]`). Every write
path runs inside the caller's `management_transaction` (hook: a second transaction after the
PUT's commit; UX: the handler; CLI: the command). `dry_run` decides the same changes and touches
nothing. A kind whose lever is off contributes to `withheld`, never to `applied`.

What each kind may write (AFFILIATIONS stage 2: rows the directory ties to by id; SAM-only
history never touched):

| Kind | Applied | Reported only |
|---|---|---|
| person | names, nickname, middle, suffix; `academic_status_id` via the label map; `token_type`; emails: a missing address is added, primary follows `mail`, nothing is deleted; phones mirrored (as the sync does, a label SAM lacks withholds); `active` through `_apply_active_transition` (the sync's own path, so the lifecycle levers and the restore hook apply) | username and `unix_uid` (identity columns are set once, at create: `User.SYNC_FIELDS`), unmapped academic label, `missing_in_sam` (SAM never originates users here) |
| position | planned end set when SAM's row has none; finished-vs-open closed to the directory's end when it carries one; employer updated in place (AFFILIATIONS § 5 "Amended D2") when SAM knows the organization; a new row for `no_sam_row` when the organization is known, a start exists and it is not a twin | `twin` (D-B), inactive with no end (D-C: never invent a date), a reopen (the directory current, SAM ended), `history_open` (D-A), `end_day`, `start_day` (the Workday winter shift; a candidate once the directory's day is trusted) |
| collaboration | finished-vs-open closed to the directory's end when it carries one; a new row when a start exists and SAM knows the institution | any planned end (PeopleDB's until the keep-alive runs; the `modifier` hook decides later), `no_directory_start`, `history_open`, `start_day` |
| organization, institution | all differing fields at once through `Organization.upsert_from_sync` / `Institution.upsert_from_sync`; a record SAM lacks is created | a parent SAM lacks; an institution type SAM lacks |
| group | nothing in this PR | members, tags, manager |

Never touched (Appendix C.4): `locked`, `charging_exempt`, `deleted`, `deactivate`,
`primary_gid`, `title`, `contact_person_upid`, `login_type_id`. The person's flag wins over an
affiliation's.

### 5.2 Hook extension

`_directory_check(kind, data, key)`: `run_check` as before (it returns the record it read); when
`config.apply_kinds` is non-empty, `with management_transaction(db.session):
reconcile_record(...)` with the sync's restore hook, inside the same fail-open `try`; a failure
rolls back that second transaction and logs one warning, and the PUT's own commit is already
durable. A second log line, `ldapsync: directory apply <kind> <key> ... applied=… withheld=…`.

### 5.3 Tests

`tests/unit/manage/test_directory_reconcile.py`: per kind, lever off -> nothing written and
`withheld` lists it; lever on -> exactly the table's writes (the `writes` statement counter from
`tests/unit/manage/test_ldapsync_user.py`); twin, D-C and history rows untouched; `dry_run`
writes nothing and still lists `applied`; an identical record -> zero statements. Hook test: apply
lever on plus `FakeDirectory` -> the second transaction's writes land; reconcile raising -> PUT
still 200 and SAM unchanged beyond the PUT.

## 6. Step 3: UX

- **Configuration card** (`VIEW_SYSTEM_CONFIG`, read-only): `_directory_block(app)` in
  `config_inspect.py` next to `_tickets_block`: `config.summary()`, mode (`off` / `report on
  sync` / `apply: <kinds>`), latch state, no live probe. A "Directory" sub-card after Help-desk
  tickets in `configuration_card.html`; rows hidden when the provider is empty.
- **User card** (`dashboards/user/partials/user_card.html`, admin wrapper passes `directory`):
  a "Directory" section rendered only when `directory_configured()`, inline in the card (the
  card is also loaded into `#userDetailsModal` on the user dashboard, so a nested modal would
  add a host-shell dependency everywhere). "Check directory" (`VIEW_USERS`) ->
  `GET /htmx/user/<username>/directory` -> `fragments/user_directory_panel.html`: the person's
  facts, current positions and collaborations as the directory holds them (sponsor org on each
  collaboration), and the drift table with both values shown; each row is marked "would apply"
  or "withheld" from `reconcile_person(dry_run=True)`, one derivation for the report and the
  write. Operator-initiated, so a card view never pays an LDAP read. "Apply" (`EDIT_USERS`,
  shown only with a lever on) -> `POST /htmx/user/<username>/directory/apply` ->
  `reconcile_person` inside `management_transaction` with the sync's restore hook -> the panel
  re-rendered with a result banner and a "Refresh card" button (a `reloadUserCard` would wipe
  the panel). An unavailable directory is the panel's alert, 200.
- **Organizations card**: a per-row "Directory" action (a bespoke route, not a `CrudSpec`
  field) shown when a directory is configured (the card is cached per user, so a config flip
  shows after `sam-admin cache --refresh`): `GET /htmx/organization/<id>/directory`
  (`VIEW_ORG_METADATA`) into `#orgDirectoryModal` (shell in `organization_modals.html`;
  facts in `.modal-facts`, never alerts, per the modal ratchet), `POST .../apply`
  (`EDIT_ORG_METADATA`) re-renders the dialog and fires `reloadOrganizationsCard` when
  something applied. The Institutions tab has the same action into `#instDirectoryModal`.
  Groups (the admin project card and the adhoc group card) are report-only panels whose SAM side
  is the sync's own `project_group` / `groups` row: `DIRECTORY_CLIENT_NEXT.md` section 3.
- Gates: regenerate `tests/unit/snapshots/dashboard_route_map.json` (`ROUTE_MAP_REGEN=1`);
  `HTMX_FRAGMENT_SHELL_DEPS` if a fragment names a host id; the `wire-dashboard-feature` skill
  checklist; CSP (no inline scripts). Tests in `tests/unit/webapp/test_directory_panels.py`
  (auth, 404, unconfigured -> hidden, fake reader -> render smoke, the apply route refuses
  without `EDIT_USERS`).

## 7. Step 4: CLI and the nightly report

- `sam-search user <u> --directory`: `data['directory'] = build_user_directory(...)` (absent
  when unconfigured via the `_UNCONFIGURED` sentinel pattern; `{'available': False}` when down);
  `display_user_directory` in `src/cli/user/display.py` (positions table, sponsor orgs, drift
  `issues_table`). The JSON envelope gains one optional key; no lockstep change.
- `sam-admin user <u> --directory-apply [--dry-run]`: `UserAdminCommand._directory_apply`, JSON
  refused for a live write (`json_unsupported_for_writes`), `management_transaction` around
  `reconcile_from_directory`.
- `sam-admin directory report [--kind K] [--limit N]`: the stage-1 sweep (AFFILIATIONS § 6): one
  `reader.session()`, paged subtree reads, bulk SAM projections (one query per table keyed by
  upid, not one per person), counts by category plus capped id lists; `--format json` supported.
  New command package `src/cli/directory/` (`commands`, `builders`, `display`), wired in
  `src/cli/cmds/admin.py`.
- Task `directory_report` (`src/scheduling/tasks/directory_report.py`, nightly 03:00 Mountain,
  `needs=('sam',)`, registered in `tasks/__init__.py`): the same sweep; `TaskResult.detail`
  carries the counts; **named in `helm/values.yaml` `SAM_TASKS_DISABLED` in the same commit**
  (the list is fail-open); `DIRECTORY_*` keys added to `cronjob-tasks.yaml` by name;
  `helm/tests/test-cronjob-render.sh` assertion. Tests: `tests/unit/tasks/test_task_directory_report.py`
  (greps `values.yaml` for the name, as `test_task_xras_notices.py` does),
  `tests/unit/cli/test_directory_cli.py`.

## 8. Docs and rules

- `.claude/rules/directory.md` (paths: `src/sam/integration/directory/**`,
  `src/sam/queries/directory.py`, `src/sam/manage/directory.py`, `tests/unit/directory/**`) plus
  an index row and one tripwire line in `CLAUDE.md`: the hook is fail-open and after commit;
  `DIRECTORY_APPLY_<KIND>` is per kind; log lines carry usernames and ids, never email addresses,
  phone numbers or personal names.
- `.env.example` block; `docs/apis/SYSTEMS_INTEGRATION_APIs.md` § 8 one paragraph.
- AFFILIATIONS: the status line points here, and Appendix D names the sync hook as the first
  consumer and the CLI as the last.

## 9. Verification

1. `pytest tests/unit/directory tests/unit/queries/test_directory_snapshots.py tests/api/test_ldapsync_directory_hook.py tests/unit/models/test_outbound_guards.py tests/unit/gates -n auto`, then the full suite.
2. Local: `docker compose up samuel-dev --watch` with `DIRECTORY_PROVIDER=ucar`,
   `DIRECTORY_URL=ldap://ldap.ucar.edu`, `DIRECTORY_CHECK_ON_SYNC=1` in `.env`; replay a captured
   user PUT from the ldap-pipeline testbed (`containers/ldap-pipeline`, the `.env.dev` stack
   pointed at `host.docker.internal:5050`, stub off) and read the `directory check` line; then
   `DIRECTORY_APPLY_PERSON=1` and confirm `applied=` names the expected ids and nothing else.
3. Dev: deploy the branch; Ben confirms pod egress to 389; dev values turn the check on; the
   shadow soak's daily check reads the new lines alongside `compare-dumps`. Step 2 levers go on
   in dev only after a few days of clean step-1 logs, one kind at a time.
4. Prod reads too (Ben, 2026-10-16): the panels and the block work there on deploy; the check is armed and fires from the daemon's first PUT after the `SAM_URL` flip. No apply lever in either values file.

## 10. Risks

- Pod egress to `ldap.ucar.edu:389` is unverified; the latch keeps a blocked route to one
  warning per worker per minute.
- The hook runs on the request thread after commit and before the reply, so the daemon's PUT
  waits for one directory read plus SAM's snapshot queries (~40-100 ms), and up to one timeout
  (5 s) per worker per minute when the directory is down, then the latch. The daemon never
  retries, so the PUT's own commit precedes the hook. **Follow-up (review of `fde9df23`, F1):**
  if the soak's PUT timings show it, move the check after the reply. Not the few lines it
  looks: `response.call_on_close` runs after Flask has popped the app and request contexts, so
  the check needs its own `app.app_context()` and a fresh session; under gunicorn's sync
  workers the worker stays busy for the same time, only the daemon's perceived latency changes.
- Directory stamps carry an offset. PeopleDB's are correct Mountain offsets; Workday writes
  every position date as midnight `-0600` year-round, wrong in winter for 580 of 2,551 (survey
  2026-10-15). `sam.dates.from_generalized_time` takes a `-0600`/`-0700` stamp as Mountain wall
  time as written and converts only `Z` stamps. The daemon converts strictly, so SAM holds
  23:00 the day before on those rows: `start_day` reports it. Position ends compare by day;
  `end_day` is a category, never a write.
- Collaboration ends: SAM floors a midnight end to 23:59:59 (`src/sam/base.py`), so a
  compare-by-instant is noisy until AFFILIATIONS § 5 "stored as sent" lands. This kind's ends are
  report-only regardless.
- Step 2 writes `active` through the sync's own transition, so the lifecycle levers govern
  deactivation side effects exactly as a daemon PUT would.
- `ldap3` 2.9.1 (2021) is stable but slow-moving; pinned `<3`.
