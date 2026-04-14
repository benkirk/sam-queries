# Create Projects — Future Work and Deferred Findings

This document records items discovered during the legacy SAM gap analysis for the
`create_projects` branch. The confirmed gap (missing `ProjectNumber` creation) was
patched in `Project.create()` on the same branch. The items below are deferred to
future phases.

---

## 1. Child Ordering in Nested-Set Tree

**What legacy SAM does:**
The Java `regenerateProjectTreeFromRoot()` method performs a full tree regeneration on
every insert and sorts children **alphabetically by projcode** before assigning
`tree_left` / `tree_right` values:

```java
private List<Project> getChildrenSortedByProjcode(Project project) {
    return project.getChildren().stream()
            .sorted(Comparator.comparing(Project::getProjcode))
            .collect(Collectors.toList());
}
```

**What the Python implementation does:**
`_ns_place_in_tree()` (`src/sam/base.py`) inserts the new node as the *last child* of
its parent using two targeted SQL UPDATEs. This is dramatically more efficient
(O(1) SQL vs. O(n) recursive) but means children's `tree_left`/`tree_right` values
reflect insertion order rather than projcode alphabetical order.

**Impact assessment:**
- All tree invariants (ancestor/descendant queries) remain correct.
- Any `ORDER BY tree_left` query that assumes children appear in projcode order would
  return results in a different sequence from legacy SAM.
- In practice, projects are almost always inserted in sequential projcode order, so
  live data impact is expected to be near-zero.

**Possible future enhancement:**
Add a `_ns_rebuild_tree(session)` classmethod to `NestedSetMixin` that regenerates
all `tree_left` / `tree_right` values for a tree root from scratch, re-sorting
children by a configurable attribute (e.g., `_ns_path_attr`). This would serve as
both a repair utility and an optional post-insert normalizer.

**Priority:** Low — cosmetic ordering difference; no functional breakage observed.

---

## 2. Tree Rebuild / Repair Utility

**What legacy SAM does:**
Because legacy SAM always does a full-tree regeneration on insert, the tree is
self-healing — calling `regenerateProjectTreeFromRoot()` at any time corrects
any coordinate drift.

**Current Python gap:**
The incremental-shift approach has no equivalent repair mechanism. If `tree_left` /
`tree_right` values ever become inconsistent (e.g., due to direct DB edits, partial
transactions, or future bugs), there is no way to fix them without manual SQL.

**Possible future work:**
Implement a classmethod, e.g.:
```python
@classmethod
def rebuild_tree(cls, session, tree_root_id: int) -> None:
    """Regenerate tree_left/tree_right for an entire tree from parent links."""
    ...
```

This would traverse the tree in memory (using `parent_id` FK links), assign fresh
coordinates sorted by `_ns_path_attr`, then bulk-update the database.

Add a corresponding `sam-admin` command: `sam-admin project --rebuild-tree <projcode>`.

**Priority:** Low — maintenance/operations concern, not a current gap.

---

## 3. Allocation Propagation to Sub-Projects (`applyToSubProjects`)

**What legacy SAM does:**
`Project.addAllocation(applyToSubProjects=true)` in `Project.java` creates a master
allocation on the parent and then recursively creates identical "child" allocations on
all active sub-projects, linked via `parent_allocation_id`. Updates to the parent
cascade to all children via `TreeWalker`.

See `legacy_sam/doc/data_structures/parent_allocation.md` and
`legacy_sam/doc/data_structures/project_tree_charging.md` for full details.

**Current Python state:**
This feature is not yet implemented. Adding allocations to sub-projects currently
requires creating each allocation independently. The `Allocation` ORM model has a
`parent_allocation_id` FK column, but no enforcement or propagation logic exists.

**Future work required for full parity:**
1. Add `is_inheriting` property to `Allocation` (`parent_allocation_id is not None`).
2. Add `@validates` guard to block direct mutations on child allocations
   (raises `InheritingAllocationException`).
3. Add `_walk_tree(action_func)` recursive helper to `Allocation`.
4. Add `update_allocation(...)` / `extend(...)` / `delete_allocation(...)` public
   methods that trigger cascades to children.
5. Add `Project.add_shared_allocation(session, ...)` classmethod that creates parent
   + child allocations atomically (equivalent to `applyToSubProjects=true`).

Reference blueprint: `legacy_sam/doc/data_structures/allocation_tree_python_hints.md`

**Priority:** Medium — required for project trees with shared allocations. Not needed
for Phase A (single-project creation).

---

## 4. Usage Roll-Up Across Project Hierarchy

**What legacy SAM does:**
The `ProjectAccountDetail` system calculates rolled-up usage for every project node
using the **ancestor's** allocation date window, not the child's. This means a
sub-project's usage appears in the parent's "used" total regardless of whether
allocations are shared.

See `legacy_sam/doc/data_structures/project_tree_charging.md` § 1 for the full
algorithm.

**Current Python state:**
`Project.get_detailed_allocation_usage()` computes usage for a single project's
own allocations. It does not traverse the project hierarchy to roll up descendant usage.

**Future work:**
Extend `get_detailed_allocation_usage()` (or add a new method) to optionally include
descendant usage rolled up using the ancestor's allocation dates — matching the
`ProjectAccountDetail.submitUsageQueriesForNodeAndAncestors` logic.

**Priority:** Medium — required for accurate parent project balance reporting when
sub-projects exist.

---

*Last updated: 2026-04-11*
*Branch: `create_projects`*
