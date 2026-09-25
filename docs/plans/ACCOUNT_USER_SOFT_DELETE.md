# Membership removal: soft-delete by end date instead of hard delete

**Status: implemented on `account-user-soft-delete` (PR #618), 2026-09-24.**
**Branch:** `account-user-soft-delete`, from `origin/staging` after PR #617 merged (`6451c978`). #617 adds `Project.live_accounts` and the reconcile changes this builds on. One PR against `staging`.

## Progress

- [x] 1. One removal primitive: end-date a started row, delete a never-started row. `remove_user_from_project` and `revoke_user_resource_access` both use it (`_end_membership` in `src/sam/manage/__init__.py`).
- [x] 2. The `change_project_admin` guard requires an unended row.
- [x] 3. Confirm the UI/API re-render hides the removed member; no reader changes (`test_re_render_sources_drop_the_member`).
- [x] 4. Tests: update the three that assume a delete or expired-row admin; add the new cases (`tests/unit/manage/test_membership_soft_delete.py`).
- [x] 5. Docs: CLAUDE.md §7 line; tick this checklist.
- [ ] 6. `make check-all` and the local smoke test.

## Context

Ben's convention, and legacy SAM's, is that `account_user.end_date` stays empty and is used for **soft deletion**. This app instead **hard-deletes** membership rows in two places, both in `src/sam/manage/__init__.py`:
- `remove_user_from_project` (the members page Remove button, `DELETE /api/v1/projects/<projcode>/members/<username>`);
- `revoke_user_resource_access` (one cell of the Manage Project → User / Resource Access grid).

**What legacy SAM does.** Read from `legacy_sam/src/main/java/edu/ucar/cisl/sam/` on 2026-09-25:
- **Legacy never deletes an `account_user` row.** No HQL or SQL deletes one.
- **Inserts are open-ended.** The only insert is `Account.assign` → `new AccountUser` (`project/domain/model/Account.java:113-118`, `user/domain/model/AccountUser.java:21-28`), which hard-codes `end_date = NULL` and start = now. That covers the UI, XRAS and AMIE.
- **Every removal sets `end_date` to the server's "now"**, through `User.unassignFromAccount` (`user/domain/model/User.java:348-354`). The callers:
  - UI member removal (`project/assignment/command/DefaultDefineProjectResourceUsersCommand.java:126-128`);
  - AMIE InactivateAccount (`amie/command/AMIEAccountManualTaskHandler.java:256-282`);
  - user deactivation through `PUT /protected/admin/userlifecycle/deactivate/{username}` → `User.unassignFromAccounts()` (`User.java:377-390`);
  - the Quartz `AssignmentsOnDecommissionedResourcesSweeper`, daily at 02:15.
- **Project inactivation does not touch memberships.**

**Why it matters.**
- **History.** The 2026-09-25 membership forensics depended on ended rows existing: the Cheyenne close on 2024-11-12 (19,614 rows), and the batched legacy deactivations on 2025-10-09, 2025-12-05 and 2026-01-28. A hard delete leaves no trace of who was removed, or when.
- **Charging.** The DDL comment on both date columns reads: *"HPC usage by a user can only be charged if their use falls within these start and end dates."* A deleted row takes that window with it.
- **Repeated rows are safe.** `account_user` has **no unique constraint** (only the PK, two FKs and indexes), so several rows for one (account, user) are allowed, and legacy already produces them on every re-add.

## Decisions (Ben, 2026-09-25)

- **Removal end-dates every unended row that has already started.**
  - Its `end_date` becomes now, floored to the second, minus 1 second. See the traps below for why.
  - **Amendment (Ben, 2026-09-24):** if that lands exactly on midnight, step back one more second. `normalize_end_date` only rewrites an exact 00:00:00, so the 1-second back-off alone moves the hole from the first second after midnight to the second one; with the extra step a removal at 00:00:01.x ends at 23:59:59 of the previous day, like one at 00:00:00.x.
  - A membership shorter than a second ends before it started (end = start − 1 s). Harmless: no reader treats a row live when end < start. `add_user_to_project` now floors its default `start_date` like `grant_user_resource_access` and `_add_live_members` do; without that, MySQL rounding could push a just-added row's start past the removal's "now" and the row would be deleted as never-started (the updated `test_removes_user_from_all_accounts` catches this).
- **A row that never started is still hard-deleted.** That is a row whose `start_date` is in the future, which the Add member modal allows. It was never effective, so no history is lost, and end-dating it would store end < start.
- **Rows that already ended are left untouched.** They are history. Today `remove_user_from_project` deletes *every* row the user has on the project, including old ended ones.
- **`change_project_admin` requires an unended row**, meaning `end_date` null or in the future. That is the same test `add_user_to_project` uses to skip. The lead is still allowed.
  - This **reverses** #616's "an ex-member with only expired rows qualifies". Once removals leave rows behind, "any row ever" would let someone removed years ago be made admin, and `Project.update` would quietly re-add them. An ex-member must be re-added first.
- **Existing guards stay:**
  - `remove_user_from_project` refuses the lead and clears `project_admin_user_id` when the admin is removed;
  - `revoke_user_resource_access` refuses both the lead and the admin.

## Implementation

### 1. One removal primitive

Put it next to the model, for example `AccountUser.end_membership(self, now)`, or as a private helper in `sam.manage`. It applies the start-date rule:
- a row with `start_date > now` → `session.delete(row)`;
- otherwise → `row.end_date = cutoff`, where `cutoff = now.replace(microsecond=0) - timedelta(seconds=1)`.

Both removal functions select only **unended** rows (`end_date IS NULL OR end_date > now`) for the user on the relevant account(s), run each one through the primitive, and flush. Neither commits.

**Traps.** Keep a one-line comment for each.
- **The midnight edge.** `normalize_end_date` (`src/sam/base.py:53`), applied by `DateRangeMixin`'s end-date validator, turns any end at exactly 00:00:00 into 23:59:59. A removal in the first second after midnight would leave the member live for the rest of the day. The 1-second back-off avoids that.
- **MySQL rounds half-up.** DATETIME has no fractional part, so a microsecond "now" can round into the next second and leave the row live for a moment. The same trap is commented at `Account._add_live_members` (`src/sam/accounting/accounts.py`) and `grant_user_resource_access`. Floor to the second.
- **The re-render in the same request.** Both routes commit and immediately re-render the members table or the grid. Unended checks use `>=`, and "now" is inclusive. Without the back-off, the member just removed could still be listed.

### 2. The `change_project_admin` guard

In `src/sam/manage/__init__.py`, the "must hold a row on the project" query gains `or_(AccountUser.end_date.is_(None), AccountUser.end_date > now)`. Its docstring changes from "even an expired one" to "an unended row".

### 3. Readers: nothing to change (census, 2026-09-25)

Every live-path read already filters to live or unended rows. Legacy and the repair runbooks already fill the table with end-dated rows, so a soft-removed member drops out of these reads exactly as a deleted one does today. The predicate letters used below:
- **(a)** live: start ≤ now and end null or ≥ now;
- **(b)** unended: end null or ≥ now;
- **(c)** `end_date IS NULL` only;
- **(d)** no date filter.

| Reader | Predicate | Effect of the change |
|---|---|---|
| Access grid and member cells: `Project.get_members_access_status` (`src/sam/projects/projects.py`) | a | none |
| `Project.active_account_users` → `account_linked_users` / `users` / `get_user_count` / `has_user` | b | none |
| Members page: `get_users_on_project` (`src/sam/queries/users.py`) | b + `User.is_active` | none |
| `User.active_projects` (`src/sam/core/users.py`): dashboard, access control, CLI, user card | b | none |
| `add_user_to_project` skip check; `_add_live_members` / `ensure_members` | b | a re-add inserts a fresh row, as legacy does |
| `Account._seed_members` sibling query | c | an ended row isn't copied onto new accounts; none |
| `--validate` (`_resources_without_live_row`); the grant guard; the add-member autocomplete exclusion (`get_project_member_user_ids`) | a | none |
| Frozen feeds `directory_access.py`, `fstree_access.py` (`src/sam/queries/`) | a | none; `project_access` never reads the table |
| `change_project_admin` guard | **d** | **changed on purpose** (Decision above) |
| `User.all_projects` / `_projects_w_dups` / `former_projects` (`src/sam/core/users.py`) | **d** | a removed project now **stays**, under `former_projects['ended']`: see below |
| `_describe_additions` history label (`src/cli/project/commands.py`, from #617) | d | an ex-member reads `ended DATE`, not `never a member`; label only |

**Re-verified against `origin/staging` on 2026-09-24.** Additional direct readers, none needing a change: `get_project_members` (`src/sam/queries/projects.py`, b), `get_user_project_access` (`src/sam/queries/statistics.py`, b), `membership_active` in `build_user_projects` (`src/cli/user/builders.py`, b), the `get_user_inaccessible_resources` fallback (`src/sam/projects/projects.py`, a), `get_user_with_details` (`src/sam/queries/users.py`, d, export only), and the `Account.users` relationship cascade. The unended operator drifts: `_add_live_members`, `_resources_without_live_row` and `directory_access` use `>`, the rest `>=`, and `fstree` also accepts a NULL start; a cutoff strictly before now satisfies all of them.

**Nothing else reads the table directly.** XRAS (`api/xras/roles.py` → `has_user`; the New/Update handlers → `add_user_to_project`), account requests (enroll/invite/reconcile → `add_user_to_project`) and `src/scheduling/` (`expiration_notices` → `project.users`) reach it only through the (b) readers above. Charges count users from `CompChargeSummary.username`; `roster`, `preflight`, `sam_merge_targets` and `merge_person` never touch it. Nothing in `src/` reassigns rows between users.

### 4. Behavior changes to accept and mention in the PR

- **Former projects.** A removed member's project now shows under "Inactive / Former Projects" on the admin user card (`src/webapp/templates/dashboards/user/partials/user_card.html`), and in `sam-search user X --inactive-projects --list-projects` (Membership column "Ended"), where it used to vanish. That matches the `g_former_projects` glossary text.
- **Audit log.** Removals log as UPDATE instead of DELETE (`src/webapp/audit/events.py`). A never-started row still logs DELETE.
- **UI text is unchanged.** The members page confirm ("will remove them from all resources") is still true.

## Tests

**Update:**
- `tests/unit/manage/test_management_functions.py` `test_removes_user_from_all_accounts`. It expects `count() == 0` with no date filter; it should now expect no *live* rows, with the rows end-dated.
- `tests/unit/manage/test_user_resource_access.py` `test_revoke_removes_membership`. Its `_membership_rows` helper has no date filter; the test should now expect the row to be end-dated and not live.
- `tests/unit/manage/test_project_lead_membership.py` `test_former_member_with_only_expired_rows_gets_live_rows` (from #616). It now expects `ValueError`. Keep a positive case where an unended member is promoted and seeded.

**Add** (factories: `make_project`, `make_account`, `make_allocation`, `make_user`):
- removal end-dates started rows at about now − 1 s, and they are no longer `AccountUser.is_active`;
- a future-start row is deleted;
- rows that already ended are untouched, with the same `end_date` and the same count;
- a re-add after removal inserts a fresh open row and keeps the ended one (2 rows);
- revoke end-dates only that account's row;
- **midnight edge:** with "now" patched to `00:00:00.4` **and** to `00:00:01.4`, the stored end is 23:59:59 of the *previous* day, not today's;
- the removed member lands in `User.former_projects()['ended']` (next to the existing `tests/unit/manage/test_user_former_projects.py`);
- `change_project_admin` rejects an ex-member whose rows are all ended, and accepts an unended member;
- the htmx `DELETE /project-members/<projcode>/<username>` re-render no longer lists the user. There is no success-path test for it today. Route writes commit on `db.session`, so follow the house convention (model-layer happy path plus render smoke).

**Must stay green:** `tests/api/test_member_management.py`, `tests/api/test_route_authz_hardening.py`, the grid-toggle tests in `tests/unit/webapp/test_htmx_handler_routes.py`, `tests/unit/cli/test_project_admin_membership.py`, and the re-add-after-expiry test in `test_management_functions.py`.

## Verification

1. `source etc/config_env.sh`, then `pytest tests/unit/manage tests/unit/webapp tests/unit/cli tests/api/test_member_management.py -q`.
2. `make check-all`: both DB backends, perf, helm, e2e. Watch for an e2e `ERR_NETWORK_IO_SUSPENDED` flake, which is a laptop-sleep artifact; rerun `make e2e`.
3. Local smoke on webdev (`docker compose up webdev --watch`, :5050; flush the fragment cache with `docker exec samuel-cache redis-cli -n 0 FLUSHDB`):
   1. Remove a member on a local project from the members page:
      - their rows remain, with `end_date` ≈ now − 1 s;
      - the members table and the access grid drop them;
      - `/api/v1/directory_access/hpc` no longer lists them in the group;
      - their user card shows the project under Former.
   2. Re-add them: two rows per account, one ended and one open.
   3. Revoke a single grid cell: only that account's row is end-dated.
   4. Try making an ex-member admin through `PUT /project-members/<projcode>/admin`: it is refused.

## Out of scope

- **Backfill.** Hard-deleted rows are gone, and nothing can reconstruct them.
- **The Add member modal's optional End Date** (a scheduled removal). #617 labels it "Usually blank; access follows the allocation duration". Leave it.
- **The external writer.** Something outside this app (legacy SAM, a sync job, or manual SQL) still end-dates rows in prod. Note it; don't chase it here.
- **Dead helpers.** `User.active_account_users` and `User.users` in `src/sam/core/users.py` iterate an `AccountUser.users` attribute that doesn't exist. Delete them in a separate cleanup.
- **Removing members on project inactivation.** Legacy doesn't do it, and neither should this change.
