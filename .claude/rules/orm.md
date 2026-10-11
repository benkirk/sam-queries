---
paths:
  - "src/sam/**/*.py"
  - "tests/factories/**/*"
  - "tests/unit/models/**/*"
  - "tests/integration/**/*"
  - "scripts/sql/**/*"
---

# ORM models and conventions

Loaded with `src/sam/`. The universal rules (§1 datetimes, §5 `is_active`, §7 writes) are in the root `CLAUDE.md`.

## Key ORM Models

### Core Models
- **User** (`users`): System users with UPID, unix_uid
  - Properties: `primary_email`, `all_emails`, `full_name`, `display_name`, `all_projects`
  - Class methods: `get_by_username(session, username)`
  - Relationships: `email_addresses`, `projects`, `accounts`

- **Organization** (`organization`): NCAR labs/sections
- **Institution** (`institution`): Universities, research orgs

- **Project** (`project`): Research projects with projcode, unix_gid
  - Properties: `active`, `lead`, `admin`
  - Instance methods: `get_detailed_allocation_usage(resource_name=None, include_adjustments=True)` — dict of usage by resource
  - Class methods: `get_by_projcode(session, projcode)`
  - Relationships: `accounts`, `allocations`, `users`

### Accounting
- **Account** (`account`): Billing accounts — links projects to resources
- **Allocation** (`allocation`): Resource allocations (hierarchical tree)
  - `is_active` hybrid property; `is_active_at(check_date)`
  - Relationships: `account`, `parent`, `children`, `transactions`
- **AllocationType** (`allocation_type`): NSC, University, Staff, etc.

### Resources
- **Resource** (`resources`) / **ResourceType** (HPC, DAV, DISK, ARCHIVE, DATA ACCESS)
- **Machine** (`machine`): Derecho, Casper, Gust — `is_active` = commissioned, not decommissioned
- **Queue** (`queue`): `is_active` = within start/end date window
- **Facility** (`facility`): UNIV, WNA, NCAR
- **Factor** / **Formula**: charging factors and `@{variable}` template formulas

### Activity/Usage
- **CompJob**/**CompActivity**, **HPCActivity**/**HPCCharge**, **DavActivity**/**DavCharge**,
  **DiskActivity**/**DiskCharge**, **ArchiveActivity**/**ArchiveCharge**

## Patterns (§2-§4, §6)

### 2. Primary Keys
- Single PK: `primary_key=True, autoincrement=True`
- Composite PK: `PrimaryKeyConstraint('col1', 'col2', name='pk_tablename')` in
  `__table_args__`. Never assume single-column PKs — check the database
  (e.g. `dav_activity` is `(dav_activity_id, queue_name)`).

### 3. Relationships
Always bidirectional via `back_populates` on both sides.

### 4. Mixins (`sam/base.py`)
- `TimestampMixin`: creation_time, modified_time
- `SoftDeleteMixin`: deleted flag + `is_active` hybrid (not deleted)
- `ActiveFlagMixin`: active flag + `is_active` hybrid (active == True)
- `DateRangeMixin`: start_date/end_date + `is_active` hybrids
- `NestedSetMixin`: left/right tree coordinates (Project trees; Organization's
  coords are vestigial)
- `SessionMixin`: `self.session` property — required for `update()` instance methods

### 6. Views
Mark with `__table_args__ = {'info': {'is_view': True}}`. Never INSERT/UPDATE/DELETE.

## Common ORM Patterns

```python
# Emails — never user.email (no such attribute)
user = User.get_by_username(session, 'benkirk')
user.primary_email;  user.all_emails

# Allocation usage (uses SessionMixin internally — do NOT pass session)
project = Project.get_by_projcode(session, 'SCSG0001')
usage = project.get_detailed_allocation_usage(resource_name='Derecho')   # {resource_name: {...}}

# Expiration queries return 4-tuples — unpack them
from sam.queries.expirations import get_projects_by_allocation_end_date
for project, allocation, resource_name, days_remaining in get_projects_by_allocation_end_date(
        session, start_date=..., end_date=..., facility_names=['UNIV', 'WNA']):
    ...
# get_projects_with_expired_allocations(...) has the same tuple shape
# (project, allocation, resource_name, days_since_expiration).
```

## Adding models and fixing drift

### Adding New ORM Models
1. Create the model in the matching domain module; add `SessionMixin` if it
   needs write methods; add `update()` / `create()` per §7.
2. Add to `sam/__init__.py` imports (`/database` picks the table up by reflection).
3. Add tests; run schema validation
   (`pytest tests/integration/test_schema_validation.py`), then the full suite.

### Fixing Schema Mismatches
Database is the source of truth: check `SHOW CREATE TABLE x\G`, update the ORM
to match, rerun schema-validation tests (they catch future drift).

## ORM pitfalls

Model modules take the common ORM names with `from ..base import *`; a new module follows suit.

❌ **DON'T** assume single-column primary keys — check the database first
❌ **DON'T** skip schema-validation tests after model changes
❌ **DON'T** use `user.email` — use `user.primary_email` (no `email` attribute)
❌ **DON'T** pass `session` to `project.get_detailed_allocation_usage()` — SessionMixin provides it
❌ **DON'T** forget the 4-tuple unpack from the expiration queries

✅ **DO** run schema-validation tests before committing model changes
✅ **DO** check the actual database schema when in doubt
✅ **DO** use bidirectional relationships with `back_populates`
