# LDAP Sync API — serving `sam-ldap-syncd` from the new SAM

**Status:** scoping / handoff, 2026-09-25. No code yet. The client-side facts in §1–§2
were checked against the production daemon on sam-app.ucar.edu (image 6c0fd35).
**See also:** `SAM_LDAP_SYNCD_REFERENCE.md` for what the daemon is and how it works,
and its bug list.
**Goal:** everything the Flask webapp must implement so that flipping one variable
(`SAM_URL` in the daemon's `prod/.env`) points `sam-ldap-syncd` at the new SAM, with
legacy Java SAM as the rollback.

---

## 1. Context

`sam-ldap-syncd` (Perl 5, NCAR/sam-ldap-syncd, George's repo; a clone lives in
`legacy_sam/container_zoo/sam-ldap-syncd/`) is SAM's **identity mirror**. It reads a
replica of the central UCAR LDAP (`sam-idms-ldap` → `sam-ldap-transformer` → `.jsl`
files) and pushes institutions, organizations, users, unix groups and GID blocks into
SAM over REST. It also reads project groups and collaborator-expiry data back out of
SAM and writes them into LDAP staging (`fdbstage`).

It went live against legacy Java SAM in July 2026: `sam-app` added the stack on
2026-06-29, the replica was pointed at the production directory on 2026-07-10 (the
Fischer Identity / IGA cutover), update stubbing was switched off on 2026-07-16 and the
first production PUT landed that evening. The "post FI cutover fixes" of 2026-08-10
(5cb852d) added the `users.deactivate` stamping and generalized the placeholder-account
deferral; the systemd service files of 2026-08-13 exist but are disabled (see
`SAM_LDAP_SYNCD_REFERENCE.md` §2.4). The legacy source deleted the PeopleDB webhooks it
replaced on 2026-07-04 ("Misc mods for new sam sync service"). It is the newest
production consumer of legacy SAM and the only one whose endpoints the new webapp has
not ported at all.

**Framing.** "SAM never creates users" (`docs/xras/PROJECT_AND_ACCOUNT_LIFECYCLE.md`
§2, `docs/plans/implemented/ACCOUNT_REGISTRATION.md`, CLAUDE.md) means SAM does not
*originate* identities. They are mirrored precisely through this daemon. An ldapsync
writer on the new side is therefore the rule's implementation, not an exception to it.
Those three passages should say "never originates" once this ships.

**Why the 30-day audit cannot size this.** `legacy_sam/doc/apis/apis_30day_usage.md`
covers 2026-05-14 to 2026-06-12, before the daemon existed. A re-count of all 31
access logs shows the whole `ldapsync/*`, `*Purge*` and `userlifecycle/*` family at
≤ 3 hits each, so `stale_apis.md` marks as stale exactly the endpoints this consumer
now depends on. Fresh Tomcat logs (from mid-July on) would show the real rate; they
live on sam.ucar.edu, not on the daemon host.

---

## 2. What the daemon needs

Sources: `lib/Constants.rc`, `lib/SamClient.pm`, `lib/Synchronizer.pm`,
`lib/SamUpdatePropagator.pm`, `lib/ProjectGroupUpdater.pm`, `lib/LifecycleUpdater.pm`,
`lib/Sam*Data.pm`; the HTTP library `sweet-pl5/lib/NetUtil/HttpClient.pm`; legacy
controllers `service/idservice/api/SyncLdapController.java`,
`service/userlifecycle/api/UserLifeCycleController.java`, `user/api/*Purge*`; and
the 2026-09-24 obfuscated snapshot.

### 2.1 Transport contract (hard-coded in the daemon)

| Aspect | Requirement | Evidence |
|---|---|---|
| URL | `SAM_URL` (env; prod `https://sam.ucar.edu:443`) + relative `api/protected/admin` + per-endpoint rpath. Prefix and rpaths are baked into the image (`lib/Constants.rc:15-38`). | `SamClient.pm:80-111` |
| Auth | HTTP Basic, **challenge-response**: the first request carries no credentials; the server must answer 401 with `WWW-Authenticate: Basic realm="Realm"`, exact string. LWP 6.52 matches on the realm; on mismatch it never sends the password, the 401 is treated like any 300–500 answer (thrown, 300 s pause, replay), and during startup the daemon aborts. | `HttpClient.pm:291,698-717`; `SamClient.pm:541-545` |
| Credential | `api_credentials.username='admin'` holding `role.name='ROLE_API_ADMIN'` (legacy `security-config.xml:32-41`). Secret `SAM_AUTH_admin` read from the environment or from the container's parmdb store (`PARM_DB=/tmp/parmdb`), which the sweet entrypoint fills from the `sam.parm` secrets file at start; it is not a file in `SECRETS_DIR`. | `Sweet.pm:260-273` |
| 404 | Means "Tomcat still deploying": **retried forever, silently**. Legacy never 404s on a mapped path; an unknown entity is 200 + empty body. | `SamClient.pm:505-572` |
| 300–500 | Thrown; the main loop logs "Pausing and continuing after exception", sleeps a literal 300 s (`Synchronizer.pm:266`) and replays, but the item is already applied to the daemon's in-memory copy and is lost until the next full dump. Exceptions: a 500 whose message says the connection was refused, closed or reset is retried like a 404; codes below 200 or above 500 are retried forever. Validation is 400 with `{"errorMessage": "..."}`; anything else 500. | `SamClient.pm:525-548` |
| Bodies | Every 2xx GET/PUT body must be valid JSON (`decode_json` dies on empty). PUT echoes a bare integer id. DELETE may return empty. DELETE is never redirect-followed, so no 3xx. | `SamClient.pm:222-223,294-295,344-349` |
| Requests | PUT sends `Content-Type: application/json` (the HttpClient default, `HttpClient.pm:386-393`); dates epoch ms; undef values are dropped only for attributes that have a normalizer, other undef keys go out as `null`. `since` would be sent as a bare `?<ms>` with no name, but the watermark never gets a value (reference bugs 4+5), so it is never sent. | `SamClient.pm:214-221,456`; `SamDataManager.pm:421-437` |
| Param names | `groupPurgePermit?unixGid=` but `groupPurge?posixGid=` (client literals). | `Constants.rc:28-35` |
| TLS | Verified only when `HTTPS_CA_DIR`/`HTTPS_CA_FILE` exists (prod sets `/etc/ssl/certs`). | `HttpClient.pm:166-183` |
| Timeout | LWP default 180 s. | — |

### 2.2 Calls that reach SAM today

| Method | Path (under `/api/protected/admin`) | Used for |
|---|---|---|
| GET | `ldapsync/status` | `--test-connections` only; body is dumped |
| GET | `ldapsync/{institution,organization,user,group,gidAllocation,projectGroup,groupTag}` | initial load into the in-memory DB; each type's list **must be non-empty** or the next restart fails (`SamDataUtil.pm:290-305`). Production sizes at the 2026-08-27 load: 1,384 / 402 / 28,447 / 310 / 1 / 5,832 / 5 |
| PUT | `ldapsync/{institution,organization,user,group,gidAllocation}` | upsert keyed by the client-supplied id |
| GET | `{user,group,institution,organization}PurgePermit?<key>=` | reads `purgeable` only |
| DELETE | `{user,group,institution,organization}Purge?<key>=` | only if purgeable; otherwise a tombstone PUT |
| GET | `ldapsync/projectGroup` (always the full list, §2.1) and `ldapsync/groupTag` | project-group pass → LDAP `ou=allGroups` (see §2.4) |
| GET | `userlifecycle/collabexpiryupdates` | lifecycle pass → LDAP collaboration `x-ucar-endDate` (see §2.4) |

### 2.3 Calls defined but not reaching SAM (daemon defects)

| Call | Why it never arrives |
|---|---|
| `GET userlifecycle/pendingdeactivations/{h}` | sent as `/pendingdeactivations/?24` (bare-`?` quirk); legacy 404s → retry forever |
| `PUT userlifecycle/deactivate/{username}` | path key `'deactivation'` vs registered `'deactivate'` (`SamClient.pm:367` vs `:95`); `$samClient` unset in `LifecycleUpdater.pm:295`. The body would be the JSON string `""` |
| `GET ldapsync/{type}/{id}` read-back | `willSamModify` is always 0 (`SamUserData.pm:289,295`); duplicate `getSAMObject` (`SamClient.pm:236,260`) would drop the id anyway |

Visible consequence in production (2026-09-25): 140 users carry `users.deactivate`
(stamped by `PUT ldapsync/user` when IDMS says inactive, the oldest on 2026-08-10, the day
5cb852d went live), all still `active=1, locked=0`, holding 724 open `account_user` rows;
none was ever finished, and the count grows.

### 2.4 The two background passes run on the shipped schedule, on stale data

`prod/.env:72-74` tries to disable the crons by setting `SYNCD_LIFECYCLE_CRON_DFLT`
and `SYNCD_PROJECTS_CRON_DFLT` to month 12. Nothing reads those names:
`bin/syncd:459-482` takes `SYNCD_LIFECYCLE_CRON` / `SYNCD_PROJECTS_CRON` from the
environment and their defaults from the `_DFLT` lines inside the image's
`lib/Constants.rc`. So both jobs run on the defaults, and because Schedule::Cron's
sixth field is seconds, `0 22 * * * *` fires every second of the 22:00 minute, each a
forked child holding the in-memory database as of daemon start
(`SAM_LDAP_SYNCD_REFERENCE.md` §3.9, bugs 13 and N4). On the production host the
scheduler process is alive (`Schedule::Cron MainLoop - next: … 22:00:00` in `podman top`)
and during the 22:00 minute its title advances one second at a time while a
`Dispatched job 1` child appears and exits within seconds. The children's output goes to
`/dev/null`, so the daemon log shows nothing of them; the ~3,270 log lines that mention
`collabexpiryupdates` are the endpoint table dumped at each daemon start.

The collaborator end-date extension therefore fires nightly, but whether it produces a
directory write is unknown: the children die within seconds, and the production replica
of the directory holds no attribute with `x-ucar-source: SAM:*`. Jackson 2.x accepts an
`is` getter returning boxed `Boolean` as an is-getter, so legacy's `ActiveUserStatus`
serializes `activeCollaborator` and the Perl key matches; the new server must emit the
same key. This is the "identify users on a project and push their LDAP end date into the
future" function: SAM computes each collaborator's nominal expiry (latest end date over
their contracts, led/admin projects, membership allocations and disk holdings, plus 90
days grace) and the daemon extends, never shortens, the LDAP collaboration end date to
match. The second half of that job, finishing deactivations, dies at its first statement
(§2.3).

---

## 3. Design

### 3.1 Mount: legacy paths, zero client change

- New package `src/webapp/api/ldapsync/`: `__init__.py` (blueprint, auth alias, error
  envelope), `sync.py` (`/ldapsync/*`), `purge.py` (`/{x}Purge`, `/{x}PurgePermit`),
  `lifecycle.py` (`/userlifecycle/*`).
- Register in `src/webapp/run.py` beside XRAS (`run.py:451` keeps the legacy servlet
  prefix `/api/xras/v1` for the same reason) at `url_prefix='/api/protected/admin'`.
  A gate in `tests/unit/gates/` asserts the prefix is claimed by exactly one blueprint.
- Rollback is flipping `SAM_URL` back; both servers speak one contract.
- Rejected alternative: new `/api/v1/ldapsync/*` paths plus an edit to the image-baked
  `Constants.rc`. Two artifacts to revert instead of one variable. Can be added later
  as an alias.

### 3.2 Auth

- `ldapsync_api_required = partial(login_or_token_required, roles=('ROLE_API_ADMIN',), deny=_deny)`,
  copying `src/webapp/api/xras/__init__.py:62`. `roles=` closes the browser-session
  path by construction.
- `_deny(401)` answers with `WWW-Authenticate: Basic realm="Realm"`. Blueprint-local;
  the global `_auth_challenge` in `src/webapp/utils/api_auth.py:58-62` stays
  `SAM API`. One test pins the header byte-exact.
- The webapp already reads `api_credentials` + `role_api_credentials` (bcrypt), the
  same tables legacy authenticates against, and `ROLE_API_ADMIN` exists (`role_id` 11).
  Config-sourced `API_KEYS` carry no roles and fail closed on `roles=` routes, so the
  `admin` key must be the DB row. The obfuscated snapshot has no `api_credentials` rows;
  confirm the row and the `SAM_AUTH_admin` secret on prod.
- Every write route `@csrf.exempt` (precedent: the `/refresh` routes). Confirm the M2M
  limiter tier will not throttle an add-file replay (thousands of PUTs in minutes).

### 3.3 Wire-contract helpers (`__init__.py`)

- `_error(status, msg)` → `{"errorMessage": msg}`; `errorhandler(ValidationError)` → 400;
  `errorhandler(Exception)` → 500 JSON, never HTML.
- No 404 for not-found on a mapped path: unknown `user/{uid}` → 200 empty body; purge
  of a nonexistent entity → 200 empty; `PurgePermit` of a nonexistent entity →
  `purgeable: true`.
- `read_since(args)`: honor `since=` and the bare-key form; a value above 2^31−1 is ms.
- `strict_slashes=False` on DELETE rules (no redirect).

### 3.4 Output shaping

Marshmallow schemas in `src/sam/schemas/ldapsync.py`, `data_key` camelCase
(`sam/schemas/disk_quota.py` is the reference; the hand-built dict is the frozen
exception, not the norm). Add an `EpochMillis` field: naive-Mountain datetime →
`America/Denver` → epoch ms (legacy JDBC used `serverTimezone=America/Denver`).
Lifecycle DTOs use `yyyy-MM-dd` strings. Booleans for `active`, `primary`,
`activeCollaborator`.

---

## 4. Phases (one PR per track)

### P1a — reads, auth, contract (cutover-blocking, no writes)

`src/sam/queries/ldapsync.py`:

| Collection | Rule |
|---|---|
| institution, organization | all rows including `deleted`; organization adds `parentOrgAcronym` |
| user | every row incl. inactive/deleted; `active = users.active OR deactivate IS NOT NULL`; `institutionIds`/`orgIds` = open link rows only; `collaborations`/`positions` = **all** `user_institution`/`user_organization` rows incl. history; `typeOfLogin` from `login_type.type`; `academicStatus` code; emails `{emailAddressId,userId,email,primary}`; phones `{extPhoneId,extPhoneUserId,extPhoneType,phoneNumber}` |
| user/{unixUid} | same mapping; unknown → 200 empty |
| group | every `adhoc_group`; `name = key = group_name`; `posixGid = unix_gid`; `usernames` = all `adhoc_system_account_entry.username`; `upids` = entry users with `contact_person_upid IS NULL`; `rolenames` = the rest; `tags` = distinct `access_branch_name` of the entries (**not** `adhoc_group_tag`) |
| gidAllocation | raw rows `{gidAllocationId,startGid,nextGid,endGid,creationTime,modifiedTime}` |
| projectGroup | all projects, projcode asc; `name` = projcode, `key` = lowercase, `posixGid = unix_gid`, `active`; `upids`/`rolenames` = users where project active, resource `configurable=1`, allocation `end_date + 90 d > now`, current `account_user`, `users.active=1` (`user_login` → upid, `role_login` → username); lead + admin always; `tags` = access branches via `access_branch_resource` + `exclude-from-google` always + `auto-renewed-project` when facility code ∈ substring of `"CN"` (reproduce, flag); `lastModified` = max(project modified/creation, every `account_user` modified/creation); `since` filter in Python. Reuse `grace_cutoff()` and the branch join in `sam/queries/directory_access.py:47,221`; the divergence from `group_populator` is by design and gets a test |
| groupTag | `access_branch.name` (accessBranch=true), then `exclude-from-google`, `auto-renewed-project` (false) |
| status | legacy reads `information_schema.tables.update_time` (NULL on InnoDB/Postgres; its fallback 500s). The daemon only prints it: compute `GREATEST(MAX(creation_time), MAX(modified_time))` per table via `sam/sqlcompat`; `accessBranches` = all branch names |

Plus: schemas, blueprint, auth alias, realm test, no-404 test, golden test (§5), and a
new §8 "LDAP Sync API" in `docs/apis/SYSTEMS_INTEGRATION_APIs.md`.

### P1b — institution / organization / gidAllocation PUTs + purge pairs

- `Institution.update/create` (`sam/core/organizations.py`) extended with
  `nsf_org_code, address, city, zip, deleted`; `institution_type_id` by
  `institution_type.type` (unknown → 400); `state_prov_id` by country code + state
  code, then by name, else NULL; **assigned PK from the payload** (the sync path
  bypasses the max+1 allocator). Update stamps `modified_time`.
- `Organization.update/create` extended with `level, level_code, tree_left/right,
  idms_unique_name, deleted`; `parent_org_id` = **active** org whose acronym matches
  case-insensitively, else NULL.
- `GidAllocation.create_block(session, start, end)` (`sam/core/groups.py`): exact
  `(startGid, endGid)` match is a no-op; a start or end inside an existing range → 400
  "Gid range {s}:{e} overlaps existing allocation."; else insert with `nextGid=startGid`.
- `src/sam/manage/purge.py`:

| Entity | Permit rule (purgeable when none apply) | Purge |
|---|---|---|
| user (`unixUid`/`upid`/`username`) | active; any `account_user` row ever; wallclock exemption; contact person of a role login; project lead/admin; contract PI/monitor; `allocation_transaction` author; resource `prim_sys_admin`; `charge_adjustment.adjusted_by`; any charge-summary rows | hard delete `users` cascading `phone, email_address, user_organization, user_institution, user_resource_home, user_resource_shell, default_project, wallclock_exemption`; `role_user` not cascaded |
| institution (`institutionId`) | any `user_institution` row | hard delete row |
| organization (`organizationId`) | any `user_organization` or `project_organization` row (children not checked) | hard delete row |
| group (`unixGid` on permit, `posixGid` or `groupname` on purge) | any `project.unix_gid` equal | hard delete `adhoc_group` (+ entries and tags by cascade); legacy runs **no permit check** on the DELETE |

Nonexistent → permit `purgeable: true`, purge no-op 200. Accept both gid param names
on both group routes. Responses: permit `{<idField>, purgeable, message}`; purge empty
200; permit violation on purge → 400 with the same text.

- Routes: 5 PUTs echoing `institutionId` / `organizationId` / `unixUid` / `posixGid` /
  `startGid` as a bare JSON integer; 4 permit GETs; 4 purge DELETEs.

### P1c — user PUT and group PUT (the hard track)

- `User.create(session, *, username, unix_uid, upid, active, ...)` and
  `User.update(...)` that never touches `username`, `unix_uid`, `upid`
  (`sam/core/users.py`). `src/sam/manage/ldapsync.py::sync_user` owns the rules:
  - a user with this `upid` exists but none with this `userName` → 400
    "Upid {0} matches username {1} (username change in ID Service?)";
  - match by `username`, case-insensitive; never by unixUid;
  - `chargingExempt`, `locked`, each email's `primary` must be present (400 if null);
  - role logins (`contactPersonUpid` set) copy first/last/middle/nickname from the
    contact user; missing contact and null login type → `last_name='unknown'`;
  - attributes written on both paths: `locked, title, first_name, last_name,
    middle_name, charging_exempt, token_type, nickname, name_suffix,
    contact_person_upid, deleted`, `academic_status_id` by code, `login_type_id` by type.
- Active transitions (update only): IDMS active and SAM not effectively active →
  `active=1, deactivate=NULL`; IDMS inactive and SAM `active=1` → `deactivate=now`
  (stays `active=1`, now "pending"); `modified_time` only on a transition.
  `User.is_active` stays `active AND NOT locked`.
- `EmailAddress.sync(session, user, wanted)`: match by address case-insensitively;
  rows not in the payload are **hard-deleted**; matched rows get `is_primary` and
  address case; new rows inserted. `Phone.sync(...)`: exact number match, orphan
  delete, `ext_phone_type_id` by `phone_type.phone_type`.
- `src/sam/core/employment.py::sync_employments(user, collaborations, positions, now)`
  for `user_institution` / `user_organization`: an explicit `collaborationId` /
  `positionId` must belong to this user (else 400); otherwise match in order
  identical → same `idms_unique_name` → exact dates → overlapping day-bounded ranges;
  overwrite start/end/idms on match, insert on miss; **rows missing from the payload
  are never ended or deleted**; `end_date` stored at 23:59:59 (`normalize_end_date`).
- `sync_group`: `key == 'ncar'` → no-op. Project with `projcode == key` (ci): no gid →
  `Project.update(unix_gid=posixGid)`; has gid → no-op. Otherwise adhoc iff at least
  one tag is an `access_branch.name` (ci): consistency 400 when the name exists with
  a different or no gid; then `AdhocGroup.upsert()` (gid immutable, name mutable,
  `active`), `adhoc_group_tag` sync, and `adhoc_system_account_entry` = canonical
  branch names × `usernames` (exact case). `upids` / `rolenames` ignored on input.

### P1d — collabexpiryupdates (the collaborator keep-alive)

- `src/sam/queries/user_lifecycle.py`: candidates = users with an open
  `user_institution` and no open `user_organization` (Staff wins), qualifying as legacy
  does (`active=1`, not deleted, `login_type_id=1`, referenced by a loader, or present
  in the latest `disk_activity` date). `nominalExpiry` = latest end date over contracts
  as PI/monitor, projects as lead/admin (+ their contracts and latest allocation),
  current `account_user` allocations, disk holdings (activity date + 90 d); with no
  association at all, today + 90 d. Emit only where `currentCollaborationEndDate` is
  null or earlier than `nominalExpiry`. Fields per legacy `ActiveUserStatus`:
  `userId, upid, unixUid, username, currentCollaborationEndDate, activeCollaborator,
  currentPositionEndDate, activeStaff, type, datedAssociations[{type,description,endDate}],
  nominalExpiry`, dates as `yyyy-MM-dd` strings, **`activeCollaborator: true`**.
- Do not port the singleton snapshot cache, the frozen-`now` bug
  (`SqlLifeCycleQueries.currentTime` set once at bean creation), or the NPE on null
  end dates.
- Cutover checklist item for George: fix the cron configuration (bug 13 in the
  reference: use the non-`_DFLT` names, sixth field `0`) so each job fires once per
  slot; the jobs fire today on the shipped defaults, sixty times per slot.

### P2 — lifecycle finish + parity fillers (gated on client fixes)

- `src/sam/manage/lifecycle.py::finish_user_deactivation(session, username, now)`:
  requires `active=1 AND deactivate IS NOT NULL` (else 400 "User {0} is not active and
  marked for deactivation."); end every current `account_user` row with the house
  soft-delete stamp (`remove_user_from_project` convention); reset `primary_gid` to
  1000 if it was one of those projects' gids; then `active=0, deactivate=NULL,
  modified_time=now`.
- **Decision (Ben, 2026-09-25): fix NCAR/sam-ldap-syncd#3 on the SAM side.** Add
  `restore_deactivated_memberships()` on reactivation (the `PUT ldapsync/user` active
  transition), restoring rows whose `end_date` matches the user's last deactivation
  stamp. No schema change. Runbook: `scripts/repair/RUNBOOK-missing-projects.md`.
- Routes: `GET pendingdeactivations/<int:h>` (usernames with `active=1 AND deactivate
  < now − h`), `PUT deactivate/<username>`, `GET ldapsync/user/<uid>`, and
  `DELETE ldapsync/{institution,organization,user,group}/{id}` as aliases onto the
  purge commands (legacy declares `consumes=application/json` on them).
- Ship behind `LDAPSYNC_LIFECYCLE_ENABLED=false` until George lands the client fixes:
  `SamClient.pm:366` key name, `LifecycleUpdater.pm:295` `$samClient`, and the bare-`?`
  path for `pendingdeactivations` (`Constants.rc:37`).
- `GET userlifecycle/activeusers`: no caller; skip.

### Docs follow-ups

- New §8 in `docs/apis/SYSTEMS_INTEGRATION_APIs.md` (with P1a).
- Amend "never writes `users`" → "never originates users" in
  `docs/xras/PROJECT_AND_ACCOUNT_LIFECYCLE.md` §2,
  `docs/plans/implemented/ACCOUNT_REGISTRATION.md` (the NUSD paragraph) and the
  CLAUDE.md account-requests bullet (with P1c).
- Rewrite the `users.deactivate` comment in `sam/core/users.py` (it now has a producer
  and a meaning: "targeted for deactivation by the identity sync; finished by P2").

---

## 5. Tests

- **Model layer** (`tests/unit/manage/test_ldapsync_*.py`, `test_purge.py`,
  `test_lifecycle.py`): every rule above via factories `make_user`, `make_institution`,
  `make_organization`, `make_user_institution`, `make_user_organization`,
  `make_adhoc_group`, `make_gid_allocation`, `make_email_address`; add
  `make_adhoc_group_tag`, `make_phone`, `make_adhoc_system_account_entry`.
- **HTTP layer** (`tests/api/test_ldapsync_api.py`): 401 realm literal; 403 without
  `ROLE_API_ADMIN`; config key fails closed; 400 envelope; no-404 rule; bare-`?since`;
  both gid param names; CSRF exempt on writes. Route-level writes COMMIT outside the
  test SAVEPOINT, so use the `committing` fixture (`tests/xras_helpers.py:92`) or patch
  the manage function; key fixture copied from `xras_keys` with `ROLE_API_ADMIN`.
- **Golden comparison** (`tests/integration/`, opt-in via env): GET each of the seven
  collections from test-sam (legacy) and from webdev on the same snapshot; normalize
  (sort by key, drop `gidAllocation` timestamps, drop `status` times); diff is empty.
- **Replay**: `scripts/ldapsync_replay.py` posts a captured `syncd/sam-log.jsl` (the
  exact PUT stream the daemon wrote; ask George for one day) at webdev, exercising
  every write path with production-shaped payloads.
- **Postgres**: run every purge on the :5434 dual backend; FK cascade behavior differs
  from MySQL.

## 6. Verification (end to end)

1. `pytest tests/unit/manage tests/api/test_ldapsync_api.py tests/unit/gates -q`.
2. `docker compose up webdev --watch`, then without and with credentials:
   ```bash
   curl -i http://localhost:5050/api/protected/admin/ldapsync/status            # expect 401, realm="Realm"
   curl -i -u admin:<pw> http://localhost:5050/api/protected/admin/ldapsync/status
   ```
3. Golden diff against test-sam for all seven collections.
4. Replay one day of `sam-log.jsl`; compare row counts in `user_institution`,
   `user_organization`, `email_address`, `adhoc_group`, `adhoc_group_tag`,
   `adhoc_system_account_entry` before and after with legacy.
5. Cutover rehearsal on samuel-dev: a test daemon instance with `SAM_URL` pointed at
   `https://samuel-dev.k8s.ucar.edu`, `--test-connections` first, then one add-file
   replay; watch with `scripts/cirrus_watch.sh --env dev`.
6. Production cutover: flip `SAM_URL` in `prod/.env`, restart the syncd container
   (`podman stop`/`start`; the systemd unit is disabled and `podman-compose up` recreates
   the other containers), watch the first pass. Rollback is the same variable.

## 7. Risks (ranked)

| # | Risk | Mitigation |
|---|---|---|
| 1 | Realm string or role row mismatch: every request ends in a 401, a 300 s pause and a replay, and a fresh start aborts | byte-exact header test; curl rehearsal without `-u` |
| 2 | Hard-delete purges on Postgres cascade differently | run each purge on :5434 |
| 3 | Email orphan removal changes `sam_merge_targets` inputs (account requests, XRAS worklist) | expected for a mirror; log counts per PUT |
| 4 | `deactivate` now written on the new side; pending users stay `is_active` until P2 | document; unchanged from legacy behavior |
| 5 | projectGroup member rule diverges from `directory_access.group_populator` (grace on allocation vs project) | test both on one fixture |
| 6 | Epoch-ms from naive-Mountain across a DST boundary | one boundary test |
| 7 | Limiter tier during an add-file replay | measure; exempt or raise the M2M tier for the blueprint |
| 8 | A 400 loses the item from the daemon's in-memory copy until the next full dump | legacy behavior; log every 400 with the payload key |

## 8. Effort

One PR per track; LOC estimates in this repo run about 3x.

| Track | Estimate |
|---|---|
| P1a reads, auth, contract, golden test | 3–4 days |
| P1b institution / organization / gidAllocation PUTs + purge | 3 days |
| P1c user PUT + group PUT | 4–5 days |
| P1d collabexpiryupdates | 2 days |
| P2 lifecycle finish + #3 restore + aliases | 3 days, blocked on client fixes |

## 9. Open questions

1. `auto-renewed-project`: reproduce legacy's `"CN".contains(code)` substring rule or
   fix it to `code in ('C', 'N')`? Which facilities does it actually name?
2. Fix the client's `since` / `lastModifiedDate` mismatch (George) so `projectGroup`
   becomes incremental, or keep serving the full list (cheap at ~7k projects)?
3. Phones: mirror them as legacy does even though nothing downstream reads `phone`?
4. Confirm with George what the nightly lifecycle job actually does today. It fires
   sixty times per slot on a stale in-memory copy and each child exits within seconds;
   the directory replica shows no `x-ucar-source: SAM:*` value, so either the children
   die before `ldapmodify` or fdbstage does not forward the writes. fdbstage's logs, or
   one run of `syncd -l` in the main process with the log open, settle it.
5. Confirm on prod the `admin` `api_credentials` row with `ROLE_API_ADMIN` (needs the
   SAM database). The `SAM_AUTH_admin` secret exists in the daemon container's parmdb
   store, fed from the `sam.parm` secrets file (Ben owns deploy mechanics).
6. Fresh Tomcat access logs from after 2026-07-16 would give the real call rates and
   the `add`-file replay size; worth pulling from sam.ucar.edu before P1c. The daemon's
   own log shows about 1,000 `PUT returned` lines in September 2026, plus a full
   collection download at each daemon start.

---

## Appendix A — container zoo survey

`legacy_sam/container_zoo/README~`: "Repos for all the helper / synchronizers SAM
uses." Every directory is its own clean git clone (`github.com/NCAR/*`, except
`amieclient` from `XSEDE`). Nothing in the zoo serves the SAM API; `sam-app` is host
configuration, not the legacy Java webapp.

| Repo | Purpose | Language | Last commit | Calls SAM? |
|---|---|---|---|---|
| sweet | Base Debian image and shell utilities shared by every NCAR SWEG container | Bash | 2026-05-29 | no |
| sweet-pl5 | Shared Perl 5 modules (HTTP client, URL, SFTP, config, logging), `http-get`/`http-put` | Perl | 2026-08-10 | library only |
| sftp-server | Minimal SFTP-only container; leftover from the retired `acct-sam-etl` | Bash | 2023-06-23 | no |
| amieclient | XSEDE's AMIE REST client library (upstream, v0.6.1) | Python | 2023-04-24 | no |
| amiemediator | Generic AMIE packet daemon driving a pluggable ServiceProvider | Python | 2026-03-04 | no |
| amie-sam-mediator | NCAR's ServiceProvider plug-in: fulfills AMIE packets via SAM and PeopleSearch/LDAP | Python | 2026-08-03 | **yes** — `/api/protected/amie/v1/*` |
| ucarldap | UCAR LDAP schema and config, base image, LDIF-log processing scripts | LDIF/Bash/Perl | 2026-07-16 | no |
| sam-idms-ldap | OpenLDAP replica of the central directory that feeds the transformer | Shell/LDIF | 2026-08-10 | no |
| sam-ldap-transformer | Turns slapcat dumps and audit logs into `.jsl` files for syncd | Bash/Perl | 2026-08-10 | no |
| sam-ldap-syncd | Pushes IDMS/LDAP state into SAM; pulls lifecycle and project-group state back to LDAP staging | Perl | 2026-08-27 | **yes** — `/api/protected/admin/{ldapsync,*Purge*,userlifecycle}/*` |
| sam-app | Host config for sam-app.ucar.edu: `REPOS` build order, `PROD_SERVICES` = amie-sam-mediator + sam-ldap-sync; `acct-sam-etl` retired 2026-05-14 | Bash/text | 2026-07-16 | no |

## Appendix B — legacy REST families vs port status

Caller identities are the Tomcat client-IP column of the audited logs.

| Family | 30-day hits | Caller | New side | Status |
|---|---|---|---|---|
| SSG/sysacct: fairShareTree/v3, wallClockExemption, queue, directoryaccess, groupstatus, dasg/diskquota | 20,920 / 11,803 / 8,697 / 2,839 / 10 / 7 | scheduler hosts (python-requests), `128.117.183.176` (Mojolicious Perl) | `/api/v1/{fstree_access,wallclock_exemption,queue,directory_access,project_access,disk_quota}` | ported (`docs/apis/SYSTEMS_INTEGRATION_APIs.md`) |
| XRAS `/api/xras/v1/*` | 3,744 | `18.223.62.77` (Ruby broker) | `/api/xras/v1/*` | ported, byte-shape frozen |
| AMIE `/api/protected/amie/v1/*` | 9,249,045 (99.6%) | `128.117.177.140` (amie-sam-mediator on sam-app; its container and systemd unit were not running there on 2026-09-25) | none | **not ported, live consumer** |
| LDAP sync, purge, userlifecycle | ≤ 3 each (pre-dates the consumer) | sam-ldap-syncd since 2026-08 | none | **not ported, live consumer — this document** |
| PeopleDB `peoplesearch/sync/*` | 5,195 | `128.117.224.29` (Apache-HttpClient) | none | superseded; controllers deleted from legacy 2026-07-04 |
| HEUV `/api/protected/heuv/v1/*` | ~2,700 | `54.85.201.121` (Ruby, AWS-hosted portal) | none | not ported, consumer known only by IP |
| Ingest/repair PUTs, refresher, `log/{level}`, `usernameChange`, admin reports, `error/*` | 0 | — | own `POST /api/v1/charge-summaries/*` (different shape) | stale |

Errata in the audit docs worth knowing: `stale_apis.md` marks three AMIE PUTs stale
that the logs show hit (`transactions/{c}/{tx}/state/cleared` 15–24,
`tasks/.../{task_name}/{qual}` 2, `tasks/.../{task_name}` 3); it cites
`PUT ldapsync/gidAllocations` at `user/api/…` while the source has `/gidAllocation`
at `service/idservice/api/SyncLdapController.java`; `dasg/diskquota` (7 hits) is
absent from the usage tables; and `apis.md` §1 still describes the deleted PeopleDB
controllers.
