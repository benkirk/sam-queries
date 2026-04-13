# Allocation Tree Topology: Deep Tree, Not Flat Star

## The Claim vs. Reality

`ALLOCATION_TREE_EDITING.md` states:

> *"Propagation topology: flat star — all child allocations point directly to the root parent allocation (`parent_allocation_id = root.allocation_id`)."*

This is **incorrect**. The live database uses a deep tree where each allocation's
`parent_allocation_id` mirrors its project's `parent_id` in the project hierarchy.

## Reproducing Query

Run against `sam-sql.ucar.edu`:

```sql
-- Show allocation tree topology for NMMM0003/Derecho on 2025-03-01.
-- Compare project.parent_id (project hierarchy) vs
-- allocation.parent_allocation_id (allocation hierarchy).
SELECT
    p.projcode,
    p.parent_id          AS proj_parent_id,
    pp.projcode          AS proj_parent_projcode,
    al.allocation_id,
    al.parent_allocation_id,
    pal.allocation_id    AS parent_alloc_id,
    pap.projcode         AS parent_alloc_projcode
FROM allocation al
JOIN account ac   ON al.account_id        = ac.account_id
JOIN project p    ON ac.project_id        = p.project_id
JOIN resources r  ON ac.resource_id       = r.resource_id
LEFT JOIN project pp    ON p.parent_id             = pp.project_id
LEFT JOIN allocation pal ON al.parent_allocation_id = pal.allocation_id
LEFT JOIN account pac   ON pal.account_id           = pac.account_id
LEFT JOIN project pap   ON pac.project_id           = pap.project_id
WHERE p.tree_root = 1106              -- NMMM0003 subtree
  AND r.resource_name = 'Derecho'
  AND al.start_date  <= '2025-03-01'
  AND (al.end_date   >= '2025-03-01' OR al.end_date IS NULL)
  AND al.deleted = 0
ORDER BY al.parent_allocation_id IS NULL DESC, al.allocation_id;
```

### Output (abbreviated)

| projcode  | proj_parent_projcode | allocation_id | parent_allocation_id | parent_alloc_projcode |
|-----------|----------------------|---------------|----------------------|-----------------------|
| NMMM0003  | —                    | 20989         | NULL                 | —                     |
| NMMM0008  | NMMM0003             | 20991         | 20989                | NMMM0003              |
| NMMM0012  | NMMM0003             | 20993         | 20989                | NMMM0003              |
| **NMMM0054** | **NMMM0012**      | **20994**     | **20993**            | **NMMM0012**          |
| **NMMM0020** | **NMMM0012**      | **20995**     | **20993**            | **NMMM0012**          |
| **P64000495** | **NMMM0012**     | **20996**     | **20993**            | **NMMM0012**          |
| NMMM0013  | NMMM0003             | 20997         | 20989                | NMMM0003              |

The bold rows prove the point: NMMM0054 is a *grandchild* of NMMM0003 (its project
parent is NMMM0012). Its `parent_allocation_id` points to NMMM0012's allocation
(20993), not to the root NMMM0003 allocation (20989).

## Legacy Java Implementation — Confirmed

The propagation logic in legacy SAM (`Project.java:261–269`) is the authoritative
source that produced the data above:

```java
// Project.java
private Allocation doPropagatingAddAllocation(ProjectAllocationRequest request) {
    Allocation allocation = doAddAllocation(request);           // 1. create alloc for THIS project
    ProjectAllocationCommand childCommand =
        new ProjectAllocationCommand(request, allocation);      // 2. wrap with THIS alloc as parent
    for (Project child : children) {
        if (child.isActive()) {
            child.doPropagatingAddAllocation(childCommand);     // 3. recurse — parent advances each level
        }
    }
    return allocation;
}
```

`ProjectAllocationCommand(request, allocation)` sets `getParent()` to the **just-created
allocation at this level** (`ProjectAllocationCommand.java:19–22`).  `Allocation.build()`
then calls `setParentAllocation(request.getParent())` (`Allocation.java:48`).

So at each level of recursion, the `parent` pointer advances — a grandchild project
gets `parent_allocation_id` set to its immediate parent project's allocation, never
the root.  This is the deep tree.  It is intentional, well-tested legacy behaviour.

## Why It Matters

The deep tree topology affects `propagate_allocation_to_subprojects()`.

**If we naively set `parent_allocation_id = parent_allocation.allocation_id` for every
descendant** (which is what the flat-star plan implied), grandchildren would end up
pointing to the root instead of their immediate allocation parent, breaking the
topology that exists today.

Concretely: when `update_allocation()` cascades a change by walking
`allocation.children`, it only follows *direct* SQLAlchemy children. In the flat-star
model, grandchildren would never be reachable from NMMM0012's allocation; in the
deep-tree model they are, because NMMM0012's allocation has them as children and
`_walk_tree()` recurses.

## Required Fix for `propagate_allocation_to_subprojects()`

Process descendants **breadth-first**, maintaining a `{project_id → allocation_id}`
map so each new child allocation can find its correct parent:

```python
# alloc_map seeds with the allocation we're propagating from
alloc_map = {parent_allocation.account.project_id: parent_allocation.allocation_id}

for child_proj in get_descendants_breadth_first(project):
    if not child_proj.active:
        continue
    proj_parent_alloc_id = alloc_map.get(child_proj.parent_id)
    new_alloc = Allocation.create(
        session,
        project_id=child_proj.project_id,
        resource_id=resource_id,
        amount=parent_allocation.amount,
        start_date=parent_allocation.start_date,
        end_date=parent_allocation.end_date,
        parent_allocation_id=proj_parent_alloc_id,   # immediate parent, not root
    )
    alloc_map[child_proj.project_id] = new_alloc.allocation_id
```

`NestedSetMixin.get_descendants()` returns nodes in left-to-right nested-set order,
which is depth-first. A simple BFS sort by depth (or by `tree_left` level) is
needed to guarantee a parent's allocation exists before any of its children are
processed.
