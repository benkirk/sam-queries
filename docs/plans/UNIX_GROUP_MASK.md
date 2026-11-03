# Unix group masks: local add/remove over a mirrored group, with a ticket and a watch

**Status:** built 2026-11-02 (PR #893), as recorded here; the decisions in § 11 are Ben's.
Rollout: the DDL on prod MySQL and dev Postgres by Ben (§ 8), the watch task switched on in
prod after its first weeks on dev (§ 4).

SAM does not create unix groups and never will from this design: upstream mints the gid.
What SAM does is hold a **signed delta** over a group it already knows, so an operator's add
or remove takes effect at once, survives the mirror, is handed to NUSD as a ticket where
upstream has to follow, and expires or confirms on its own. The first customer is `cgdoce`
(RC-39483, RC-40776), waiting since August for the Google Groups to LDAP path.

## 1. What is true

**Adhoc groups are a mirror.** The daemon PUTs each group's whole membership to
`/api/protected/admin/ldapsync/group` and `sync_group` (`src/sam/manage/ldapsync.py`)
rewrites `adhoc_system_account_entry` (`src/sam/core/groups.py`, one row per group, branch
and username) to that set. The PUT lands on SAMuel since 2026-11-02. Legacy has no hand-add
path of its own and maps only the three group tables, so it keeps reading SAM's entries.

**Project groups are derived, never stored.** `group_populator`
(`src/sam/queries/directory_access.py`, the HPC provisioner's feed, 300 s cache) builds them
per access branch from accounts on configurable resources whose allocation ended less than
`ACCESS_GRACE_PERIOD` (90 days) ago, and admits a member only if that username holds an
account on the branch (the `flatLoadDependentAccount` invariant). `project_groups`
(`src/sam/queries/ldapsync.py`) is the daemon's SAM-to-directory copy of the same rule, so a
project group's membership reaches LDAP through it; its incremental `since` keys on
`last_modified`.

**A local row is live at once.** `directory_access`, HEUV, the cards and SAMuel's own RBAC
(`AuthUser.roles` reads the entry table ungated; `csg` and `nusd` are grant subjects) all
read the entry table.

## 2. The mask

One table, `unix_group_mask` (`scripts/sql/create_unix_group_mask.sql`, `_pg.sql` twin),
keyed by **gid** so one row shape covers both kinds; the gid spaces are disjoint (adhoc
1004–80000 with a unique key; projects 68277–99193, no duplicates, 2026-11-02).

| column | meaning |
|---|---|
| `mask_id` | PK |
| `unix_gid`, `kind` | the group; `kind` is `adhoc` or `project`, resolved from the gid when the row opens (`AdhocGroup.get_by_unix_gid`, else `Project.unix_gid`) |
| `username` | `users.username` as stored (resolved case-insensitively); 12 characters at most for an adhoc group (the entry width) |
| `op` | `add` or `remove` |
| `requested_by`, `note`, `creation_time` | who, why, when (app clock, naive Mountain) |
| `expires_at` | NULL means "until upstream agrees"; a project mask must carry one, 90 days at most |
| `closed_at`, `resolution`, `closed_by` | `confirmed`, `expired` or `withdrawn` |

No foreign keys (the `external_ticket` convention). One open row per (gid, username) is an
application rule in `open_mask` (MySQL has no partial unique index); an overdue open row is
expired on the way when a new one opens. ORM: `UnixGroupMask` (`SessionMixin`; `create`,
`update`, `close`, `is_active` on `closed_at`, `is_open_at(now)` adds the expiry). The ticket,
when there is one, is `external_ticket(entity_type='unix_group_mask', entity_id=mask_id)`.

## 3. How a mask takes effect

**Adhoc, materialized** (`sam/manage/groups.py`):

1. `open_mask` runs the guards (§ 7), inserts the row and, for `add`, an entry row on every
   branch the group's tags name (`AdhocGroup.branch_names`, what `sync_group` calls
   `matching`); for `remove` it deletes the member's rows. `cgdoce` names hpc, hpc-data and
   hpc-dev, so an add is three rows.
2. `sync_group` applies the gid's open masks (`sam/queries/group_masks.py::open_masks`) after
   computing `wanted`, usernames compared case-insensitively with the PUT's spelling kept: a
   PUT cannot revert a pending change, and a mask upstream has caught up with is a no-op.
3. `close_mask`: a withdrawn or expired adhoc mask takes its rows back (`unmaterialize`); a
   confirmed one leaves them, since upstream is the source again.

**Project, at read time.** `project_mask_feed` reads every project mask once: the open ones
resolved (the member's upid, the project's branches from accounts on configurable resources
**whatever the allocation's end or the project's active flag**, since the day-100 straggler's
project is usually inactive) and `{gid: latest open or close stamp}`. `group_populator`
injects the group on those branches and adds the member only where they hold an account
(the invariant holds; the card says so); a remove subtracts. `project_groups` folds the
member key in, keeps the branch tags, and takes the stamps into `last_modified`, so the
daemon's incremental pull sees a grant appear and vanish. The lead and admin are always
members there, so a remove never drops them. `project_access.get_project_group_status` (the
scheduler's view) is untouched.

## 4. The watch task

`unix_group_masks` (`src/scheduling/tasks/unix_group_masks.py`, hourly :40,
`needs=('sam',)`) runs `sweep_masks` (`sam/manage/groups.py`) from the occurrence clock:

- past `expires_at`: close `expired` (an adhoc mask reverses its rows).
- adhoc with a drifted materialization: re-assert it and count it (`reasserted`).
- adhoc, directory configured: one `reader.group(gid)` per distinct gid; the member's upid
  present (`add`) or absent (`remove`) in `member_upids` closes the row `confirmed`.
- a login with no upid (a role login such as `csgteam`) can never be confirmed: counted
  `unconfirmable`, flagged on the tab, closed by withdrawal or expiry.
- the ticket closed (`external_ticket.closed_at`, stamped by the hourly refresh) with the
  directory still disagreeing: counted `flagged`, shown on the row; never auto-closed.

`TaskResult.detail` carries the counts; an unreadable directory stops the reads and says so
in the headline. The task never files or comments (`test_no_other_task_reaches_a_ticket_writer`)
and reads the four `DIRECTORY_*` keys the CronJob already forwards. **Named in prod's
`SAM_TASKS_DISABLED`** (the list is fail-open); dev runs it. Until prod runs it, a project
mask stops applying at `expires_at` on its own (the readers check it) but an overdue adhoc
mask keeps its rows until a sweep or a withdrawal.

## 5. The ticket

Adhoc masks only; a project grant is SAM's own group and files nothing. Filed from the
webapp after the write commits (`webapp/dashboards/admin/group_mask_tickets.py`, the
`register/handoff_mail.py` shape):

- kind `hpc_group` in `KINDS` (`src/sam/integration/tickets/base.py`);
  `JIRA_HPC_GROUP_REQUEST_TYPE_ID` 31 ("General help" until the desk has an "HPC group"
  type) and `JIRA_HPC_GROUP_LABELS` `sam-hpc-group` in `webapp.env`.
- handle `SAM-GM-<mask_id>` in the summary; notify kind `hpc_group_ticket` (family
  `account`, template `src/sam/notify/templates/hpc_group_ticket.txt`), dedup
  `hpc_group_ticket:<mask_id>`; link `external_ticket('unix_group_mask', mask_id)`. The body
  carries the group, the member, the change, the note, what SAM did locally and when, and
  the masks-tab link; the ticket says upstream (Google Groups) is what NUSD changes.
- mail fallback to `NOTIFY_ACCOUNT_TICKET_TO` when no provider is armed or it fails; the
  hourly learn half looks up both handle families (`LEARN_KINDS` in `tickets/learn.py`) so a
  mailed ticket's key is linked later.
- a Withdraw posts one internal comment when the provider can; the ticket is NUSD's to close.
- the success line names the ticket key, or says no ticket path is configured.

## 6. UI

One Flask-free manage layer (`open_mask`, `close_mask`, `materialize`, `sweep_masks`) under
three surfaces; all writes are `EDIT_GROUPS` (held by `allocation_admin`: NUSD and CSG).

- **Admin > Users & Groups > Group masks** (`dashboards/admin/group_masks_routes.py`, the
  Last seen shape): every mask with kind and state chips, a group-or-user search, an
  open-only switch, sortable columns, pagination; Edit (a `CrudSpec` over expiry and note)
  and Withdraw. New mask opens one form: an adhoc-group picker (gid) or a project picker,
  the user picker, the op, the expiry, the note. The table refreshes itself on
  `reloadGroupMasks`.
- **Adhoc card**: Add member and Remove member open the same form pinned to the group; a
  Pending changes list under them (self-refreshing) with Withdraw; `reloadGroupCard`
  re-renders the card by name. Materialized rows render like any member.
- **Admin project card**: Group access with Grant access (pinned, 7 days by default, 90 at
  most), the pending list and Withdraw. The card also opens inside `#projectDetailsModal`,
  so its opener is the `show-detail-modal` action and the shells
  (`group_mask_modals.html`) ride with `shared/project_details_modal.html`.
- **Directory panels**: one line per open mask saying whether the directory agrees, from
  the record the panel already fetched (`mask_agreement`). The GET still never writes.
- `status_badge` gained `pending`, `confirmed` and `withdrawn`; `ticket_link` is one macro.

## 7. Guards (`open_mask`)

- A gid no adhoc group or project holds, an unknown user, an inactive adhoc group, or one
  whose tags name no access branch: refused. No path creates a group.
- An adhoc group that is an RBAC grant subject (`RoleCatalog.group_subjects`): refused
  unless the operator holds `MANAGE_ROLES`; the webapp passes both in. Adding someone to
  `csg` grants them the CSG role in SAMuel the moment the row lands.
- `remove` of a member whose `primary_gid` is this group: refused; change the primary group
  first.
- A member with no unix account on a branch: allowed; the grant is invisible there until
  they hold one (the invariant in § 1 is never relaxed).
- A project mask must carry `expires_at`, 90 days at most; an adhoc mask may be open-ended.
- A project mask also passes the operator's facility scope for `EDIT_GROUPS`.

## 8. DDL and rollout

The table lives in the SAM database: SAM never migrates its schema, but an additive table is
routine and Ben applies the script himself. The `external_ticket` steps
(`implemented/TICKET_PROVIDER.md` § 8.3) were followed: the script with its verification
SELECTs (0 rows, 12 columns, 3 indexes, 0 FKs), the `_BOOTSTRAP_TABLES` tuple in
`tests/conftest.py`, the pin in `tests/integration/test_schema_validation.py` (utf8mb4
`note`), the anonymizer purge, the factory.

```
mysql -u <hpc-writer> -h <host> -p sam < scripts/sql/create_unix_group_mask.sql
psql -h csg-postgres.k8s.ucar.edu -d sam_dev -f scripts/sql/create_unix_group_mask_pg.sql
```

Rehearsed twice on the local MySQL and on the :3307 test container (idempotent). Prod got
the table 2026-11-02; sam_dev too, and the twin ends with `ALTER TABLE ... OWNER TO sam_dev`
because a superuser apply left the table unreadable by the app's role (a `make refresh-dev`
builds it from the ORM as `sam_dev`). Helm: the task name in
`SAM_TASKS_DISABLED`, the two `JIRA_HPC_GROUP_*` keys in `webapp.env`.

## 9. What was proven on the laptop

Against the local snapshot on samuel-dev (2026-11-02): Add member on `cgdoce` wrote three
entry rows, the card reloaded with the member and the pending row; Withdraw from the tab
closed the row and removed the three entries. Grant access on UNID0010 (expired 2026-07-01,
inactive since 2026-10-03) for a user with hpc and hpc-data accounts: after a cache refresh
`/api/v1/directory_access/hpc` and `/hpc-data` listed `unid0010` with him, `/hpc-dev` did
not. Unit: a PUT does not revert an open mask, a confirmed mask is a no-op, the sweep's
expire / confirm / drift / no-upid / unreadable cases, the ticket's two paths.

## 10. Decisions

Ben, 2026-11-02: a mask table in the SAM database, applied by SQL script; group-wide rows,
materialized across the group's branches as the daemon does; the mask honored inside
`sync_group`; a new ticket kind named "HPC group"; the mask generalizes to project groups
with a time limit and no ticket; a masks tab on the admin Users & Groups page; a project
grant flows to LDAP through the daemon's feed; a role login may be masked and is flagged
unconfirmable; the table and ORM names; the 90-day cap; the interim request type 31.

## 11. Out of scope

Creating or deleting groups (`CREATE_GROUPS`, `DELETE_GROUPS` stay withheld); the Google
Groups to LDAP path and the daemon's own bugs (`DIRECTORY_CLIENT_NEXT.md` § 2); any
`DIRECTORY_APPLY_GROUP` lever; `groupstatus` (`sam/queries/project_access.py`); a CLI verb
(the manage layer is Flask-free so one can follow); auto-closing a ticket; a per-row Remove
on the adhoc card's member tables (the shared `render_user_rows` macro has no action slot).
