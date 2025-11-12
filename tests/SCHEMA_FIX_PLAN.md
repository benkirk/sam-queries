# Schema Fix Plan for SAM ORM Models

**Date:** 2025-11-12
**Status:** In Progress

---

## Overview

After analyzing the database schema, we found **58 issues across 28 models**:
- Type mismatches (FLOAT vs NUMERIC, DATE vs DATETIME, etc.)
- Missing columns in ORM models
- Incorrect VIEW definitions (7 views completely wrong)

---

## Priority 1: Fix VIEW-Based ORM Models (CRITICAL)

All 7 VIEW-based ORM models are **completely incorrect**. They were defined as if they were regular tables with relationships, but VIEWs are read-only query results with different schemas.

### Views to Fix

| View Name | Current ORM Status | Action Required |
|-----------|-------------------|-----------------|
| `comp_activity_charge` | Wrong schema, has relationships | Complete rewrite |
| `xras_user` | Wrong schema, has relationships | Complete rewrite |
| `xras_role` | Wrong schema, has relationships | Complete rewrite |
| `xras_action` | Wrong schema, has relationships | Complete rewrite |
| `xras_allocation` | Wrong schema, has relationships | Complete rewrite |
| `xras_hpc_allocation_amount` | Wrong schema, has relationships | Complete rewrite |
| `xras_request` | Wrong schema, has relationships | Complete rewrite |

### View: `comp_activity_charge`

**Actual View Schema:**
```sql
username (varchar 35)
projcode (varchar 30)
job_id (varchar 35)
job_name (varchar 255)
queue_name (varchar 100)
machine (varchar 100)
start_time (int - epoch)
end_time (int - epoch)
submit_time (int - epoch)
unix_user_time (double)
unix_system_time (double)
queue_wait_time (bigint)
num_nodes_used (int)
cos (int)
exit_status (varchar 20)
interactive (int)
processing_status (bit)
error_comment (mediumtext)
activity_date (datetime)
load_date (datetime)
num_cores_used (int)
external_charge (float 22,8)
job_idx (int)
util_idx (int)
wall_time (double)
core_hours (float 22,8)
charge (float 22,8)
charge_date (datetime)
unix_uid (int)
```

**Current ORM:** Has relationships to CompActivity, completely wrong

**Fix:** Remove relationships, match actual view columns

### View: `xras_user`

**Actual View Schema:**
```sql
username (varchar 35)
firstName (varchar 50)
middleName (varchar 40)
lastName (varchar 50)
phone (varchar 50)
organization (varchar 80)
email (varchar 255)
academicStatus (varchar 100)
```

**Current ORM:** Has local_user_id FK, role_id FK, etc. - completely wrong

**Fix:** This is a simple user profile view, no relationships needed

### View: `xras_role`

**Actual View Schema:**
```sql
projectId (varchar 30)
username (varchar 35)
role (varchar 17)
```

**Current ORM:** Has xras_role_id PK, role_name, relationships - completely wrong

**Fix:** This is a project membership view showing user roles

### View: `xras_action`

**Actual View Schema:**
```sql
allocationId (int)
projectId (varchar 30)
actionType (varchar 12)
amount (float 15,2)
endDate (datetime)
dateApplied (datetime)
```

**Current ORM:** Has xras_action_id PK, action_name - completely wrong

**Fix:** This is an allocation history view

### View: `xras_allocation`

**Actual View Schema:**
```sql
allocationId (int)
projectId (varchar 30)
allocationBeginDate (datetime)
allocationEndDate (datetime)
allocatedAmount (float 15,2)
remainingAmount (double 25,8)
resourceRepositoryKey (int unsigned)
```

**Current ORM:** Has xras_allocation_id PK, relationships to XrasRequest - completely wrong

**Fix:** This is an allocation summary view

### View: `xras_hpc_allocation_amount`

**Actual View Schema:**
```sql
allocation_id (int)
allocated (float 15,2)
used (double 22,8)
remaining (double 22,8)
```

**Current ORM:** Has xras_hpc_allocation_amount_id PK, relationships - partially wrong

**Fix:** Simpler than current model, no auto PK needed

### View: `xras_request`

**Actual View Schema:**
```sql
requestBeginDate (date)
requestEndDate (date)
allocationIds (text)
allocationType (varchar 20)
projectTitle (varchar 255)
projectId (varchar 30)
xrasFosTypeId (int)
```

**Current ORM:** Has xras_request_id PK, relationships to XrasUser/XrasAction - completely wrong

**Fix:** This is a project request summary view

---

## Priority 2: Fix Type Mismatches (HIGH)

### FLOAT vs NUMERIC Mismatches

**Database uses FLOAT, ORM uses NUMERIC:**

| Model | Column | DB Type | ORM Type | Fix |
|-------|--------|---------|----------|-----|
| Allocation | amount | FLOAT | NUMERIC(15,2) | Change to Float |
| AllocationTransaction | requested_amount | FLOAT | NUMERIC(15,2) | Change to Float |
| AllocationTransaction | transaction_amount | FLOAT | NUMERIC(15,2) | Change to Float |
| AllocationType | default_allocation_amount | FLOAT | NUMERIC(15,2) | Change to Float |
| ArchiveCharge | terabyte_year | FLOAT | NUMERIC(22,8) | Change to Float |
| ArchiveCharge | charge | FLOAT | NUMERIC(22,8) | Change to Float |
| ArchiveChargeSummary | terabyte_years | FLOAT | NUMERIC(22,8) | Change to Float |
| ArchiveChargeSummary | charges | FLOAT | NUMERIC(22,8) | Change to Float |
| CompChargeSummary | core_hours | FLOAT | NUMERIC(22,8) | Change to Float |
| CompChargeSummary | charges | FLOAT | NUMERIC(22,8) | Change to Float |
| CompActivity | external_charge | FLOAT | NUMERIC(15,8) | Change to Float |
| CompActivity | charge | FLOAT | NUMERIC(22,8) | Change to Float |
| CompActivity | core_hours | FLOAT | NUMERIC(22,8) | Change to Float |
| DavActivity | charge | FLOAT | NUMERIC(22,8) | Change to Float |
| DavActivity | core_hours | FLOAT | NUMERIC(22,8) | Change to Float |
| DavCharge | terabyte_year | FLOAT | NUMERIC(22,8) | Change to Float |
| DavCharge | charge | FLOAT | NUMERIC(22,8) | Change to Float |
| DavChargeSummary | terabyte_years | FLOAT | NUMERIC(22,8) | Change to Float |
| DavChargeSummary | charges | FLOAT | NUMERIC(22,8) | Change to Float |
| DavChargeSummary | core_hours | FLOAT | NUMERIC(22,8) | Change to Float |
| DiskCharge | terabyte_year | FLOAT | NUMERIC(22,8) | Change to Float |
| DiskCharge | charge | FLOAT | NUMERIC(22,8) | Change to Float |
| DiskChargeSummary | terabyte_years | FLOAT | NUMERIC(22,8) | Change to Float |
| DiskChargeSummary | charges | FLOAT | NUMERIC(22,8) | Change to Float |
| HPCActivity | external_charge | FLOAT | NUMERIC(15,8) | Change to Float |
| HPCActivity | charge | FLOAT | NUMERIC(22,8) | Change to Float |
| HPCActivity | core_hours | FLOAT | NUMERIC(22,8) | Change to Float |
| HPCCharge | charge | FLOAT | NUMERIC(22,8) | Change to Float |
| HPCCharge | core_hours | FLOAT | NUMERIC(22,8) | Change to Float |
| HPCChargeSummary | charges | FLOAT | NUMERIC(22,8) | Change to Float |
| HPCChargeSummary | core_hours | FLOAT | NUMERIC(22,8) | Change to Float |
| MachineFactor | factor_value | FLOAT | NUMERIC(15,2) | Change to Float |
| Queue | wall_clock_hours_limit | FLOAT | NUMERIC(5,2) | Change to Float |
| WallclockExemption | time_limit_hours | FLOAT | NUMERIC(5,2) | Change to Float |

### DOUBLE vs FLOAT Mismatches

| Model | Column | DB Type | ORM Type | Fix |
|-------|--------|---------|----------|-----|
| CompActivity | unix_system_time | DOUBLE | FLOAT | Change to Float (SQLAlchemy Float maps to DOUBLE) |
| CompActivity | unix_user_time | DOUBLE | FLOAT | Change to Float |
| CompActivity | wall_time | DOUBLE | FLOAT | Change to Float |
| DavActivity | unix_system_time | DOUBLE | FLOAT | Change to Float |
| DavActivity | unix_user_time | DOUBLE | FLOAT | Change to Float |
| HPCActivity | unix_system_time | DOUBLE | FLOAT | Change to Float |
| HPCActivity | unix_user_time | DOUBLE | FLOAT | Change to Float |

**Note:** SQLAlchemy `Float` actually maps to MySQL `DOUBLE`, so these are OK as-is.

### DATE vs DATETIME Mismatches

**Database uses DATE, ORM uses DATETIME:**

| Model | Column | DB Type | ORM Type | Fix |
|-------|--------|---------|----------|-----|
| ArchiveChargeSummary | activity_date | DATE | DATETIME | Change to Date |
| ArchiveChargeSummaryStatus | activity_date | DATE | DATETIME | Change to Date |
| CompChargeSummary | activity_date | DATE | DATETIME | Change to Date |
| CompChargeSummaryStatus | activity_date | DATE | DATETIME | Change to Date |
| DavChargeSummary | activity_date | DATE | DATETIME | Change to Date |
| DavChargeSummaryStatus | activity_date | DATE | DATETIME | Change to Date |
| DiskChargeSummary | activity_date | DATE | DATETIME | Change to Date |
| DiskChargeSummaryStatus | activity_date | DATE | DATETIME | Change to Date |
| HPCChargeSummary | activity_date | DATE | DATETIME | Change to Date |
| HPCChargeSummaryStatus | activity_date | DATE | DATETIME | Change to Date |

### BIT vs BOOLEAN Mismatches

| Model | Column | DB Type | ORM Type | Fix |
|-------|--------|---------|----------|-----|
| CompActivity | processing_status | BIT | BOOLEAN | OK - Boolean maps to BIT(1) |
| DavActivity | processing_status | BIT | BOOLEAN | OK - Boolean maps to BIT(1) |
| HPCActivity | processing_status | BIT | BOOLEAN | OK - Boolean maps to BIT(1) |

**Note:** These are fine, `Boolean` correctly maps to `BIT(1)` in MySQL.

### TIMESTAMP vs DATETIME Mismatches

| Model | Column | DB Type | ORM Type | Fix |
|-------|--------|---------|----------|-----|
| Country | deletion_time | DATETIME | TIMESTAMP | Change to DateTime |
| StateProv | deletion_time | DATETIME | TIMESTAMP | Change to DateTime |
| StateProv | modified_time | DATETIME | TIMESTAMP | Change to DateTime |

### CHAR vs VARCHAR Mismatches

| Model | Column | DB Type | ORM Type | Fix |
|-------|--------|---------|----------|-----|
| ArchiveActivity | type_act | CHAR(1) | VARCHAR(1) | Change to String(1) with CHAR |

---

## Priority 3: Missing Columns (MEDIUM)

| Model | Missing Column | Fix |
|-------|----------------|-----|
| ArchiveActivity | modified_time | Add column |
| CompActivity | modified_time | Add column |
| DavActivity | modified_time | Add column |
| XrasResourceRepositoryKeyResource | resource_repository_key | Remove extra columns, fix schema |

---

## Priority 4: Server Defaults for Timestamps (LOW)

Some timestamp fields don't have server-side defaults but the ORM expects them:

| Model | Column | Current | Fix Needed |
|-------|--------|---------|------------|
| AccountUser | creation_time | No default | Add `server_default=text('CURRENT_TIMESTAMP')` |
| Various | modified_time | Some missing | Verify all have `onupdate=text('CURRENT_TIMESTAMP')` |

**Note:** This may be a database-level issue, not ORM. Check actual table definitions.

---

## Implementation Plan

### Phase 1: Fix VIEW Models (CRITICAL - Day 1)

1. **Create new VIEW ORM models** in `python/sam/integration/xras_views.py`
   - Use `__table_args__ = {'info': dict(is_view=True)}` to mark as views
   - No primary keys (or use composite key from view columns)
   - No relationships (views don't support FKs)
   - Match exact column names from views

2. **Deprecate old XRAS models** in `python/sam/integration/xras.py`
   - Add deprecation warnings
   - Update imports in `__init__.py`

3. **Fix CompActivityCharge view** in `python/sam/activity/computational.py`
   - Match actual view schema
   - Remove relationships

### Phase 2: Fix Type Mismatches (HIGH - Day 1-2)

1. **Change NUMERIC to Float** (33 columns across 20+ models)
   - Import: `from sqlalchemy import Float`
   - Change: `Column(Numeric(15, 2))` → `Column(Float)`

2. **Change DATETIME to Date** (10 summary table columns)
   - Import: `from sqlalchemy import Date`
   - Change: `Column(DateTime)` → `Column(Date)`

3. **Change TIMESTAMP to DateTime** (3 columns)
   - Geography models: deletion_time, modified_time

### Phase 3: Add Missing Columns (MEDIUM - Day 2)

1. **Add modified_time** to activity tables
   - ArchiveActivity, CompActivity, DavActivity

2. **Fix XrasResourceRepositoryKeyResource**
   - Match actual table schema

### Phase 4: Test Everything (Day 2-3)

1. Run inventory script: `python tests/orm_inventory.py`
2. Run test suite: `pytest tests/ -v`
3. Add new tests for fixed models
4. Add tests for VIEW models (read-only)

---

## Testing Strategy

### For Regular Tables

```python
# Test that fixes work
def test_allocation_amount_is_float(session):
    allocation = session.query(Allocation).first()
    assert isinstance(allocation.amount, float)
```

### For Views

```python
# Views are read-only
def test_comp_activity_charge_view(session):
    records = session.query(CompActivityCharge).limit(10).all()
    assert len(records) > 0
    record = records[0]
    assert record.username is not None
    assert record.projcode is not None
```

---

## Breaking Changes

### VIEW Models - BREAKING

The XRAS view models are completely changing structure:
- Old: `XrasUser` had `local_user_id`, `xras_role_id`, `active` fields
- New: `XrasUser` has `username`, `firstName`, `lastName`, `email` fields

**Migration:** Any code using these models needs to be updated.

### Type Changes - NON-BREAKING

Changing NUMERIC to Float doesn't break existing code:
- Both return Python `float` type
- Both handle decimal precision
- SQLAlchemy handles conversion transparently

### Date vs DateTime - POTENTIALLY BREAKING

Changing DateTime to Date may affect code that expects time component:
- Old: `activity_date` was `datetime.datetime`
- New: `activity_date` will be `datetime.date`

**Check:** Any code doing time-based queries on summary tables.

---

## Files to Modify

### High Priority (Views)
1. `python/sam/integration/xras.py` - Complete rewrite
2. `python/sam/activity/computational.py` - Fix CompActivityCharge

### Medium Priority (Types)
3. `python/sam/accounting/allocations.py` - Float fixes
4. `python/sam/activity/archive.py` - Float fixes
5. `python/sam/activity/hpc.py` - Float fixes
6. `python/sam/activity/dav.py` - Float fixes
7. `python/sam/activity/disk.py` - Float fixes
8. `python/sam/activity/computational.py` - Float fixes
9. `python/sam/summaries/archive_summaries.py` - Date + Float fixes
10. `python/sam/summaries/comp_summaries.py` - Date + Float fixes
11. `python/sam/summaries/dav_summaries.py` - Date + Float fixes
12. `python/sam/summaries/disk_summaries.py` - Date + Float fixes
13. `python/sam/summaries/hpc_summaries.py` - Date + Float fixes
14. `python/sam/resources/machines.py` - Float fixes
15. `python/sam/operational.py` - Float fixes
16. `python/sam/geography.py` - DateTime fixes

---

## Success Criteria

- [ ] All 7 VIEW models match actual view schemas
- [ ] All type mismatches resolved (0 mismatches)
- [ ] Inventory script shows 0 issues
- [ ] All existing tests still pass
- [ ] New tests for VIEW models pass
- [ ] New tests for summary tables pass
- [ ] Documentation updated

---

**Status:** Ready to begin implementation
**Next Step:** Start with Phase 1 (Fix VIEW models)
