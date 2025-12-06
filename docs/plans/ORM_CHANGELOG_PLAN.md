# SAM ORM Change Logging Implementation Plan

## Overview
Implement server-side audit logging for SAM ORM model changes (inserts, updates, deletes) using SQLAlchemy event listeners. This will track all changes to the `sam` database while excluding the `system_status` database.

## Current State Analysis

### Flask Application Structure
- **Main app**: `src/webapp/run.py` with `create_app()` factory pattern
- **Extensions**: Flask-SQLAlchemy singleton in `src/webapp/extensions.py`
- **Blueprints**: auth, user_dashboard, status_dashboard, admin, api/v1
- **Database init**: Two databases configured via Flask-SQLAlchemy binds

### Database Configuration
- **SAM database**: Default Flask-SQLAlchemy binding
  - Base class: `Base` from `src/sam/base.py`
  - Metadata: `Base.metadata`
  - Models: All SAM ORM models in `src/sam/` modules

- **System Status database**: Secondary binding via `__bind_key__ = "system_status"`
  - Base class: `StatusBase` from `src/system_status/base.py`
  - Metadata: `StatusBase.metadata`
  - Models: All system_status models in `src/system_status/` modules

### Authentication
- **Flask-Login**: Session management with `current_user` proxy
- **AuthUser wrapper**: Wraps SAM User objects
- **Dev mode**: Auto-login via `DISABLE_AUTH=1` and `DEV_AUTO_LOGIN_USER`
- **User identification**: `current_user.username` available in request context

## Implementation Design

### 1. Directory Structure

Create new audit blueprint at `src/webapp/audit/`:
```
src/webapp/audit/
├── __init__.py       # Blueprint + init_audit() function
├── logger.py         # Rotating file logger
└── events.py         # SQLAlchemy event handlers
```

### 2. Core Components

#### A. Rotating File Logger (`logger.py`)
- **Function**: `get_audit_logger(logfile_path)` - Returns singleton logger
- **Handler**: RotatingFileHandler (10MB files, 5 backups)
- **Format**: `"%(asctime)s [%(levelname)s] %(message)s"`
- **Default path**: `/var/log/sam/model_audit.log` with fallback to temp dir
- **Auto-create directory** with permission error handling

#### B. Event Handlers (`events.py`)
- **Event**: `@event.listens_for(Session, "before_flush")`
- **Exclusion logic**: `should_track(obj)` - filters by:
  - `obj.__table__.metadata not in EXCLUDED_METADATA` (excludes system_status)
  - `obj.__class__.__name__ not in EXCLUDED_MODELS` (excludes ApiCredentials)
- **User tracking**: `responsible_user()` - returns `current_user.username` or `"anonymous"`
- **Change detection**:
  - **INSERT**: Log `session.new` objects with primary key only (consistent format)
  - **UPDATE**: Log `session.dirty` objects with `diff_for_object()` using `inspect(obj).attrs.history`
  - **DELETE**: Log `session.deleted` objects with primary key
- **Error handling**: Wrap in try/except to prevent audit failures from breaking app

#### C. Blueprint Registration (`__init__.py`)
- **Blueprint**: `audit_bp` (empty, no routes needed)
- **Function**: `init_audit(app, db, excluded_metadata, logfile_path)`
  - Calls `init_audit_events()` to register event handlers
  - Registers blueprint with app

### 3. Integration Point

**File**: `src/webapp/run.py`

Insert after `db.init_app(app)` (line 75), before Flask-Login initialization:

```python
# Audit logging initialization
if app.config.get('AUDIT_ENABLED', True):
    from webapp.audit import init_audit
    from system_status.base import StatusBase

    init_audit(
        app=app,
        db=db,
        excluded_metadata=[StatusBase.metadata],
        logfile_path=app.config.get('AUDIT_LOG_PATH', '/var/log/sam/model_audit.log')
    )
```

### 4. Configuration

**Environment Variables**:
- `AUDIT_ENABLED` (default: `1`) - Enable/disable audit logging
- `AUDIT_LOG_PATH` (default: `/var/log/sam/model_audit.log`) - Log file path
- `SAM_LOG_DIR` (default: `/var/log/sam`) - Base log directory

**Docker Compose** (`compose.yaml`):
```yaml
volumes:
  - ./logs:/var/log/sam
environment:
  - AUDIT_ENABLED=1
  - AUDIT_LOG_PATH=/var/log/sam/model_audit.log
```

### 5. Log Format

**Example entries**:
```
2025-12-06 10:30:45 [INFO] user=benkirk action=INSERT model=Project pk=(1234,)
2025-12-06 10:31:12 [INFO] user=benkirk action=UPDATE model=Project pk=(1234,) changes={'title': {'old': 'Old Title', 'new': 'New Title'}}
2025-12-06 10:32:00 [INFO] user=benkirk action=DELETE model=Account pk=(5678,)
2025-12-06 10:33:15 [INFO] user=anonymous action=INSERT model=User pk=(9012,)
```

### 6. Exclusion Strategy

#### A. Database Exclusion (system_status)
**Key insight**: Both `Base` and `StatusBase` use Flask-SQLAlchemy's `db.Model` in Flask context, but have **different metadata objects**:
- SAM models: `Base.metadata`
- System Status models: `StatusBase.metadata`

#### B. Model Exclusion (ApiCredentials)
**Security consideration**: Exclude `ApiCredentials` model to avoid logging password hash changes.

**Filtering logic**:
```python
EXCLUDED_METADATA = set([StatusBase.metadata])
EXCLUDED_MODELS = {'ApiCredentials'}  # Security-sensitive models

def should_track(obj):
    if not hasattr(obj, "__table__"):
        return False
    if obj.__table__.metadata in EXCLUDED_METADATA:
        return False
    if obj.__class__.__name__ in EXCLUDED_MODELS:
        return False
    return True
```

### 7. User Identification

**Function**: `responsible_user()`
```python
try:
    from flask_login import current_user
    if current_user and current_user.is_authenticated:
        return current_user.username
except RuntimeError:
    pass  # No Flask request context (CLI, background job)
return "anonymous"
```

**Handles**:
- ✅ Authenticated web users → username
- ✅ Dev auto-login → username
- ✅ Anonymous visitors → "anonymous"
- ✅ CLI scripts → "anonymous"
- ✅ Background jobs → "anonymous"

### 8. Testing Strategy

**Test file**: `tests/unit/test_audit_logging.py`

**Test cases**:
1. `test_audit_insert` - Verify INSERT logged with primary key only
2. `test_audit_update` - Verify UPDATE with field changes
3. `test_audit_delete` - Verify DELETE logged
4. `test_audit_excludes_status` - Verify system_status models excluded
5. `test_audit_excludes_api_credentials` - Verify ApiCredentials excluded
6. `test_audit_captures_user` - Verify username captured
7. `test_audit_anonymous` - Verify "anonymous" when not logged in
8. `test_audit_rotation` - Verify log file rotation

**Fixtures**:
- `audit_log_file` - Temporary log file for testing
- `app_with_audit` - Flask app with audit enabled

### 9. Implementation Sequence

1. **Create directory** `src/webapp/audit/`
2. **Implement** `logger.py` with rotating file handler
3. **Implement** `events.py` with SQLAlchemy event listeners
4. **Implement** `__init__.py` with blueprint and init function
5. **Integrate** into `run.py`
6. **Write tests** in `tests/unit/test_audit_logging.py`
7. **Manual verification** - Start app, make changes, check log

**Estimated time**: 3-4 hours

### 10. Rollback Plan

If issues arise:
- Set `AUDIT_ENABLED=0` environment variable
- Or comment out `init_audit()` call in `run.py`
- Audit logging is read-only, no data loss risk

## Critical Files

### To Create:
1. `src/webapp/audit/__init__.py` - Blueprint registration
2. `src/webapp/audit/logger.py` - Rotating file logger
3. `src/webapp/audit/events.py` - SQLAlchemy event handlers
4. `tests/unit/test_audit_logging.py` - Comprehensive tests

### To Modify:
1. `src/webapp/run.py` - Add `init_audit()` call after line 75
2. `compose.yaml` - Add volume mount and environment variables (optional)

## Key Design Decisions

1. **Metadata-based exclusion** - Most reliable way to filter system_status models
2. **before_flush event** - Captures changes before database write, allows access to old/new values
3. **File logging** - Avoids circular database dependencies, easy to rotate/archive
4. **Feature flag** - Easy to disable without code changes
5. **Error suppression** - Audit failures won't break application
6. **Fallback paths** - Auto-create directories or use temp dir if permissions fail

## User Preferences (Confirmed)

1. **Log file path**: `/var/log/sam/model_audit.log` (standard system location)
2. **Default state**: Enabled by default (`AUDIT_ENABLED=1` unless explicitly disabled)
3. **INSERT logging**: Primary key only (consistent with UPDATE/DELETE)
4. **Exclusions**: system_status database + ApiCredentials model (security)

## Success Criteria

- ✅ All INSERT/UPDATE/DELETE operations on SAM models logged (except ApiCredentials)
- ✅ System Status models excluded from logging
- ✅ ApiCredentials model excluded from logging (security)
- ✅ INSERT logs show primary key only (not full object data)
- ✅ Username captured for authenticated users
- ✅ Log file rotation working (10MB files, 5 backups)
- ✅ Zero impact on existing functionality
- ✅ All tests passing (including ApiCredentials exclusion test)
- ✅ Manual verification shows expected log entries
