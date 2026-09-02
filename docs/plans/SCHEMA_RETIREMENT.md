# SAM Schema Retirement Audit

Living inventory of database objects SAM no longer uses, to be dropped once legacy
Java SAM 2.0.3 is retired and SAM owns the schema outright
(`POSTGRES_MIGRATION.md` Horizon 2). Nothing here is dropped in compatibility mode —
legacy SAM may still read these objects. Initial focus: **VIEWS**. A tables pass
follows.

## Views — all 7 are unused by SAM's own code (audited 2026-09-01)

None of the 7 mapped views has a purpose-built query site in `src/`: no
`session.query`, `select()`, relationship/join, marshmallow schema, or raw-SQL
`FROM`. The XRAS API and charge rollups were reimplemented onto **base tables**. The
only readers are the generic Flask-Admin auto-registration (which maps *every* ORM
model) and `tests/integration/test_views.py`.

Inventory is 1:1:1 across the ORM, the running dev MySQL, and
`containers/sam-sql-dev/dump/views.sql` — no unmapped DB views, no stale ORM
mappings.

| View | ORM class (file) | SAM query sites | Base objects it reads | Retire when |
|---|---|---|---|---|
| `xras_user` | `XrasUserView` (`integration/xras_views.py:23`) | 0 | users, phone, phone_type, user_organization, organization, user_institution, institution, email_address, academic_status | legacy SAM gone |
| `xras_role` | `XrasRoleView` (`integration/xras_views.py:50`) | 0 | users, project | legacy SAM gone |
| `xras_action` | `XrasActionView` (`integration/xras_views.py:72`) | 0 | project, account, allocation, allocation_type, allocation_transaction | legacy SAM gone |
| `xras_allocation` | `XrasAllocationView` (`integration/xras_views.py:97`) | 0 | project, account, allocation, xras_resource_repository_key_resource, **+ view `xras_hpc_allocation_amount`** | legacy SAM gone — AFTER/with the view it depends on |
| `xras_hpc_allocation_amount` | `XrasHpcAllocationAmountView` (`integration/xras_views.py:122`) | 0 | allocation, account, resources, resource_type, hpc_charge_summary | legacy SAM gone — last, `xras_allocation` depends on it |
| `xras_request` | `XrasRequestView` (`integration/xras_views.py:144`) | 0 | project, account, allocation, allocation_type | legacy SAM gone |
| `comp_activity_charge` | `CompActivityChargeView` (`activity/computational.py:260`) | 0 | comp_activity, comp_job, comp_charge_summary | see note (not XRAS; confirm no external reader) |

### Why SAM abandoned the views (from `sam/queries/xras_access.py`)

The reimplemented query layer ports legacy Java SAM's Hibernate named queries and
runs them against base tables, explicitly *not* the views:

- **`xras_user` — ~560x slower + wrong data.** Its `GROUP BY u.user_id` materializes
  all ~28k rows before any filter (0.409 s via the view vs 0.0007 s with the
  predicate inside the grouped query), and it computes a different email
  (per-tier `COALESCE(MIN(...))` vs the named query's per-row `ANY_VALUE(COALESCE(...))`)
  — porting it would ship a silent data divergence.
- **`xras_allocation` — 6-8 s regardless of filter**, because
  `xras_hpc_allocation_amount` aggregates `hpc_charge_summary` across *all*
  allocations before joining.
- **`xras_request` — fails under `ONLY_FULL_GROUP_BY`** (dev/CI enable it, prod does
  not): its `ORDER BY al.end_date` names a different expression from the GROUP BY's
  `cast(... as date)`.

The views are the interface **legacy Java SAM 2.0.3** serves XRAS from (they present
SAM data in XRAS's shape). SAM's Python code has no dependency on them.

### Retirement gate

SAM-code independence is established. Before DROPping the DB views:

1. **Legacy Java SAM 2.0.3 retired** (Horizon 2) — it is the known consumer.
2. **Confirm no other consumer** outside this repo: XRAS reading the views directly,
   a reports/BI tool, or a DB grant of the `xras_*` objects to any external role.
   This cannot be settled from the SAM tree — verify with the DBA / XRAS team.
3. **Drop in dependency order:** `xras_allocation` before (or with)
   `xras_hpc_allocation_amount`; the rest are independent.

### Near-term (Horizon 1) — safe now, done in this change

- Removed the dead `from sam.integration.xras_views import *` in
  `webapp/api/v1/charges.py` (it pulled in six view classes the module never uses;
  `charges.py` queries `Account` + the `*ChargeSummary` tables).

### Deliberately NOT done in Horizon 1

- **Keep the 7 ORM view classes, `tests/integration/test_views.py`, and the
  Flask-Admin pages** while the DB views still exist. The ORM classes + the test are
  the ORM-vs-DB drift check on these live objects, and the admin pages are the only
  visibility. De-map them in Horizon 2, in the same change that DROPs the views.

## Tables — follow-up pass (not yet done)

Materially harder than views: ~100 mapped tables, legacy Java SAM *writes* many, and
the ORM reads many indirectly via relationships. A retirement candidate needs all
three: no ORM read (direct or via relationship), no legacy writer, no inbound FK.
Same two-source method (code-reference sweep + external-consumer check) plus an FK
graph. Deferred to a dedicated pass.
