# PBS Reservation Collection - Implementation Plan

## Executive Summary

Add PBS reservation collection to the HPC collectors (Derecho and Casper) by:
1. Creating a `ReservationParser` to parse `pbs_rstat -f` output
2. Adding reservation handling to existing POST endpoints (`/api/v1/status/derecho`, `/api/v1/status/casper`)
3. Using upsert logic to update existing reservations or insert new ones
4. Integrating into both collector workflows with graceful error handling

## Design Decisions

**Endpoint Strategy**: Include reservations in existing status POST endpoints (like login_nodes, queues, filesystems)
- Simpler collector integration (one API call per system)
- Consistent with current architecture pattern
- All status data posted together with same timestamp

**Update Strategy**: Upsert logic (update if exists, insert if new)
- Identify duplicates by `(system_name, reservation_name)` composite key
- Update times/node_count if reservation already exists
- Keeps database clean with one record per active reservation
- No need for cleanup logic

## PBS Reservation Format Analysis

PBS `pbs_rstat -f` produces output like:
```
Resv ID: R416808.casper-pbs
Reserve_Name = casper-a100-testing
Reserve_Owner = root@casper-pbs.hpc.ucar.edu
reserve_state = RESV_RUNNING
reserve_start = Wed Nov 12 12:00:00 2025
reserve_end = Wed Dec 31 23:59:00 2025
Resource_List.nodect = 2
Resource_List.select = 1:host=casper41:ncpus=128:ngpus=4
resv_nodes = (casper41:ncpus=128)+(casper44:ncpus=128)
Authorized_Users = +user1,+user2
partition = pbs-default

Resv ID: R416809.casper-pbs
Reserve_Name = maintenance-window
...
```

**Key Observations**:
1. Reservations separated by blank lines
2. Key-value pairs separated by ` = `
3. DateTime format: `Wed Nov 12 12:00:00 2025`
4. Node count sources: `Resource_List.nodect` or count nodes in `resv_nodes`
5. Partition field may be present
6. Reserve_Name is unique per system

---

## Implementation Plan

### Phase 1: Create Reservation Parser

**File**: `/Users/benkirk/codes/sam-queries/collectors/lib/parsers/reservations.py` (NEW)

**Pattern to Follow**: `/Users/benkirk/codes/sam-queries/collectors/lib/parsers/queues.py` and `filesystems.py`

**Class Structure**:
```python
class ReservationParser:
    """Parse PBS pbs_rstat -f output."""
    
    @staticmethod
    def parse_reservations(rstat_output: str, system_name: str) -> List[dict]
```

**Implementation Steps**:

1. **Split into reservation blocks**:
   - Split text by double newline `\n\n`
   - Filter out empty blocks
   - Each block represents one reservation

2. **Parse key-value pairs**:
   - Split each line by ` = `
   - Build dict of reservation attributes
   - Example: `Reserve_Name = casper-a100-testing` → `{'Reserve_Name': 'casper-a100-testing'}`

3. **Extract required fields**:
   - `reservation_name`: From `Reserve_Name` field
   - `description`: From `Reserve_Name` or `Resv ID` (PBS doesn't have description field)
   - `start_time`: Parse `reserve_start` datetime
   - `end_time`: Parse `reserve_end` datetime
   - `node_count`: From `Resource_List.nodect` OR count `resv_nodes` entries
   - `partition`: From `partition` field (may be missing)

4. **DateTime parsing**:
   ```python
   from datetime import datetime
   
   # PBS format: "Wed Nov 12 12:00:00 2025"
   dt = datetime.strptime(time_str, "%a %b %d %H:%M:%S %Y")
   return dt.isoformat()  # Convert to ISO format for API
   ```

5. **Node count extraction**:
   - **Priority 1**: Use `Resource_List.nodect` if present
   - **Priority 2**: Parse `resv_nodes` field:
     ```python
     # Format: "(node1:ncpus=128)+(node2:ncpus=128)"
     # Split by '+', count entries
     if 'resv_nodes' in data:
         nodes = data['resv_nodes'].split('+')
         node_count = len(nodes)
     ```
   - **Fallback**: Set to `None`

6. **Error Handling**:
   - Log warnings for unparseable reservations
   - Continue parsing remaining reservations
   - Return partial results rather than failing completely
   - Handle missing fields gracefully (use None/defaults)

**Return Format**:
```python
[
    {
        'system_name': 'derecho',
        'reservation_name': 'MONTHLY_MAINTENANCE',
        'description': 'Monthly maintenance window',
        'start_time': '2025-02-01T06:00:00',
        'end_time': '2025-02-01T18:00:00',
        'node_count': 2488,
        'partition': 'pbs-default'
    },
    ...
]
```

**Edge Cases to Handle**:
- Empty output (no active reservations) → return `[]`
- Malformed datetime strings → log warning, skip reservation
- Missing `Reserve_Name` → skip reservation (required field)
- Missing `reserve_start` or `reserve_end` → skip reservation
- Missing partition → set to `None`
- No node count available → set to `None`

---

### Phase 2: Update API Endpoint

**File**: `/Users/benkirk/codes/sam-queries/python/webui/api/v1/status.py` (MODIFY)

**Changes Required**:

#### 2.1: Add Reservation Handling to `ingest_derecho()` function

Location: After filesystem handling (around line 228)

```python
# Handle reservation status if provided
reservations = data.get('reservations', [])
if reservations:
    from sqlalchemy import and_
    
    reservation_ids = []
    for resv_data in reservations:
        # Upsert logic: check if reservation exists
        existing = session.query(ResourceReservation).filter(
            and_(
                ResourceReservation.system_name == 'derecho',
                ResourceReservation.reservation_name == resv_data['reservation_name']
            )
        ).first()
        
        if existing:
            # Update existing reservation
            existing.description = resv_data.get('description')
            existing.start_time = datetime.fromisoformat(resv_data['start_time'])
            existing.end_time = datetime.fromisoformat(resv_data['end_time'])
            existing.node_count = resv_data.get('node_count')
            existing.partition = resv_data.get('partition')
            existing.updated_at = datetime.now()
            reservation_ids.append(existing.reservation_id)
        else:
            # Insert new reservation
            resv = ResourceReservation(
                system_name='derecho',
                reservation_name=resv_data['reservation_name'],
                description=resv_data.get('description'),
                start_time=datetime.fromisoformat(resv_data['start_time']),
                end_time=datetime.fromisoformat(resv_data['end_time']),
                node_count=resv_data.get('node_count'),
                partition=resv_data.get('partition'),
            )
            session.add(resv)
            session.flush()
            reservation_ids.append(resv.reservation_id)
    
    result['reservation_ids'] = reservation_ids
```

**Key Points**:
- Use `filter(and_(...))` for composite key lookup
- Parse ISO datetime strings from collector back to datetime objects
- Update `updated_at` timestamp on existing records
- Include `reservation_ids` in response

#### 2.2: Add Reservation Handling to `ingest_casper()` function

Location: After filesystem handling (around line 404)

**Identical logic to Derecho**, just change `system_name='casper'`

#### 2.3: Update Docstrings

Add to both `ingest_derecho()` and `ingest_casper()` docstrings:
```
- reservations (optional): List of reservation dicts
```

**Testing Considerations**:
- Test with empty reservations array
- Test with single reservation
- Test with multiple reservations
- Test upsert logic (same reservation posted twice)
- Test datetime parsing

---

### Phase 3: Integrate into Derecho Collector

**File**: `/Users/benkirk/codes/sam-queries/collectors/derecho/collector.py` (MODIFY)

**Changes Required**:

#### 3.1: Add Import

Line 26 (after other parser imports):
```python
from parsers.reservations import ReservationParser
```

#### 3.2: Add Collection Logic

Location: In `collect()` method, after filesystem collection (around line 142)

```python
# Collect reservation data
try:
    self.logger.info("Collecting reservation data...")
    rstat_output = self.pbs.get_reservations()
    data['reservations'] = ReservationParser.parse_reservations(
        rstat_output,
        'derecho'
    )
    self.logger.info(f"  Reservations: {len(data['reservations'])} active")
except Exception as e:
    self.logger.error(f"Failed to collect reservation data: {e}")
    data['reservations'] = []
```

**Error Handling**:
- Wrap in try/except block
- Log error but continue collection
- Set `data['reservations'] = []` on failure
- Mirrors pattern used for other collection types (login nodes, filesystems, etc.)

**Logging**:
- Info level: "Collecting reservation data..."
- Info level: Number of reservations found
- Error level: Collection failure with exception

---

### Phase 4: Integrate into Casper Collector

**File**: `/Users/benkirk/codes/sam-queries/collectors/casper/collector.py` (MODIFY)

**Changes Required**:

#### 4.1: Add Import

Line 26 (after other parser imports):
```python
from parsers.reservations import ReservationParser
```

#### 4.2: Add Collection Logic

Location: In `collect()` method, after filesystem collection (around line 196)

**Identical logic to Derecho**, just change system name to `'casper'`:

```python
# Collect reservation data
try:
    self.logger.info("Collecting reservation data...")
    rstat_output = self.pbs.get_reservations()
    data['reservations'] = ReservationParser.parse_reservations(
        rstat_output,
        'casper'
    )
    self.logger.info(f"  Reservations: {len(data['reservations'])} active")
except Exception as e:
    self.logger.error(f"Failed to collect reservation data: {e}")
    data['reservations'] = []
```

---

## Testing Strategy

### Unit Tests for ReservationParser

Create test file: `/Users/benkirk/codes/sam-queries/collectors/tests/test_reservation_parser.py`

**Test Cases**:

1. **Empty output** - no active reservations
   ```python
   output = ""
   result = ReservationParser.parse_reservations(output, 'derecho')
   assert result == []
   ```

2. **Single reservation** - parse all fields correctly
   ```python
   output = """
   Resv ID: R416808.casper-pbs
   Reserve_Name = test-reservation
   reserve_start = Wed Nov 12 12:00:00 2025
   reserve_end = Wed Dec 31 23:59:00 2025
   Resource_List.nodect = 5
   partition = pbs-default
   """
   result = ReservationParser.parse_reservations(output, 'casper')
   assert len(result) == 1
   assert result[0]['reservation_name'] == 'test-reservation'
   assert result[0]['node_count'] == 5
   ```

3. **Multiple reservations** - parse multiple blocks
4. **Missing partition** - handle gracefully
5. **Missing node count** - fallback to resv_nodes parsing
6. **Malformed datetime** - skip reservation, log warning
7. **Missing required fields** - skip reservation

### Integration Tests

1. **Dry-run collection**:
   ```bash
   ./collectors/derecho/collector.py --dry-run --verbose
   # Check logs for "Reservations: N active"
   ```

2. **JSON-only output**:
   ```bash
   ./collectors/derecho/collector.py --json-only | jq '.reservations'
   ```

3. **API posting**:
   ```bash
   ./collectors/derecho/collector.py --verbose
   # Check API response includes reservation_ids
   ```

4. **Upsert verification**:
   - Post same data twice
   - Verify database has only one record per reservation_name
   - Verify updated_at timestamp changes

### Manual Testing Scenarios

1. **No active reservations** - verify empty array handling
2. **Active maintenance window** - verify all fields populated
3. **Multiple simultaneous reservations** - verify all captured
4. **Expired reservation** - verify filtered by GET endpoint's `upcoming_only` parameter

---

## Edge Cases & Error Handling

### Parser Edge Cases

| Scenario | Handling | Expected Result |
|----------|----------|-----------------|
| Empty `pbs_rstat` output | Return `[]` | No errors, empty array |
| Malformed datetime | Log warning, skip reservation | Partial results |
| Missing Reserve_Name | Skip reservation | Continue parsing others |
| Missing start/end time | Skip reservation | Continue parsing others |
| No node count field | Try resv_nodes, else None | Reservation with node_count=None |
| Missing partition | Set to None | Reservation with partition=None |
| Duplicate reservation names | Upsert updates existing | One record per name |
| PBS command timeout | Exception caught by collector | reservations=[], log error |
| PBS command failure | Exception caught by collector | reservations=[], log error |

### Collector Edge Cases

| Scenario | Handling | Expected Result |
|----------|----------|-----------------|
| SSH timeout to PBS server | Try/except in collect() | reservations=[], continue collection |
| Parser exception | Try/except in collect() | reservations=[], log error |
| API POST failure | Handled by api_client retry logic | Retry up to 3 times |
| Empty reservations array | Posted to API | No records inserted |

### API Edge Cases

| Scenario | Handling | Expected Result |
|----------|----------|-----------------|
| reservations key missing | data.get('reservations', []) | No processing, no error |
| Empty reservations array | Skip processing block | No records inserted |
| Duplicate POST (same data) | Upsert logic | Updates existing records |
| Invalid datetime format | fromisoformat() raises ValueError | 500 error, rollback |
| Missing required field | Database constraint violation | 500 error, rollback |

---

## Database Schema

**Table**: `resource_reservations` (already exists)

```sql
CREATE TABLE resource_reservations (
    reservation_id INT PRIMARY KEY AUTO_INCREMENT,
    system_name VARCHAR(64) NOT NULL,
    reservation_name VARCHAR(128) NOT NULL,
    description TEXT,
    start_time DATETIME NOT NULL,
    end_time DATETIME NOT NULL,
    node_count INT,
    partition VARCHAR(64),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME ON UPDATE CURRENT_TIMESTAMP,
    
    INDEX ix_reservation_system_name (system_name),
    INDEX ix_reservation_start_time (start_time),
    UNIQUE KEY uq_system_reservation (system_name, reservation_name)  -- ADD THIS INDEX
);
```

**Schema Change Required**: Add unique constraint for upsert logic
```sql
ALTER TABLE resource_reservations 
ADD UNIQUE KEY uq_system_reservation (system_name, reservation_name);
```

This ensures one reservation per (system_name, reservation_name) combination and enables efficient upsert lookups.

---

## Code Patterns Summary

### Parser Pattern (from filesystems.py, queues.py)
```python
class ReservationParser:
    @staticmethod
    def parse_reservations(rstat_output: str, system_name: str) -> List[dict]:
        """Parse PBS rstat output."""
        logger = logging.getLogger(__name__)
        
        try:
            # Parsing logic
            return results
        except Exception as e:
            logger.error(f"Parse error: {e}")
            return []
```

### Collector Pattern (from derecho/collector.py)
```python
# In collect() method:
try:
    self.logger.info("Collecting reservation data...")
    output = self.pbs.get_reservations()
    data['reservations'] = ReservationParser.parse_reservations(output, 'derecho')
    self.logger.info(f"  Reservations: {len(data['reservations'])} active")
except Exception as e:
    self.logger.error(f"Failed to collect reservation data: {e}")
    data['reservations'] = []
```

### API Upsert Pattern (new for reservations)
```python
# In ingest_derecho()/ingest_casper():
reservations = data.get('reservations', [])
if reservations:
    from sqlalchemy import and_
    reservation_ids = []
    
    for resv_data in reservations:
        existing = session.query(ResourceReservation).filter(
            and_(
                ResourceReservation.system_name == 'derecho',
                ResourceReservation.reservation_name == resv_data['reservation_name']
            )
        ).first()
        
        if existing:
            # Update fields
            existing.start_time = datetime.fromisoformat(resv_data['start_time'])
            # ... other fields ...
            reservation_ids.append(existing.reservation_id)
        else:
            # Insert new
            resv = ResourceReservation(...)
            session.add(resv)
            session.flush()
            reservation_ids.append(resv.reservation_id)
    
    result['reservation_ids'] = reservation_ids
```

---

## Implementation Sequence

**CRITICAL**: Follow this exact order to avoid dependency issues:

1. **Add database unique constraint** (if not exists)
   - Run migration to add unique key on (system_name, reservation_name)

2. **Create ReservationParser**
   - File: `collectors/lib/parsers/reservations.py`
   - Implement parsing logic
   - Test with sample PBS output

3. **Update API endpoints**
   - File: `python/webui/api/v1/status.py`
   - Add reservation handling to `ingest_derecho()`
   - Add reservation handling to `ingest_casper()`
   - Test with curl/Postman

4. **Update Derecho collector**
   - File: `collectors/derecho/collector.py`
   - Add import and collection logic
   - Test with `--dry-run` and `--json-only`

5. **Update Casper collector**
   - File: `collectors/casper/collector.py`
   - Add import and collection logic
   - Test with `--dry-run` and `--json-only`

6. **End-to-end testing**
   - Run both collectors
   - Verify data in database
   - Test upsert logic
   - Verify GET endpoint returns data

---

## Verification Checklist

After implementation, verify:

- [ ] Parser handles empty output correctly
- [ ] Parser handles malformed datetimes gracefully
- [ ] Parser extracts node count from both sources
- [ ] Parser logs warnings for unparseable entries
- [ ] API endpoint accepts reservations array
- [ ] API upsert logic updates existing records
- [ ] API upsert logic inserts new records
- [ ] Derecho collector includes reservations in POST
- [ ] Casper collector includes reservations in POST
- [ ] Collectors continue on reservation fetch failure
- [ ] GET endpoint returns reservations correctly
- [ ] Database has unique constraint on (system_name, reservation_name)
- [ ] Logs show reservation collection activity
- [ ] Dry-run mode works for both collectors
- [ ] JSON-only mode includes reservations

---

## PBS DateTime Format Reference

**PBS Output**: `Wed Nov 12 12:00:00 2025`
**strptime format**: `"%a %b %d %H:%M:%S %Y"`
**Output**: ISO 8601 string `"2025-11-12T12:00:00"`

```python
from datetime import datetime

pbs_time = "Wed Nov 12 12:00:00 2025"
dt = datetime.strptime(pbs_time, "%a %b %d %H:%M:%S %Y")
iso_time = dt.isoformat()  # "2025-11-12T12:00:00"
```

---

## Example Data Flow

1. **PBS Command**: `pbs_rstat -f` on derecho
2. **Parser Output**:
   ```python
   [
       {
           'system_name': 'derecho',
           'reservation_name': 'MONTHLY_MAINTENANCE',
           'description': 'MONTHLY_MAINTENANCE',
           'start_time': '2025-02-01T06:00:00',
           'end_time': '2025-02-01T18:00:00',
           'node_count': 2488,
           'partition': 'pbs-default'
       }
   ]
   ```

3. **Collector POST**:
   ```json
   {
       "timestamp": "2025-11-27T09:00:00",
       "cpu_nodes_total": 2488,
       "...": "...",
       "reservations": [
           {
               "system_name": "derecho",
               "reservation_name": "MONTHLY_MAINTENANCE",
               "description": "MONTHLY_MAINTENANCE",
               "start_time": "2025-02-01T06:00:00",
               "end_time": "2025-02-01T18:00:00",
               "node_count": 2488,
               "partition": "pbs-default"
           }
       ]
   }
   ```

4. **API Response**:
   ```json
   {
       "success": true,
       "status_id": 12345,
       "login_node_ids": [1, 2, 3],
       "queue_ids": [10, 11],
       "filesystem_ids": [20, 21],
       "reservation_ids": [30]
   }
   ```

5. **Database Record**:
   ```sql
   INSERT INTO resource_reservations (
       system_name, reservation_name, description,
       start_time, end_time, node_count, partition
   ) VALUES (
       'derecho', 'MONTHLY_MAINTENANCE', 'MONTHLY_MAINTENANCE',
       '2025-02-01 06:00:00', '2025-02-01 18:00:00', 2488, 'pbs-default'
   )
   ON DUPLICATE KEY UPDATE
       start_time = VALUES(start_time),
       end_time = VALUES(end_time),
       ...
   ```

---

## Notes

- ReservationParser follows same static method pattern as other parsers
- Error handling is consistent with existing collector patterns
- No changes needed to PBSClient (get_reservations() already exists)
- No changes needed to schemas (ResourceReservationSchema already exists)
- GET endpoint already exists and works correctly
- Database model already exists
- Only new code: parser + API upsert logic + collector integration

