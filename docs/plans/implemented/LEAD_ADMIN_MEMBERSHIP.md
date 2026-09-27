# Lead and admin membership: assigning a lead or admin makes them a member

**Status: implemented (PR #616); bulk remediation not started.** Handoff for a fresh session.
**Branch:** `lead-admin-membership`, from `origin/staging`. One PR against `staging`, opened only after `make check-all` passes and a local smoke test is done.

## Progress

- [x] 1. `Project.ensure_members(*user_ids)` choke point, with `_seed_members` reusing its insert helper
- [x] 2. `Project.update` seeds a new lead or admin when the value changes
- [x] 3. `change_project_admin` routes through `Project.update` (clearing the admin stays a direct write)
- [x] 4. XRAS role endpoint: reword the comment and the warning (behavior is inherited from 2)
- [x] 5. `revoke_user_resource_access` refuses the admin, the same way it refuses the lead
- [x] 6. CLI: `--reconcile` really reconciles and reports what it added; `--validate` flags a lead or admin who is not a live member
- [x] 7. Docs: CLAUDE.md CLI line and the §7 invariant note
- [x] 8. Tests (model, XRAS, CLI), `make check-all`, local smoke test
- [x] 9. `Project.users` = lead + admin + row holders; the access grid shows the lead and admin with their true state

## Context: the incident (2026-09-24)

Prod `https://sam.hpc.ucar.edu/api/v1/directory_access/hpc` omitted CESM0020's lead (yeager, user_id 3114) from group `cesm0020`.

**How the feed works.** It emits a project group's members only from live `account_user` rows:
- the account is on a configurable resource in the branch, with an allocation whose `end_date` is inside the 90-day grace window;
- the row has `start_date <= NOW()` and `end_date` null or in the future;
- the user has `u.active`.

See `src/sam/queries/directory_access.py:73-90` (`_SQL_PROJECT_MEMBERS`) and `:125-154` (`_SQL_MEMBERSHIP`). The feed never consults `project_lead_user_id`. It is a frozen legacy-compat blueprint (CLAUDE.md §API), so **fix the data, not the feed.**

**What was wrong with CESM0020.** yeager had **zero** `account_user` rows on any CESM0020 account, ever. The other 20 members were present. Evidence points to a lead change in the old Java SAM:
- `project.modified_time` is 2022-09-27 14:43;
- two other yeager-led projects were edited in the same minute;
- there are no `xras_action_log` rows for CESM0020.

**Remediation applied on prod.** Ben pressed "Reconcile all" on Manage Project → User / Resource Access (`projects_routes.py:3537`, which calls `reconcile_project_access`, which calls `Account._seed_members` per non-deleted account). That:
- added yeager to every non-deleted CESM0020 account (Casper, Casper GPU, Derecho, Derecho GPU, and the retired Cheyenne, Gust and Gust GPU), with `start_date` = now (seconds truncated) and `end_date` = NULL;
- also filled in the admin, and every member who holds an open-ended row on any other CESM0020 account, across all seven;
- did not copy members whose rows have a future end date, because only `end_date IS NULL` rows are copied (`accounts.py:169-178`);
- removed nothing.

The retired accounts are harmless in the feed: Cheyenne and Gust are not in the `hpc` branch, and their allocations ended in 2023.

**A trap found on the way.** `sam-admin project <code> --reconcile` is a **stub**. `src/cli/project/commands.py:431-437` prints "✅ Project X reconciled" and writes nothing. It was recommended first, and prod was left unchanged until the web button was used.

**How big the problem is.** These counts come from the local snapshot, a near-current prod copy, 2026-09-24. Re-measure on prod before any bulk action.

| | active `hpc`-branch projects | absent from group entirely | missing on some resources |
|---|---|---|---|
| lead | 1472 | 104 | 45 |
| admin | 1263 | 240 | 33 |

The 104 lead-absent projects break down as:
- **49 "never a member"**: the CESM0020 shape, where the lead was set without membership;
- **55 "had rows, all ended"**: many were ended in one burst at **2026-01-28 15:10–15:20**, which ended 1,101 rows across 158 projects and 217 users, including 134 lead rows on 44 projects.

The Python code has **no** `AccountUser` end-date writer. Its only removals are hard deletes, and both refuse the lead. That burst matches legacy Java `PUT /protected/admin/userlifecycle/deactivate/{username}` (`legacy_sam/.../DefaultFinishUserDeactivationCommand.java:56-60`, which calls `User.unassignFromAccounts()` at `User.java:377-389`). That is the known sam-ldap-syncd#3 defect documented in `scripts/repair/RUNBOOK-missing-projects.md:14-21` and `2026-07-restore-orphaned-memberships.sql`. Runbook query 1b (`RUNBOOK-missing-projects.md:61-77`), grouped by `(user_id, end_date)`, can confirm it on prod. **Bulk remediation is out of scope**: it is Ben's data call once `--validate` and `--reconcile` exist.

## The gaps (census, 2026-09-24; re-verify line numbers)

The only thing that seeds the lead and admin is `Account._seed_members` (`src/sam/accounting/accounts.py:143-200`). It runs from:
- `Account.create` (82-109);
- `Account.get_or_create` when it revives a soft-deleted account (137-139);
- `reconcile_project_access` (`src/sam/manage/__init__.py:329-360`), reachable only from the web button.

**No path that changes the lead or admin of an existing project seeds membership on its existing accounts.**

| Path | Sets | Gap |
|---|---|---|
| Details tab `_ProjectUpdateHandler` (`src/webapp/dashboards/admin/projects_routes.py:772-837`, calls `self.project.update(**data)` at ~810) | lead, admin | no membership; only an FK-existence check (796-802) |
| `Project.update` (`src/sam/projects/projects.py:309-368`, lead/admin at 357-360) | lead, admin | plain assignment; `None` means no change, so it cannot clear the admin |
| XRAS role change (`src/webapp/api/xras/roles.py:41-121`, write at 113) | lead | deliberately no roster row, for legacy parity; only logs a warning (103-110) |
| XRAS Update (`src/sam/xras/handlers/update.py:262-270`) | lead, admin | a PI whose role begins in the future is kept out of the roster (`src/sam/xras/roster.py:240-256`, "legacy defect 3") and becomes lead with no rows on existing accounts |
| `change_project_admin` (`src/sam/manage/__init__.py:185-226`), used by the members page (`project_members.py:175-205`) and `PUT /api/v1/projects/<projcode>/admin` (`api/v1/projects.py:550-603`) | admin | the guard (211-220) accepts **any** `AccountUser` row, even an expired one; the direct FK write (222) adds no live row |
| `revoke_user_resource_access` (`manage/__init__.py:280-326`) | — | lead guard at 309-310, **no admin guard**, so an admin can be revoked down to zero rows |
| `sam-admin project --validate` (`src/cli/project/commands.py:410-429`) | — | a placeholder that checks only "missing lead" and "missing allocation type" |
| `sam-admin project --reconcile` (`commands.py:431-437`; flag at `src/cli/cmds/admin.py:85`, help text "Reconcile allocations") | — | a stub that prints success and does nothing |

Already safe:
- `Project.create`, and the admin Create Project handler (`projects_routes.py:566-692`);
- XRAS New (`src/sam/xras/handlers/new.py:159-229`);
- in both, the first account's `_seed_members` adds the lead and admin;
- `remove_user_from_project` (133-182) refuses the lead and clears `project_admin_user_id` when the admin is removed.

Not covered, and accepted: Flask-Admin `ProjectAdmin` (`src/webapp/admin/custom_model_views.py:72-100`) bypasses `update()`, but it is off in prod (`config.py:356`).

Blind spot, brought into scope as item 9 (Ben, 2026-09-24):
- `Project.get_members_access_status` (`projects.py:573+`) and the members page "Grant access" look only at `self.users`, the people who already have rows, so a lead with no rows never appears there;
- the access grid's "Reconcile all" button renders only when `member_rows` is non-empty;
- the grid rendered every lead cell `checked disabled` whatever the real state, so a lead with gaps also looked healthy.

## Decisions (Ben, 2026-09-24)

- **Seed on change only.** A Details save with an unchanged lead writes nothing. Projects that already lack their lead are healed with `--reconcile` or the button, not as a side effect of saving.
- **The old lead or admin keeps their membership.** Nothing is revoked on reassignment.
- **`--reconcile` is wired to `reconcile_project_access`**, the membership reconcile, not removed. Rewrite the help text.
- **`revoke_user_resource_access` blocks the admin**, as it does the lead.
- **Breaking legacy parity on the XRAS role endpoint is intended.** A lead who is not a member is the defect.

## Implementation

### 1. `Project.ensure_members(*user_ids) -> list[AccountUser]`

This goes in `projects.py`, which already imports `Account` and `AccountUser` through `from ..accounting.accounts import *`.
- For each `Account.is_active` (non-deleted) account of the project, and each user id: add `AccountUser(start_date=datetime.now().replace(microsecond=0), end_date=None)` **unless** the user already has an unended row there (`end_date IS NULL OR end_date > now`).
- Those skip semantics match `add_user_to_project` (`manage/__init__.py:113-117`).
- The microsecond truncation matches `_seed_members` (`accounts.py:189-192`, the MySQL rounding trap).
- It flushes, never commits, and returns the rows it added.
- Factor the per-account insert into one helper on `Account` and have `_seed_members` use it, so the two paths cannot drift. Also make `_seed_members` return the rows it added.

### 2. `Project.update` seeds on change

At 357-360, when `project_lead_user_id` or `project_admin_user_id` is not None **and differs from the current value**, assign it and then call `self.ensure_members(new_id)`.

This closes the Details tab, the XRAS role change and the XRAS Update defect-3 lead in one place.

### 3. `change_project_admin`

Keep the guard. Replace the direct write with `project.update(project_admin_user_id=new_admin_user_id)` when an id is given. Keep `project.project_admin_user_id = None` for clearing, because `update()` treats None as "no change".

A former member whose only rows have expired passes the guard and now also gets live rows.

### 4. XRAS role endpoint

In `roles.py:103-113`, replace the "Legacy inserts no roster row, so neither do we" comment with the invariant, and change the warning to say the lead is being added as a member. The response bytes are unchanged.

### 5. Admin revoke guard

In `revoke_user_resource_access`, add `if project.project_admin_user_id == user_id: raise ValueError("Cannot revoke the project admin's access")`, next to the lead guard at 309-310.

The access-grid toggle (`projects_routes.py:3505-3512`) already turns that `ValueError` into a panel error. Confirm it renders.

### 6. CLI (`src/cli/project/commands.py`)

`_reconcile_project`:
- run `reconcile_project_access` inside `management_transaction(self.session)`;
- have `reconcile_project_access` return the rows it added (collected from `_seed_members`). Its return value is currently None and nothing reads it;
- print a table of the added rows (username, resource) or "nothing to add", and exit 0;
- set the help text at `cli/cmds/admin.py:85` to "Give the lead, admin and every member access to every project resource".

`_validate_project`:
- for each of the lead and the admin, list the non-deleted accounts where they have no live row (`start_date <= now` and `end_date` null or in the future, the same predicate as the feed);
- add an issue per role that names the resources, e.g. `Project lead yeager is not a member on: Casper, Derecho, ...`, and suggest `--reconcile`;
- exit EXIT_ERROR (2), as the existing issues do.

### 9. `Project.users` means everyone on the project

It is systemic. `Project.users` meant "holds an unended row". Its consumers mostly wanted "who belongs", and three hand-rolled unions had grown around it: `roster`, `get_users_on_project`, and the members API's separate lead and admin. `User.active_projects` already counts a project the user leads or administers with no rows.

- **`users`** is lead + admin + `account_linked_users`, deduplicated.
- **`account_linked_users`** is the old body, rows only. `has_user`, which the XRAS role warning uses, reads it.
- **`roster`** is deleted and its four callers use `users`.
- **Knock-on effects**:
  - The detector gains `is_admin`.
  - The provisioning check, the CLI `--list-users` and `active_user_count` now include a lead or admin with no rows.
  - `GET /api/v1/projects/<projcode>/members` `total_members` is `len(project.users)`. It used to add the lead and admin on top of their own rows, counting them twice.
- **Grid** (`project_access_grid_htmx.html`):
  - an admin badge;
  - a lead or admin cell shows its real state;
  - an unchecked cell stays clickable, so it can be granted;
  - a checked cell is locked, because revoking it is refused.
- **Members page** (`members_table.html`): the partial-access warning no longer skips the lead.
- **Not changed.** "Every member on every resource" stays the expected shape, surfaced by the grid and `--validate` and fixed by the reconcile paths. It is not enforced in code. Only the lead and admin are enforced.
- **Open.** `account_linked_users` still ignores `start_date` and deleted accounts, where `AccountUser.is_active` and the feed do not. Aligning them is a separate change to the predicate.

### 7. Docs

- **CLAUDE.md** CLI block: `sam-admin project X --reconcile` now means the membership reconcile.
- **CLAUDE.md** §7 or Common Pitfalls, one line: the lead and admin must be live members, and `Project.update` enforces this.
- **Comment budget**, per CLAUDE.md §12: one-line docstrings, no changelog phrasing.

## Tests

**Model.** Use a new `tests/unit/manage/test_project_lead_membership.py` or `test_management_functions.py`, with factories (`make_project`, `make_account`, `make_user`, `make_allocation`).
- `ensure_members`:
  - adds a row on each live account;
  - skips an existing live row;
  - re-adds after an expired row;
  - ignores a deleted account;
  - returns the rows it added.
- `update(project_lead_user_id=new)`:
  - seeds the new lead;
  - leaves the old lead's rows alone;
  - writes no rows when the value is unchanged.
- The same three for the admin.
- `change_project_admin`:
  - a former member with only expired rows gets live rows;
  - clearing to None still works.
- `revoke_user_resource_access` raises for the admin.
- `reconcile_project_access` returns the rows it added.

**Existing tests that must stay green.** The existing tests are at `test_management_functions.py:105,120,240-281`, `tests/api/test_member_management.py:133-190`, `tests/unit/webapp/test_crud_operations.py:272-363` (`TestAccountCreatePropagation`) and `tests/unit/manage/test_user_resource_access.py:187,241`.

**XRAS.**
- `tests/api/test_xras_roles.py`: the `no_write` fixture (55-70) monkeypatches `Project.update`, so `test_success_asks_for_the_right_write` (265-280) should still pass. Add a real-`update()` test in which the lead gains rows.
- `tests/unit/xras/test_xras_update_handler.py:325/340`: assert membership too, including the defect-3 shape (a future-dated PI left out of the roster; see `tests/unit/xras/test_xras_roster.py:175-205`).

**CLI.** No `--validate` or `ProjectAdminCommand` tests exist yet. Follow the invocation pattern already used in `tests/unit/cli/`:
- `--validate` flags a lead-less factory project and exits 2;
- `--reconcile` fixes it and exits 0;
- a second `--validate` passes.

**What changed from this plan in the tests.**
- The XRAS roles route test stays write-free (`no_write`). A route write commits on `db.session`, outside the SAVEPOINT, so the lead's new rows are asserted at the model layer, in `tests/unit/manage/test_project_lead_membership.py`.
- The provisioning tests use fake project objects, so the change in what `users` means is covered by `TestProjectUsers` instead.

## Verification

1. `source etc/config_env.sh`, then run:
   `pytest tests/unit/manage tests/unit/xras tests/unit/cli tests/api/test_xras_roles.py tests/api/test_member_management.py -q`
2. `make check-all` (both database backends, perf, helm, e2e).
3. Local smoke test on the dev DB. The CLI needs `SAM_DB_*` exported from `LOCAL_SAM_DB_*`.
   1. Find a local project whose lead has no live rows (the census query is below).
   2. `sam-admin project <code> --validate` gives 2 and names the lead.
   3. `--reconcile` lists the added rows.
   4. `--validate` then reports ✅.
   5. The local `/api/v1/directory_access/hpc` lists the lead in the group.
4. Change a lead on the webdev Details tab and confirm the new lead's `account_user` rows appear.

Census query, for the local DB or prod read-only; re-run to find targets or to measure a fix:

```sql
WITH live AS (
  SELECT DISTINCT a.account_id, a.project_id FROM account a
  JOIN project p ON p.project_id=a.project_id AND p.active=1
  JOIN resources r ON r.resource_id=a.resource_id AND r.configurable=1
  JOIN access_branch_resource abr ON abr.resource_id=r.resource_id
  JOIN access_branch ab ON ab.access_branch_id=abr.access_branch_id AND ab.name='hpc'
  JOIN allocation al ON al.account_id=a.account_id AND al.end_date > NOW() - INTERVAL 90 DAY
  WHERE a.deleted=0)
SELECT p.projcode, u.username FROM project p
JOIN users u ON u.user_id=p.project_lead_user_id AND u.active=1
WHERE p.active=1 AND EXISTS (SELECT 1 FROM live l WHERE l.project_id=p.project_id)
  AND NOT EXISTS (SELECT 1 FROM live l JOIN account_user au ON au.account_id=l.account_id
      AND au.user_id=p.project_lead_user_id AND au.start_date<=NOW()
      AND (au.end_date IS NULL OR au.end_date>NOW()) WHERE l.project_id=p.project_id)
ORDER BY p.projcode;
```

## Out of scope and follow-ups

- **Bulk remediation of the other ~104 lead-absent and ~240 admin-absent projects.** Use `--validate` and `--reconcile` once they ship. Treat the 55 end-dated projects, including the 2026-01-28 burst, separately until Runbook query 1b confirms they are legacy whole-user closes.
- **The feed itself.** It is frozen; make no change there.
- **Flask-Admin `ProjectAdmin`.** It is off in prod.
