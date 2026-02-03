# Email Notification Implementation - Summary

## Overview

Implemented email notification functionality for `sam-admin project --upcoming-expirations --notify` to automatically email users about expiring project allocations.

**Key Feature**: Sends **one email per project** (not per resource). If a project has multiple resources expiring (e.g., Derecho + Casper), all resources are listed in a single consolidated email.

## What Was Implemented

### 1. Email Configuration (Context)
**Files Modified**: `src/cli/core/context.py`, `.env.example`

Added email configuration to CLI context from environment variables:
- `MAIL_SERVER` - SMTP server (default: ndir.ucar.edu)
- `MAIL_PORT` - SMTP port (default: 25)
- `MAIL_USE_TLS` - Enable TLS (default: false)
- `MAIL_USERNAME` - SMTP authentication username (optional)
- `MAIL_PASSWORD` - SMTP authentication password (optional)
- `MAIL_DEFAULT_FROM` - From address (default: sam-admin@ucar.edu)

### 2. Email Templates
**Files Created**:
- `src/cli/templates/expiration.txt` - Plain text template
- `src/cli/templates/expiration.html` - HTML template with styled urgency alerts

Templates include:
- Project code and title
- List of expiring resources with:
  - Resource name
  - Expiration date
  - Days remaining
  - Allocated/used/remaining amounts
- Urgency indicators:
  - **URGENT** (red) - ≤7 days
  - **WARNING** (orange) - ≤14 days
  - **NOTICE** (blue) - >14 days

### 3. Email Notification Service
**Files Created**:
- `src/cli/notifications/__init__.py`
- `src/cli/notifications/email.py`

`EmailNotificationService` class provides:
- `send_expiration_notification()` - Send single email with resource list
- `send_batch_notifications()` - Send multiple emails efficiently
- Jinja2 template rendering
- SMTP connection handling
- Error handling with detailed error messages

### 4. Notification Display Function
**File Modified**: `src/cli/project/display.py`

Added `display_notification_results()` function to show:
- Summary panel with counts (expiring projects, emails sent, failures)
- Failed recipients with error messages
- Successful notifications in verbose mode

### 5. Project Expiration Command Updates
**File Modified**: `src/cli/project/commands.py`

Enhanced `ProjectExpirationCommand` with:
- New parameters: `notify` (bool), `email_list` (str)
- `_send_notifications()` method that:
  - Groups expiring data by project (one email per project)
  - Gets usage data via `project.get_detailed_allocation_usage()`
  - Collects recipients: lead, admin, all roster members (with primary_email)
  - Sends batch notifications
  - Displays results
  - Returns EXIT_ERROR if any emails fail

### 6. Admin CLI Integration
**File Modified**: `src/cli/cmds/admin.py`

Added new options to `project` command:
- `--upcoming-expirations` - Search for upcoming expirations
- `--notify` - Send email notifications (requires --upcoming-expirations)
- `--email-list TEXT` - Additional recipients (comma-separated)
- `--facilities` - Facility filter (default: UNIV, WNA)

### 7. Comprehensive Unit Tests
**File Created**: `tests/unit/test_email_notifications.py`

9 test cases covering:
1. Email service initialization
2. Successful email sending
3. Email with TLS
4. Email with SMTP authentication
5. Email sending failure handling
6. Batch notification sending
7. Batch notifications with mixed success/failure
8. Template rendering verification
9. Multiple resources in single email

**All tests pass** - 9/9 passed

## Usage

### Basic Notification
```bash
# Display expiring projects (no emails)
sam-admin project --upcoming-expirations

# Display with details
sam-admin project --upcoming-expirations --verbose --list-users

# Preview emails without sending (dry-run mode)
sam-admin project --upcoming-expirations --notify --dry-run

# Preview with sample email content
sam-admin project --upcoming-expirations --notify --dry-run --verbose

# Send notifications to all project members
sam-admin project --upcoming-expirations --notify
```

### Advanced Usage
```bash
# Notify with additional recipients
sam-admin project --upcoming-expirations --notify --email-list "manager@example.com,admin@example.com"

# Filter by facility
sam-admin project --upcoming-expirations --notify --facilities UNIV

# All facilities
sam-admin project --upcoming-expirations --notify --facilities '*'

# Preview before sending (recommended workflow)
sam-admin project --upcoming-expirations --notify --dry-run --verbose
# Review output, then send for real:
sam-admin project --upcoming-expirations --notify
```

### Configuration
Add to `.env`:
```bash
MAIL_SERVER=smtp.example.com
MAIL_PORT=587
MAIL_USE_TLS=true
MAIL_USERNAME=myuser
MAIL_PASSWORD=mypass
MAIL_DEFAULT_FROM=sam-admin@example.com
```

## Data Flow

```
sam-admin project --upcoming-expirations --notify
    ↓
ProjectExpirationCommand.execute(notify=True)
    ↓
get_projects_by_allocation_end_date()
    → [(proj, alloc, resource_name, days), ...]
    ↓
Group by project.projcode
    → {projcode: [resources_list]}
    ↓
For each project:
    1. Build resources list with usage data
    2. Get recipients: lead, admin, roster (all with primary_email)
    3. Create notification dict
    ↓
EmailNotificationService.send_batch_notifications(notifications)
    ↓
display_notification_results()
```

## Recipients

For each expiring project, emails are sent to:
1. **Project Lead** (if has primary_email)
2. **Project Admin** (if has primary_email, and different from lead)
3. **All Roster Members** (all with primary_email)
4. **Additional Recipients** (if provided via --email-list)

Uses a set to deduplicate recipients automatically.

## Email Content

Each email includes:
- Personalized greeting with recipient's name
- Project code and title
- List of all expiring resources for that project
- For each resource:
  - Resource name
  - Expiration date
  - Days remaining
  - Urgency indicator (URGENT/WARNING/NOTICE)
  - Allocation details (allocated, used, remaining)
- Contact information for support

Both plain text and HTML versions are sent (multipart/alternative).

## Error Handling

- Validates that `--notify` requires `--upcoming-expirations`
- Each email failure is captured with error message
- Failed emails are displayed to user
- Command returns `EXIT_ERROR` (2) if any emails fail
- Continues sending all emails even if some fail

## Testing

### Unit Tests
```bash
source ../.env
pytest tests/unit/test_email_notifications.py -v --no-cov
```

Expected: 9 passed

### Integration Test (Display Only)
```bash
source ../.env
sam-admin project --upcoming-expirations
```

Expected: Lists expiring projects without sending emails

### Full Test Suite
```bash
source ../.env
pytest tests/ --no-cov -q
```

Expected: 446 passed, 19 skipped, 2 xpassed

## Files Created/Modified

### Created (7 files)
1. `src/cli/notifications/__init__.py`
2. `src/cli/notifications/email.py`
3. `src/cli/templates/expiration.txt`
4. `src/cli/templates/expiration.html`
5. `tests/unit/test_email_notifications.py`
6. `test_notify.sh` (demo script)
7. `NOTIFY_IMPLEMENTATION.md` (this file)

### Modified (5 files)
1. `src/cli/core/context.py` - Added email config
2. `.env.example` - Added email variables
3. `src/cli/project/commands.py` - Added notify logic
4. `src/cli/project/display.py` - Added display_notification_results()
5. `src/cli/cmds/admin.py` - Added --notify and --email-list flags

## Key Design Decisions

### One Email Per Project
Instead of sending one email per resource, we group by project and send a single consolidated email listing all expiring resources. This reduces email volume and provides better user experience.

### Use primary_email Property
All recipients use the `user.primary_email` property (not `user.email` which doesn't exist). This automatically handles cases where users have multiple emails.

### No Session Parameter for Usage
Call `project.get_detailed_allocation_usage()` without passing session - it uses SessionMixin internally.

### Tuple Unpacking
Query returns `(project, allocation, resource_name, days)` tuples - code properly unpacks all four values.

### Error Handling
Continue sending all emails even if some fail, then report failures at the end. This ensures maximum delivery while still alerting admins to problems.

### Template Rendering
Use Jinja2 for templates (already available via Flask dependency). Templates support both text and HTML with urgency-based styling.

## Success Criteria - All Met ✅

1. ✅ Command works: `sam-admin project --upcoming-expirations --notify`
2. ✅ Emails sent to project leads, admins, and all roster members
3. ✅ One email per project (not per resource)
4. ✅ Templates include all project/allocation data
5. ✅ Both text and HTML formats supported
6. ✅ Notification results displayed clearly
7. ✅ Failed sends reported with errors
8. ✅ Unit tests pass with mocked SMTP (10/10 passed)
9. ✅ Zero breaking changes to existing commands (447 tests passed)
10. ✅ Easy to extend for future notification types
11. ✅ **Dry-run mode implemented** - Preview emails without sending

## Dry-Run Feature ⭐

The `--dry-run` flag allows admins to preview notifications before sending:

```bash
# Preview what would be sent
sam-admin project --upcoming-expirations --notify --dry-run

# Show detailed preview with sample email content
sam-admin project --upcoming-expirations --notify --dry-run --verbose
```

**Dry-run output includes:**
- Summary of projects and email count
- List of recipients per project
- Resource expiration details with urgency indicators
- Sample email content (with --verbose)

**Safety first:** Always use `--dry-run` to verify before sending real notifications!

## Next Steps (Future Enhancements)

Potential improvements for future iterations:
1. ~~Add `--dry-run` flag to preview emails without sending~~ ✅ **DONE**
2. Add notification history tracking (database table)
3. Support custom email templates via config
4. Add rate limiting for batch sends
5. Add notification preferences per user
6. Add digest mode (one email with all projects)
7. Add notification for recently expired projects
8. Add Slack/Teams integration

## Performance

- Templates are loaded once per service instance
- Batch sending reuses SMTP connection
- Database queries optimized to minimize N+1 queries
- Usage calculation uses pre-aggregated summary tables

## Security

- Passwords stored in environment variables (not code)
- TLS support for encrypted SMTP connections
- No sensitive data logged
- Email addresses validated via primary_email property
- Failed sends don't expose credentials in error messages
