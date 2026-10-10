# HEUV API port: `/api/protected/heuv/v1` on SAMuel

**Status:** in progress 2026-10-10 (planned 2026-10-10, Ben).
**Branch:** `heuv-api` from `origin/staging`; one PR to `staging`, one commit per Progress step, every commit green.

On approval of this plan (planning session, 2026-10-10) this file is committed verbatim as
`docs/plans/HEUV_API_PORT.md` on branch `heuv-api` in a docs-only PR against `staging`; the
implementing session starts from that PR with the kickoff prompt in §14 and ticks the checklist
as each step lands. Every `file:line` below was read on 2026-10-10 against `origin/main`
`439601a6`; **re-verify each before relying on it** (`grep -n` the symbol), and read `CLAUDE.md`
first (legacy-API rules, "Output shaping", §7-§9, Comment Budget, Testing).

## Progress

- [x] 0. Re-verify: `git fetch && git switch heuv-api && git rebase origin/staging` (the branch exists from the docs-only PR); rerun the probe (§3.3) into a fresh scratch dir to refresh the golden shapes; rerun the log tally (§3.2); diff against Appendix A and this doc's traffic tables; set **Status** to `in progress` with the date. *(2026-10-10: probe matches Appendix A; tally identical to window C, no rotation since 10-09. Note: the probe's file names collide on a case-insensitive filesystem, so `scsg0001`/`HPC` variants need distinct names.)*
- [x] 1. Lift the `/api/protected` wire kit out of `src/webapp/api/ldapsync/__init__.py` into `src/webapp/api/protected.py`; ldapsync imports it. Proof: `pytest tests/api/test_ldapsync_api.py` unchanged and green.
- [x] 2. Lift the legacy account-status/threshold calculator out of `src/sam/queries/fstree_access.py` into `src/sam/queries/account_status.py`; fstree imports it; add Waiting / No Allocation / Disabled. Proof: fstree tests green, and `get_fstree_data` for all 23 configurable resources (plus `None`) byte-identical on the local DB before/after (the parity tool compares legacy with a *deployed* SAMuel, so it cannot see an uncommitted lift).
- [x] 3. Lift the API-key test fixtures (`xras_keys`, `ldapsync_keys`) into one `role_keys(monkeypatch, {user: [roles]})` helper in `tests/xras_helpers.py`; both adopt it. Proof: xras + ldapsync tests green.
- [x] 4. Schemas `src/sam/schemas/heuv.py` (+ `UtcDateTime` in `src/sam/schemas/wire.py`), exported from `schemas/__init__.py`.
- [x] 5. Queries `src/sam/queries/heuv.py` + `User.legacy_full_name`. *(As built: `Project.search_by_pattern` gains `limit=None`, `escape=`, `member_username=`; factories `make_account_user`, `make_default_project`, `make_access_branch`. Local DB vs the 2026-10-10 legacy probe: 10 of 14 bodies byte-identical; the rest differ only by F3 order or snapshot data. `group` measured 248 ms locally (legacy 161): the `username=` bind pick stands.)*
- [x] 6. Blueprint `src/webapp/api/heuv/` registered in `src/webapp/run.py`, limiter-exempt. *(The HTTP half of the step-7 tests lands here: `tests/api/test_heuv_api.py`, 27 tests, MySQL and Postgres.)*
- [x] 7. Tests `tests/api/test_heuv_api.py` + perf baseline `heuv_report_usage` in `tests/perf/baselines.json`.
- [ ] 8. Parity `utils/parity --api heuv` (both directions, named normalizations); run against samuel-dev; write the ruleset-divergence statements for Ben (§7) and STOP for his decision before changing rules. *(2026-10-10: tool built — `utils/parity/heuv.py`, credential `SAM_HEUV_USER`/`SAM_HEUV_PASS` because `SAM_LEGACY_USER=ssg` gets 403 on HEUV. Dry run, legacy prod vs a local server of this branch on the local snapshot, 42 projects / 60 users: 12 of 14 route groups pass; the 2 failures and the only nonzero §7 counter are charge totals and project count, all explained by the older local data. samuel-dev run 2026-10-10 on `sha-7470cc8`: results and statements in §7.1; waiting on Ben's decisions.)*
- [ ] 9. Docs (§11), ledger entry, deck lines, CLAUDE.md net-zero edit.

---

## 1. Context

Legacy SAM (Java/Spring on `sam-tomcat`, `https://sam.ucar.edu`) serves a read-only researcher-portal
feed at `/api/protected/heuv/v1/*` ("High End User View"). Spring role `ROLE_API_HEUV`
(`legacy_sam/src/main/resources/spring/security-config.xml:33`), Basic auth against the shared
`api_credentials` table, roles via `role_api_credentials -> role`. It is listed "not ported" in
`docs/plans/LDAP_SYNC_API.md:532` and the deck. SAMuel will serve the **same path, auth and bytes**,
so each caller repoints by changing the host only.

## 2. Decisions log (2026-10-10, Ben)

| # | Decision |
|---|---|
| D1 | **Scope:** the 7 used routes (`defaultproject`, `group`, `access`, `assignedproject`, `search/projcode`, `report/project/{p}`, `report/usage/project/{p}`) plus the cheap unused reads (`userrolelogin`, `wallclockexemption`, `assignedresource`, `project/{p}/hierarchy`, `access`, `access/resource/{r}`). |
| D2 | **Not ported** (deliberately; 404/405 on SAMuel, listed in `docs/apis/HEUV_API.md`): the 3 PUTs (`user/{u}/access`, `/defaultproject`, `/group`) and the heavy reports (`hierarchicalusage`, `monthlycomputeusage[detail]`, `usercomputeusage[detail]`, `allocationchange`, `dataholdings[detail]`). Zero hits in every log window. |
| D3 | **Fidelity:** keep the visible contract; fix legacy bugs; every difference is documented in §6 and pinned by a **named parity normalization rule**. Anything not named fails parity. |
| D4 | The `admin` API credential holds `ROLE_API_HEUV` on prod for the whole port (Ben granted it 2026-10-10; he will not revoke it until cutover completes). Revoke SQL in §12. |
| P1 | Policy (CLAUDE.md "Output shaping"): a new legacy-shaped endpoint declares output with Marshmallow `data_key` schemas, never a hand-built dict. |
| P2 | Policy: parity comparators check **both** directions. The existing ones are mostly legacy-subset-of-new (`utils/parity/comparators.py:304-311` admits it) and hid real surplus bugs. |
| P3 | Policy: when a legacy-vs-new ruleset divergence shows up, write "Old returns X when (1)(2)(3) / New returns Y when ... / difference + counts" for Ben and **stop**; he decides. Do not silently adopt either rule. |
| P4 | Policy: internal-API error messages may be more informative than legacy, but never leak driver text or tracebacks. |
| P5 | Policy: SAMuel must not break legacy (shared DB). HEUV is read-only, so this only touches the shared `api_credentials` / `role_api_credentials` rows: SAMuel reads them, never writes. |

## 3. Evidence and how to re-capture

### 3.1 Traffic (Tomcat access logs; GET only; 200 unless noted)

| Route | A: 05-14..06-12 | B: 08-14..09-10 | C: 09-11..10-09 | avg ms (C) | avg bytes (C) |
|---|---:|---:|---:|---:|---:|
| `report/usage/project/{p}` | 784 | 1,197 (+18 400, +6 500) | 1,197 (+6 400, +3 500) | 291 | 2,088 |
| `report/project/{p}` | 403 | 490 | 522 | 128 | 2,090 |
| `user/{u}/assignedproject` | 384 | 520 | 487 (+1 400) | 137 | 1,024 |
| `user/{u}/group` | 366 | 510 | 500 (+3 400) | 161 | 395 |
| `user/{u}/defaultproject` | 366 | 510 | 500 (+2 400) | 121 | 15 |
| `user/{u}/access` | 366 | 510 | 500 (+3 400) | 135 | 156 |
| `search/projcode` | 19 | 12 | 8 | 103 | 120 |
| the other 17 routes | 0 | 0 | 0 | | |

- Query strings ever seen (A+B+C): `thresholdlimited=` on every `assignedproject` call (`false` in all but one), `fragment=` (14) and `fragment=&username=` (6) on `search/projcode`. **No caller ever sent `reportDate`, `resourcename` or `active`.**
- 500s: 9 across B+C, all NPEs on `report/usage` (C's 3 were `UCUB0160` x2 and `UPSU0047`); window A's access log was not tallied by status, but `legacy_sam/sam-tomcat_logs/sam.log.2026-06-04:217` holds the same NPE trace (UCUB0160). 400s are unknown usernames/projcodes.
- Clients (C): `54.85.201.121` Ruby 3,440 (AWS portal, ~92%); `52.11.85.75` python-httpx/0.28.1 188 (AWS, `report/usage` only, ~6-13/day); NCAR-internal Ruby `128.117.65.128` 79, `128.117.66.170` 7; residential Ruby 18. Window B also saw `128.117.70.59` Ruby 69 and residential `73.229.225.183`/`73.153.103.177`. ~125 calls/day. 147 distinct users and 141 projects in A.
- The gap 06-12..08-14 is not recoverable. Older table: `legacy_sam/doc/apis/apis_30day_usage.md:101-112`.
- Latency is Tomcat `%D` as logged (ms); bytes are the response-body count after chunking.

### 3.2 How the tally was measured (re-run at step 0)

On `sam-tomcat.ucar.edu`: `/var/log/tomcat/tomcat-sam_access.log` is a **named pipe feeding syslog; never read it** (reading steals entries). `/tomcat/tomcat-sam/logs/access.log` is the 0-byte rotation stub. The readable data is `/tomcat/tomcat-sam/logs/access.log-YYYYMMDD.gz` (30 dailies) and the NetApp snapshots `/tomcat/tomcat-sam/logs/.snapshot/weekly.*/access.log-*.gz` (oldest `weekly.2026-09-13_0015`, reaching back to 2026-08-14). Window A is the repo copy `legacy_sam/sam-tomcat_logs/access.log-*.gz`. Lines carry a syslog prefix, so the fields are: `$5` client IP, `$12` path, `$14` status, `$15` bytes, `$16` ms, `$17` UA. Aggregate on the server; bring back counts only.

```bash
ssh sam-tomcat.ucar.edu 'cd /tomcat/tomcat-sam/logs && zcat access.log-*.gz \
  | grep -F "/api/protected/heuv/" \
  | awk '"'"'{ split($12,p,"?"); r=p[1]; sub(/.*\/v1\//,"",r);
           gsub(/user\/[^\/]+\//,"user/U/",r); gsub(/project\/[^\/]+$/,"project/P",r);
           n[r" "$14]++; if ($14==200) { lat[r]+=$16; by[r]+=$15; c[r]++ }
           ua=$17; gsub(/"/,"",ua); cl[$5" "ua]++ }
     END { for (k in n) print "ROUTE",k,n[k];
           for (r in c) printf "LAT %s ms=%.0f bytes=%.0f n=%d\n",r,lat[r]/c[r],by[r]/c[r],c[r];
           for (k in cl) print "CLIENT",k,cl[k] }'"'"' | sort'
# Window B: replace `access.log-*.gz` with `.snapshot/weekly.2026-09-13_0015/access.log-*.gz`.
```

### 3.3 Probe recipe (refresh the golden shapes)

The key lives in `containers/ldap-pipeline/secrets/sam.parm` as `SAM_AUTH_admin=<password>`; Basic user `admin`. Load it into a variable, never echo it.

```bash
PW=$(sed -n 's/^SAM_AUTH_admin=//p' containers/ldap-pipeline/secrets/sam.parm)
B=https://sam.ucar.edu/api/protected/heuv/v1; OUT=$SCRATCH/heuv; mkdir -p "$OUT"
for r in user/benkirk/defaultproject user/benkirk/group user/benkirk/access \
  'user/benkirk/assignedproject?thresholdlimited=false' 'user/benkirk/assignedproject?thresholdlimited=true' \
  'user/benkirk/assignedresource?thresholdlimited=false' user/benkirk/userrolelogin \
  user/benkirk/wallclockexemption 'user/benkirk/wallclockexemption?active=true' \
  'search/projcode?fragment=SCSG' 'search/projcode?fragment=scs&username=benkirk' 'search/projcode?fragment=zzzz' search/projcode \
  report/project/SCSG0001 report/project/scsg0001 report/usage/project/SCSG0001 project/NCIS0001/hierarchy \
  access access/resource/hpc access/resource/HPC access/resource/Derecho \
  user/nosuchuserxx/group user/nosuchuserxx/wallclockexemption report/project/NOSU9999 report/usage/project/NOSU9999; do
  f=$(echo "$r" | tr '/?&=' '____'); curl -sS -u "admin:$PW" -D "$OUT/$f.hdr" -o "$OUT/$f.out" "$B/$r"; done
```

Compare with `python3 -c 'import json,sys; ...'` on key order (`json.load` preserves it) and on raw bytes. Legacy response headers to expect: `content-type: application/json` (no charset) on 200/400, chunked; 404 has `content-length: 0`; the wallclock 500 is `text/html;charset=utf-8`.

## 4. Wire contract (all routes)

| Item | Rule |
|---|---|
| Mount | `/api/protected/heuv/v1`, `strict_slashes=False`, GET only; limiter-exempt like ldapsync (`src/webapp/run.py:459-460`: `limiter.limiter.exempt(bp)` then `register_blueprint(..., url_prefix=)`). |
| Body | compact JSON via `compact_json` (`src/webapp/api/helpers.py:19-22`: `separators=(',',':')`, `ensure_ascii=False`, `sort_keys=False`), `Content-Type: application/json` (no charset), nulls included, keys in legacy declaration order (= Marshmallow field order with `data_key`). Legacy serializes getter-only properties (`userCount`, `thresholdLimited`, `label`) after the field-backed ones; the orders in §5 already reflect that. |
| Auth | `heuv_api_required = partial(login_or_token_required, roles=('ROLE_API_HEUV',), deny=_deny)` modeled on `src/webapp/api/ldapsync/__init__.py:58-60` and `api/xras/__init__.py:62-64`. `login_or_token_required(permission=None, *, roles, deny)` is `src/webapp/utils/api_auth.py:228-372`; `roles=` closes the session path (any request without `Authorization` gets 401, :352-355), a key lacking the role gets 403 (:334-341). DB keys come from `ApiCredentials.as_api_key_map` (`src/sam/security/roles.py:92-110`) carrying the legacy role names, so the prod `heuv` credential (id 10) and `admin` work unchanged. Under `RBAC_SOURCE=db` a roles-only route with no Permission is authorized by the role link alone (`docs/plans/LDAPSYNC_DEV_SHADOW.md:20-25`). `_deny` sets `WWW-Authenticate: Basic realm="Realm"` on 401 only (ldapsync :51-55). |
| 400 | `{"errorMessage":"<javaMethod>.<param>: <text>"}`. Texts (`legacy_sam/src/main/resources/MessageResources_en_US.properties:79-87`): `Project X does not exist.` / `Project X is not active.` / `Resource X does not exist.` / `Username X does not exist.` — X echoes the **path value as sent** (`${validatedValue}`), not the canonical one. Method/param prefixes per route in §5. **Do not** reuse ldapsync's `validation_message` text (`ValidationException:\n <msg>`, :46-48); the shared kit takes the message as given. |
| 500 | `{"errorMessage":<msg or null>}` (`legacy_sam/.../presentation/rest/ApiController.java:44-58`). On SAMuel: a fixed string, never driver text (P4); use the `Exception` handler pattern at ldapsync :84-89 minus the `{Type}: {msg}` echo. |
| 404 | Only `access/resource/{r}` with no exact match: empty body, no content-type. Do **not** use `register_error_handlers` (`api/helpers.py:46-72`) — it rewrites 404 to `{"error":"Resource not found"}`. |
| Lookup case | Legacy's `@UsernameExists/@ProjcodeExists/@ResourceNameExists` are case-insensitive (`ignoreCase()` in `HibernateUserRepository:54`, `HibernateProjectRepository:55`, `HibernateResourceRepository:34`); responses echo the DB's canonical value. On SAMuel: `Project.get_by_projcode` upper-cases (`src/sam/projects/projects.py:142`), but `User.get_by_username` is an exact `==` (`src/sam/core/users.py:125`) — case-insensitive only through MySQL collation, **not on Postgres (samuel-dev)**. Use `func.lower(User.username) == username.lower()` (or `ci_like`) in `queries/heuv.py`; same for resource and access-branch names. |
| DBs | Must run on MySQL (prod) and Postgres (samuel-dev, `make refresh-dev`). ORM only; no `DATE()`, `NOW()`, `LIKE` collation assumptions; use `sam_now()` / `datetime.now()` (naive Mountain, CLAUDE.md §1). |
| Dates | `yyyy-MM-dd` fields are Joda `DateTimeFormat.forPattern("yyyy-MM-dd")` in the JVM default zone = Mountain (`legacy_sam/.../util/DateUtil.java:38,96-98`; nothing in legacy sets `America/Denver`). Naive DB value -> `.strftime('%Y-%m-%d')` matches (`sam.dates.format_ymd`, `src/sam/dates.py:31`). The two `yyyy-MM-dd HH:mm:ss` fields (assigned*) are Jackson `@JsonFormat(pattern=...)` with **no timezone** -> rendered in **UTC** (verified: DB `2026-06-15 11:22:32` MDT -> `"2026-06-15 17:22:32"`). Convert naive Mountain -> UTC DST-aware with `ZoneInfo('America/Denver')` (`sam.dates.SERVER_TZ`, `src/sam/dates.py:13`; `sam.fmt.naive_local_to_utc`, `src/sam/fmt.py:63`). |
| Rounding | Java `Math.round` = `floor(x + 0.5)` (half-up), never Python `round()` (half-even). One helper `java_round()` in `queries/heuv.py`. |
| Amounts | Legacy sums allocation amounts in `Float` (float32) including transaction replay; SAMuel uses exact Decimal/float64 (fix F4). |

## 5. Per-route spec cards

Legacy source root `J = legacy_sam/src/main/java/edu/ucar/cisl/sam`. "Window" = legacy `AccountUser.isAssigned(now)` (`J/user/domain/model/AccountUser.java:35`): `start <= now AND (end IS NULL OR end > now)` on raw timestamps. SAMuel's `AccountUser.is_active` / `unended()` use `>=` (§7 b).

### 5.1 `GET user/{u}/defaultproject` — `getUserDefaultProjects.username`

| | |
|---|---|
| Java | `J/user/api/UserDefaultProjectController.java:35`; `J/user/UserConfig.java:350-353` (singleton `Params` race, fix F6) |
| Shape | `[{"username","resourceName","projcode"}]` |
| Rules | every `default_project` row for the user, any resource/project state; sort `resourceName`. Local DB: 210 rows, all `HPSS` -> nearly every answer is `[]` (avg 15 bytes). |
| SAMuel | `DefaultProject` (`src/sam/projects/projects.py:1019-1046`; `User.default_projects` `core/users.py:82`) — first reader. |
| Tests | user with 0 rows -> `[]`; 2 rows sorted; unknown user 400 text; lowercase username -> canonical. |

### 5.2 `GET user/{u}/group` — `getUserGroups.username`

| | |
|---|---|
| Java | `J/user/api/UserGroupController.java:35`; `J/user/api/UserGroupGenerator.java:47-77`; `J/infrastructure/domain/repository/HibernateHpcGroupRepository.java:27-60`; SQL `userDirectoryAccessHpcGroups` `legacy_sam/src/main/resources/hibernate/jdbcQuery.xml:599-631`; `HpcGroup.java:7-8` |
| Shape | `[{"username","groupName","unixGid","primary","project","projcode"}]` |
| Rows | synthetic `ncar`/1000 **first**, then SQL rows sorted by `groupName` (Java natural order, case-sensitive). Project branch: account_user window AND `(al.end_date + 90 DAY) > NOW()` (NULL end -> dropped; no `al.start_date`, no `deleted` checks) AND `p.active AND p.unix_gid IS NOT NULL` AND `r.configurable` AND resource in an access branch AND `u.active`; emits `LOWER(projcode)`, `unix_gid`. UNION adhoc: `adhoc_system_account_entry` joined on `access_branch_name = ab.name`, same account/allocation/project(active, no unix_gid test)/resource gates, `ag.active`. Inactive user -> only `ncar`. |
| Fields | `primary = users.primary_gid == gid` (NULL primary_gid -> legacy NPE 500; 0 such users locally); `project = name != 'ncar' AND a project with that code exists (case-insens.)`; `projcode = upper(name)` when project else null. |
| SAMuel | `get_user_group_access(session, username=, include_projects=True)` (`src/sam/queries/lookups.py:124-221`) = ungated adhoc stage (:160-186) + `group_populator()` (`src/sam/queries/directory_access.py:221-327`, gated at :290-299, `ACCESS_GRACE_PERIOD=90` :42, `grace_cutoff` :47). `DEFAULT_COMMON_GROUP='ncar'` / `DEFAULT_COMMON_GROUP_GID=1000` (`src/sam/core/groups.py:17-18`). **Measure**: `group_populator` is population-wide; if HEUV `group` p50 exceeds legacy's 161 ms, add a `username=` bind to its SQL (ledger item -> pick). Watch §7 a. |
| Tests | ncar first + `primary` true for gid 1000; a project group and an adhoc group; inactive user -> `[ncar]`; unknown -> 400. |

### 5.3 `GET user/{u}/access` — `getUserAccessibleResources.username`

| | |
|---|---|
| Java | `J/user/api/UserAccessibleResourceController.java:35-53`; `J/user/UserConfig.java:310-330`; `J/user/settings/query/DefaultUserCustomizableResourceSelector.java:20-33`; `J/user/api/CustomizedAccessibleResourceTransformer.java:25-50` |
| Shape | `[{"username","resourceName","resourceType","homeDirectory","shellName"}]` |
| Rows | resources of the user's windowed `account_user` rows (no project/allocation/deleted/user-active checks) with `resources.configurable=1` -> their access branches -> keep a branch whose **name is also a resource name** (case-insens.) -> that resource must be commissioned today (day granularity, NULL decommission = open) and have `default_resource_shell_id`. Result is a Java `Set` -> order nondeterministic (fix F3: sort by `resourceName`). Locally the branches are `hpc`, `hpc-data`, `hpc-dev`; only `hpc` and `hpc-dev` exist as resources -> at most two rows. |
| Fields | `homeDirectory` = `user_resource_home` override else `default_home_dir_base + "/" + username`; `shellName` = `user_resource_shell` override else the resource's default shell name else null. |
| SAMuel | `AccessBranch` / `AccessBranchResource` (`src/sam/security/access.py:9,30`), `UserResourceHome` / `UserResourceShell` (`src/sam/core/users.py:833,858`), `Resource.configurable/default_home_dir_base/default_resource_shell_id` (`src/sam/resources/resources.py:40,55,56`). `queries/shells.py:active_login_resources` filters by resource type HPC/DAV (:24-36) — **not** this rule; do not reuse. |
| Tests | override home + default shell; user with no configurable rows -> `[]`; sorted; unknown -> 400. |

### 5.4 `GET user/{u}/assignedproject?thresholdlimited=&resourcename=` — `getUserAssignmentByProject.username` / `.resourceName`

| | |
|---|---|
| Java | `J/user/api/UserAssignmentController.java:34-74`; `J/project/account/accountuser/query/UserAssignmentQuery.java:33-54`; `J/user/api/assignment/project/ResourceAssignment.java:11,14` |
| Shape | `[{"projcode","primary","title","resourceAssignments":[{"resourceName","startDate","endDate"}]}]` |
| Rows | one entry per windowed `account_user` row (duplicates possible: 2 duplicate (account,user) pairs exist locally); no project/allocation/deleted/resource checks (retired Cheyenne, Yellowstone rows appear); `primary = users.primary_gid == project.unix_gid`; sort projcode then resourceName. `thresholdlimited`: `true` -> `account.first_threshold IS NOT NULL`, `false` -> `IS NULL`, absent/other -> no filter (second_threshold ignored; differs from `Account.isThresholdLimited`). `resourcename` (optional, `@ResourceNameExists`, `equalsIgnoreCase`) -> 400 `Resource X does not exist.` when unknown. Dates `yyyy-MM-dd HH:mm:ss` **UTC** (§4). |
| SAMuel | plain ORM over `AccountUser`/`Account`/`Project`/`Resource`; **do not** reuse `serialize_projects_by_role` (`api/helpers.py:159-198`, different membership rules). |
| Tests | UTC rendering of a known MDT and a known MST timestamp; `true`/`false`/absent/`maybe`; primary flag; sorted; unknown user and unknown resourcename 400 texts. |

### 5.5 `GET user/{u}/assignedresource?thresholdlimited=` — `getUserAssignmentByResource.username`

Same rows as 5.4 pivoted: `[{"resourceName","projectAssignments":[{"projcode","primary","title","startDate","endDate"}]}]`, sorted resourceName then projcode (`J/user/api/assignment/resource/UserAssignmentByResourceGenerator.java:41-44`, `ProjectAssignment.java:13,16`). No `resourcename` param.

### 5.6 `GET user/{u}/userrolelogin` — `getUserRoleLogins.username`

`J/user/api/UserRoleLoginController.java:25`; `J/user/domain/repository/HibernateUserRepository.java:98-118`. Shape `["benkirk","csgteam",...]` = `[username]` (no active check) + usernames with `contact_person_upid = user.upid AND active`, ordered case-insensitively (113 users carry a contact_person_upid locally). Reuse `User.upid`, `User.contact_person_upid`, `User.is_active` would fold in `locked` — legacy tests `active` only; use `User.active == True` here and comment the exception as CLAUDE.md §5 does for statistics.

### 5.7 `GET user/{u}/wallclockexemption?active=` — fix F7 (legacy 500)

`J/user/api/UserWallclockExemptionController.java:21-26` (does **not** extend `ApiController`, so `@UsernameExists` failure -> Tomcat HTML 500); `J/user/wallclockexemption/query/WallclockExemptionQuery.java:51-55`; `J/user/api/UserWallclockExemption.java`. Shape `{"username","resources":[{"resourceName","queues":[{"queueName","exemptions":[{"active","startDate","endDate","hourLimit","comment"}]}]}]}`; `active` = `isActiveToday()` (day-inclusive); `?active=true|false` filters (other values ignored); `hourLimit = round(time_limit_hours)`; dates `yyyy-MM-dd`; sort resource, queue, startDate desc. SAMuel: `WallclockExemption` + `get_wallclock_exemption_data` (`src/sam/queries/wallclock_exemption_access.py:22`) is per-resource/active-only with a different shape — reuse the model, not the function. `username` echoes the path **as sent** (`UserWallclockExemptionGenerator` reads the query param, not the user). Error prefix on SAMuel: `getUserWallclockExemptions.username` (legacy's handler is misnamed `getUserDefaultProjects`; the 500 never showed a prefix, so this is a free choice — record it in §6 F7).

### 5.8 `GET search/projcode?fragment=&username=` — never 400

`J/project/api/ProjectSearchController.java:24-38`; `J/project/project/query/SearchByProjcodeFragmentQuery.java:33-58`. Shape `[{"projcode","title"}]`; `active=true`, `lower(projcode) LIKE '%frag%'` (unescaped `%`/`_`, fix F8), optional join to a windowed `account_user` for `username` (no user-active check; unknown username -> `[]`), `ORDER BY projcode`. No fragment -> all active projects (1,506 rows, 169,756 bytes live). SAMuel: extend `Project.search_by_pattern` (`src/sam/projects/projects.py:146-194`, `ci_like`, ordered) with a `member_username=` filter rather than `search_projects_by_code_or_title` (`src/sam/queries/projects.py:36-68`: matches titles, no ORDER BY).

### 5.9 `GET report/project/{p}` — `getReportProject.projcode` (exists only; inactive projects served)

| | |
|---|---|
| Java | `J/reporting/api/ReportProjectController.java:25-29`; `J/reporting/api/ReportProjectQuery.java:26-88`; `Project.java:162-164,594`; `Account.java:197-199`; `Allocation.java:72-81,421-427`; `User.java:591-605` |
| Shape | `{"projcode","title","leadUsername","leadName","adminUsername","adminName","abstractText","hierarchical","accounts":[{"resourceName","thresholdLimited","allocations":[{"startDate","endDate","active"}]}]}` |
| Rules | `hierarchical = parent_id IS NOT NULL OR has any child (active or not)`; **all** accounts (deleted and decommissioned-resource included: SCSG0001 shows 22 back to Lynx 2012) that have >= 1 non-future allocation, sorted resourceName; allocations with `start > today 00:00` dropped (deleted allocations included), sorted startDate desc; `endDate = min(end, resource.decommission_date)` (NULL end + NULL decommission -> legacy NPE 500, fix F5; 0 NULL ends locally); `active = start <= now <= end(23:59:59)`; `thresholdLimited = first OR second threshold set`; names = legacy `getFullName` = (nickname if non-blank else first) + middle + last, non-blank parts space-joined. Data oddity emitted as-is: SCSG0001 Stratus has start `2026-10-01` > end `2025-12-31`. |
| SAMuel | `Project.get_by_projcode`, `Project.lead/admin`, `Project.parent_id`, children; add `User.legacy_full_name` (neither `full_name` :513 nor `display_name` :519 matches). `abstractText` is the raw column (CRLF preserved). |
| Tests | hierarchical true via parent and via child; future allocation dropped; decommission min; `endDate` null (F5); name with nickname+middle; inactive project 200; lowercase projcode -> canonical. |

### 5.10 `GET report/usage/project/{p}[?reportDate=]` — `getProjectUsageReport.projcode` (exists AND active)

| | |
|---|---|
| Java | `J/reporting/api/UsageReportController.java:32-44`; `J/reporting/api/ProjectUsageReportQuery.java:28-53`; `J/reporting/api/usagereport/{ProjectUsageTreeFactory.java:27-44, ProjectUsageReportGeneratorFactory.java:12-18, AccountUsageReportVisitor.java:51-68, DataHoldingsAccountUsageReportVisitor.java:26-28, ThresholdLimitedAccountUsageReportVisitor.java:16-37,69,82, TreeProjectUsageFacade.java:38-40}`; `J/reporting/accountstatustree/query/DefaultProjectAccountDetailRepository.java:31-37`; `J/reporting/domain/model/DefaultAccountStatusCalculator.java`; `J/domain/AccountStatus.java`; `J/domain/allocation/DateBoundedAllocationAmount.java:40-44`; `J/accounting/domain/model/{CollectedNDayUsage.java, NDayUsagePeriod.java:50-93}`; `J/domain/UsageThresholdPeriod.java:61-69`; `J/util/DateRange.java:228-234`; `J/reporting/domain/repository/BatchedChargeQueriesRunner.java:137-165`; charges SQL `ReportingNamedQuery.xml:313-366`, holdings `:164-199` |
| Shape | `{"projcode","reportDate":"yyyy-MM-dd","accountReports":[...]}`. **AccruedCharges** row keys in order: `resourceName,resourceType,resourceUsageType,status,allocationStartDate,allocationEndDate,allocationPropagated,allocationAmount,usernames,totalCharges,adjustments,balance,thresholdReports,thresholdLimited,userCount`. **DataHoldings** row: `resourceName,resourceType,resourceUsageType,status,allocationStartDate,allocationEndDate,allocationPropagated,allocationAmount,usernames,totalHoldings,numberOfFiles,balance,userCount`. `thresholdReports` item: `period,percentLimit,allocationAmount,charges,percentUsage,label`. |
| Accounts | accounts with `creation_time <= reportDate AND project.active AND resource commissioned on reportDate AND allocations non-empty` (`account.deleted` ignored; decommissioned resources **excluded**, unlike 5.9). `resourceUsageType` = `DataHoldings` for DISK-type resources else `AccruedCharges`. Row order: Java HashMap -> nondeterministic (fix F3: DataHoldings rows then AccruedCharges, each by resourceName). |
| Per row | `resourceType` upper-cased; `status` from the calculator (Normal, Overspent, Exceed One Threshold, Exceed Two Thresholds, Expired, Waiting, No Allocation, Disabled, No Account; Overspent = amount < debit); `allocation*` from the allocation active on reportDate with amount/dates replayed from `allocation_transaction` rows created <= reportDate (NULL end -> NPE at `AccountUsageReportVisitor.java:66`, F5); `allocationPropagated = allocation.isInheriting()`; `usernames` = `ProjectAccountDetailDTO.getActiveUsers()` usernames, sorted (**re-verify the predicate** in that DTO; expect windowed account_user rows); `userCount = len(usernames)`. No active allocation: `allocation*` null, `totalCharges 0`, `adjustments 0`, `balance null`, `thresholdReports []`, `thresholdLimited false` (the "Expired" Laramie row in Appendix A). |
| Charges | `totalCharges = round(debit)`, `debit = charges + adjustments` over the **subtree** (project + all descendants, inactive included, created <= reportDate) with `DATE(activity_date)` in `[DATE(allocStart), DATE(reportDate)]` from `{hpc,dav,comp}_charge_summary` (comp adds `charges IS NOT NULL`) and `charge_adjustment` rows in effect in that range; `adjustments = round(debit - charges)`; `balance = round(amount - debit)`. Example: UTAM0027 Derecho 500000 - 367064 = 132936. |
| Thresholds | only on AccruedCharges rows whose status is one of the four "active" statuses (`thresholdLimited = status in {Normal, Overspent, Exceed One Threshold, Exceed Two Thresholds}`; so it can be true while 5.9's flag is false). Two items, periods 30 and 90, `label = f"{period}-Day"`. `percentLimit` = first/second threshold **only when both are set**, else null. `allocationAmount = round(period * amount / D)` with `D = inclusiveDays(start, end) - 1 = (date(end) - date(start)).days` (verified: 25,000,000 x 30 / 364 = 2,060,440; 500,000 x 30 / 596 = 25,168). `charges` = adjusted charges in `[max(startOfDay(reportDate) - period, startOfDay(allocStart)), endOfDay(reportDate)]` (period+1 days). `percentUsage = round(100 * charges / allocationAmount_unrounded)`; when the unrounded threshold allocation is exactly 0 legacy NPEs (F1); a one-day allocation gives `D = 0` -> `Infinity` -> `allocationAmount` 9223372036854775807 (document, do not reproduce: emit null, rule F1). |
| DataHoldings | `totalHoldings = round(TB)` and `numberOfFiles` from `disk_charge_summary` at the **latest `activity_date` <= reportDate** for that resource (`HibernateFileStorageRepository.java:48-58`), summed over the subtree; `balance = round(amount - totalHoldings)`. |
| reportDate | optional `yyyy-MM-dd`; honored by the query (`ProjectUsageReportQuery.java:41-45`) but the tree cache key is `(resource, projcode, treeLastModified)` (`DefaultProjectAccountTreeQuery.java:37-43`), so a warm cache serves today's tree regardless. No caller ever sent it (§3.1). Fix F9: 400 `{"errorMessage":"getProjectUsageReport.reportDate: reportDate is not supported."}`. |
| Lowercase | `report/usage/project/scsg0001` -> legacy NPE 500 (case-sensitive `IdCoupledTree.getNode`); fix F2 -> 200 canonical. |
| SAMuel | **one computation, no new sum over the summary tables** (CLAUDE.md "Allocation usage is one computation"): `build_user_projects_resources_batched(session, [project], state=read_model_rows_for(session, project))` (`src/sam/queries/dashboard.py:160`; `src/sam/queries/allocation_state.py:383`; read model `AccountAllocationState` `src/sam/summaries/allocation_state.py:17` with `allocated, used, self_used, adjustments, rolling_windows, is_inheriting, start_date, end_date, charges_by_type`); kernel `usage_anchor/anchored_charges/batch_charges` (`src/sam/accounting/calculator.py:177,195,151`), `sums_as_subtree` (`src/sam/base.py:335`); rolling windows `trailing_window_charges` (`src/sam/queries/rolling_usage.py:29`) / `get_project_rolling_usage` (:48); status from step 2. Legacy averaged 291-343 ms with a 23.6 s max; the first SCSG0001 probe took 2.6 s. Target: p50 < legacy; measure with the perf baseline. |
| Tests | key order per variant; Expired row nulls; thresholdLimited vs 5.9 flag; `percentLimit` both-set rule; F1 zero-amount null; F2 lowercase; F3 order; F9 reportDate 400; inactive project 400 `is not active.`. |

### 5.11 `GET project/{p}/hierarchy` — `getHierarchyOfProject.projcode` (exists AND active)

`J/project/api/ProjectHierarchyController.java:25`; `J/project/api/ProjectHierarchyQuery.java:26-61`; `HierarchicalProject.java:13`. Shape `{"projcode","parentProjcode","rootProjcode","children":[recursive]}`, from the **root** of the requested project's tree (root itself not filtered on active), children = active children in a `TreeSet` by projcode. SAMuel: `Project.get_root` (`src/sam/projects/projects.py:845-860`) + `children` filtered by `Project.is_active`. Circular trees -> legacy 500; SAMuel: guard with a visited set, 500 envelope.

### 5.12 `GET access` and `GET access/resource/{r}` — `getAccessibleResources`, `getAccessibleResource.resourceName`

`J/resource/api/AccessibleResourceController.java:26-39`; `J/resource/resource/query/AccessibleResourceQuery.java:29-33`; `BaseController.java:24`. List: configurable resources whose name is also an access-branch name (case-insens.), **no commissioned filter**, sorted by name; shape `[{"resourceName","resourceType","login","shells":[{"shellName","dflt"}]}]`, `login = shells non-empty`, shell order nondeterministic (F3: sort `shellName`). Single: `{r}` must pass `@ResourceNameExists` (unknown -> 400 `Resource X does not exist.`); exactly one case-insensitive match -> object, else **404 empty body** (`Derecho` exists but is not a branch -> 404).

## 6. Deliberate fixes (each is a named parity normalization rule)

| Rule | Legacy | SAMuel |
|---|---|---|
| F1 `usage.zero_threshold_alloc` | 500 NPE when the unrounded threshold allocation is 0 (also `Infinity` on a one-day allocation) | `percentUsage: null`, `allocationAmount: null` |
| F2 `usage.lowercase_projcode` | 500 NPE | 200, canonical projcode |
| F3 `order.sorted` | HashMap/Set order on `accountReports`, `access` rows, shells | DataHoldings then AccruedCharges each by resourceName; `access` by resourceName; shells by shellName |
| F4 `amount.exact` | float32 sums | exact; differences only past 7 significant digits, tolerance 1 unit after rounding |
| F5 `null_end_date` | 500 NPE (report/project endDate, usage allocationEndDate) | `null` |
| F6 `defaultproject.race` | shared singleton `Params` could serve another user's rows | gone by construction (no rule needed; listed for the record) |
| F7 `wallclock.unknown_user` | Tomcat HTML 500 | 400 envelope, prefix `getUserWallclockExemptions.username` |
| F8 `search.escape` | `%`/`_` in `fragment` are wildcards | escaped (`ci_like` escaping) |
| F9 `usage.reportDate_rejected` | honored only on a cold cache | 400 `reportDate is not supported.` |
| F10 `search.max_sample` | n/a | the parity client samples all-projects search once; no normalization |

**Kept as visible contract:** UTC rendering of assigned* dates; `thresholdlimited` first-threshold-only rule; retired resources in `assignedproject`; 400 (not 404) on unknown user/project; inactive projects in `report/project`; `report/usage` 400 on an inactive project; deleted accounts in `report/project`; `project` flag true for an adhoc group whose name is a projcode; `${validatedValue}` echo of the path as sent.

## 7. Ruleset-divergence watchlist (P3: statement + counts for Ben, then stop)

Ben, 2026-10-10: legacy bugs this port reproduces may get their own follow-on plan after the PR. Candidates so far: k (parent cascade hands a child No Account / No Allocation), j (deleted allocations count in legacy's usage tree), the one-day-shorter fstree divisor (g, §13 Q1), legacy's usage replay ignoring date-only ADJUSTMENT rows (§7.1, not reproduced).

| | Where SAMuel may diverge | How to measure at step 8 |
|---|---|---|
| a | **group**: `get_user_group_access` stage 1 (`lookups.py:160-186`) reads `adhoc_system_account_entry` with **no** dependent-account gate; legacy and `group_populator` (:290-299) require a windowed account_user on a configurable resource in that branch with `(al.end_date + 90d) > NOW()` and an active project. Both legacy and `directory_access` drop NULL-end allocations; `queries/ldapsync.py:255` keeps them (documented deviation at :248). | count users with new ⊋ legacy on `group`; list surplus `groupName`s; expected direction: SAMuel surplus. |
| b | **membership window**: legacy `end > now` (exclusive, `AccountUser.java:35`, `jdbcQuery.xml:601`, search query). SAMuel inclusive `>=`: `DateRangeMixin` (`src/sam/base.py:179-186`), `unended` (`src/sam/manage/__init__.py:133-135`), `projects.py:412,420`, `core/users.py:418`, `queries/users.py:104,348`, fstree SQL `:265-266`; exclusive `>`: `accounting/accounts.py:191`, `directory_access.py:84-86,141-144`, `queries/ldapsync.py:256-258`, `queries/user_lifecycle.py:105`. 799 account_user rows end at exactly midnight locally; only a row ending at the request second differs. | parity runs at `:30` past the hour; difference is 0 unless a membership ends during the run. Use the inclusive ORM predicate; note the spelling in the ledger. |
| c | **usage subtree**: legacy sums the project and all descendants (inactive included, `creation_time <= reportDate`) within **this** node's allocation window, charges window `[DATE(allocStart), DATE(reportDate)]` unbounded by allocation end; SAMuel's kernel picks subtree vs leaf per project via `sums_as_subtree()` (`base.py:335`) and anchors by (project, account). | for every sampled project compare `totalCharges`/`balance` per row; bucket differences by `hierarchical`; expect zero for leaves. |
| d | legacy "account active" (`Account.java:190`) ignores `account.deleted`; SAMuel `live_accounts` (`projects.py:393-396`) excludes deleted. 0 deleted accounts locally (`SELECT SUM(deleted) FROM account`). | count rows present in one side only on `report/usage`. |
| e | `report/project` includes deleted accounts and decommissioned resources; `report/usage` excludes decommissioned resources but ignores `deleted`. | row-set equality per route. |
| f | DataHoldings source: legacy = `disk_charge_summary` at the latest **resource-wide** snapshot `<= reportDate`, `SUM(bytes) / 1e12` (decimal TB, `ActivityDatedProjectFileStorage.java:75`), `CAST(SUM(number_of_files))`; SAMuel = `bulk_get_subtree_disk_capacity` (per-account current snapshot) with its `used_bytes / 1e12` and `file_count`, not its TiB figure. Holdings are reported even without an active allocation (assumption to confirm). | compare `totalHoldings`, `numberOfFiles` on all DISK rows; tolerance 1 after rounding. |
| g | **threshold divisor**: legacy `D = (date(end) - date(start)).days`; fstree `_compute_threshold_data` (`fstree_access.py:361-416`, :395) and `get_project_rolling_usage` (`rolling_usage.py:~241`) use `max((end - start).days - 1, 1)` — **one day shorter**. SCSG0001 Derecho: legacy 2,060,440 vs the fstree formula 2,066,115. The lifted calculator (step 2) must take the divisor as a parameter so fstree keeps its current output; whether fstree should also change is a P3 statement for Ben (fstree parity tolerance may have hidden this). | compare `thresholdReports[].allocationAmount` exactly; count mismatches. |
| h | `usernames` on usage rows: legacy (`DefaultProjectAccountDetailDTOTransformer:40-44`, SQL `accountUsersForResource`) = every account_user row on any of the project's accounts on the resource, **day-granular** window, no user-active or deleted test, duplicates kept, sorted. SAMuel implements exactly that. | set difference per row. |
| j | **deleted allocations**: legacy's usage tree classifies every allocation row, deleted included (no Hibernate `where`), so a renew-with-replace leftover can be the "active" one; `queries/heuv.py` uses `Account.live_allocations`. `report/project` keeps deleted rows (visible contract, §5.9). | count usage rows whose active allocation differs. |
| k | **parent cascade**: legacy hands a child the parent's non-Normal *charging* status, including No Account / No Allocation (live 2026-10-10: NCIS0014 and NCGD0071 `Data_Access` report `No Account` with their own active allocation, because the parent has no account there). Implemented as legacy; a candidate fix for Ben. | count rows whose status comes from an ancestor. |
| i | status precedence: `DefaultAccountStatusCalculator.java` vs the lifted `_compute_status` (`fstree_access.py:294-358`: Overspent > Exceed Two > Exceed One > Normal; None amount -> Normal) + lifecycle statuses from SQL (`:115-229`: Waiting/Expired/No Account). Disabled and No Allocation have no SAMuel source yet. | count status mismatches by (legacy, new) pair. |

### 7.1 samuel-dev parity results (2026-10-10, `sha-7470cc8`; P3 statements, Ben decides)

`--api heuv` (42 projects, 60 users): 12 of 14 route groups pass. Every `user/*` route,
`report/project`, `project/hierarchy`, `access` and `access/resource` is byte-identical
after the named fixes (F2, F3, F7 fired). Counters a, d, e, f, g, h, i are **0**. p50 ms
legacy / dev: `report/usage` 321 / 48, `report/project` 194 / 48, `group` 241 / **371** (the
`username=` bind pick). A 200-project `report/usage` sweep (663 rows) explains every other
difference as data, not rules:

| | Rows | Cause |
|---|---:|---|
| identical or within named fixes | 583 | |
| totalCharges short on dev, allocation older than the clone cutoff | 68 | `containers/sam-sql-dev/config.yaml` keeps 730 d of `comp_charge_summary`, 400 d of the rest; this also explains the 4 Overspent -> Normal flips (all 2022-2023 starts) |
| totalCharges and window charges both differ, dev a few hours ahead | 11 | dev lane posts its own charges hourly |
| legacy 500, port 200 with null threshold amounts | 2 | F1 (UPSU0047 is the 500 in the logs) |
| `search/projcode` all: legacy 1,506, dev 1,500 | 6 | activated or created on prod after dev's snapshot |
| totalCharges 162 vs 163, windows equal | 1 | rounding at .5 of a float sum; F4-like |
| **active allocation end date differs** | **1** | **statement below** |

**Statement (SVST0002 Casper).** Old: `report/usage` takes the allocation's dates and amount
by replaying `allocation_transaction` and ignores date-only ADJUSTMENT rows, so after
SAMuel's editor moved the end from 2027-10-30 to 2026-10-31 (two ADJUSTMENT rows,
transaction_amount 0) legacy still reports `allocationEndDate` 2027-10-30 and threshold
amounts 884 / 2,653. Legacy's own `report/project` says 2026-10-31. New: the allocation row
(2026-10-31; thresholds 6,250 / 18,750). Difference: 1 of 663 sampled rows; any allocation
whose dates SAMuel edited. Candidate for the follow-on list (legacy bug), not reproduced.

Not measurable on dev: §7 c on allocations older than the clone cutoff needs prod data
(a read-only `hpc-reader` run of this branch's query layer was refused by the session's
permission policy; Ben's call). §7 j (deleted allocations) and k (parent cascade) show no
sampled row; k is implemented as legacy.

## 8. Implementation notes per step

**Step 1 — wire kit** `src/webapp/api/protected.py`: move `json_response` (ldapsync :31-34), `empty_response` (:37-39), `error_response` (:42-43), `_deny`/`AUTH_REALM` (:28, :51-55), the `HTTPException` and `Exception` handlers (:78-89) behind a `register_protected_handlers(bp, *, five_hundred_text=)` function; ldapsync keeps `validation_message` and its `SyncValidationError`/marshmallow handlers locally. Byte-identical ldapsync output.

**Step 2 — calculator** `src/sam/queries/account_status.py` (as built): `charge_status(usage, amount, thresholds, windows, divisor)`, `lifecycle_status(...)`, `legacy_divisor` / `fstree_divisor`, `threshold_allocation`, `java_round`, the status constants. fstree passes `fstree_divisor`; HEUV passes `legacy_divisor`. Legacy precedence (`DefaultAccountStatusCalculator.java`, read 2026-10-10): No Account > (no active allocation) Waiting / Expired / No Allocation > **Disabled = project inactive** (not an account flag) > **Normal when `project.charging_exempt` and the resource is chargeable** (every non-DISK row; `DataHoldings.isChargeable()` is false) > the parent's non-Normal *charging* status (which can itself be No Account / No Allocation) > Overspent > Exceed Two / One > Normal. fstree implements neither the exemption nor a cascade of anything but the three charge statuses; HEUV composes the full chain in step 5. Proof: local byte-diff (Progress step 2).

**Step 3 — test helper**: `role_keys(monkeypatch, mapping, plaintext=None)` (as built; default XRAS_PW; ldapsync passes its own `ADMIN_PW`) in `tests/xras_helpers.py` wrapping the `api_auth._get_db_api_keys` patch used by `xras_keys` (`:167-168`) and `ldapsync_keys` (`tests/ldapsync_helpers.py:25-26`); keep both fixture names as one-line wrappers. `basic_auth(username, password)` is `tests/xras_helpers.py:137`.

**Step 4 — schemas** `src/sam/schemas/heuv.py`: one schema per shape in §5, `data_key` camelCase, declaration order = legacy, `allow_none=True` where legacy emits null, `dump_only`. Reference `src/sam/schemas/disk_quota.py:13-21`, `src/sam/schemas/ldapsync.py`, `src/sam/schemas/wire.py` (`EpochMillis` :24-38 is the pattern for the new `UtcDateTime(fields.Field)` that converts naive Mountain -> `"%Y-%m-%d %H:%M:%S"` UTC). Dates via `format_ymd`.

**Step 5 — queries** `src/sam/queries/heuv.py`: plain functions returning records/dicts for the schemas; nothing Flask. Reuse per §5; `User.legacy_full_name` property on `src/sam/core/users.py` next to `full_name` (:513) with a 1-line comment naming legacy `getFullName`. Nothing re-exported from `sam/queries/__init__.py` (gate `tests/unit/gates/test_layer_imports.py`).

**Step 6 — blueprint** `src/webapp/api/heuv/{__init__,user,project,report,access}.py` (package like `api/ldapsync/`): `heuv_api_required`, `bad_request(prefix, text)` building `{"errorMessage": f"{prefix}: {text}"}`, lookups helpers `_user_or_400(username, prefix)` etc. Register in `src/webapp/run.py` next to :459-460 with `url_prefix='/api/protected/heuv/v1'`, limiter-exempt. No caching to start — measure. `src/webapp/README.md:83` tree gains a `heuv/` line.

## 9. Test plan

| Layer | What | Fixtures |
|---|---|---|
| Auth | no key 401 (+ `WWW-Authenticate`), key without role 403, logged-in session without key 401, HEUV key 200; `/api/protected/admin` ownership test (`tests/api/test_ldapsync_api.py:302-306`) still passes; add the same one-owner assertion for `/api/protected/heuv` | `role_keys(monkeypatch, {'heuv': ['ROLE_API_HEUV'], 'nobody': ['ROLE_XRAS']})`, `basic_auth`, `client` (`tests/conftest.py:640`) |
| Bytes | per route a **literal `response.data ==` assertion** against a factory-built scenario (key order, null placement, date formats, compact separators); 400 bodies literal | factories (`tests/factories/__init__.py`): `make_user(session, username=, first_name=, last_name=, active=, nickname=, middle_name=, primary_gid=)` (`core.py:160`), `make_project` (`projects.py:152`), `make_account(session, project, resource)` (`:337`, adds the lead as AccountUser), `make_allocation(session, account, amount=, start_date=, end_date=, parent=)` (`:361`), `make_resource(session, resource_type=, resource_name=, commission_date=)` (`resources.py:26`), `make_adhoc_group` / `make_adhoc_system_account_entry(session, group, username, branch='hpc')` (`core.py:293,355`), `make_wallclock_exemption(session, user, queue, start_date, end_date, time_limit_hours=)` (`operational.py:13`), `make_charge_adjustment`, `make_comp_charge_summary`, `make_allocation_transaction`. **Missing — add in this PR:** `make_account_user(session, account, user, start_date=, end_date=)` and `make_default_project(session, user, project, resource)` in `tests/factories/projects.py`; `make_access_branch(session, name, resources=[])` in `tests/factories/security.py`. |
| Rules | each row of §5 "Tests" + each §6 fix; UTC conversion on an MST and an MDT timestamp; `java_round(2.5) == 3`, `java_round(-0.5) == 0` | |
| Read model | the usage route needs `AccountAllocationState` rows: use the refresh helper the dashboard tests use (grep `read_model_rows_for` in `tests/`) or run on `active_project` from `tests/conftest.py` with structural asserts only | Layer-1 fixtures `active_project`, `multi_project_user`, `hpc_resource` |
| Perf | `tests/perf/test_route_query_counts.py` gets `heuv_report_usage` and `heuv_group`; baseline key in `tests/perf/baselines.json` (`{"queries": N, "notes": ...}`, read by `get_baseline` in `tests/perf/conftest.py:46-54`); run `pytest -m perf -n 0` | |
| Gates | route-map parity pins **dashboards only** (`tests/unit/gates/test_route_map_parity.py:39-57`) — no regen needed; `tests/unit/gates/test_docs.py` (spelling, links, cited paths, line budgets: CLAUDE.md 1067 at `:524`, `SYSTEMS_INTEGRATION_APIs.md` 960 at `:538`, default 250 at `:520`; `docs/plans/**` and `docs/presentations/**` are exempt via `is_record` `:42-44`); `test_layer_imports.py`; `test_notify_import_graph.py` | |
| Local smoke | `docker compose up samuel-dev --watch`; `curl -u heuv:<local-pw> http://localhost:5050/api/protected/heuv/v1/user/benkirk/group`; the local DB holds `heuv` (id 10) with `ROLE_API_HEUV` (role 15) and `admin` (id 4) with `ROLE_API_ADMIN` only — grant locally with the §12 INSERT if you want to probe with `admin` | |

Route handlers use `db.session`, so HTTP-layer tests see committed rows only; byte tests therefore build data via factories inside the test transaction and call the **query/schema layer** for exact bytes, and use the HTTP layer for auth/400/404/key-order smoke (house convention, CLAUDE.md Testing).

## 10. Parity tool extension (`utils/parity/`)

| | |
|---|---|
| Entry | `check_legacy_apis.py`: add `heuv` to `--api` choices (`:327-328`) and a branch in the dispatch chain (`:395-445`). Credentials: existing `_resolve_credentials` (`:68-104`, `SAM_LEGACY_USER/PASS`, `SAM_NEW_API_USER/PASS` falling back to legacy) — the `admin` key works on both sides once SAMuel serves HEUV. Bases `:59-60` (`https://sam.ucar.edu`, `https://samuel.k8s.ucar.edu`); pass `--new-base https://samuel-dev.k8s.ucar.edu` first. |
| Client | `clients.py`: `HeuvClient(base, auth)` returning `(status, bytes)` like `XrasClient._get_raw` (`:132`). While there, fold the three tolerance idioms (`_get allow_404/allow_500` `:25`, `disk_quota allow_403` `:74`, `_get_raw allow=` `:132`) into one `allow=(...)` tuple (sweep pick; keep behavior). |
| Sampling | HTTP only: projects = legacy `search/projcode` (no fragment) -> all active projcodes, sample N (default 40, `--sample-size`), always including `SCSG0001`, `NCIS0001` (hierarchical), one inactive from `report/project` of a known code; users = union of `usernames` from the sampled `report/usage` bodies (cap 60). |
| Compare | `utils/parity/heuv.py::compare_heuv` (own module; `comparators.py` is 1,455 lines): per route and sample, apply the **named normalizations F1-F9 only** (sort where F3 says, null where F1/F5 say, drop a legacy 500 where the fix predicts it and assert new 200), then **byte equality** via `_byte_check` (`:1330`) / `_first_difference` (`:1310`). Row-set checks are **symmetric** (`^` like xras `:1441`), never `subset_diff` (`helpers.py:61-69`). Report: per route `compared / identical / normalized-identical / different`, first 5 diffs with the JSON path, plus the §7 counters (surplus groups per user, hierarchical totalCharges deltas, threshold allocationAmount deltas, status pairs). |
| README | `utils/parity/README.md`: table row (`:10` section), Usage line (`:84`), Tolerances bullet (`:126`); fix the stale "five APIs" count and add the missing diskquota row while there. Note the pre-existing bug for a follow-up, not this PR: `comparators.py:1150` `within_tolerance(..., pct=0.05)` is ±0.05% not ±5% (`helpers.py:48` divides by 100). |

## 11. Docs to update (verify each line first)

| Path | Edit | Cap |
|---|---|---|
| `docs/apis/HEUV_API.md` (new) | contract (§4-§6), traffic table, not-ported list (D2), cutover pointer | 250 default |
| `docs/apis/SYSTEMS_INTEGRATION_APIs.md` | **one** pointer line (file is 953/960; §8 at `:851-856` is the ldapsync model) | 960 |
| `CLAUDE.md:188` | `ROLE_XRAS`, `ROLE_API_ADMIN` -> add `ROLE_API_HEUV`; `:236` mounts list gains `api/heuv/`; **net-zero lines** (1067/1067) | 1067 |
| `docs/plans/LDAP_SYNC_API.md:532` | HEUV row in Appendix B (`:521`, header `:525`): status -> ported, PR # | exempt |
| `docs/plans/HARD_DELETE_AUDIT.md:185,212` | `default_project` consumer now known (HEUV `defaultproject`, no fallback) | exempt |
| `docs/presentations/samuel/_4-systems.qmd:34,182,704`, `_A-peers.qmd:195`, `docs/plans/SAMUEL_PRESENTATION.md:347` | "not ported" -> ported | exempt |
| `_4-systems.qmd:528`, `_B-apis.qmd:16`, `SAMUEL_PRESENTATION.md:350` | stale "nothing is mounted at /api/protected" (ldapsync already is) -> list `admin` + `heuv` | exempt |
| `src/webapp/README.md:83` | tree line for `api/heuv/` | 710 (597 now) |
| `src/sam/security/roles.py:101-103` | stale docstring "roles not yet enforced on the token path" — correct in passing (1 line) | |
| `utils/parity/README.md` | §10 | 250 (144 now) |
| `docs/plans/UNPLANNED_CITY_LEDGER.md` | entry **#21**, area mode, format per `#19`/`#20` (`:1322`, `:1392`) and `.claude/skills/unplanned-city-sweep/SKILL.md:178-186`: Mode/Why/Done (picks 1-3 with PR)/Open; metrics row from `scripts/sweep_inventory.py` | exempt |
| `src/sam/security/rbac_defaults.py:60-66` | **no edit** — the comment is about Permission unions, not role names (draft was wrong) | |

Ledger **Open** items to record: `username=` bind for `group_populator` (if measured slow); the two membership-window spellings (§7 b) with the file list; `has_active_allocations` vs `Allocation.is_active` re-spelling (`projects.py:711-725` vs `allocations.py:77-85`), `get_active_allocation` / `get_latest_allocation_for_project` (`queries/allocations.py:76-107`, zero callers); dead helpers `queries/projects.py` `search_projects_by_title :71`, `get_projects_by_lead :134`, `get_project_with_full_details :147`, `get_project_members :162`, `queries/users.py get_user_with_details :250` (zero callers); `search_projects_by_code_or_title` has no ORDER BY; wallclock admin edits (`webapp/dashboards/admin/blueprint.py:1128-1276`) never invalidate `api/v1/wallclock_exemption.py`'s two cache layers (:28,39,53; model `invalidate_queue_cache` `api/v1/queue.py:72-82`); `register_error_handlers` discards 404 text; fstree threshold divisor (§7 g); parity tolerance bug (§10).

## 12. Cutover runbook (Ben's steps; nothing here is started by the PR)

1. **Reachability.** SAMuel's ingress is `traefik-external` with hosts `samuel.k8s.ucar.edu` + `sam.hpc.ucar.edu` (CNAME; `helm/values.yaml:79-89`, `docs/README-k8s.md:252,277-280`), `path: /` Prefix, **no IP allowlist, no path block** (`helm/templates/ingress.yaml`; nothing matches whitelist/source-range in `helm/`), so `/api/protected/heuv/v1` is reachable wherever the host is — the repo treats it as internet-routable (`docs/plans/implemented/K8S_DEV_ENVIRONMENT.md:20`). Confirm from AWS anyway: ask each owner to `curl -u ... https://sam.hpc.ucar.edu/api/protected/heuv/v1/search/projcode?fragment=zzzz` from `54.85.201.121` and `52.11.85.75`.
2. **Owners.** Ruby portal `54.85.201.121` (known only by IP) and python-httpx `52.11.85.75` (`report/usage` only) — identify via the portal team / AWS account owners; residential and `128.117.*` Ruby callers are developers of the same portal.
3. **Parity gate.** `check_legacy_apis.py --api heuv --new-base https://samuel-dev.k8s.ucar.edu`, then against prod SAMuel; all §7 statements decided.
4. **Host swap** by each owner (`sam.ucar.edu` -> `sam.hpc.ucar.edu`); same credentials. Legacy stays up.
5. **Dual watch** until legacy HEUV hits reach zero: SAMuel side `scripts/cirrus_watch.sh` + a `kubectl logs` grep for `/api/protected/heuv/` statuses; legacy side the §3.2 tally daily.
6. **Rollback** = swap the host back.
7. **Revoke** `ROLE_API_HEUV` from `admin` on prod once both callers are on SAMuel and parity has been re-run with the `heuv` credential:

```sql
-- grant used 2026-10-10 (for the record)
INSERT INTO role_api_credentials (role_id, api_credentials_id)
  SELECT r.role_id, a.api_credentials_id FROM role r JOIN api_credentials a
  WHERE r.name='ROLE_API_HEUV' AND a.username='admin';
-- revoke
DELETE rac FROM role_api_credentials rac JOIN role r USING (role_id)
  JOIN api_credentials a USING (api_credentials_id)
  WHERE r.name='ROLE_API_HEUV' AND a.username='admin';
```

Then `sam-admin cache --refresh` is **not** needed (API-key map has its own TTL), but confirm `admin` gets 403 on a HEUV route afterwards.

**samuel-dev** got the same grant on 2026-10-10 by a direct row on CNPG `sam_dev` (Ben chose it over `make refresh-dev`, which would reset the LDAP sync soak). A refresh from a pre-grant clone drops it again. Revoke there (Postgres):

```sql
DELETE FROM role_api_credentials rac USING role r, api_credentials a
  WHERE rac.role_id = r.role_id AND rac.api_credentials_id = a.api_credentials_id
    AND r.name = 'ROLE_API_HEUV' AND a.username = 'admin';
```

## 13. Open questions for Ben

1. §7 g: the fstree/rolling-usage threshold divisor is one day shorter than legacy's. Fix fstree too (changes its live output) or keep two divisors?
2. `Disabled` / `No Allocation` statuses: is there a SAM-side source (an account flag) or do we document them as never emitted? *Legacy source answers the source half (step 2): Disabled = inactive project, so `report/usage` (400 on an inactive project) never emits it; No Allocation = an account with no active, future or prior allocation, or inherited through the parent cascade.*
3. F9: reject `reportDate` with 400, or accept and honor it (SAMuel can compute historical dates cheaply from the read model)? No caller sends it today.
4. Cutover order: both callers at once, or the python-httpx one (`report/usage` only, 6-13/day) first as a canary?

## 14. Kickoff prompt for the implementing session

Paste into a fresh Claude Code session in `/Users/benkirk/codes/project_samuel/devel` (or a
worktree on `heuv-api`); replace `#NNN`.

```
Implement docs/plans/HEUV_API_PORT.md — the port of legacy SAM's HEUV API to SAMuel. The
branch is heuv-api and PR #NNN (docs-only, against staging) already carries the plan; keep
working on that branch and PR. Read CLAUDE.md, then the whole plan, before writing code.

Rules for this session:
- Work the plan's Progress checklist in order: one commit per step, each commit green on its
  own tests; tick the box and push after each step so the PR tracks progress. Steps 1-3 are
  behavior-preserving lifts — prove them with the named tests / parity run before moving on.
- Re-verify every file:line in the plan (grep the symbol) before relying on it; fix the plan
  in the same commit when it is wrong.
- The legacy `admin` API key (containers/ldap-pipeline/secrets/sam.parm, SAM_AUTH_admin) holds
  ROLE_API_HEUV on legacy prod for the whole port. Probe legacy read-only (plan §3.3) whenever
  a shape or rule question comes up; never print the key, never call the PUT routes.
- Stop and report to me, do not decide, at: (a) step 8's ruleset divergences (plan §7) — write
  the old/new ruleset statement with counts (P3); (b) any §13 open question you reach;
  (c) anything the plan got wrong that changes a decision in §2.
- Parity against samuel-dev needs a dev deploy of the branch; ask me when you are ready for
  it. The cutover (§12) is mine — do not start it.
- Follow CLAUDE.md §12 comment budget and the docs gate; CLAUDE.md edits must be net-zero.
```

---

## Appendix A — Golden shapes (synthetic values; key order, types, null placement and date formats are exact)

```
# user/{u}/defaultproject                        (benkirk live: [])
[{"username":"benkirk","resourceName":"HPSS","projcode":"SCSG0001"}]

# user/{u}/group  (ncar first; then by groupName; project groups carry projcode)
[{"username":"benkirk","groupName":"ncar","unixGid":1000,"primary":true,"project":false,"projcode":null},
 {"username":"benkirk","groupName":"csgteam","unixGid":68122,"primary":false,"project":false,"projcode":null},
 {"username":"benkirk","groupName":"scsg0001","unixGid":68283,"primary":false,"project":true,"projcode":"SCSG0001"}]

# user/{u}/access
[{"username":"benkirk","resourceName":"hpc","resourceType":"HPC","homeDirectory":"/glade/u/home/benkirk","shellName":"bash"},
 {"username":"benkirk","resourceName":"hpc-dev","resourceType":"DATA ACCESS","homeDirectory":"/glade/u/home/benkirk","shellName":"bash"}]

# user/{u}/assignedproject?thresholdlimited=false   (dates are UTC)
[{"projcode":"SCSG0001","primary":false,"title":"CSG systems project","resourceAssignments":[
   {"resourceName":"Campaign_Store","startDate":"2022-01-19 18:45:06","endDate":null},
   {"resourceName":"Casper","startDate":"2022-01-19 18:45:05","endDate":null},
   {"resourceName":"Cheyenne","startDate":"2022-01-19 18:45:05","endDate":"2023-12-31 06:59:59"}]}]

# user/{u}/assignedresource?thresholdlimited=false
[{"resourceName":"Casper","projectAssignments":[
   {"projcode":"SCSG0001","primary":false,"title":"CSG systems project","startDate":"2022-01-19 18:45:05","endDate":null}]}]

# user/{u}/userrolelogin
["benkirk","csgteam","spk_stk_adm"]

# user/{u}/wallclockexemption            (?active=true on none active -> {"username":"benkirk","resources":[]})
{"username":"benkirk","resources":[{"resourceName":"Derecho","queues":[{"queueName":"main","exemptions":[
  {"active":false,"startDate":"2024-09-04","endDate":"2025-09-04","hourLimit":48,"comment":""}]}]}]}

# search/projcode?fragment=SCSG          (zzzz -> [])
[{"projcode":"SCSG0001","title":"CSG systems project"},{"projcode":"SCSG0002","title":"CSG collaborators, courtesy accounts, friendly users, and test system users"}]

# report/project/{p}
{"projcode":"SCSG0001","title":"CSG systems project","leadUsername":"benkirk","leadName":"Ben Shelton Kirk","adminUsername":"benkirk","adminName":"Ben Shelton Kirk","abstractText":"systems access for consulting group\r\nExempt flag added 3/8/2018 per dhart","hierarchical":false,"accounts":[
 {"resourceName":"Derecho","thresholdLimited":false,"allocations":[{"startDate":"2026-10-01","endDate":"2027-09-30","active":true},{"startDate":"2024-10-01","endDate":"2026-09-30","active":false}]},
 {"resourceName":"Stratus","thresholdLimited":false,"allocations":[{"startDate":"2026-10-01","endDate":"2025-12-31","active":false}]}]}

# report/usage/project/{p}   (one DataHoldings row, one Normal AccruedCharges row, one Expired row)
{"projcode":"SCSG0001","reportDate":"2026-10-10","accountReports":[
 {"resourceName":"Campaign_Store","resourceType":"DISK","resourceUsageType":"DataHoldings","status":"Normal","allocationStartDate":"2026-10-01","allocationEndDate":"2027-09-30","allocationPropagated":false,"allocationAmount":23,"usernames":["benkirk","csgteam"],"totalHoldings":7,"numberOfFiles":547755,"balance":16,"userCount":2},
 {"resourceName":"Derecho","resourceType":"HPC","resourceUsageType":"AccruedCharges","status":"Normal","allocationStartDate":"2026-10-01","allocationEndDate":"2027-09-30","allocationPropagated":false,"allocationAmount":25000000,"usernames":["benkirk","csgteam"],"totalCharges":3147,"adjustments":0,"balance":24996853,"thresholdReports":[{"period":30,"percentLimit":null,"allocationAmount":2060440,"charges":3147,"percentUsage":0,"label":"30-Day"},{"period":90,"percentLimit":null,"allocationAmount":6181319,"charges":3147,"percentUsage":0,"label":"90-Day"}],"thresholdLimited":true,"userCount":2},
 {"resourceName":"Laramie","resourceType":"HPC","resourceUsageType":"AccruedCharges","status":"Expired","allocationStartDate":null,"allocationEndDate":null,"allocationPropagated":false,"allocationAmount":null,"usernames":["benkirk","csgteam"],"totalCharges":0,"adjustments":0,"balance":null,"thresholdReports":[],"thresholdLimited":false,"userCount":2}]}
# Overspent example (synthetic UABC0001 Casper GPU): "status":"Overspent",...,"allocationAmount":500,...,"totalCharges":542,"adjustments":0,"balance":-42,"thresholdReports":[{"period":30,"percentLimit":null,"allocationAmount":38,"charges":0,"percentUsage":0,"label":"30-Day"},...],"thresholdLimited":true

# project/{p}/hierarchy
{"projcode":"NCIS0001","parentProjcode":null,"rootProjcode":"NCIS0001","children":[{"projcode":"NCIS0014","parentProjcode":"NCIS0001","rootProjcode":"NCIS0001","children":[]}]}

# access   /   access/resource/hpc (single object, same element shape)
[{"resourceName":"hpc","resourceType":"HPC","login":true,"shells":[{"shellName":"bash","dflt":true},{"shellName":"tcsh","dflt":false},{"shellName":"zsh","dflt":false}]},
 {"resourceName":"hpc-dev","resourceType":"DATA ACCESS","login":true,"shells":[{"shellName":"bash","dflt":true},{"shellName":"tcsh","dflt":false}]}]

# errors (400, application/json)
{"errorMessage":"getUserGroups.username: Username nosuchuserxx does not exist."}
{"errorMessage":"getReportProject.projcode: Project NOSU9999 does not exist."}
{"errorMessage":"getProjectUsageReport.projcode: Project XXXX0001 is not active."}
{"errorMessage":"getUserAssignmentByProject.resourceName: Resource Nosuch does not exist."}
# access/resource/Derecho -> 404, empty body, content-length: 0
```

## Appendix B — Not ported (D2), for `docs/apis/HEUV_API.md`

| Route | Java | Why |
|---|---|---|
| `PUT user/{u}/access`, `/defaultproject`, `/group` | `UserAccessibleResourceController.java:40`, `UserDefaultProjectController.java:41`, `UserGroupController.java:41` | writes; zero calls |
| `report/hierarchicalusage/project/{p}` | `UsageReportController.java:46` | heavy; zero calls |
| `report/monthlycomputeusage[detail]/project/{p}/resource/{r}` | `MonthlyComputeUsageReportController.java:33,49` | heavy; zero calls |
| `report/usercomputeusage[detail]/...` | `UserComputeUsageReportController.java:34,50` | heavy; zero calls |
| `report/allocationchange/project/{p}/resource/{r}` | `AllocationChangeReportController.java:30` | zero calls |
| `report/dataholdings[detail]/project/{p}/resource/{r}` | `DataHoldingsReportController.java:33,49` | zero calls |
