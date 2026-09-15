# FY26 → FY27 Divisional Renew — PROD handoff (UNCOMMITTED working note)

Status as of 2026-09-06. **This file is intentionally untracked** — a working
checklist for the prod renew pass (planned next week). Do not commit.

## Prerequisite (BLOCKER)

**PR #518 must be merged to staging → main and deployed to prod first.** It
carries the renew `allow_zero` fix. Without it, any tree containing a 0-amount
reserve hits `Error renewing allocations: Amount must be > 0, got 0.0` and the
whole tree-renew rolls back. Known 0-reserve tree: **NCGD0006** (NCGD0013
Derecho = 0.00). Confirm the deployed image carries #518 before starting
(prod was `sha-b8b6d4e` pre-fix).

## What this is

Copy the (now correctly linked) FY26 divisional allocation trees forward to
FY27, per tree, using the existing one-click **Renew** UX. The FY26 relink
remediation (92 relinks, applied to prod 2026-09-06) already fixed the shared
topology, so renew faithfully mirrors amounts + `parent_allocation_id` structure.

## The 10 root NCAR/Divisional projects to renew

All active, all hold FY26 compute allocations at the root. Renew ONE per tree.

| # | Root | Lab | Descendants | Notes |
|---|------|-----|-------------|-------|
| 1 | NACD0001 | ACOM | 3 | |
| 2 | NASP0001 | EdEC/ASP | 9 | |
| 3 | NCGD0006 | CGD | 54 | **has 0-reserve** (NCGD0013 Derecho=0) — needs #518 |
| 4 | NCIS0001 | CISL | 16 | |
| 5 | NEOL0001 | EOL | 21 | |
| 6 | NHAO0003 | HAO | 9 | |
| 7 | NMMM0003 | MMM lab-wide | 30 | |
| 8 | NMMM0033 | MMM Reserve | 9 | no Casper GPU at root; NMMM0082 Derecho note below |
| 9 | NRAL0001 | RAL Monthly | 6 | Monthly parent — renewed as an annual FY27 period on dev |
| 10 | NRAL0002 | RAL Yearly | 23 | |

Compute resource IDs: Casper 21, Casper GPU 24, Derecho 25, Derecho GPU 26.

## Renew procedure (per root)

Admin → project `/admin/project/<ROOT>/edit` → Allocations tab → **Renew**:
- New Start `10/01/2026`, New End `09/30/2027` (auto-proposed — verify).
- Leave all compute resources checked; Scale = `1` (verbatim copy).
- Do NOT check **Replace existing** (only for correcting a prior accidental renew).
- Click **Renew Allocations**. Renew walks the whole tree (root + descendants).

## Verification (read-only) — run after EACH root

Prod is read-only via: `mysql -h sam-sql.ucar.edu sam` (dev was
`mysql -u root -h 127.0.0.1 -proot sam`). For each root, three checks:

### 1. Amounts == source & topology preserved (expect ok = N, missing = 0, mismatch = 0)

```sql
WITH src AS (
  SELECT acct.project_id pid, acct.resource_id rid, a.amount amt, a.start_date sstart,
         (a.parent_allocation_id IS NOT NULL) shared,
         ROW_NUMBER() OVER (PARTITION BY acct.project_id,acct.resource_id ORDER BY a.start_date DESC) rn
  FROM allocation a JOIN account acct ON a.account_id=acct.account_id
  WHERE a.deleted=0 AND acct.resource_id IN (21,24,25,26)
    AND a.start_date<='2026-09-06' AND (a.end_date IS NULL OR a.end_date>='2026-09-06')),
tgt AS (
  SELECT acct.project_id pid, acct.resource_id rid, a.amount amt,(a.parent_allocation_id IS NOT NULL) shared
  FROM allocation a JOIN account acct ON a.account_id=acct.account_id
  WHERE a.deleted=0 AND acct.resource_id IN (21,24,25,26)
    AND a.start_date>='2026-10-01' AND a.start_date<'2026-10-02'
    AND a.end_date>='2027-09-01' AND a.end_date<'2027-10-01')
SELECT r.resource_name,
       SUM(s.amt<=>t.amt AND s.shared<=>t.shared) ok,
       SUM(t.amt IS NULL) missing_fy27,
       SUM(NOT (s.amt<=>t.amt) OR NOT (s.shared<=>t.shared)) mismatch
FROM project root
JOIN project p ON (p.project_id=root.project_id OR (p.tree_left>root.tree_left AND p.tree_right<root.tree_right AND p.tree_root=root.tree_root))
JOIN src s ON s.pid=p.project_id AND s.rn=1
JOIN resources r ON r.resource_id=s.rid
LEFT JOIN tgt t ON t.pid=s.pid AND t.rid=s.rid
WHERE root.projcode='<ROOT>' GROUP BY r.resource_name ORDER BY r.resource_name;
```

To list anomalies (mismatch / missing / mid-year source), swap the final SELECT
for a per-row listing filtered on
`t.amt IS NULL OR NOT(s.amt<=>t.amt) OR NOT(s.shared<=>t.shared) OR s.sstart>'2025-10-01'`.

### 2. 0-reserve spot check (NCGD0006 only)

```sql
SELECT p.projcode, a.amount, a.parent_allocation_id, DATE(a.start_date), DATE(a.end_date)
FROM allocation a JOIN account acct ON a.account_id=acct.account_id JOIN project p ON acct.project_id=p.project_id
WHERE p.projcode='NCGD0013' AND acct.resource_id=25 AND a.deleted=0 AND a.start_date>='2026-10-01';
-- expect amount 0.00, parent_allocation_id NULL, 2026-10-01 -> 2027-09-30
```

### 3. 0 balance at 2026-10-02 (expect 0)

```sql
SELECT COALESCE(SUM(c),0) FROM (
 SELECT cs.charges c FROM comp_charge_summary cs JOIN account acct ON cs.account_id=acct.account_id AND acct.resource_id IN (21,24,25,26)
   JOIN project p ON acct.project_id=p.project_id JOIN project root ON root.projcode='<ROOT>'
   AND (p.project_id=root.project_id OR (p.tree_left>root.tree_left AND p.tree_right<root.tree_right AND p.tree_root=root.tree_root))
   WHERE cs.activity_date>='2026-10-01'
 UNION ALL
 SELECT cs.charges FROM dav_charge_summary cs JOIN account acct ON cs.account_id=acct.account_id AND acct.resource_id IN (21,24,25,26)
   JOIN project p ON acct.project_id=p.project_id JOIN project root ON root.projcode='<ROOT>'
   AND (p.project_id=root.project_id OR (p.tree_left>root.tree_left AND p.tree_right<root.tree_right AND p.tree_root=root.tree_root))
   WHERE cs.activity_date>='2026-10-01') u;
```

Note: at prod-run time `activity_date>='2026-10-01'` may be slightly non-zero if
FY27 has begun and real jobs have run — that is fine (real usage), not a renew
defect. The point is that renew created fresh allocations, not that usage is 0.

## Expected results (from the dev pass, all 10 clean)

ok counts per root (Casper / Casper GPU / Derecho / Derecho GPU):
NACD0001 3/2/2/2 · NASP0001 3/2/2/2 · NCGD0006 15/15/14/14 · NCIS0001 7/6/6/6 ·
NEOL0001 11/10/10/10 · NHAO0003 6/5/5/5 · NMMM0003 17/16/16/16 ·
NMMM0033 8/(none)/9/3 · NRAL0001 7/7/7/7 · NRAL0002 14/13/13/13.
All: 0 mismatch, 0 missing (except the NMMM0082 note), 0 FY27 balance on the
frozen snapshot.

## Known data notes (NOT renew bugs — do not "fix" during renew)

- **Mid-year sub-projects** mirror their *mid-year* amount, because renew uses
  `find_source_alloc_at` = latest-start source active at the source date
  (NCGD0068, NMMM0083, NMMM0085, and NRAL0002/NRAL0001's NERP/NTMA/NWCA/NWSP).
- **NMMM0082 Derecho**: correctly skipped by renew's overlap guard — it already
  holds a Derecho alloc spanning 2026-04-28 → 2027-04-30 (overlaps FY27). It has
  a coverage gap May–Sep 2027; align separately (extend/replace) only if desired.
- **NMMM0083** is a standalone mid-year duplicate of the MMM shared amounts; if
  it should be a shared member that is a separate data decision.

## Pointers

- Renew engine: `src/sam/manage/renew.py::renew_project_allocations`
  (+ `allow_zero=True` on both `Allocation.create` calls).
- Route: `POST /htmx/renew-allocations/<projcode>` (`_RenewAllocationsHandler`).
- Relink remediation: `scripts/repair/relink_shared_allocations.py`.
- Both changes: PR #518 (branch `relink-shared-allocations`).
