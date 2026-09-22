# FY27 renew — "truncate existing" refactor (as built)

**Status:** IMPLEMENTED on branch `fstree_renew_truncate` (off `origin/staging`).
**Prereqs (shipped to main):** #594 (fstree Expired-roster), #595 (fstree Waiting lifecycle).

## As built (decisions)

- `renew.py` `_soft_delete_overlapping_allocs` → **`_truncate_overlapping_allocs`**:
  an overlap that **starts before `new_start`** is truncated (`end_date =
  new_start − 1s`) via `update_allocation` (EDIT→ADJUSTMENT, `transaction_amount`
  0, replay no-op); one that **starts on/after `new_start`** (fully contained —
  the double-click-same-period case) is still soft-deleted. Inheriting children
  are skipped **only** in the truncate branch (the master's truncation cascades
  their `end_date`, and `update_allocation` refuses them); they are still
  soft-deleted directly in the fully-contained branch (a soft-delete does not
  cascade).
- **Kept the `replace_existing` param/form-field name** (documented behavior
  change), changing only the user-facing label to **"Truncate existing"** and
  the handler messages. This avoids churning the form schema + route-map parity
  + ~15 test call sites for a cosmetic rename.
- Regression tests: `TestRenewTruncatesFYCrossing` in
  `tests/unit/manage/test_renew_extend.py` (contiguous coverage, no gap/overlap,
  `replay==amount` on both rows, zero-delta ADJUSTMENT not DELETE, divergent-tree
  children). Existing `TestRenewReplaceExisting` (same-period double renew) still
  soft-deletes and passes unchanged.
- Runbook `docs/plans/FY27_PROD_RENEW_HANDOFF.md` updated ("Truncate existing" is
  safe for FY-crossing). Remediation record committed:
  `scripts/repair/reconcile_fy27_renew_gap.sql`.

---

## Original handoff (below, for reference)


## Why

The FY26→FY27 renew, run with **"overwrite/replace existing"** checked
(`replace_existing=True`) on FY-crossing projects, soft-deletes the crossing
allocation and creates a full-FY27 replacement **starting 2026-10-01**. Because
the replacement starts at the FY boundary rather than the old allocation's start,
the account has **no active allocation** between now and 09-30 — fstree reports
`Waiting`, and the project can't run/charge. Two projects hit this (NCGD0073,
NMMM0082); the data was remediated on prod 2026-09-22 via
`scripts/repair/reconcile_fy27_renew_gap.sql`. The root cause is the
delete-and-recreate behavior. **Forward fix: renew should TRUNCATE the
overlapping allocation to the handoff boundary instead of deleting it.**

Operator guidance already warned against this: `docs/plans/FY27_PROD_RENEW_HANDOFF.md`
("Do NOT check Replace existing", and it names NMMM0082).

## Scope (decided with Ben)

Replace the soft-delete branch of renew's replace path with **truncation**, and
**retire the delete behavior** (rename the control "overwrite/replace existing" →
"truncate existing"). One edge case (below) may keep a narrow soft-delete.

## Changes

- **`src/sam/manage/renew.py`** — replace `_soft_delete_overlapping_allocs`
  (~lines 118-142) with `_truncate_overlapping_allocs`. For each overlapping
  allocation that **starts before `new_start`**: truncate `end_date` to
  `new_start − 1s` via `update_allocation(...)` (writes `EDIT`→`ADJUSTMENT`,
  amount unchanged ⇒ replay no-op) **instead of** `deleted=True`. Call sites:
  root (~285-336) and per-descendant (~355-367).
  - **Edge to decide:** an overlap that **starts on/after `new_start`** (fully
    inside `[new_start, new_end]`) can't be truncated (`end < start`).
    Recommendation: keep a narrow soft-delete for that genuinely-superseded case
    (or skip). Only the crossing case (starts before `new_start`) needs truncation.
  - `update_allocation` raises `InheritingAllocationException` on an inheriting
    child — confirm truncation targets are roots/masters or route via the master.

- **`src/webapp/dashboards/admin/projects_routes.py`** (`_RenewAllocationsHandler`,
  ~1709-1860) + its template — rename the control to **"Truncate existing"** and
  `replace_existing` → `truncate_existing` (or keep the param name, change behavior
  + label). Regenerate route-map parity snapshot if the form field name changes.

- **`tests/unit/manage/`** renew tests — assert **contiguous coverage** (old
  `end_date == new_start − 1s`; new row full-period; no gap; no overlap) and
  `replay==amount` on both rows; update any test asserting `deleted=True` on the
  replace path. Add a regression: an FY-crossing renew leaves **no coverage gap**.

- **`docs/plans/FY27_PROD_RENEW_HANDOFF.md`** — update guidance (the "Do NOT check
  Replace existing" note becomes "Truncate existing is safe for FY-crossing").

- **(Optional)** commit `scripts/repair/reconcile_fy27_renew_gap.sql` as the
  remediation record (it carries an active `COMMIT`; applied to prod 2026-09-22).

## Mechanics / reuse (verified)

- `update_allocation` (`src/sam/manage/allocations.py` ~296-438): `allowed_fields =
  {amount, start_date, end_date, description}`; **shrinking `end_date` is allowed**
  (only `end >= start` checked); when `amount` is unchanged the audit row's
  `transaction_amount = 0.0` — a replay no-op. Does NOT commit — wrap in
  `management_transaction` (renew already does).
- Replay invariant: `replay_amount` (`allocations.py` ~446-479). `EXTENSION` and
  zero-amount `ADJUSTMENT` rows don't move replay. Assert `replay==amount` in tests.
- Contrast: `src/sam/manage/extend.py` *refuses to shrink* `end_date` — so
  truncation must go through `update_allocation`, not extend.

## Branch / PR

Fresh branch off `origin/staging` (local mains are stale). PR `--base staging`.
Separate from #594/#595.

## Verify

- `pytest tests/unit/manage` (+ the new renew tests); `pytest tests/unit`;
  route-map parity gate if the form field was renamed.
- Manual (`docker compose up webdev --watch`, :5050): Admin → Edit Project →
  Allocations → renew with **"Truncate existing"** on an FY-crossing project;
  confirm the old allocation is **truncated (not deleted)**, the new full-period
  row is created, coverage is contiguous, and there's no `Waiting` gap.

## References

- Session plan: `~/.claude/plans/read-only-in-the-async-parasol.md` (Part 2).
- Memory: `project_fstree_lifecycle_and_renew_gap`.
- Remediation applied: `scripts/repair/reconcile_fy27_renew_gap.sql` (prod 2026-09-22).
- Runbook: `docs/plans/FY27_PROD_RENEW_HANDOFF.md`.
