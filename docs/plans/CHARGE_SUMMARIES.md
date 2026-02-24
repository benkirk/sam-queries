# Plan: Charge Summary Write Management — Comp, Disk, Archive

**Branch:** `summary_charges`
**Date:** 2026-02-24
**Status:** Approved for implementation (reviewer corrections applied 2026-02-24)

---

## 1. Context and Motivation

NCAR's SAM system (System for Allocation Management) tracks HPC resource usage via
daily pre-aggregated charge summary tables. Historically these tables have been populated
by a Java/Hibernate accounting batch process. This plan covers replacing that Java process
with a Python implementation using the existing SQLAlchemy ORM.

Three summary tables are in scope:

| Table | Purpose | Models |
|-------|---------|--------|
| `comp_charge_summary` | Daily HPC computational charges | `CompChargeSummary`, `CompChargeSummaryStatus` |
| `disk_charge_summary` | Daily disk storage charges | `DiskChargeSummary`, `DiskChargeSummaryStatus` |
| `archive_charge_summary` | Daily HPSS archive charges | `ArchiveChargeSummary`, `ArchiveChargeSummaryStatus` |

The deliverables are:
1. **Python management functions** in `src/sam/manage/summaries.py` for direct ORM
   interaction from external batch processes
2. **REST API endpoints** in `src/webapp/api/v1/charges.py` for web-based access
3. **Marshmallow input schemas** for API validation
4. **Comprehensive tests** covering both layers

---

## 2. Data Architecture Background

### Dual-Bookkeeping Pattern (from ACCOUNTING_DETAILS.md)

All three summary tables use a dual-bookkeeping pattern to handle identity changes over time:

| Field Type | Prefix | Semantics | Mutable? |
|---|---|---|---|
| **Actual (raw)** | `act_` | Exact values from job scheduler | **NO** — DB trigger enforces immutability |
| **Resolved** | (none) | SAM's current internal mapping | **YES** — updated when user/project changes |

Fields: `act_username` / `username`, `act_projcode` / `projcode`, `act_unix_uid` / `unix_uid`.

The `act_` fields form the natural key for each daily bucket. A database trigger
(`upd_comp_charge_summary_immutable_check`) prevents them from being updated after insert.

### Model Field Comparison

| Field | CompChargeSummary | DiskChargeSummary | ArchiveChargeSummary |
|-------|-------------------|-------------------|----------------------|
| `activity_date` | NOT NULL | NOT NULL | NOT NULL |
| `act_username` | String(35) | String(35) | String(35) |
| `act_projcode` | String(30) | String(30) | String(30) |
| `act_unix_uid` | Integer | Integer | Integer |
| `username` | String(35) | String(35) | String(35) |
| `projcode` | String(30) | String(30) | String(30) |
| `unix_uid` | Integer | Integer | Integer |
| `user_id` | FK users (nullable) | FK users (NOT NULL) | FK users (NOT NULL) |
| `account_id` | FK account (nullable) | FK account (NOT NULL) | FK account (NOT NULL) |
| `facility_name` | String(30) | String(30) | String(30) |
| `machine` | String(100), NOT NULL | — | — |
| `machine_id` | FK machine | — | — |
| `queue` | String(100), NOT NULL | — | — |
| `queue_id` | FK queue | — | — |
| `resource` | String(40) | — | — |
| `cos` | Integer | — | — |
| `sweep` | Integer | — | — |
| `num_jobs` | Integer | — | — |
| `core_hours` | Float | — | — |
| `charges` | Float | Float | Float |
| `number_of_files` | — | Integer | Integer |
| `bytes` | — | BigInteger | BigInteger |
| `terabyte_years` | — | Float | Float |
| `error_comment` | Text | — | — |

### Natural Keys for Upsert Lookup

| Model | Natural Key Columns |
|-------|-------------------|
| `CompChargeSummary` | `(activity_date, act_username, act_projcode, machine, queue, resource)` |
| `DiskChargeSummary` | `(activity_date, act_username, act_projcode, account_id)` |
| `ArchiveChargeSummary` | `(activity_date, act_username, act_projcode, account_id)` |

---

## 3. Files to Modify / Create

| File | Action | Purpose |
|------|--------|---------|
| `src/sam/manage/summaries.py` | **CREATE** | Core management functions + shared validation helpers |
| `src/sam/manage/__init__.py` | **MODIFY** | Export 3 public functions |
| `src/sam/schemas/charges.py` | **MODIFY** | Append input schemas (base + 3 specialized) |
| `src/webapp/utils/rbac.py` | **MODIFY** | Add `MANAGE_CHARGE_SUMMARIES` permission |
| `src/webapp/api/v1/charges.py` | **MODIFY** | Add 3 POST endpoints + shared helper |
| `tests/unit/test_manage_summaries.py` | **CREATE** | Unit tests for management layer |

Existing API test file to extend:
- `tests/api/test_charge_endpoints.py` — add POST test classes

---

## 4. Implementation: `src/sam/manage/summaries.py`

### 4a. Shared Private Validation Helpers

These are the foundation for all three upsert functions. Each raises `ValueError` with
a human-readable message identifying exactly what was not found.

```python
"""
Charge summary management functions.

Write operations for comp_charge_summary, disk_charge_summary, and
archive_charge_summary tables. These replace the Java/Hibernate batch
process that previously populated these tables.

All functions use session.flush() (not commit). Callers must wrap in
management_transaction() for proper commit/rollback handling.

Update semantics: PUT-style full replacement. All mutable fields are set
to whatever the caller provides; omitting an optional field writes NULL.
Callers needing partial updates must first read the existing row and merge.

WARNING: The upsert functions use query-then-insert (not atomic
ON DUPLICATE KEY UPDATE). The summary tables have no UNIQUE constraint on
their natural key columns, so concurrent writes for the same natural key
(date, user, project, ...) bucket may produce duplicate rows. Batch
processes must serialize writes for the same natural key.
"""
from datetime import date
from typing import Optional, Tuple
from sqlalchemy.orm import Session

from sam.core.users import User
from sam.projects.projects import Project
from sam.resources.resources import Resource
from sam.resources.machines import Machine, Queue
from sam.accounting.accounts import Account
from sam.summaries.comp_summaries import CompChargeSummary
from sam.summaries.disk_summaries import DiskChargeSummary
from sam.summaries.archive_summaries import ArchiveChargeSummary


def _resolve_user(session: Session, act_username: str, act_unix_uid: Optional[int]) -> User:
    """
    Look up user by username; fall back to unix_uid if username not found.
    act_unix_uid may be None for jobs submitted before identity sync.
    Raises ValueError if neither lookup succeeds.
    """
    user = User.get_by_username(session, act_username)
    if not user and act_unix_uid is not None:
        user = session.query(User).filter_by(unix_uid=act_unix_uid).first()
    if not user:
        uid_str = f"unix_uid={act_unix_uid}" if act_unix_uid is not None else "no uid"
        raise ValueError(
            f"User '{act_username}' ({uid_str}) not found in SAM"
        )
    return user


def _resolve_project(session: Session, act_projcode: str) -> Project:
    """Look up project by projcode. Raises ValueError if not found."""
    project = Project.get_by_projcode(session, act_projcode)
    if not project:
        raise ValueError(f"Project '{act_projcode}' not found in SAM")
    return project


def _resolve_resource(session: Session, resource_name: str) -> Resource:
    """Look up resource by name. Raises ValueError if not found."""
    resource = Resource.get_by_name(session, resource_name)
    if not resource:
        raise ValueError(f"Resource '{resource_name}' not found in SAM")
    return resource


def _resolve_account(
    session: Session,
    project: Project,
    resource: Resource,
    include_deleted: bool = False,
) -> Account:
    """
    Look up account linking project to resource.
    Pass include_deleted=True for historical backfill against accounts that
    have since been marked deleted.
    Raises ValueError if no matching account exists.
    """
    account = Account.get_by_project_and_resource(
        session,
        project.project_id,
        resource.resource_id,
        exclude_deleted=not include_deleted,
    )
    if not account:
        qualifier = " (including deleted)" if include_deleted else ""
        raise ValueError(
            f"No account{qualifier} found for project '{project.projcode}' "
            f"on resource '{resource.resource_name}'"
        )
    return account


def _resolve_machine(
    session: Session, machine_name: str, resource: Resource
) -> Machine:
    """Look up machine by name within resource. Raises ValueError if not found."""
    machine = session.query(Machine).filter_by(
        name=machine_name, resource_id=resource.resource_id
    ).first()
    if not machine:
        raise ValueError(
            f"Machine '{machine_name}' not found on resource '{resource.resource_name}'"
        )
    return machine


def _resolve_or_create_queue(
    session: Session,
    queue_name: str,
    resource: Resource,
    create_if_missing: bool,
) -> Queue:
    """
    Look up queue by name within resource.
    If not found and create_if_missing=True, auto-creates the queue.
    If not found and create_if_missing=False, raises ValueError with actionable hint.
    """
    queue = session.query(Queue).filter_by(
        queue_name=queue_name, resource_id=resource.resource_id
    ).first()
    if not queue:
        if not create_if_missing:
            raise ValueError(
                f"Queue '{queue_name}' not found on resource '{resource.resource_name}'. "
                f"Pass create_queue_if_missing=True to create it automatically."
            )
        queue = Queue(
            queue_name=queue_name,
            resource_id=resource.resource_id,
            description=f"Auto-created from charge summary import",
        )
        session.add(queue)
        session.flush()  # Populate queue_id before referencing it
    return queue
```

### 4b. `upsert_comp_charge_summary()`

**Signature:**
```python
def upsert_comp_charge_summary(
    session: Session,
    *,
    activity_date: date,
    act_username: str,
    act_projcode: str,
    act_unix_uid: Optional[int],
    resource_name: str,
    machine_name: str,
    queue_name: str,
    num_jobs: int,
    core_hours: float,
    charges: float,
    # Optional resolved overrides (default to act_ values if omitted)
    username: Optional[str] = None,
    projcode: Optional[str] = None,
    unix_uid: Optional[int] = None,
    resource: Optional[str] = None,
    facility_name: Optional[str] = None,
    cos: Optional[int] = None,
    sweep: Optional[int] = None,
    error_comment: Optional[str] = None,
    create_queue_if_missing: bool = False,
    include_deleted_accounts: bool = False,
) -> Tuple[CompChargeSummary, str]:
    """
    Insert or update a CompChargeSummary row.

    Entity resolution order (stops on first error):
      1. User  (_resolve_user)
      2. Project  (_resolve_project)
      3. Resource  (_resolve_resource)
      4. Account  (_resolve_account)
      5. Machine  (_resolve_machine)
      6. Queue  (_resolve_or_create_queue)

    Returns:
        Tuple of (record, action) where action is 'created' or 'updated'.

    Raises:
        ValueError: If any required entity is not found.

    Notes:
        Does NOT commit. Caller must use management_transaction().
        PUT semantics: all mutable fields replaced; omitted optionals write NULL.
        Pass include_deleted_accounts=True for historical charge backfill.
    """
    user = _resolve_user(session, act_username, act_unix_uid)
    project = _resolve_project(session, act_projcode)
    res = _resolve_resource(session, resource_name)
    account = _resolve_account(session, project, res, include_deleted=include_deleted_accounts)
    machine = _resolve_machine(session, machine_name, res)
    queue = _resolve_or_create_queue(session, queue_name, res, create_queue_if_missing)

    # Resolved field defaults — explicit None checks preserve falsy values (e.g. uid=0)
    resolved_username = username  if username  is not None else act_username
    resolved_projcode = projcode  if projcode  is not None else act_projcode
    resolved_unix_uid = unix_uid  if unix_uid  is not None else act_unix_uid
    resource_col      = resource  if resource  is not None else resource_name

    # Facility name: use caller-provided value; fall back to resource heuristic
    if facility_name is None and res.facility_resources:
        fr = res.facility_resources[0]
        facility_name = fr.facility.facility_name if fr.facility else None

    # Natural key lookup
    existing = session.query(CompChargeSummary).filter(
        CompChargeSummary.activity_date == activity_date,
        CompChargeSummary.act_username == act_username,
        CompChargeSummary.act_projcode == act_projcode,
        CompChargeSummary.machine == machine_name,
        CompChargeSummary.queue == queue_name,
        CompChargeSummary.resource == resource_col,
    ).first()

    if existing is None:
        # INSERT — set all fields including immutable act_ fields
        record = CompChargeSummary(
            activity_date=activity_date,
            act_username=act_username,
            act_projcode=act_projcode,
            act_unix_uid=act_unix_uid,
            username=resolved_username,
            projcode=resolved_projcode,
            unix_uid=resolved_unix_uid,
            user_id=user.user_id,
            account_id=account.account_id,
            facility_name=facility_name,
            machine=machine_name,
            machine_id=machine.machine_id,
            queue=queue_name,
            queue_id=queue.queue_id,
            resource=resource_col,
            num_jobs=num_jobs,
            core_hours=core_hours,
            charges=charges,
            cos=cos,
            sweep=sweep,
            error_comment=error_comment,
        )
        session.add(record)
        action = 'created'
    else:
        # UPDATE — overwrite only mutable fields; NEVER touch act_ fields
        existing.username = resolved_username
        existing.projcode = resolved_projcode
        existing.unix_uid = resolved_unix_uid
        existing.user_id = user.user_id
        existing.account_id = account.account_id
        existing.facility_name = facility_name
        existing.machine_id = machine.machine_id
        existing.queue_id = queue.queue_id
        existing.num_jobs = num_jobs
        existing.core_hours = core_hours
        existing.charges = charges
        existing.cos = cos
        existing.sweep = sweep
        existing.error_comment = error_comment
        record = existing
        action = 'updated'

    session.flush()
    return record, action
```

### 4c. `upsert_disk_charge_summary()` and `upsert_archive_charge_summary()`

Disk and Archive share identical field structure; extract a shared private helper:

```python
def _upsert_storage_summary(
    session: Session,
    model_cls,        # DiskChargeSummary or ArchiveChargeSummary
    *,
    activity_date: date,
    act_username: str,
    act_projcode: str,
    act_unix_uid: Optional[int],
    resource_name: str,
    charges: float,
    number_of_files: Optional[int] = None,
    bytes: Optional[int] = None,
    terabyte_years: Optional[float] = None,
    username: Optional[str] = None,
    projcode: Optional[str] = None,
    unix_uid: Optional[int] = None,
    facility_name: Optional[str] = None,
    include_deleted_accounts: bool = False,
) -> Tuple[object, str]:
    """Shared upsert logic for DiskChargeSummary and ArchiveChargeSummary."""
    user = _resolve_user(session, act_username, act_unix_uid)
    project = _resolve_project(session, act_projcode)
    res = _resolve_resource(session, resource_name)
    account = _resolve_account(session, project, res, include_deleted=include_deleted_accounts)

    # Resolved field defaults — explicit None checks preserve falsy values (e.g. uid=0)
    resolved_username = username  if username  is not None else act_username
    resolved_projcode = projcode  if projcode  is not None else act_projcode
    resolved_unix_uid = unix_uid  if unix_uid  is not None else act_unix_uid

    # Facility name: use caller-provided value; fall back to resource heuristic
    if facility_name is None and res.facility_resources:
        fr = res.facility_resources[0]
        facility_name = fr.facility.facility_name if fr.facility else None

    # Natural key: (activity_date, act_username, act_projcode, account_id)
    existing = session.query(model_cls).filter(
        model_cls.activity_date == activity_date,
        model_cls.act_username == act_username,
        model_cls.act_projcode == act_projcode,
        model_cls.account_id == account.account_id,
    ).first()

    if existing is None:
        record = model_cls(
            activity_date=activity_date,
            act_username=act_username,
            act_projcode=act_projcode,
            act_unix_uid=act_unix_uid,
            username=resolved_username,
            projcode=resolved_projcode,
            unix_uid=resolved_unix_uid,
            user_id=user.user_id,
            account_id=account.account_id,
            facility_name=facility_name,
            number_of_files=number_of_files,
            bytes=bytes,
            terabyte_years=terabyte_years,
            charges=charges,
        )
        session.add(record)
        action = 'created'
    else:
        existing.username = resolved_username
        existing.projcode = resolved_projcode
        existing.unix_uid = resolved_unix_uid
        existing.user_id = user.user_id
        existing.account_id = account.account_id
        existing.facility_name = facility_name
        existing.number_of_files = number_of_files
        existing.bytes = bytes
        existing.terabyte_years = terabyte_years
        existing.charges = charges
        record = existing
        action = 'updated'

    session.flush()
    return record, action


def upsert_disk_charge_summary(session: Session, **kwargs) -> Tuple[DiskChargeSummary, str]:
    """Insert or update a DiskChargeSummary row. Delegates to _upsert_storage_summary."""
    return _upsert_storage_summary(session, DiskChargeSummary, **kwargs)


def upsert_archive_charge_summary(session: Session, **kwargs) -> Tuple[ArchiveChargeSummary, str]:
    """Insert or update an ArchiveChargeSummary row. Delegates to _upsert_storage_summary."""
    return _upsert_storage_summary(session, ArchiveChargeSummary, **kwargs)
```

---

## 5. `src/sam/manage/__init__.py` — Additions

```python
from .summaries import (
    upsert_comp_charge_summary,
    upsert_disk_charge_summary,
    upsert_archive_charge_summary,
)

__all__ = [
    # ... existing exports ...
    'upsert_comp_charge_summary',
    'upsert_disk_charge_summary',
    'upsert_archive_charge_summary',
]
```

---

## 6. Input Schemas (`src/sam/schemas/charges.py`)

Append four marshmallow `Schema` classes (plain, not SQLAlchemy auto-schemas).
These are **input/load** schemas for API deserialization — distinct from the existing
output schemas (`CompChargeSummarySchema`, etc.) which are dump-only.

### Hierarchy

```
marshmallow.Schema
  └── BaseChargeSummaryInputSchema          # act_ fields, resource_name, charges
        ├── CompChargeSummaryInputSchema    # adds machine/queue, comp metrics, cos, sweep
        └── StorageChargeSummaryInputSchema # adds file/byte/terabyte_years metrics
              ├── DiskChargeSummaryInputSchema    # (thin alias)
              └── ArchiveChargeSummaryInputSchema # (thin alias)
```

### `BaseChargeSummaryInputSchema`

```python
from marshmallow import Schema, fields, validate

class BaseChargeSummaryInputSchema(Schema):
    activity_date            = fields.Date(required=True)
    act_username             = fields.Str(required=True, validate=validate.Length(max=35))
    act_projcode             = fields.Str(required=True, validate=validate.Length(max=30))
    act_unix_uid             = fields.Int(load_default=None)  # nullable: jobs may lack uid
    resource_name            = fields.Str(required=True, validate=validate.Length(max=40))
    charges                  = fields.Float(required=True)
    # Optional resolved overrides
    username                 = fields.Str(load_default=None, validate=validate.Length(max=35))
    projcode                 = fields.Str(load_default=None, validate=validate.Length(max=30))
    unix_uid                 = fields.Int(load_default=None)
    # Facility override — bypasses [0]-index heuristic on multi-facility resources
    facility_name            = fields.Str(load_default=None, validate=validate.Length(max=30))
    # Allow resolving historically deleted accounts (for backfill)
    include_deleted_accounts = fields.Bool(load_default=False)
```

### `CompChargeSummaryInputSchema`

```python
class CompChargeSummaryInputSchema(BaseChargeSummaryInputSchema):
    machine_name            = fields.Str(required=True, validate=validate.Length(max=100))
    queue_name              = fields.Str(required=True, validate=validate.Length(max=100))
    resource                = fields.Str(load_default=None)  # resource column override
    num_jobs                = fields.Int(required=True, validate=validate.Range(min=0))
    core_hours              = fields.Float(required=True, validate=validate.Range(min=0))
    cos                     = fields.Int(load_default=None)
    sweep                   = fields.Int(load_default=None)
    error_comment           = fields.Str(load_default=None)
    create_queue_if_missing = fields.Bool(load_default=False)
```

### `StorageChargeSummaryInputSchema` (base for Disk + Archive)

```python
class StorageChargeSummaryInputSchema(BaseChargeSummaryInputSchema):
    number_of_files = fields.Int(load_default=None, validate=validate.Range(min=0))
    bytes           = fields.Int(load_default=None, validate=validate.Range(min=0))
    terabyte_years  = fields.Float(load_default=None, validate=validate.Range(min=0))

class DiskChargeSummaryInputSchema(StorageChargeSummaryInputSchema):
    pass

class ArchiveChargeSummaryInputSchema(StorageChargeSummaryInputSchema):
    pass
```

---

## 7. RBAC Permission (`src/webapp/utils/rbac.py`)

Add one entry to the `Permission` enum under "Reports and analytics":

```python
MANAGE_CHARGE_SUMMARIES = "manage_charge_summaries"  # Write charge summary records
```

- `ROLE_PERMISSIONS["admin"]` — auto-included (it's `[p for p in Permission]`)
- `ROLE_PERMISSIONS["facility_manager"]` — **not** included (write is system-level only)
- No other role changes needed

---

## 8. API Endpoints (`src/webapp/api/v1/charges.py`)

### Shared internal helper

```python
def _handle_charge_summary_post(schema, upsert_fn, output_schema_cls):
    """
    Shared handler for all three POST charge-summary endpoints.
    Validates input, calls management function, returns serialized result.
    """
    from marshmallow import ValidationError
    from sam.manage import management_transaction

    data = request.get_json()
    if not data:
        return jsonify({'error': 'Request body must be JSON'}), 400

    try:
        validated = schema.load(data)
    except ValidationError as e:
        return jsonify({'error': 'Validation failed', 'details': e.messages}), 400

    try:
        with management_transaction(db.session):
            record, action = upsert_fn(db.session, **validated)
    except ValueError as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 422
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Internal error: {str(e)}'}), 500

    status_code = 201 if action == 'created' else 200
    return jsonify({
        'success': True,
        'action': action,
        'charge_summary': output_schema_cls().dump(record),
    }), status_code
```

### Three route handlers

```python
@bp.route('/charge-summaries/comp', methods=['POST'])
@login_required
@require_permission(Permission.MANAGE_CHARGE_SUMMARIES)
def create_comp_charge_summary():
    """POST /api/v1/charge-summaries/comp — Insert/update a comp charge summary."""
    from sam.manage import upsert_comp_charge_summary
    return _handle_charge_summary_post(
        CompChargeSummaryInputSchema(),
        upsert_comp_charge_summary,
        CompChargeSummarySchema,
    )

@bp.route('/charge-summaries/disk', methods=['POST'])
@login_required
@require_permission(Permission.MANAGE_CHARGE_SUMMARIES)
def create_disk_charge_summary():
    """POST /api/v1/charge-summaries/disk — Insert/update a disk charge summary."""
    from sam.manage import upsert_disk_charge_summary
    return _handle_charge_summary_post(
        DiskChargeSummaryInputSchema(),
        upsert_disk_charge_summary,
        DiskChargeSummarySchema,
    )

@bp.route('/charge-summaries/archive', methods=['POST'])
@login_required
@require_permission(Permission.MANAGE_CHARGE_SUMMARIES)
def create_archive_charge_summary():
    """POST /api/v1/charge-summaries/archive — Insert/update an archive charge summary."""
    from sam.manage import upsert_archive_charge_summary
    return _handle_charge_summary_post(
        ArchiveChargeSummaryInputSchema(),
        upsert_archive_charge_summary,
        ArchiveChargeSummarySchema,
    )
```

The three new schemas (`CompChargeSummaryInputSchema`, `DiskChargeSummaryInputSchema`,
`ArchiveChargeSummaryInputSchema`) need to be imported at the top of `charges.py`.

---

## 9. Test Plan (`tests/unit/test_manage_summaries.py`)

### Class: `TestResolverHelpers`

Tests for the shared private validation helpers (imported directly for white-box testing):

| Test | What it validates |
|------|-------------------|
| `test_resolve_user_by_username` | Happy path via username |
| `test_resolve_user_by_uid_fallback` | Falls back to uid when username not found |
| `test_resolve_user_not_found` | Raises `ValueError` containing both username and uid |
| `test_resolve_user_none_uid` | `act_unix_uid=None` uses username only; error message contains "no uid" |
| `test_resolve_project_not_found` | Raises `ValueError` containing projcode |
| `test_resolve_resource_not_found` | Raises `ValueError` containing resource name |
| `test_resolve_account_not_found` | Raises `ValueError` containing project + resource |
| `test_resolve_account_include_deleted` | Deleted account found when `include_deleted=True` |
| `test_resolve_machine_not_found` | Raises `ValueError` containing machine + resource |
| `test_resolve_queue_not_found_no_create` | Raises `ValueError` with hint about flag |
| `test_resolve_queue_creates_when_flag_set` | New `Queue` row in session after flush |

### Class: `TestUpsertCompChargeSummary`

Uses `session` fixture (auto-rollback) and live test entities from DB.

| Test | Scenario |
|------|----------|
| `test_insert_new` | Valid inputs → row created, returns `('created')` |
| `test_update_existing` | Same natural key → row updated, returns `('updated')` |
| `test_act_fields_immutable_on_update` | `act_username`, `act_projcode`, `act_unix_uid` unchanged after update |
| `test_resolved_fields_default_from_act` | Omitted `username`/`projcode` default to act_ values |
| `test_zero_unix_uid_preserved` | `unix_uid=0` stored as 0, not replaced by `act_unix_uid` |
| `test_put_semantics_nulls_optional_fields` | Second call omitting `cos` overwrites stored value with NULL |
| `test_facility_name_override` | Explicit `facility_name` in payload stored, bypassing heuristic |
| `test_include_deleted_accounts` | `include_deleted_accounts=True` finds account default would miss |
| `test_invalid_user_raises` | Unknown username + uid → `ValueError` containing "User" |
| `test_invalid_project_raises` | Unknown projcode → `ValueError` containing "Project" |
| `test_invalid_resource_raises` | Unknown resource → `ValueError` containing "Resource" |
| `test_invalid_machine_raises` | Unknown machine → `ValueError` containing "Machine" |
| `test_missing_queue_no_flag_raises` | Missing queue, flag=False → `ValueError` with hint |
| `test_create_queue_if_missing` | Missing queue, flag=True → Queue created, insert succeeds |
| `test_no_session_commit` | After call, session not committed (rollback cleans up) |

### Class: `TestUpsertStorageChargeSummaries`

Use `pytest.mark.parametrize` over `(model_cls, upsert_fn)` pairs to avoid duplication:

```python
STORAGE_CASES = [
    (DiskChargeSummary, upsert_disk_charge_summary, "Campaign Store"),
    (ArchiveChargeSummary, upsert_archive_charge_summary, "HPSS"),
]
```

| Test | Scenario |
|------|----------|
| `test_insert_new[disk]` / `[archive]` | Happy path insert |
| `test_update_existing[disk]` / `[archive]` | Natural key hit → update |
| `test_act_fields_immutable[disk]` / `[archive]` | act_ unchanged after update |
| `test_invalid_user[disk]` / `[archive]` | Unknown user → `ValueError` |
| `test_invalid_project[disk]` / `[archive]` | Unknown project → `ValueError` |
| `test_invalid_resource[disk]` / `[archive]` | Unknown resource → `ValueError` |
| `test_no_account[disk]` / `[archive]` | Valid project+resource but no account → `ValueError` |

### Class: `TestInputSchemas`

| Test | Schema | Scenario |
|------|--------|----------|
| `test_comp_valid_input` | Comp | All fields → clean dict |
| `test_comp_missing_required` | Comp | Missing `activity_date` → `ValidationError` |
| `test_comp_invalid_date` | Comp | Bad date string → `ValidationError` |
| `test_comp_negative_num_jobs` | Comp | `num_jobs=-1` → `ValidationError` |
| `test_comp_queue_flag_defaults_false` | Comp | Absent flag → `False` |
| `test_disk_valid_input` | Disk | All fields → clean dict |
| `test_archive_valid_input` | Archive | All fields → clean dict |
| `test_storage_negative_bytes` | Disk | `bytes=-1` → `ValidationError` |
| `test_storage_missing_required` | Archive | Missing `charges` → `ValidationError` |

### API test additions to `tests/api/test_charge_endpoints.py`

| Test | Endpoint | Scenario | Expected |
|------|----------|----------|---------|
| `test_post_comp_created` | `/comp` | Valid body, first insert | **201**, `action=created` |
| `test_post_comp_updated` | `/comp` | POST twice same key | **200**, `action=updated` |
| `test_post_comp_validation_error` | `/comp` | Missing field | 400 |
| `test_post_comp_unknown_user` | `/comp` | Unknown user | 422 |
| `test_post_comp_queue_missing_no_flag` | `/comp` | Unknown queue | 422 with hint |
| `test_post_comp_queue_autocreate` | `/comp` | Unknown queue + flag | 201 |
| `test_post_comp_facility_name_override` | `/comp` | Explicit `facility_name` | 201, stored value matches |
| `test_post_disk_created` | `/disk` | Valid body | 201 |
| `test_post_disk_validation_error` | `/disk` | Missing field | 400 |
| `test_post_archive_created` | `/archive` | Valid body | 201 |
| `test_post_unauthorized` | all | Non-admin user | 403 |
| `test_post_unauthenticated` | all | No login | 401/302 |

---

## 10. Implementation Sequence

Build bottom-up, verify at each layer before proceeding:

1. `src/sam/manage/summaries.py` — helpers + 3 public functions
2. `src/sam/manage/__init__.py` — expose public API
3. `tests/unit/test_manage_summaries.py` — unit tests (validates management layer)
4. `src/sam/schemas/charges.py` — append 4 input schemas
5. `src/webapp/utils/rbac.py` — add `MANAGE_CHARGE_SUMMARIES`
6. `src/webapp/api/v1/charges.py` — add shared helper + 3 POST routes
7. `tests/api/test_charge_endpoints.py` — API integration tests

---

## 11. Verification

```bash
# Run new management layer tests
source ../.env && pytest tests/unit/test_manage_summaries.py -v

# Run full suite for regressions
source ../.env && pytest tests/ --no-cov

# API smoke tests (requires running webapp: docker compose up)

# Comp charge summary
curl -X POST http://localhost:5050/api/v1/charge-summaries/comp \
  -H "Content-Type: application/json" \
  -d '{
    "activity_date": "2025-01-15",
    "act_username": "benkirk",
    "act_projcode": "SCSG0001",
    "act_unix_uid": 12345,
    "resource_name": "Derecho",
    "machine_name": "derecho",
    "queue_name": "main",
    "num_jobs": 10,
    "core_hours": 1234.5,
    "charges": 987.65
  }'

# Disk charge summary
curl -X POST http://localhost:5050/api/v1/charge-summaries/disk \
  -H "Content-Type: application/json" \
  -d '{
    "activity_date": "2025-01-15",
    "act_username": "benkirk",
    "act_projcode": "SCSG0001",
    "act_unix_uid": 12345,
    "resource_name": "Campaign Store",
    "terabyte_years": 0.5,
    "charges": 25.0
  }'

# Archive charge summary
curl -X POST http://localhost:5050/api/v1/charge-summaries/archive \
  -H "Content-Type: application/json" \
  -d '{
    "activity_date": "2025-01-15",
    "act_username": "benkirk",
    "act_projcode": "SCSG0001",
    "act_unix_uid": 12345,
    "resource_name": "HPSS",
    "number_of_files": 500,
    "terabyte_years": 1.2,
    "charges": 60.0
  }'
```
