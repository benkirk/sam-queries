# ORM Model Review: Completeness, Optimization, and Deficiency Fixes

## Context

The SAM ORM layer (98 models across 10 domains) was reviewed against the live database (`sam-sql.ucar.edu`) for correctness, consistency, and optimization opportunities. The codebase is mature with excellent schema validation tests (100% model coverage), but several issues were found ranging from correctness bugs to consistency improvements.

**Database verification was performed** for all findings below — this is not speculative.

---

## Phase 1: Correctness Fixes

### 1.1 Fix `DiskResourceRootDirectory` timestamps
**File**: `src/sam/resources/resources.py:234-237`

**Current code**:
```python
creation_time = Column(TIMESTAMP, nullable=False,
                       server_default=text('CURRENT_TIMESTAMP'),
                       onupdate=datetime.utcnow)
modified_time = Column(TIMESTAMP, server_default=text('CURRENT_TIMESTAMP'),
                       onupdate=text('CURRENT_TIMESTAMP'))
```

**DB reality** (confirmed):
```sql
creation_time timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
modified_time timestamp NULL DEFAULT NULL
```

**Issues**:
- `onupdate=datetime.utcnow` is a Python callback using deprecated `datetime.utcnow` (Python 3.12+), conflicts with project convention of naive datetimes, and is redundant since DB handles it
- `modified_time` ORM says `server_default=CURRENT_TIMESTAMP, onupdate=CURRENT_TIMESTAMP` but DB says `DEFAULT NULL` with no auto-update
- The DB schema itself is unusual (creation_time auto-updates, modified_time is NULL) — our ORM should match it faithfully

**Fix**: Match ORM to actual DB DDL:
```python
creation_time = Column(TIMESTAMP, nullable=False,
                       server_default=text('CURRENT_TIMESTAMP'),
                       server_onupdate=text('CURRENT_TIMESTAMP'))
modified_time = Column(TIMESTAMP)  # NULL default, no auto-update (per DB)
```

### 1.2 Fix `HPCCos` duplicate `modified_time` override
**File**: `src/sam/activity/hpc.py:100-106`

**Current code**: Inherits `TimestampMixin` AND manually defines `modified_time = Column(TIMESTAMP, nullable=False, ...)`.

**DB reality**: `modified_time timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP`

The `TimestampMixin.modified_time` does NOT specify `nullable=False`, but the DB column is `NOT NULL`. So the manual override adds `nullable=False` — this is intentional and necessary.

**Fix**: Keep the override but add a comment explaining why. Alternatively, remove `TimestampMixin` and define both columns explicitly. The cleanest approach: keep `TimestampMixin` and just override with `nullable=False`:
```python
class HPCCos(Base, TimestampMixin):
    ...
    # Override mixin: DB column is NOT NULL (mixin defaults to nullable)
    modified_time = Column(TIMESTAMP, nullable=False,
                           server_default=text('CURRENT_TIMESTAMP'),
                           onupdate=text('CURRENT_TIMESTAMP'))
```

This is actually correct as-is — just needs a comment. **Low priority.**

### 1.3 Fix `DavCos` — same pattern as 1.2 but opposite
**File**: `src/sam/activity/dav.py:130-136`

**DB reality**: `modified_time timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP`

The DB column IS nullable, matching `TimestampMixin` default. The manual override here adds `nullable=False` which contradicts the DB.

**Fix**: Remove `nullable=False` from the manual override, or remove the override entirely since the mixin matches:
```python
class DavCos(Base, TimestampMixin):
    ...
    # No modified_time override needed — mixin matches DB
```

### 1.4 Fix `UserAlias` TimestampMixin mismatch
**File**: `src/sam/core/users.py:495-510`

**Current code**: Inherits `TimestampMixin` + manually defines `modified_time = Column(TIMESTAMP(3), ...)`. No explicit `creation_time`.

**DB reality**:
- `creation_time datetime NULL DEFAULT CURRENT_TIMESTAMP` (nullable!)
- `modified_time timestamp(3) NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)`

**Issues**:
- `TimestampMixin.creation_time` is `DateTime, nullable=False` but DB is `nullable=True`
- `TimestampMixin.modified_time` is `TIMESTAMP` but DB is `TIMESTAMP(3)` (fractional seconds)

**Fix**: Remove `TimestampMixin` from `UserAlias` inheritance. Define both columns explicitly:
```python
class UserAlias(Base):  # No TimestampMixin
    ...
    creation_time = Column(DateTime, server_default=text('CURRENT_TIMESTAMP'))  # nullable
    modified_time = Column(TIMESTAMP(3), server_default=text('CURRENT_TIMESTAMP(3)'))
```

---

## Phase 2: Consistency Improvements

### 2.1 Convert `Factor.is_active` and `Formula.is_active` to hybrid properties
**File**: `src/sam/resources/charging.py:39-46, 86-93`

Both use plain `@property` for date-range active checks, preventing SQL-side filtering (`session.query(Factor).filter(Factor.is_active)`). Other models use `@hybrid_property` for the same pattern.

**Preferred approach**: Have `Factor` and `Formula` use `DateRangeMixin`. Both already have `start_date`/`end_date` columns matching the mixin interface. This gives `is_active_at()`, `is_currently_active` hybrid property for free.

**Backward compat**: Add `is_active = is_currently_active` alias since existing code uses `factor.is_active`.

### 2.2 Add `Contract` hybrid `is_active` property
**File**: `src/sam/projects/contracts.py`

`Contract` has `is_active_at()` and `start_date`/`end_date` but no hybrid `is_active`. Same approach as 2.1 — consider `DateRangeMixin`.

### 2.3 Clean up commented-out deprecated code
**Files**:
- `src/sam/core/users.py`: `# xras_user = relationship()` DEPRECATED comment
- `src/sam/accounting/allocations.py`: `# xras_allocation = relationship()` DEPRECATED comment
- `src/sam/resources/resources.py`: `# xras_hpc_amounts` DEPRECATED comment

**Fix**: Remove. Git history preserves it.

### 2.4 Expand `__all__` in `src/sam/__init__.py`
**File**: `src/sam/__init__.py:204-226`

Only 16 of 98 models in `__all__`. Since the webapp uses `from sam import *`, this limits what's available.

**Fix**: Expand `__all__` to include all imported model names, or remove it entirely.

---

## Phase 3: Optional Polish

### 3.1 `AllocationTransactionType` → `enum.StrEnum`
**File**: `src/sam/accounting/allocations.py:122-129`

Currently a plain class with string constants. DB confirmed no corresponding table. Convert to `enum.StrEnum` for type safety. Low priority.

### 3.2 Document `disk_resource_root_directory` schema anomaly
The DB has `creation_time` with auto-update and `modified_time` with NULL default — semantically the names are swapped. Add a code comment noting this upstream DB quirk.

### 3.3 Populate subdirectory `__init__.py` files
Enable `from sam.accounting import Account` etc. Currently all 8 are empty. Low priority — the top-level imports work fine.

---

## Findings Confirmed as Non-Issues

- **DavCharge FK to composite-PK DavActivity**: DB FK references just `dav_activity_id`, which has a UNIQUE constraint. Auto-increment guarantees uniqueness. Correct as-is.
- **CompJob lacks User FK**: Intentional — raw job data from batch systems, usernames may not match SAM users.
- **DatasetActivity has no relationships**: Confirmed — DB table has no FK columns. Standalone metric collection.
- **NestedSetMixin used only by Organization and Project**: Confirmed — only tables with `tree_left` column. Complete.
- **6 tables without ORM models**: All infrastructure/temp tables. Correctly excluded.
- **CompChargeSummary PK naming**: DB artifact (`charge_summary_id` vs `{type}_charge_summary_id`). ORM mirrors DB faithfully.

---

## Files to Modify

| File | Phase | Changes |
|------|-------|---------|
| `src/sam/resources/resources.py` | 1, 2 | Fix DiskResourceRootDirectory timestamps; remove deprecated comments |
| `src/sam/activity/hpc.py` | 1 | Add comment to HPCCos modified_time override |
| `src/sam/activity/dav.py` | 1 | Fix DavCos modified_time nullable mismatch |
| `src/sam/core/users.py` | 1, 2 | Fix UserAlias TimestampMixin; remove deprecated comments |
| `src/sam/resources/charging.py` | 2 | Factor/Formula → DateRangeMixin + hybrid is_active |
| `src/sam/projects/contracts.py` | 2 | Add is_active hybrid property or DateRangeMixin |
| `src/sam/accounting/allocations.py` | 2, 3 | Remove deprecated comment; optionally StrEnum |
| `src/sam/__init__.py` | 2 | Expand __all__ |

---

## Verification

1. **Run schema validation**: `source ../.env && pytest tests/integration/test_schema_validation.py -v`
   - Catches any column type/nullable mismatches immediately

2. **Run full test suite**: `source ../.env && pytest tests/ --no-cov`
   - All 380+ tests should pass

3. **Spot-check queries against live DB**:
   ```bash
   mysql -h sam-sql.ucar.edu sam -e "SELECT * FROM hpc_cos LIMIT 3;"
   mysql -h sam-sql.ucar.edu sam -e "SELECT * FROM dav_cos LIMIT 3;"
   mysql -h sam-sql.ucar.edu sam -e "SELECT * FROM user_alias LIMIT 3;"
   mysql -h sam-sql.ucar.edu sam -e "SELECT * FROM disk_resource_root_directory;"
   ```

4. **Test new hybrid properties**:
   ```python
   from sam import Factor, Formula
   # These should work after Phase 2 changes:
   session.query(Factor).filter(Factor.is_currently_active).all()
   session.query(Formula).filter(Formula.is_currently_active).all()
   ```
