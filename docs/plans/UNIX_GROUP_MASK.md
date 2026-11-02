# Unix group masks: local add/remove over a mirrored group, with a ticket and a watch

**Status:** design, 2026-11-02. Nothing is built; Ben implements on the laptop and evaluates
there. The decisions in § 11 are Ben's unless marked open.

SAM does not create unix groups and never will from this design: upstream mints the gid.
What SAM may do is hold a **signed delta** over a group it already knows, so an operator's
add or remove takes effect at once, survives the mirror, is handed to NUSD as a ticket where
upstream has to follow, and expires or confirms on its own.

## 1. What is true today

**Adhoc groups are a mirror.** The daemon PUTs each group's whole membership and
`sync_group` (`src/sam/manage/ldapsync.py`) rewrites `adhoc_system_account_entry`
(`src/sam/core/groups.py`, one row per group, branch and username, no dates, no
provenance) to that set: rows the PUT lacks are deleted, rows it carries are added. A row
an operator inserted by hand is reverted by the next PUT for that gid. PUTs are rare
while Google Groups to LDAP is broken (`DIRECTORY_CLIENT_NEXT.md` § 2), but a daemon
restart re-sends about a hundred groups, so "rare" is not "never". Tomcat stops serving
the daemon on 2026-11-02, so SAMuel's `sync_group` is the only place the mirror lands.

**Project groups are derived, never stored.** `group_populator`
(`src/sam/queries/directory_access.py`) builds them per access branch from accounts on
configurable resources whose allocation ended less than `ACCESS_GRACE_PERIOD` (90 days)
ago, members from live `account_user` rows, and admits any group member only if that
username holds a unix account on the branch (the `flatLoadDependentAccount` invariant:
the downstream provisioner must never see a member without an account). `project_groups`
(`src/sam/queries/ldapsync.py`) is the daemon's SAM-to-directory copy of the same rule.
On day 91 the project's group is gone from both; on day 100 a straggler who wants to
reclaim group-projected files has no path short of reactivating the project for everyone.

**A local row is live at once.** `directory_access` (the HPC provisioner, 300 s cache),
HEUV, the user and group cards, and SAMuel's own RBAC (`adhoc_system_account_entry` is the
grant subject: `nusd` is `allocation_admin`, `csg` is `csg`) all read the entry table.

**What exists to build on.** The adhoc card and the admin project card already carry
"Check directory" (`dashboards/user/partials/group_card.html`,
`dashboards/user/partials/project_card.html`, panels in `sam/manage/directory_panel.py`),
report-only. `EDIT_GROUPS` exists, is held by `allocation_admin` and `csg`, and no route
uses it; `CREATE_GROUPS` and `DELETE_GROUPS` stay withheld. Tickets: `file_once`
(`src/sam/integration/tickets/filing.py`), `external_ticket` (polymorphic, no FKs), and
the hourly `refresh_ticket_status` (`src/sam/integration/tickets/learn.py`), which
re-reads every open ticket whatever its entity type. The directory reader answers
`GroupRecord.member_upids` for a gid; `User.upid` is the join.

## 2. The mask

One table, `unix_group_mask`, keyed by **gid** so one row shape covers both kinds:

| column | meaning |
|---|---|
| `mask_id` | PK |
| `unix_gid`, `kind` | the group; `kind` is `adhoc` or `project`, resolved from the gid at write time (`AdhocGroup.get_by_unix_gid`, else `Project.unix_gid`) and stored for display and the ticket rule |
| `username` | the member; 12 characters at most for an adhoc group (`ENTRY_USERNAME_WIDTH`) |
| `op` | `add` or `remove` |
| `requested_by`, `note`, `creation_time` | who, why, when (app clock, naive Mountain) |
| `expires_at` | NULL means "until upstream agrees"; a project mask must carry one (§ 7) |
| `closed_at`, `resolution`, `closed_by` | `confirmed`, `expired` or `withdrawn` |

No foreign keys (the `external_ticket` convention). Unique on open rows per
(`unix_gid`, `username`): one standing intent per member per group. The ticket, when
there is one, is `external_ticket(entity_type='unix_group_mask', entity_id=mask_id)`.
An open row is SAM's standing intent; the two kinds honor it differently.

## 3. How a mask takes effect

**Adhoc, materialized.** The entry table is SAM's copy of upstream, and every reader
above consumes it, so the mask is written through to it and then protected:

1. Write time (`sam/manage/groups.py`, inside `management_transaction`): insert the mask;
   for `add` insert an entry row on every branch the group's tags name, exactly as the
   daemon populates a group; for `remove` delete those rows.
2. The mirror: `sync_group`, after computing `wanted` from the PUT, applies the gid's open
   masks (`wanted |= adds x branches; wanted -= removes x branches`). A PUT can no longer
   revert a pending change, and a mask that upstream has caught up with is a no-op.
3. The watcher (§ 4) confirms against the directory and re-asserts the rows if anything
   else drifted them.

**Project, at read time.** Nothing is stored to protect, so the two derivations apply
open, unexpired masks for project gids: `group_populator` and `project_groups` share one
helper that returns `{gid: (adds, removes)}` for `now`. An `add` injects the group
(projcode lowercased, `project.unix_gid`) on every branch where the project holds an
account on a configurable resource, ungated by allocation end, and adds the username only
if they are in that branch's account set; the invariant holds, and the card says when a
grant is invisible because the member has no account on the branch. A `remove` subtracts.
`project_access.get_project_group_status` (the scheduler's view) is not touched: a reclaim
is about files through the group, not queue access. The 300 s `directory_access` cache
and the provisioner's own cadence bound how fast a grant reaches a host.

**Legacy SAM** reads the entry table and never sees the mask. It keeps working.

## 4. The watcher task

`unix_group_masks`, hourly, `needs=('sam',)`, read-only against the directory and Jira,
**named in `helm/values.yaml` `SAM_TASKS_DISABLED` in the same commit** (the list is
fail-open; dev runs it). Per open mask, oldest first:

- past `expires_at`: close `expired`; an adhoc mask also reverses its materialization.
- adhoc, directory configured: one `reader.group(gid)` per distinct gid; the user's upid
  present (`add`) or absent (`remove`) in `member_upids` closes the row `confirmed`.
  Upstream is now the source again and the entry rows match it.
- adhoc, materialization drifted (row missing after an `add`, present after a `remove`):
  re-assert it and count it, so the ledger shows which path reverted it.
- ticket closed (`external_ticket.closed_at` set by the hourly refresh) with the directory
  unchanged: flag it on the card ("closed without the change"); never auto-close. NUSD may
  have declined, and an operator withdraws.

`TaskResult.detail` carries the counts (expired, confirmed, reasserted, flagged, unreadable);
an unreadable directory skips confirmation and reports, like `directory_report`. The task
never files or comments: `TICKET_PROVIDER` and `JIRA_WRITE_ENABLED` do not reach the CronJob
for it (`test_helm_jira_levers.py`), and the hourly status refresh already covers its tickets.
The CronJob forwards the four `DIRECTORY_*` read keys by name already.

## 5. The ticket

Adhoc masks only; a project grant is SAM's own group and files nothing. Filed from the
webapp after the write commits, never inside `management_transaction`, through `file_once`:

- kind `hpc_group` added to `KINDS` (`src/sam/integration/tickets/base.py`); per-kind env
  `JIRA_HPC_GROUP_REQUEST_TYPE_ID`, `JIRA_HPC_GROUP_SERVICE_DESK_ID`, `JIRA_HPC_GROUP_LABELS`
  (`sam-hpc-group`). The desk gets an "HPC group" request type (Ben asks NUSD); until it
  exists the env points at 31 "General help".
- handle `SAM-GM-<mask_id>` in the summary; ledger kind `hpc_group_ticket`, family
  `account`, template `hpc_group_ticket.txt` beside `account_ticket.txt`
  (`src/sam/notify/templates/`), dedup `hpc_group_ticket:<mask_id>`; `LinkTarget`
  (`unix_group_mask`, mask_id, operator). Body: the group (name, gid, source), the member,
  the op, the note, what SAM did locally and when, and that upstream (Google Groups or
  PeopleDB) is what NUSD changes; link to the mask row on the masks tab (§ 6).
- mail fallback to `NOTIFY_ACCOUNT_TICKET_TO`, as `handoff_mail.py` does; the learn half
  of the hourly pass is extended to this kind so a mailed ticket's key is found by handle.
- a Withdraw posts one internal comment when the provider can (`provider.comment`), else
  nothing; the ticket is NUSD's to close.

## 6. UI

Three surfaces, one Flask-free manage layer (`open_mask`, `close_mask`, `materialize` in
`sam/manage/groups.py`) so `sam-admin` can follow later. All writes are `EDIT_GROUPS`.

- **Admin > Users & Groups > Group masks**: a third `page_tabs` entry in
  `dashboards/admin/base_admin_users.html` beside Search and Last seen, a routes module
  shaped like `dashboards/admin/last_seen_routes.py`. One table of every mask: group, kind,
  user, op, requested by and when, expires, ticket key (`ticket_link` macro), state, and
  the flag from § 4. Filters: kind, state, a text match on group or user, and the
  open-only toggle via `read_active_only`. Create opens a modal: a group picker (the
  `htmx_search_groups` typeahead, plus a project picker), the user typeahead with a new
  search context, op, expiry (blank for adhoc, default +7 days for project), note. Edit
  changes expiry and note only (`CrudSpec(actions=('edit',))`). Withdraw closes the row
  `withdrawn` and, for adhoc, reverses the materialization. Create and Withdraw stay
  bespoke `HtmxFormHandler`s: they touch entry rows and file a ticket after commit, which
  is more than the crud spec expresses (its own hard rule).
- **Adhoc card**: an Add member button and a per-row Remove on each branch tab open the
  same Create handler pinned to the group; a Pending changes list under the tabs shows the
  open masks with Withdraw. Materialized rows render like any member; a pending one gets
  a small badge.
- **Admin project card**: a "Group access" section beside Directory: grant one user for N
  days (the same handler, `op=add`, kind project), the live grants, Withdraw.
- **Directory panels**: one line per open mask saying whether the directory agrees right
  now, read from the record the panel already fetched. The GET still never writes.

Gates: route-map regen, the modal-shell contract (new fragments that reach a host shell go
in `HTMX_FRAGMENT_SHELL_DEPS`), CSP lint, action cells, Font Awesome names.

## 7. Guards

- An adhoc group that is an RBAC grant subject (`SamuelRoleGrant`, subject type group):
  refuse unless the operator holds `MANAGE_ROLES`. Adding someone to `csg` grants them the
  CSG role in SAMuel the moment the row lands.
- `remove` of a member whose `primary_gid` is this group: refuse; the user card changes
  the primary group first (`User.set_primary_gid` validates against membership).
- A member with no unix account on a branch: allowed, with the card saying the grant is
  invisible there until they hold one; the invariant in § 1 is never relaxed.
- A project mask must carry `expires_at`; the form defaults +7 days and caps at 90
  (open: the cap). An adhoc mask may be open-ended.
- `kind` is resolved at write time and a gid that matches neither table is refused. No
  path creates a group.
- Scope: `EDIT_GROUPS` for both kinds; a project mask also passes the admin project
  card's facility check (`require_project_facility_permission`).

## 8. DDL and rollout

The table lives in the SAM database (Ben, 2026-11-02): SAM never migrates its schema,
but an additive table is routine and Ben applies the script himself. The no-DDL homes
were weighed and set aside: `manual_task` (a legacy free-text table SAMuel never reads),
`notification_log` (the mail ledger; the Notifications card counts its rows), an encoded
`adhoc_group_tag` (rewritten by every PUT, and projects have no tag table), and a
`system_status` table (Alembic, no DBA, but every SAM-side reader would need the second
bind handed in). Follow the `external_ticket` steps (`implemented/TICKET_PROVIDER.md` § 8.3):

1. a `CREATE TABLE IF NOT EXISTS` script under scripts/sql/ in the
   `create_external_ticket.sql` idiom (InnoDB, utf8mb3 except `note`, verification
   SELECTs, "NO DROP"); the `UnixGroupMask` ORM (`SessionMixin`, `create()` and
   `close()`, an `is_active` hybrid on `closed_at`), exported from `sam/__init__.py`;
   one `_BOOTSTRAP_TABLES` tuple in `tests/conftest.py`; the pin in
   `tests/integration/test_schema_validation.py`; the anonymizer list in
   `containers/sam-sql-dev/anonymize_sam_db.py` (usernames only, no prose to scrub beyond
   `note`); `dbbrowse/redact.py` needs nothing (no secret-shaped column).
2. Rehearse on the local MySQL and the :3307 test container twice; prod gets the table
   before the deploy, by Ben; samuel-dev builds it from the ORM on `make refresh-dev`.
3. Helm: the task name in `SAM_TASKS_DISABLED`; the three `JIRA_HPC_GROUP_*` keys in
   `webapp.env` only; the Live/Disabled comment block.

## 9. Build order (one commit each)

1. Table, ORM, bootstrap, schema pin, anonymizer; `tests/unit/models/` coverage.
2. `sam/manage/groups.py`: `open_mask`, `close_mask`, `materialize`, the § 7 guards;
   the read-time helper; `sync_group` applies masks (extend
   `tests/unit/manage/test_ldapsync_user.py::TestGroup` with "a PUT does not revert an
   open mask" and "a confirmed mask is a no-op").
3. `group_populator` and `project_groups` apply project masks; `directory_access` and
   `ldapsync` reads tests cover an expired project with a grant, the account-set gate,
   and expiry.
4. The ticket kind, template, env, and the webapp filing path; `test_helm_jira_levers.py`
   unchanged (no task files).
5. The watcher task, registered and named in `values.yaml`; a values-grep test like
   `test_task_xras_notices.py::test_it_ships_switched_off`.
6. The masks tab, the card controls, the panel line; route map, gates, browser smoke.
7. `.claude/rules/directory.md` and this doc to as-built.

## 10. Verification

- `pytest tests/unit/manage tests/unit/queries/test_directory_access_queries.py
  tests/unit/queries/test_ldapsync_reads.py tests/unit/directory tests/unit/tasks
  tests/unit/webapp/test_directory_panels.py -n 4`, the structural gates, then the full suite.
- Laptop, against the local snapshot: add a member to `csg` with `MANAGE_ROLES`, see the
  row on the card and in `/api/v1/directory_access` after the cache; PUT the group through
  the ldapsync API without that member and see the row survive; run the task with a
  `FakeDirectory` that lists the upid and see the mask close `confirmed`. Grant one user
  on a project expired more than 90 days and see the group and member in
  `directory_access` for the branch where they hold an account, absent where they do not;
  advance the clock past expiry and see both vanish. Withdraw each kind.
- With the live directory (`.env` keys): the panel line for an open mask; `sam-admin
  tasks --run unix_group_masks --dry-run`.

## 11. Decisions

Ben, 2026-11-02: a mask table in the SAM database, applied by SQL script, rather than
columns on the entry table or a no-DDL encoding (§ 8); group-wide rows,
materialized across the group's branches as the daemon does; the mask honored inside
`sync_group` now that Tomcat no longer serves the daemon; a new ticket kind named
"HPC group"; the mask generalizes to project groups with an optional time limit and no
ticket; a masks table on the admin Users & Groups page.

Open, with the recommendation taken above: the table and ORM names (`unix_group_mask`,
`UnixGroupMask`); the project-grant cap (90 days); refusing RBAC grant-subject groups below
`MANAGE_ROLES`; the interim request type (31) until the desk has "HPC group".

## 12. Out of scope

Creating or deleting groups (`CREATE_GROUPS`, `DELETE_GROUPS` stay withheld); the Google
Groups to LDAP path and the daemon's own bugs (`DIRECTORY_CLIENT_NEXT.md` § 2); any
`DIRECTORY_APPLY_GROUP` lever; `groupstatus` (`sam/queries/project_access.py`); a CLI verb
(the manage layer is Flask-free so one can follow); auto-closing a ticket.
