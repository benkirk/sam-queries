# Hard deletes (and soft deletes) that legacy SAM reads as meaning: audit

Written 2026-10-02 from `docs/plans/HARD_DELETE_AUDIT_HANDOFF.md`. Legacy paths below are relative
to `~/codes/sam/src/main/` (`H/` = `resources/hibernate/`, `J/` = `java/edu/ucar/cisl/sam/`).

**Posture (Ben, 2026-10-02):** SAMuel must not break legacy until legacy is retired. Where the two
disagree, SAMuel changes. So every row below ends in a SAMuel-side verdict, not a question.

## Summary

| # | Site | Table | Legacy impact | Verdict |
|---|---|---|---|---|
| 1 | Renew supersede, `src/sam/manage/renew.py:175` | `allocation` (**soft** delete) | **Live.** 79 rows on prod that legacy still serves as current | **Change** (low-hanging): collapse the dead row's window + repair |
| 2 | Disk-root delete, `resources.py:443` / `resources_routes.py:778-791` | `disk_resource_root_directory` | Legacy disk engine: absent = ingest error / re-map; inactive = exempt | **Changed** — route and `delete()` removed; the active toggle is the retire path |
| 3 | Disk re-import, `src/cli/accounting/commands.py:928-938, 1192-1197` | `disk_charge_summary`, `disk_charge`, `disk_activity` | None from the delete; coexistence risk closed on prod (no triggers) | **Keep**; 2026-06-06 left legacy-shaped |
| 4 | Contract unlink, `projects_routes.py:2995` | `project_contract` | Same as legacy's own unlink | **Keep** |
| 5 | Never-started membership, `src/sam/manage/__init__.py:142` | `account_user` | None in any systems output | **Keep** |
| 6 | `account_request`, `samuel_role_permission`, `account_allocation_state`, `notification_*` | SAMuel-only | None | **Keep** |
| 7 | 17 `delete-orphan` collections | user/account/resource children | None today: no remover exists | **Keep**; tripwire list in §7 |

The trap this generalizes is not only hard deletes. **Legacy ignores `allocation.deleted` and
`account.deleted` in every systems output**, so a SAMuel soft-delete on either is invisible to
legacy — row 1 is that case, found by this audit.

## Old-vs-new rulesets

### 1. Renew supersedes a fully-contained allocation — `allocation.deleted = 1` (CHANGE)

**SAMuel.** `_truncate_overlapping_allocs` (`renew.py:130-185`): an overlap that starts on/after the
new start is "superseded": `deleted = True` plus a `[DELETE]` ADJUSTMENT transaction for 0. Dates,
amount and parent are left as they were. `deletion_time` is not set. SAMuel's readers skip it.

**Legacy.** `deleted` is filtered only in GUI repository criteria (`HibernateAllocationRepository`).
Every systems reader sees the row:
- sysacct / directoryaccess / groupstatus SQL (`H/jdbcQuery.xml:443-464, 561-566, 607-623, 749-754`)
  join `allocation` on `(end_date + grace) > NOW()` with **no `deleted` filter**.
- fairShareTree v2/v3 `DefaultProjectAccountDetailRepository.setAllocationData`: loops every
  allocation on the account; each one whose range holds the target date **overwrites**
  `activeAllocation` / `allocationId` / `allocationInherited`. `Account.allocations` is a Hibernate
  `set`, so which overlapping row wins is iteration order — not determined.
- diskquota (`Account.getLatestAllocation()`), user lifecycle (`lifecycleQuery.xml:162-176`).

**Blast radius (prod, read-only `hpc-reader`, 2026-10-02; the local snapshot is identical):** 79
superseded rows — every `deleted=1` allocation on prod — all written 2026-08-04 → 2026-09-22 by
Renew's pre-#598 `replace_existing=True` path (76 from one run, `Superseded by renew on 2026-10-01 →
2027-09-30`). Every one still has `end_date > NOW()`, none starts in the future, and each overlaps
exactly one live row on the same account (no dead row lacks a live twin).
- Amount: identical to the live row in all 79 → fairShareTree `allocationAmount` unaffected today.
- **Parent differs in 57/79** (56 inheriting children of a dead master, 1 dead root whose live twin
  is a child) → legacy may serve `allocationInherited` / `allocationId` from the dead row, and (for the
  shared-pool case) roll charges up a different tree. No live allocation has a dead parent.
- **Dates differ in 6/79.** `UFSU0023` Casper / Campaign_Store / Derecho: the dead rows end
  **2033-07-31**, the live ones 2027-09-30 → legacy keeps UFSU0023's groups and directory access
  for six years past its real end. `NCGD0071-73` Data_Access: the dead rows (2026-03/04/05 →
  2027-03/04/05) were the real allocations until 2026-09-30; the live twins start 2026-10-01 and
  nothing else covers the months before. These three are what #598 now truncates instead of deleting.

**Non-destructive form legacy honors?** Not an end before the start: legacy's
`DateRange.validateStartEnd` throws `startDate follows endDate` (`J/util/DateRange.java:347-351`), and
`compareDateToRange` calls it for every allocation in the fairShareTree loop, so one such row breaks
the tree for its resource. Legacy decides "active" from the **row's** dates before any transaction
replay (`DateBoundedAllocationAmount(allocation, targetDate)` runs only for the active row), and
`Account.getLatestAllocation()` picks by `end_date`.

Ben's priority (2026-10-02): low-hanging fixes that don't undo SAMuel's own choices or force a refactor.

| Option | SAMuel change | Legacy view | Fit |
|---|---|---|---|
| **C. Keep the flag, end the row where its twin begins** — `end_date` = later of (end of the `start_date` day, twin's `start_date` − 1 s) | ~5 lines in `renew.py` (log the `[DELETE]` txn first, so it snapshots the real window, then collapse) + repair script | A one-day allocation in the past: `prior`, never `active`; loses `getLatestAllocation` to the live row | **Recommended.** Soft-delete and the transaction history SAMuel shows (`include_deleted=True`, `allocations/blueprint.py:1049`) are kept |
| A. Hard-delete the superseded row + its transactions | `renew.py` deletes; repair script | What legacy's own GUI delete does (`Allocation.delete()`) | Rejected: erases the history SAMuel deliberately keeps |
| B. Edit in place instead of supersede | Renew updates the contained row rather than creating a twin | One row | Deferred: refactor of Renew + child linking + #692 guard |

The "twin start − 1 s" arm is for the NCGD0071-73 shape: collapsing those to one day would erase
six real months from legacy's history (past-date fairShareTree, accounting-engine re-charges); ending
them at 2026-09-30 23:59:59 is exactly #598's truncation. 76 rows share their twin's start and
collapse to one day. Today's `renew.py` supersedes only rows starting on/after the new start, so the
code change only ever hits the one-day arm.

Residual of C: a supersede whose `start_date` is still in the future (the double-click case) is
`active` to legacy on that one day, alongside the live row. None of the 79 starts in the future.

Not in scope, SAMuel-side: NCGD0071-73 are also missing from SAMuel's own history for 2026-03 →
2026-09 (the #597 gap). Undeleting them as truncated rows would match #598; Ben's call.

Repair query (read-only census, works on prod):
```sql
SELECT d.allocation_id, p.projcode, r.resource_name, d.start_date, d.end_date, d.amount,
       l.allocation_id AS live_id, l.end_date AS live_end,
       (d.parent_allocation_id <=> l.parent_allocation_id) AS same_parent
FROM allocation d
JOIN allocation l ON l.account_id = d.account_id AND l.deleted = 0
                 AND l.start_date <= d.end_date AND l.end_date >= d.start_date
JOIN account ac ON ac.account_id = d.account_id
JOIN project p USING (project_id) JOIN resources r USING (resource_id)
WHERE d.deleted = 1;
```
Repair (C): set each dead row's `end_date` to the rule above, only for rows whose
`[DELETE] Superseded by renew` transaction proves SAMuel wrote the flag (all 79); log an EDIT
transaction per row (amount 0) so the change is in the history. Amount is untouched, so
replay(history) == amount still holds.

### 2. Disk resource root directory hard delete (CHANGE)

**SAMuel.** `DiskResourceRootDirectory.delete()` (`resources.py:437-444`) hard-deletes, reached only by
`POST /admin/htmx/admin/disk-roots/<id>/delete` (`DELETE_RESOURCES`). No template links to it; the UI
renders only the active toggle.

**Legacy.**
- diskquota / dashboard path → resource (`DefaultDiskResourceDirectoryByPathSelector.java:34-49`)
  skips inactive rows: absent ≡ inactive.
- Disk accounting engine (`MappedNetworkNaturalDomainRepository.java:113-120`) ignores `active`;
  `DiskActivityChargeExemptionPolicy.java:10-11` charges an **inactive** root as exempt, while an
  **absent** root fails L2 validation (`DefaultDiskResourceRootDirectoryValidator.java:37-43`) or
  re-maps to a shorter prefix on a different resource.
- Legacy never writes the table.

**Blast radius.** None today (no UI path). Matters only if someone POSTs by hand and legacy's glade
ingest still runs (§3 says its Quartz job is still scheduled).

**Change (done).** Route and `DiskResourceRootDirectory.delete()` removed; nothing linked to them.
`toggle-active` already gives legacy the `active=0` it honors. The `DELETE_RESOURCES` routes that
remain are all soft retires (resource/machine decommission date, queue end date, fair-share Unset).

### 3. Disk accounting re-import (KEEP)

**SAMuel.** `sam-admin accounting --disk` (cron `scripts/cron/accounting/disk/run_ncar_accounting.sh`)
deletes the `(snap_date, resource)` slice of `disk_charge` → `disk_activity` and the day's
`disk_charge_summary` for the resource's accounts, then re-inserts.

**Legacy.**
- `disk_charge_summary`: legacy's own recompute is `DELETE ... WHERE activity_date = :date`
  (`H/AccountingNamedQuery.xml:386-391`) — wider than SAMuel's (all resources). Delete is native.
- `disk_activity` / `disk_charge`: legacy upserts on the unique key, never deletes. No legacy reader
  keys on row ids; the only FK in is `disk_charge → disk_activity` (NO ACTION), which SAMuel orders
  correctly. All readers aggregate by date/account/project.

**Not the delete, but found here (coexistence):**
- Legacy's prod config still schedules `gladeActivityIngest` and the disk-summary recompute
  (`app/env/sam.complete.properties:50,64`). Its V4 triggers on `disk_charge`
  (`db/migration/V4__DiskChargeSummaryStatus.sql:30-50`) mark the date `current=FALSE`; the next
  Quartz tick then deletes the day and rewrites it in legacy shape (NULL `act_*`, `facility_name`).
- Snapshot evidence it happened once: **2026-06-06**, all 2,405 summary rows legacy-shaped, totals
  ~3.5% off SAMuel's. Every other week since is SAMuel-shaped and all status rows are `current=1`, so
  either prod has no triggers or they stopped firing. The snapshot strips triggers, so it can't tell.
- SAMuel writes unresolved tier-1 rows with `processing_status=0` (`commands.py:1233`); legacy's manual
  `PUT /protected/admin/dasg/repair/{days}` picks up `!= 1` and would charge them with legacy's formula.

**Prod read-only checks (2026-10-02):**
- `information_schema.TRIGGERS`: **zero triggers in the `sam` schema** — legacy's V4 migration never
  reached prod, so SAMuel's `disk_charge` writes cannot flip a date to `current=FALSE`.
- `disk_charge_summary_status WHERE current = 0`: **none**.
- Legacy-shaped summary days since 2026-05-01: **only 2026-06-06** (2,405 rows), same as the snapshot.
  It predates the stamp-first ordering in `commands.py:878-892` (#301, 2026-06-12, which exists for
  the status FK); legacy's Quartz recompute evidently caught that date while it was not current.

So the coexistence risk is closed for new imports. Left over: 2026-06-06 on prod carries legacy's
totals (~3.5% above SAMuel's). Re-running `sam-admin accounting --disk` for that date would restore
SAMuel's — a prod write, Ben's call. `processing_status=0` rows remain reachable by legacy's manual
`PUT /protected/admin/dasg/repair/{days}` only.

### 4. `project_contract` unlink (KEEP)

**SAMuel.** Deletes the link row; if no other project links the contract, also sets
`contract.end_date = now`.
**Legacy.** Its own GUI unlink is the same orphan delete (`Project.purgeContract`, `Project.java:417-420`).
No active/end column on the link. Readers (user-lifecycle `activeProjectContracts`, AMIE
`getBestContract`, XRAS `getLatestContractEndDate`) see what legacy's unlink would produce.
The extra contract end-date is read only through project links (gone) and AMIE's grant DTO
(`DefaultAMIEContractQuery.java:57`, display). Harmless.

### 5. Never-started `account_user` delete (KEEP)

**SAMuel.** `_end_membership` deletes a row only when `start_date > now`; started rows are end-dated.
**Legacy.** Every systems reader requires `start_date <= NOW()` — sysacct, directoryaccess, heuv
groups (`H/jdbcQuery.xml:200-208, 399-465, 567, 606-621`), fairShareTree users
(`HibernateUserRepository.java:186-207`), the accounting engine — so a never-started row is
already invisible. Legacy cannot end such a row itself (`Account.unassign` touches only rows
assigned now). Only the user-lifecycle count and the AMIE history scan lose the pending row, which is
the intent of the removal.

### 6. SAMuel-only tables (KEEP)

`account_request` (purge task), `samuel_role_permission` (role edit), `account_allocation_state`
(read-model refresh), `notification_template_override`, `notification_addressing`: no legacy mapping
or SQL. `system_status` deletes are a different database.

### 7. `delete-orphan` collections (KEEP; tripwires)

No SAMuel code removes a child from any of the 17, deletes a cascading parent, or writes
`account.deleted = 1`. If a future change does, this is what legacy reads:

| Table | Legacy reads presence as | Safe SAMuel form |
|---|---|---|
| `user_institution`, `user_organization` | Affiliation; lifecycle deactivation candidacy; LDAP sync matches by PK and **throws** on a vanished id (`UserEmploymentSynchronizer.java:66-76`) | End-date; never delete |
| `charge_adjustment` | fairShareTree `adjustedUsage` / `balance` / `accountStatus`; legacy never deletes | Compensating adjustment |
| `allocation_transaction` | fairShareTree replays them for `allocationAmount` | Never delete apart from its allocation |
| `account` | fairShareTree resource node, charge routing; `deleted` is ignored | Never delete; flag is a no-op to legacy |
| `allocation` | sysacct access + fairShareTree; `deleted` is ignored | End-date (end ≥ start); delete only inside legacy's guard |
| `facility_resource` | Facility-on-resource membership | NULL the percentage (#698) |
| `adhoc_group_tag`, `adhoc_system_account_entry` | Group/member on a branch; LDAP sync re-creates | Leave to LDAP |
| `default_project` | heuv `/user/{u}/defaultproject` entry, no fallback | Uncertain — consumer unknown |
| `user_resource_shell` / `_home`, `email_address`, `phone`, `responsible_party`, `product` | Override / contact / display; legacy deletes these itself | Delete is legacy-native |

## Open items (not part of the fix)

From the handoff, unchanged: `HPC_Futures_Lab` membership gap, `allocationAmount` truncate-vs-round,
Cheyenne shares summing to 100.95.

## Implemented (branch `fix/legacy-safe-deletes`)

1. §1 option C: `renew.py::_collapse_superseded`, called after the `[DELETE]` transaction is logged
   so the history keeps the real window. Tests in `tests/unit/manage/test_renew_extend.py`.
2. §1 repair: `scripts/repair/collapse_superseded_allocations.sql` (79 rows; dry-run on the 3306
   snapshot, which matches prod: 0 live to legacy, 0 overlaps past a shared start day, 0 end < start,
   79 audit rows). Ben applies it on prod.
3. §2: disk-root hard-delete route and method removed; route-map snapshot regenerated.

**Ben's call, no code:** re-import 2026-06-06 disk (§3); reactivate NCGD0071-73 Data_Access as
truncated rows (§1), as `reconcile_fy27_renew_gap.sql` did for NCGD0073 Casper.

**Not audited:** soft retires that set a flag legacy may not honor. Generated CRUD sets
`active=False` on many entities; `allocation.deleted` (§1) shows the shape of the risk.
