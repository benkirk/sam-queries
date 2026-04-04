# Charging Integration Guide

How external utilities can post daily charge summaries into SAM. Two integration
paths are supported: **REST API** (recommended for remote / language-agnostic
callers) and **direct Python/ORM** (for co-located Python processes).

Both paths funnel into the same upsert functions and produce identical results.

---

## Prerequisites

Before posting charges you need the following entities to already exist in SAM:

| Entity | How it's resolved | Notes |
|--------|-------------------|-------|
| **User** | `act_username` (falls back to `act_unix_uid`) | Must exist in `users` table |
| **Project** | `act_projcode` | Must exist in `project` table |
| **Resource** | `resource_name` | e.g. `Derecho`, `Campaign Store`, `Quasar` |
| **Account** | Auto-resolved from project + resource | Links a project to a resource |
| **Machine** | `machine_name` (comp only) | Optional if resource has exactly one machine |
| **Queue** | `queue_name` (comp only) | Can be auto-created with a flag |

The upsert functions resolve these entities in order and raise `ValueError`
(ORM) or return HTTP 422 (API) on the first lookup failure, with a message
identifying which entity was not found.

---

## Charge Summary Types

SAM tracks three types of daily charge summaries:

### Computational (`comp_charge_summary`)
HPC and DAV job charges aggregated per (date, user, project, machine, queue).

### Disk (`disk_charge_summary`)
Daily storage charges for disk resources (Campaign Store, Glade, etc.).

### Archive (`archive_charge_summary`)
Daily HPSS/tape archive charges.

Disk and archive share identical field sets and differ only in which table
they target.

---

## Option 1: REST API

### Base URL

```
POST /api/v1/charge-summaries/comp
POST /api/v1/charge-summaries/disk
POST /api/v1/charge-summaries/archive
```

### Authentication

All POST endpoints require login and the `MANAGE_CHARGE_SUMMARIES` permission.
Only the `admin` role includes this permission by default. Callers must
authenticate via the webapp's session mechanism (cookie-based login).

### Comp Charge Summary

```bash
curl -X POST https://sam.example.org/api/v1/charge-summaries/comp \
  -H 'Content-Type: application/json' \
  -b cookies.txt \
  -d '{
    "activity_date": "2025-06-15",
    "act_username": "jsmith",
    "act_projcode": "SCSG0001",
    "act_unix_uid": 12345,
    "resource_name": "Derecho",
    "queue_name": "main",
    "num_jobs": 42,
    "core_hours": 8500.0,
    "charges": 6750.25
  }'
```

**Required fields:**

| Field | Type | Description |
|-------|------|-------------|
| `activity_date` | `YYYY-MM-DD` | The date the charges apply to |
| `act_username` | string (≤35) | Username as recorded by the scheduler |
| `act_projcode` | string (≤30) | Project code as recorded by the scheduler |
| `resource_name` | string (≤40) | SAM resource name (e.g. `Derecho`) |
| `queue_name` | string (≤100) | Queue/partition name |
| `num_jobs` | int (≥0) | Number of jobs in this bucket |
| `core_hours` | float (≥0) | Total core-hours consumed |
| `charges` | float | Charges in allocation units |

**Optional fields:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `machine_name` | string (≤100) | Auto-resolved | Machine within resource. Omit if resource has exactly one machine; required if multiple. |
| `act_unix_uid` | int | `null` | UID from scheduler (fallback for user lookup) |
| `facility_name` | string (≤30) | Auto-resolved | Override facility. Defaults to `project.allocation_type.panel.facility`. |
| `username` | string | = `act_username` | Resolved/corrected username |
| `projcode` | string | = `act_projcode` | Resolved/corrected project code |
| `unix_uid` | int | = `act_unix_uid` | Resolved/corrected UID |
| `resource` | string | = `resource_name` | Override for the `resource` column value |
| `cos` | int | `null` | Class of service |
| `sweep` | int | `null` | Sweep identifier |
| `error_comment` | string | `null` | Error annotation |
| `create_queue_if_missing` | bool | `false` | Auto-create queue if not found |
| `include_deleted_accounts` | bool | `false` | Allow matching deleted accounts (for backfill) |

### Disk / Archive Charge Summary

```bash
curl -X POST https://sam.example.org/api/v1/charge-summaries/disk \
  -H 'Content-Type: application/json' \
  -b cookies.txt \
  -d '{
    "activity_date": "2025-06-15",
    "act_username": "jsmith",
    "act_projcode": "SCSG0001",
    "resource_name": "Campaign Store",
    "charges": 12.50,
    "number_of_files": 45000,
    "bytes": 1099511627776,
    "terabyte_years": 0.75
  }'
```

Replace `/disk` with `/archive` for HPSS charges — the payload is identical.

**Required fields:**

| Field | Type | Description |
|-------|------|-------------|
| `activity_date` | `YYYY-MM-DD` | Date the charges apply to |
| `act_username` | string (≤35) | Username as recorded |
| `act_projcode` | string (≤30) | Project code as recorded |
| `resource_name` | string (≤40) | SAM resource name |
| `charges` | float | Charges in allocation units |

**Optional fields:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `act_unix_uid` | int | `null` | UID (fallback for user lookup) |
| `number_of_files` | int (≥0) | `null` | File count |
| `bytes` | int (≥0) | `null` | Total bytes |
| `terabyte_years` | float (≥0) | `null` | TB-years metric |
| `facility_name` | string | Auto-resolved | Override facility name |
| `username` | string | = `act_username` | Resolved username |
| `projcode` | string | = `act_projcode` | Resolved project code |
| `unix_uid` | int | = `act_unix_uid` | Resolved UID |
| `include_deleted_accounts` | bool | `false` | Allow deleted accounts |

### Response Format

**Success (201 Created / 200 Updated):**
```json
{
  "success": true,
  "action": "created",
  "charge_summary": {
    "charge_summary_id": 98765,
    "activity_date": "2025-06-15",
    "account_id": 1234,
    "user_id": 567,
    "num_jobs": 42,
    "core_hours": 8500.0,
    "charges": 6750.25
  }
}
```

**Validation Error (400):**
```json
{
  "error": "Validation failed",
  "details": {
    "activity_date": ["Missing data for required field."]
  }
}
```

**Entity Not Found (422):**
```json
{
  "error": "User 'jsmith' (unix_uid=12345) not found in SAM"
}
```

### Upsert Semantics

All three endpoints use **PUT-style upsert**:

- If no row matches the natural key, a new row is **created** (HTTP 201).
- If a row matches, all mutable fields are **replaced** (HTTP 200). Fields
  omitted from the payload are written as `NULL` — this is intentional to
  support corrections.
- The `act_*` fields (act_username, act_projcode, act_unix_uid) are
  **immutable** after creation — updates never overwrite them.

**Natural keys:**

| Type | Natural key columns |
|------|---------------------|
| Comp | `(activity_date, act_username, act_projcode, machine, queue, resource)` |
| Disk / Archive | `(activity_date, act_username, act_projcode, account_id)` |

---

## Option 2: Direct Python / ORM

For co-located Python processes that share the SAM database connection,
call the management functions directly. This avoids HTTP overhead and is
suitable for batch importers running on the same host.

### Setup

```python
from sqlalchemy.orm import Session
from sam.session import create_sam_engine
from sam.manage import (
    management_transaction,
    upsert_comp_charge_summary,
    upsert_disk_charge_summary,
    upsert_archive_charge_summary,
)
from datetime import date

engine, _ = create_sam_engine()
```

### Posting Comp Charges

```python
with Session(engine) as session:
    with management_transaction(session):
        record, action = upsert_comp_charge_summary(
            session,
            activity_date=date(2025, 6, 15),
            act_username='jsmith',
            act_projcode='SCSG0001',
            act_unix_uid=12345,
            resource_name='Derecho',
            queue_name='main',
            num_jobs=42,
            core_hours=8500.0,
            charges=6750.25,
            # machine_name is optional for single-machine resources
        )
        print(f"{action}: id={record.charge_summary_id}")
    # Auto-committed here
```

### Posting Disk Charges

```python
with Session(engine) as session:
    with management_transaction(session):
        record, action = upsert_disk_charge_summary(
            session,
            activity_date=date(2025, 6, 15),
            act_username='jsmith',
            act_projcode='SCSG0001',
            act_unix_uid=12345,
            resource_name='Campaign Store',
            charges=12.50,
            number_of_files=45000,
            bytes=1099511627776,
            terabyte_years=0.75,
        )
```

### Posting Archive Charges

```python
with Session(engine) as session:
    with management_transaction(session):
        record, action = upsert_archive_charge_summary(
            session,
            activity_date=date(2025, 6, 15),
            act_username='jsmith',
            act_projcode='SCSG0001',
            act_unix_uid=12345,
            resource_name='Quasar',
            charges=60.0,
            number_of_files=1000,
            terabyte_years=1.2,
        )
```

### Batch Import Pattern

For daily batch jobs that process many rows, group work inside a single
transaction per logical batch. Each `management_transaction` block commits
atomically — if any row fails, the entire block rolls back.

```python
import csv
from datetime import datetime

with Session(engine) as session:
    with management_transaction(session):
        with open('daily_charges.csv') as f:
            for row in csv.DictReader(f):
                upsert_comp_charge_summary(
                    session,
                    activity_date=datetime.strptime(row['date'], '%Y-%m-%d').date(),
                    act_username=row['username'],
                    act_projcode=row['projcode'],
                    act_unix_uid=int(row['uid']) if row['uid'] else None,
                    resource_name=row['resource'],
                    queue_name=row['queue'],
                    num_jobs=int(row['num_jobs']),
                    core_hours=float(row['core_hours']),
                    charges=float(row['charges']),
                    create_queue_if_missing=True,  # auto-create unknown queues
                )
        # All rows committed together on block exit
```

For very large imports, consider chunking into smaller transactions to
limit rollback scope:

```python
CHUNK_SIZE = 500

with Session(engine) as session:
    for i in range(0, len(rows), CHUNK_SIZE):
        chunk = rows[i:i + CHUNK_SIZE]
        with management_transaction(session):
            for row in chunk:
                upsert_comp_charge_summary(session, **row)
        print(f"Committed rows {i}–{i + len(chunk) - 1}")
```

### Error Handling

All three upsert functions raise `ValueError` when entity resolution fails.
The error message identifies exactly which entity was not found:

```python
try:
    with management_transaction(session):
        upsert_comp_charge_summary(session, ...)
except ValueError as e:
    # e.g. "User 'jsmith' (unix_uid=12345) not found in SAM"
    # e.g. "No account found for project 'SCSG0001' on resource 'Derecho'"
    # e.g. "Resource 'Derecho' has 3 machines (a, b, c); machine_name must be provided explicitly"
    log.error(f"Charge import failed: {e}")
```

The `management_transaction` context manager handles rollback automatically
on any exception — you never need to call `session.rollback()` manually.

---

## Entity Resolution Details

### Facility Name

When `facility_name` is omitted, it is derived from the project's allocation
type chain: `project.allocation_type.panel.facility.facility_name`. This
follows the canonical SAM data model and produces the correct facility for
the project's allocation panel. If any link in the chain is `NULL`, the
facility_name column is written as `NULL`.

You can override this by passing `facility_name` explicitly — useful for
edge cases where a project spans multiple facilities.

### Machine Name (comp only)

`machine_name` is optional. When omitted:

- If the resource has **exactly one machine**, it is auto-resolved.
- If the resource has **zero machines**, a `ValueError` is raised.
- If the resource has **two or more machines**, a `ValueError` is raised
  listing all machine names so the caller knows which to specify.

When provided explicitly, the machine must exist on the specified resource.

### Queue Auto-Creation (comp only)

By default, an unknown queue name raises `ValueError`. Pass
`create_queue_if_missing=True` to auto-create the queue on the resource.
This is useful during initial data import when queue definitions may not
yet be complete.

### Deleted Accounts (backfill)

By default, only active (non-deleted) accounts are matched. For historical
backfill of charges against projects that have since been decommissioned,
pass `include_deleted_accounts=True`.

---

## Concurrency Warning

The upsert functions use a query-then-insert pattern (not atomic
`ON DUPLICATE KEY UPDATE`). The summary tables have no `UNIQUE` constraint
on their natural key columns, so **concurrent writes for the same natural
key may produce duplicate rows**.

For production batch processes:
- Serialize writes for the same (date, user, project, resource) bucket.
- Run one importer instance per resource at a time.
- If duplicates occur, they can be detected and cleaned up by querying
  for rows sharing the same natural key.

---

## Function Signatures Reference

### upsert_comp_charge_summary

```python
def upsert_comp_charge_summary(
    session: Session,
    *,
    activity_date: date,           # Required
    act_username: str,             # Required
    act_projcode: str,             # Required
    act_unix_uid: Optional[int],   # Required (can be None)
    resource_name: str,            # Required
    queue_name: str,               # Required
    num_jobs: int,                 # Required
    core_hours: float,             # Required
    charges: float,                # Required
    machine_name: Optional[str] = None,        # Auto-resolved if omitted
    username: Optional[str] = None,            # Defaults to act_username
    projcode: Optional[str] = None,            # Defaults to act_projcode
    unix_uid: Optional[int] = None,            # Defaults to act_unix_uid
    resource: Optional[str] = None,            # Defaults to resource_name
    facility_name: Optional[str] = None,       # Auto-resolved from project
    cos: Optional[int] = None,
    sweep: Optional[int] = None,
    error_comment: Optional[str] = None,
    create_queue_if_missing: bool = False,
    include_deleted_accounts: bool = False,
) -> Tuple[CompChargeSummary, str]:  # Returns (record, 'created'|'updated')
```

### upsert_disk_charge_summary / upsert_archive_charge_summary

```python
def upsert_disk_charge_summary(    # Same signature for archive
    session: Session,
    *,
    activity_date: date,           # Required
    act_username: str,             # Required
    act_projcode: str,             # Required
    act_unix_uid: Optional[int],   # Required (can be None)
    resource_name: str,            # Required
    charges: float,                # Required
    number_of_files: Optional[int] = None,
    bytes: Optional[int] = None,
    terabyte_years: Optional[float] = None,
    username: Optional[str] = None,
    projcode: Optional[str] = None,
    unix_uid: Optional[int] = None,
    facility_name: Optional[str] = None,
    include_deleted_accounts: bool = False,
) -> Tuple[DiskChargeSummary, str]:  # Returns (record, 'created'|'updated')
```
