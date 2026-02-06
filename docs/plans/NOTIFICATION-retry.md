# Email Notification Retry Mechanism - Implementation Plan

## Overview
Add a simple, extensible retry mechanism to the email notification system to handle partial failures when sending batches of ~300 emails. The solution uses JSON files for persistence (no database) and supports resuming from where it left off.

## Current System
- **File**: `src/cli/notifications/email.py` - `EmailNotificationService` class
- **Method**: `send_batch_notifications(notifications: List[Dict], dry_run: bool)`
- **Current behavior**: Returns `{'success': [...], 'failed': [...]}`
- **Pain point**: If crash occurs at email 150/300, all progress lost and re-run sends duplicates

## Solution Design

### 1. Retry File Format
**Location**: `logs/notifications/notification_batch_{type}_{timestamp}.json`

**Structure**:
```json
{
  "metadata": {
    "batch_id": "expiration_20260205_143022",
    "notification_type": "expiration",
    "created_at": "2026-02-05T14:30:22",
    "total_count": 300,
    "success_count": 149,
    "failed_count": 1,
    "pending_count": 150,
    "status": "in_progress"  // or "completed", "partial"
  },
  "notifications": [
    {
      "id": "SCSG0001_benkirk@ucar.edu",  // Unique: {project}_{email}
      "status": "success",  // "pending", "success", "failed"
      "attempt_count": 1,
      "last_attempt_at": "2026-02-05T14:30:25",
      "error": null,
      "notification": {
        "subject": "...",
        "recipient": "benkirk@ucar.edu",
        "project_code": "SCSG0001",
        ...  // Full notification dict
      }
    }
  ]
}
```

**Key Features**:
- **Unique ID**: `{project_code}_{recipient}` prevents duplicate sends (idempotent)
- **Incremental updates**: File updated after each email sent
- **Self-contained**: Full notification data stored (no database dependency)
- **Max retries**: Track attempt count (default: 3 max)

### 2. Core Components

#### A. New File: `src/cli/notifications/retry.py`
**NotificationRetryManager class** with methods:
- `__init__(base_dir, max_retries)` → Initialize with configurable directory (validates writability)
- `create_batch(notifications, notification_type, command_args)` → Creates new retry file
- `load_batch(batch_file)` → Loads batch from JSON file
- `update_notification_status(batch_file, notification_id, success, error)` → Updates after each send
- `get_pending_notifications(batch_file)` → Returns list of unsent notifications
- `get_batch_summary(batch_file)` → Returns stats dict
- `list_incomplete_batches()` → Finds all in-progress batches
- `validate_directory()` → Check if base_dir is writable, create if needed
- `_generate_id(notification)` → Creates unique ID from project_code + recipient

Uses dataclasses for type safety:
- `NotificationEntry` - Single notification with retry metadata
- `BatchMetadata` - Batch-level stats
- `NotificationBatch` - Container for both

#### B. Modified: `src/cli/notifications/email.py`
Add new method `send_batch_notifications_with_retry()`:
```python
def send_batch_notifications_with_retry(
    self,
    notifications: List[Dict],
    dry_run: bool = False,
    retry_file: Optional[Path] = None,
    log_dir: str = "logs/notifications",
    notification_type: str = "expiration"
) -> Dict
```

**Logic**:
1. Create `NotificationRetryManager(base_dir=log_dir)`
2. Call `validate_directory()` - exit with error if not writable
3. If `retry_file` provided → Load pending notifications from file
4. Else → Create new batch file
5. Loop through notifications:
   - Send email via existing `send_expiration_notification()`
   - Update retry file after each send
6. Return results dict with added `retry_file` path

#### C. Modified: `src/cli/project/commands.py`
Update `ProjectExpirationCommand._send_notifications()`:
- Add parameters: `log_dir`, `retry_file`
- Call new `send_batch_notifications_with_retry()` instead of old method
- Pass through `log_dir` parameter
- Display retry file path in results
- Show retry command if failures occurred

#### D. Modified: `src/cli/cmds/admin.py`
Add CLI options:
- `--log-dir <path>` - Directory for retry files (default: `./logs/notifications`)
- `--retry-file <path>` - Resume from specific retry file
- `--list-incomplete` - Show all incomplete batches

**Directory Writability Check**:
Before sending any emails, validate that log directory exists and is writable:
- Try to create directory if it doesn't exist
- Test writability by creating/removing a temp file
- Exit with clear error message if not writable
- Suggest using `--log-dir` to specify alternative location

### 3. User Workflows

#### Initial Send (Default - Retry Enabled)
```bash
$ sam-admin project --upcoming-expirations --notify

Created retry file: logs/notifications/notification_batch_expiration_20260205_143022.json
Sending notifications... 299/300 sent, 1 failed

Retry file: logs/notifications/notification_batch_expiration_20260205_143022.json
To retry: sam-admin project --upcoming-expirations --notify --retry-file <path>
```

#### Retry Failed Emails
```bash
$ sam-admin project --upcoming-expirations --notify --retry-file logs/notifications/notification_batch_expiration_20260205_143022.json

Resuming batch: expiration_20260205_143022
  Already sent: 299
  Retrying: 1

Sending... 1/1 sent, 0 failed
All notifications sent successfully!
```

#### List Incomplete Batches
```bash
$ sam-admin project --list-incomplete

Found 2 incomplete batch(es):
  notification_batch_expiration_20260205_143022.json
    Progress: 149/300 sent, 1 failed, 150 pending
```

#### Custom Log Directory
```bash
# Use alternative directory if ./logs/ not writable
$ sam-admin project --upcoming-expirations --notify --log-dir /tmp/sam_logs

Created retry file: /tmp/sam_logs/notifications/notification_batch_expiration_20260205_143022.json
Sending notifications... 300/300 sent

# Error if directory not writable
$ sam-admin project --upcoming-expirations --notify
Error: Log directory './logs/notifications' is not writable.
  Use --log-dir to specify an alternative location.
```

### 4. Idempotency & Safety
- **Unique ID**: Each notification has ID = `{project_code}_{recipient_email}`
- **Status tracking**: Only sends notifications with `status="pending"` and `attempt_count < max_retries`
- **No duplicates**: Successfully sent emails (status="success") are never retried
- **Max retries**: After 3 failed attempts, status changes to "failed" and excluded from future retries
- **Incremental updates**: File updated after EACH email, so crash only loses current email

### 5. Error Handling
- **Directory not writable**: Check before sending, exit with helpful error and `--log-dir` suggestion
- **Directory doesn't exist**: Auto-create with `mkdir -p` (parents=True)
- **File corruption**: Wrap JSON load/save in try/except, log error and exit gracefully
- **Missing retry file**: Error message if `--retry-file` points to non-existent file
- **SMTP failures**: Catch exception, mark as failed, continue to next email
- **Partial completion**: Batch status becomes "partial" if some succeeded, some failed

### 6. Extensibility (Future)
- **Other notification types**: Pass `notification_type="renewal_reminder"` to create different batches
- **Rate limiting**: Add `time.sleep()` between sends (optional parameter)
- **Scheduled retries**: Cron job to run `--retry-all-incomplete` (future feature)
- **Cleanup**: Archive completed batches to `logs/notifications/archive/` after 30 days (future)

## Implementation Steps

### Phase 1: Core Retry Manager (src/cli/notifications/retry.py)
1. Create dataclasses: `NotificationEntry`, `BatchMetadata`, `NotificationBatch`
2. Implement `NotificationRetryManager` class with `__init__(base_dir, max_retries)`
3. Add `validate_directory()` method:
   - Create directory if doesn't exist (parents=True)
   - Test writability by creating temp file `.write_test` and removing it
   - Raise clear exception if not writable
4. Add methods: `create_batch()`, `load_batch()`, `update_notification_status()`
5. Add methods: `get_pending_notifications()`, `get_batch_summary()`, `list_incomplete_batches()`
6. Implement `_generate_id()` and `_save_batch()` helpers

### Phase 2: Email Service Integration (src/cli/notifications/email.py)
1. Add import: `from .retry import NotificationRetryManager`
2. Add new method: `send_batch_notifications_with_retry()` with `log_dir` parameter
3. Implement logic:
   - Create `NotificationRetryManager(base_dir=log_dir)`
   - Call `validate_directory()` before sending (raises exception if not writable)
   - Resume mode: Load from retry file
   - New batch mode: Create retry file
   - Dry-run mode: Skip retry file creation but still validate directory
   - Update retry file after each send
4. Wrap directory validation in try/except and return helpful error message
5. Return results with `retry_file` path

### Phase 3: CLI Command Integration (src/cli/project/commands.py)
1. Update `_send_notifications()` signature with `log_dir` and `retry_file` params
2. Replace `send_batch_notifications()` call with `send_batch_notifications_with_retry()`
3. Pass through `log_dir` and `retry_file` parameters from CLI
4. Catch directory validation errors and display user-friendly message with `--log-dir` suggestion
5. Update result display to show retry file path and retry command

### Phase 4: CLI Options (src/cli/cmds/admin.py)
1. Add `--log-dir <path>` option (default: `logs/notifications`)
2. Add `--retry-file <path>` option with Path validation
3. Add `--list-incomplete` flag (uses `--log-dir` to find batches)
4. Implement `--list-incomplete` handler (early exit after listing)
5. Pass `log_dir` and `retry_file` to `ProjectExpirationCommand`

### Phase 5: Display Updates (src/cli/project/display.py)
1. Update `display_notification_results()` to show retry file path
2. Add helpful message if failures: "To retry: sam-admin project ... --retry-file <path>"
3. Format incomplete batch listing output with stats

### Phase 6: Testing
1. Unit tests for `NotificationRetryManager` (test all methods)
2. Mock file I/O to test batch creation/loading/updating
3. Integration test: full flow (create → send → fail → retry → complete)
4. Mock SMTP to simulate failures at different points
5. Verify idempotency (no duplicate sends on retry)
6. Test edge cases: empty batch, all failed, all success, corrupted file

### Phase 7: Documentation
1. Update README with retry mechanism explanation
2. Add examples of retry workflows
3. Document retry file format
4. Add troubleshooting section (corrupt files, max retries exceeded)

## Critical Files

### New Files
- `src/cli/notifications/retry.py` - Core retry manager (400+ lines)

### Modified Files
- `src/cli/notifications/email.py` - Add `send_batch_notifications_with_retry()` method with `log_dir` param
- `src/cli/project/commands.py` - Update `_send_notifications()` to support retry with directory validation
- `src/cli/cmds/admin.py` - Add CLI options: `--log-dir`, `--retry-file`, `--list-incomplete`
- `src/cli/project/display.py` - Display retry file path and retry command

### Test Files
- `tests/unit/test_notification_retry.py` - NEW: Unit tests for retry manager
- `tests/integration/test_notification_retry_flow.py` - NEW: Integration tests for full retry flow

## Verification & Testing

### Manual Testing
1. Send batch of 5 test emails (retry always enabled)
2. Verify retry file created in `logs/notifications/`
3. Kill process mid-send (Ctrl+C)
4. Resume with `--retry-file` and verify only unsent emails are sent
5. Check no duplicates sent
6. Test `--list-incomplete` shows in-progress batches
7. Test `--log-dir /tmp/sam_logs` with custom directory
8. Test directory writability check:
   - Try sending with read-only directory (should fail gracefully)
   - Verify error message suggests `--log-dir`

### Automated Testing
1. Run unit tests: `pytest tests/unit/test_notification_retry.py -v`
2. Run integration tests: `pytest tests/integration/test_notification_retry_flow.py -v`
3. Verify all existing tests still pass: `pytest tests/ --no-cov`

### Edge Cases to Test
- Empty notification list
- All notifications succeed
- All notifications fail (check max retries)
- Corrupted retry file (JSON parse error)
- Missing retry file when `--retry-file` provided
- Non-writable log directory (permission denied)
- Log directory doesn't exist (should auto-create)
- Custom log directory with `--log-dir`
- Concurrent sends to same retry file (should fail gracefully)

## Design Principles

✅ **Simple over complex**: JSON files, not database
✅ **Idempotent by default**: Unique IDs prevent duplicates
✅ **Transparent progress**: File updated after each send
✅ **Extensible**: Works for any notification type (not hard-coded)
✅ **Always enabled**: Retry tracking always on (no opt-out needed)
✅ **Safe**: Max retries (3) prevents infinite loops, directory validation prevents crashes
✅ **Resumable**: Load pending notifications from file
✅ **Configurable**: Custom log directory with `--log-dir`

## Notes
- Retry tracking is ALWAYS ENABLED (no disable option)
- Default log directory: `logs/notifications` (override with `--log-dir`)
- Directory writability checked before sending (fails fast with clear error)
- Max retries = 3 attempts per notification (configurable in class constructor)
- Retry file updated incrementally (crash-safe)
- Unique ID format: `{project_code}_{recipient_email}` ensures no duplicates
- Future: Add cleanup job to archive completed batches after 30 days
