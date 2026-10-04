# Hard deletes that legacy SAM reads as meaning: audit handoff

Written 2026-10-02 after the `facility_resource` incident. **This is input for a planning
session, not a fix list.** Ben wants an audit before anything changes.

## The incident this generalizes

- **2026-10-01:** Admin → Resources → **Unset** removed the bogus Derecho / Derecho GPU fair-share
  overrides on prod and dev. Unset **deleted** the `facility_resource` rows.
- **SAMuel was fine.** Its query `LEFT JOIN`s the table and falls back to the facility default.
- **Legacy SAM broke.** It reads the row as *facility-on-resource membership*, so
  `/fairShareTree/v3/Derecho` began serving `"facilities": []`:
  - `DefaultFacilitiesForResourceQuery` walks `resource.getFacilityResources()`.
  - The legacy GUI's resource list for New Allocation Transaction (`findConfigurableForFacility`)
    reads the same rows.
- **Repaired 2026-10-02:**
  - `scripts/repair/restore_derecho_facility_resource.sql` restored 12 rows with a NULL percentage, so
    both stacks use the default.
  - A `PUT .../fairShareTree/v3/{resource}` rebuilt legacy's in-memory tree.
  - `clear_override` now NULLs the value instead of deleting the row.
  - PR branch: `fix/facility-resource-keep-row`.

**The trap in general:** SAMuel decides "this row is optional, so absent = default" and deletes it.
Legacy, which still serves systems consumers, decides "row present = member" and silently drops
the entity. The parity checker cannot see this until the delete actually happens in prod.

## Question for each delete site

For every hard delete on a table that legacy Java reads, answer:
1. **What does legacy infer from the row's presence?** Check `~/codes/sam`: the `hbm.xml` mappings,
   the Java sets walked with `.getXxx()`, and the named queries in `hibernate/jdbcQuery.xml`.
2. **What would be the right non-destructive form?** Options: NULL a column, soft-delete (`deleted` /
   `active`), end-date, or "the delete is genuinely safe".
3. **Does legacy's GUI or Quartz job also write the table?** If so, both stacks can disagree on the same row.

Write the result as old-vs-new rulesets side by side, with blast radius
(the old-vs-new ruleset style used for the fstree parity diffs). Then stop for Ben and George's call.

## Starting census (grep, 2026-10-02; verify, don't trust)

**Explicit `session.delete`, 11 sites:**
- `src/sam/manage/__init__.py:142` — `_end_membership` deletes a **never-started** `account_user` row.
  This is deliberate (CLAUDE.md §7). Check legacy's `getUsersAssignedToProjectOnResource` window reading anyway.
- `src/sam/resources/resources.py:443` — the hard-delete method for a disk resource root directory.
  Its docstring already prefers soft-delete.
- `src/sam/projects/contracts.py:473` and `src/webapp/dashboards/admin/projects_routes.py:2995` —
  `project_contract` unlink.
- `src/sam/manage/account_requests.py:316` — `account_request` purge. This is a SAMuel-only table, so likely safe.
- `src/sam/security/samuel_roles.py:102` — SAMuel RBAC tables. Legacy does not read them; likely safe.
- `src/sam/summaries/allocation_state.py:96` — SAMuel read model. Likely safe.
- `src/webapp/dashboards/admin/notifications_routes.py:344,524` — SAMuel notification tables. Likely safe.
- `src/webapp/dashboards/status/blueprint.py:749` and `src/webapp/api/v1/status.py:640` — the
  `system_status` DB. Not legacy.

**`cascade='all, delete-orphan'`, 17 relationships.** Removing a child from the collection deletes it:
- `Account`: `allocations`, `charge_adjustments`, `responsible_parties`.
- `Allocation`: `transactions`.
- `User`: `default_projects`, `email_addresses`, `institutions`, `organizations`, `phones`,
  `resource_homes`, `resource_shells`.
- `AdhocGroup`: `tags`, `system_accounts`.
- `Project`: `contracts`, `default_projects`.
- `Resource`: `facility_resources`.
- `operational.py:52`: `products`.

Find which write paths actually remove from these collections. `user_resource_shell`/`_home`,
`user_institution`, `user_organization` and `default_project` feed legacy sysacct outputs, so they are
the first suspects.

**Also sweep:**
- `query(...).delete()` / `delete(synchronize_session=...)` bulk deletes. None were found by the first grep;
  re-check with broader patterns.
- `text("DELETE ...")` in `scripts/` and `src/scheduling/`.
- Manual CLI paths (`sam-admin`), not only the webapp.

## Tooling that helps

- **`utils/parity/check_legacy_apis.py`.** Run it with the SSG key for both stacks:
  `SAM_NEW_API_USER=$SAM_LEGACY_USER SAM_NEW_API_PASS=$SAM_LEGACY_PASS`. The parity key gets a 403 on
  SAMuel's `fstree_access` under DB-backed RBAC.
  - Most checks are *legacy ⊆ new*, so a legacy-side drop shows up only where a check runs in the
    new → legacy direction (e.g. `new facilities ⊆ legacy`). See `utils/parity/README.md`.
- **Local dress rehearsal.** Perform the delete on the 3306 snapshot, then diff legacy behavior by reading
  the Java. We have no local legacy server.

## Open items found along the way (not part of the audit)

- **`HPC_Futures_Lab`.** Legacy has only the CISL `facility_resource` row, so it serves CISL alone at 1%.
  SAMuel's per-resource endpoint serves five other facilities, on lifecycle-only nodes. This is the
  same membership-semantics gap, seen from the other side.
- **`allocationAmount` rounding.** Legacy truncates and SAMuel rounds. On prod: UWAS0169 has 99999.98,
  NERP0002 has 4010001.50.
- **Cheyenne.** Legacy returns nothing for it. SAMuel still serves it, and its shares sum to 100.95
  (NCAR 29, CISL 1.9 overrides).
