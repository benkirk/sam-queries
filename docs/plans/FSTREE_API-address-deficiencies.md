# Plan: FairShare Tree API — Known Deficiencies

## Context

The current `/api/v1/fstree_access` implementation (`src/sam/queries/fstree_access.py`) has two
documented gaps vs. the legacy Java endpoint:

1. **`accountStatus` is a two-state simplification** — "Normal"/"Overspent" only.
   Legacy Java computes up to 11 statuses including threshold-exceedance tiers and lifecycle states.

2. **Charge aggregation is per-account only** — legacy Java rolls up charges across the full MPTT
   project subtree so parent OVERSPENT propagates down to children.

This plan addresses both deficiencies.

---

## Deficiency 1: accountStatus Enhancement

### Legacy Java Status Values (from `AccountStatus.java`)

| Status | String | Trigger |
|---|---|---|
| `ACCOUNT_STATUS_NORMAL` | `"Normal"` | charges ≤ allocation; no thresholds exceeded |
| `ACCOUNT_STATUS_EXCEED_ONE_THRESHOLD` | `"Exceed One Threshold"` | N-day usage exceeds exactly 1 threshold |
| `ACCOUNT_STATUS_EXCEED_TWO_THRESHOLDS` | `"Exceed Two Thresholds"` | N-day usage exceeds both thresholds |
| `ACCOUNT_STATUS_OVERSPENT` | `"Overspent"` | total charges > allocation amount |
| `ACCOUNT_STATUS_EXPIRED` | `"Expired"` | no active allocation, has prior allocation |
| `ACCOUNT_STATUS_WAITING` | `"Waiting"` | no active allocation, has future allocation |
| `ACCOUNT_STATUS_NO_ALLOCATION` | `"No Allocation"` | no allocation at all |
| `ACCOUNT_STATUS_DISABLED` | `"Disabled"` | project is inactive |
| `ACCOUNT_STATUS_NORMAL` (exempt) | `"Normal"` | project is charging-exempt |
| `NO_ACCOUNT` | `"No Account"` | no account exists |

The fstree skeleton query already filters to `p.active IS TRUE` and `al.deleted IS FALSE` with
a current date window — so in practice the fstree will only surface **5 statuses**:

| Priority | Status | Condition |
|---|---|---|
| 1 | `"Overspent"` | `adjustedUsage > allocationAmount` |
| 2 | `"Exceed Two Thresholds"` | both N-day periods exceeded |
| 3 | `"Exceed One Threshold"` | exactly one N-day period exceeded |
| 4 | `"No Allocation"` | account exists but no current active allocation |
| 5 | `"Normal"` | default |

### N-Day Threshold Logic (from `NDayUsagePeriod.java`)

For each threshold period P (P=30 days, P=90 days) with per-account percentage T%:

```
threshold_allocation = P × allocationAmount / (allocation_duration_days − 1)
use_limit            = threshold_allocation × (T / 100)
date_range_start     = max(today − P days, allocation_start_date)
date_range_end       = today

exceeded = (charges_in_date_range > use_limit)
```

Where `charges_in_date_range` = SUM of comp+dav+adjustments for account over [date_range_start, today].

**Per-account thresholds** come from `account.first_threshold` (30-day %) and
`account.second_threshold` (90-day %). Both are NULL for ~99.7% of accounts (17,472/17,484).
When NULL, no threshold check is performed for that period — the period is simply skipped.

**Data reality check:**
- 17,472 accounts have NO thresholds (NULL/NULL) → status stays Normal/Overspent only
- 12 accounts have thresholds set → need N-day charge window queries
- This makes threshold logic a low-cost conditional path

### Implementation: `_compute_status_with_thresholds()`

New helper in `fstree_access.py`:

```python
def _compute_status(
    adjusted_usage: float,
    allocation_amount: Optional[float],
    first_threshold: Optional[int],   # 30-day threshold %
    second_threshold: Optional[int],  # 90-day threshold %
    start_date: Optional[datetime],
    end_date: Optional[datetime],
    window_charges_30: float,          # charges in last 30 days (0.0 if no threshold)
    window_charges_90: float,          # charges in last 90 days (0.0 if no threshold)
) -> str:
```

Priority order (matching `DefaultAccountStatusCalculator.java`):
1. Check `adjustedUsage > allocationAmount` → `"Overspent"`
2. Count exceeded N-day thresholds (only for accounts that have them set)
3. Return `"Exceed Two Thresholds"` / `"Exceed One Threshold"` / `"Normal"`

**Threshold window charges**: Only needed for the 12 accounts with thresholds.
These are queried lazily — skeleton pass collects `{account_id}` where thresholds are not NULL,
then a targeted window-charge query runs for just those accounts.

---

## Deficiency 2: Hierarchical Subtree Charge Aggregation

### The Problem

Legacy Java calls `ProjectAccountTreeQuery.getProjectAccountTree()` which traverses the MPTT
project tree and sums charges from all descendants. A parent project's `adjustedUsage`
includes charges from all its sub-projects. If a parent is overspent, that status is propagated
**down** to all children in a pre-order walk.

Currently `fstree_access.py` uses `batch_get_account_charges()` (per-account charges only).

### Database Reality

From the active project data:
- 1,483 active projects, **all** have valid MPTT coordinates (tree_root IS NOT NULL)
- 28 projects have children (non-trivial hierarchies)
- Largest: NCGD0006 (52 children), CESM0002 (32), NMMM0003 (29)

### Implementation Approach: Switch to `batch_get_subtree_charges()`

**Step 1**: Add MPTT columns to `_SQL_FSTREE_SKELETON`:
```sql
p.tree_root,
p.tree_left,
p.tree_right,
```

**Step 2**: Build `alloc_infos` keyed by `allocation_id` (not `account_id`) with subtree coords:
```python
{
    'key':           row.allocation_id,
    'resource_id':   row.resource_id,
    'resource_type': row.resource_type,
    'tree_root':     row.tree_root,
    'tree_left':     row.tree_left,
    'tree_right':    row.tree_right,
    'start_date':    row.start_date,
    'end_date':      row.end_date or now,
}
```

**Step 3**: Call `Project.batch_get_subtree_charges(session, alloc_infos)` instead of
`batch_get_account_charges()`.

**Step 4**: Build `charge_map` keyed by `allocation_id` (not `account_id`) and update the
assembly loop to look up by `row.allocation_id`.

**OVERSPENT propagation**: Once subtree charges are correct, propagation of parent OVERSPENT
to children is handled naturally — if parent charges (including descendant charges) exceed
allocation, parent is OVERSPENT, and that status propagates to children via the pre-order
status walk in Python assembly (see below).

### Parent → Child Status Propagation

Matching `DefaultAccountStatusCalculator.java`'s pre-order walk:

```python
# In assembly loop, after computing resource status for a project:
# If parent is OVERSPENT/EXCEED_*, propagate to children

# Track parent status per (alloc_type → projcode):
parent_status: Dict[str, str] = {}   # projcode → accountStatus for that resource

for projcode, proj_data in sorted_projects:
    parent_projcode = ...  # from row.parent_id → projcode mapping
    parent_s = parent_status.get(parent_projcode, 'Normal')

    if parent_s not in ('Normal', None):
        # Inherit non-Normal parent status (propagation)
        final_status = parent_s
    else:
        # Compute own status independently
        final_status = _compute_status(...)

    parent_status[projcode] = final_status
```

This requires knowing the parent projcode. The skeleton query needs `p.parent_id` added, then
a Python mapping `{project_id: projcode}` built from skeleton rows.

---

## Implementation Plan

### Files to Modify

**`src/sam/queries/fstree_access.py`** — primary changes:

1. **`_SQL_FSTREE_SKELETON`**: Add `p.tree_root`, `p.tree_left`, `p.tree_right`, `p.parent_id`,
   `a.first_threshold`, `a.second_threshold` to SELECT.

2. **`_compute_status()`**: Replace the two-state function with the full priority-ordered
   implementation (Overspent → Exceed Two → Exceed One → Normal), accepting threshold
   window charges as input parameters.

3. **`get_fstree_data()`**:
   - Build `alloc_infos` for `batch_get_subtree_charges()` (using `allocation_id` as key,
     adding `tree_root/left/right`, `resource_id`).
   - Separate accounts with thresholds into a small targeted window-charge query for the
     30-day and 90-day periods.
   - Build `project_id → projcode` mapping and `projcode → parent_projcode` mapping from
     skeleton rows for propagation.
   - In the assembly loop: apply parent status propagation per-resource before setting
     each resource's final `accountStatus`.

**`tests/unit/test_fstree_queries.py`** — additional tests:
- `test_subtree_charges_differ_from_account_charges()` — verifies hierarchy projects
  (e.g. NCGD0006 on Derecho) show rolled-up usage.
- `test_account_status_values_extended()` — verify new status strings are valid.
- `test_parent_overspent_propagates_to_child()` — if any parent is Overspent, all its
  children should also be Overspent on the same resource.

**`docs/apis/SYSTEMS_INTEGRATION_APIs.md`** — update `accountStatus` table to list all 5
statuses, note N-day threshold logic.

### Critical Files (reference only, do not modify)

| File | Purpose |
|---|---|
| `src/sam/projects/projects.py:620` | `batch_get_subtree_charges()` — alloc_infos structure |
| `src/sam/projects/projects.py:810` | `batch_get_account_charges()` — being replaced |
| `legacy_sam/.../NDayUsagePeriod.java` | Exact threshold formula |
| `legacy_sam/.../DefaultAccountStatusCalculator.java` | Status priority order |
| `legacy_sam/.../AccountStatus.java` | Status string constants |

---

## Sequencing

1. **Subtree charges first** (structural change, higher correctness impact):
   - Update skeleton SQL (add MPTT cols + parent_id + thresholds)
   - Switch to `batch_get_subtree_charges()`
   - Update assembly to key by `allocation_id`

2. **Status enhancement second** (additive, builds on correct charge totals):
   - Update `_compute_status()` for full priority chain
   - Add targeted 30/90-day window queries for threshold accounts
   - Add parent propagation in assembly loop

3. **Tests + doc updates** last.

---

## Performance Considerations

- `batch_get_subtree_charges()` groups by `(resource_type, start_date, end_date)`. With
  ~1,133 distinct date pairs across 3,095 accounts, this could issue many queries in fallback
  mode. The VALUES CTE primary path should handle it efficiently.
- The threshold window query only runs for 12 accounts — negligible cost.
- Net performance change: roughly similar to current (0.7s/Derecho) since the charge tables
  are the bottleneck regardless of aggregation scope.

---

## Verification

```bash
source etc/config_env.sh

# Unit + API tests
pytest tests/unit/test_fstree_queries.py tests/api/test_fstree_access.py -v --no-cov

# Spot-check hierarchical project shows rolled-up charges
python3 -c "
from sam.session import create_sam_engine
from sqlalchemy.orm import Session
from sam.queries.fstree_access import get_fstree_data

engine, _ = create_sam_engine()
s = Session(engine)
r = get_fstree_data(s, resource_name='Derecho')
# Find NCGD0006 (largest hierarchy, 52 children)
for fac in r['facilities']:
    for at in fac['allocationTypes']:
        for p in at['projects']:
            if p['projectCode'].startswith('NCGD'):
                for res in p['resources']:
                    print(p['projectCode'], res['name'], res['adjustedUsage'], res['accountStatus'])
"

# Full regression suite
pytest tests/ --no-cov -q
```

---

## Known Simplifications (Deliberate)

- `"Expired"`, `"Waiting"`, `"No Allocation"`, `"Disabled"`, `"No Account"` lifecycle statuses
  are **not implemented** — the skeleton query already filters these out by requiring
  `p.active IS TRUE` and a current active allocation.  The five statuses above cover 100%
  of the rows that will appear in the fstree response.
- Charging-exempt projects: `project.charging_exempt` field exists in the DB; checking it
  and short-circuiting to `"Normal"` is a one-liner addition if needed later.
