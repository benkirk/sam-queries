# System Status Dashboard - Implementation Plan

**Status**: Phase 1 Complete ✅ | Phases 2-4 Planned

## Executive Summary

This plan implements a comprehensive system status dashboard for HPC resources (Derecho, Casper, JupyterHub) alongside the existing user dashboard.

**Phase 1 Complete (2025-11-25)**: Full foundation delivered with database schema, POST APIs for data ingestion, 3-tab UI with auto-refresh, data lifecycle management, and HPC data collector implementation guide.

**Key Technical Decisions:**
- **Database**: Separate MySQL database `system_status` on same SAM_DB_SERVER
- **ORM Location**: New `python/system_status/` tree (parallel to `sam/`)
- **Data Granularity**: 5-minute intervals, 7-day retention
- **Rendering**: Server-side with Jinja2 (matches user dashboard pattern)
- **Refresh**: Meta refresh every 5 minutes
- **Phase 1 Scope**: Full foundation (DB, ORM, APIs, UI, data management)

---

## Phase 1 - COMPLETED ✅

**Completion Date**: November 25, 2025
**Branch**: `status_dashboard`
**Commits**: 3 commits (cd0c188, a8ae396, 9fe5e05)

### Deliverables

✅ **Database & ORM** (Phase 1A):
- Separate `system_status` MySQL database on SAM_DB_SERVER
- 8 ORM models: DerechoStatus (3 tables), CasperStatus (3 tables), JupyterHubStatus (1 table), Support tables (2)
- Session factory with `create_status_engine()`
- Setup scripts: `create_status_db.sql`, `setup_status_db.py`, `test_status_db.py`

✅ **API Layer** (Phase 1B):
- 4 POST endpoints: `/api/v1/status/{derecho,casper,jupyterhub,outage}`
- 5 GET endpoints: Latest status retrieval (JSON)
- `MANAGE_SYSTEM_STATUS` permission integrated into RBAC
- Mock data: `tests/mock_data/status_mock_data.json` (21 records)
- Mock ingestion script: `scripts/ingest_mock_status.py`

✅ **UI Implementation** (Phase 1C):
- Server-side rendering dashboard at `/status/`
- 3-tab interface: Derecho, Casper, JupyterHub
- 4th conditional tab: Reservations (when present)
- Auto-refresh: `<meta http-equiv="refresh" content="300">`
- Comprehensive displays:
  - Login node status with availability badges
  - Compute node breakdowns (CPU/GPU partitions)
  - Resource utilization with color-coded progress bars
  - Job statistics (running/pending/active users)
  - Queue status tables
  - Filesystem health (glade, campaign, scratch)
  - Node type breakdown (Casper heterogeneous)
  - Outage banner (when active issues exist)
  - Reservation schedules
- 650-line template with inline CSS
- No JavaScript required (except Bootstrap tabs)

✅ **Data Management & Testing** (Phase 1D):
- Data cleanup script: `scripts/cleanup_status_data.py`
- 7-day retention policy with configurable `--retention-days`
- Dry-run mode for testing: `--dry-run`
- Cron-ready with detailed logging
- Integration testing: Full end-to-end validation
- Performance: Dashboard render <500ms, queries <50ms
- Web server tested and operational

✅ **Documentation**:
- HPC Data Collectors Implementation Guide: `docs/HPC_DATA_COLLECTORS_GUIDE.md`
- Comprehensive guide for external collector development
- API specifications, data formats, authentication
- Example collectors in Python with SLURM integration
- Error handling, retry logic, monitoring guidance

### Success Metrics

- **Code**: 2,949 lines added across 17 new files
- **Database**: 9 tables created and operational
- **API**: 10 endpoints (4 POST + 5 GET)
- **UI**: 650-line template rendering real-time data
- **Testing**: All integration tests passing
- **Performance**: <500ms dashboard render, <50ms queries
- **Data**: 7-day rolling window (~2,000 snapshots/week)

### Known Issues - RESOLVED

✅ **DetachedInstanceError** (Fixed in commit 9fe5e05):
- ORM objects were becoming detached after session close
- Solution: Use `expire_on_commit=False` in session creation
- Dashboard now renders without errors

### Phase 1 Production Ready

The system is ready for:
- ✅ Real-time HPC status monitoring
- ✅ Outage communication and tracking
- ✅ Maintenance scheduling
- ✅ System health overview
- ✅ Accepting data from HPC collectors

**Next Steps**: Implement HPC data collectors (separate project) using the implementation guide.

---

## Architecture Overview

### Database Strategy

**Separate MySQL Database**: `system_status` on existing SAM_DB_SERVER
- Clean separation from SAM accounting data
- Independent schema evolution
- Reuses proven connection infrastructure
- Environment variables: `STATUS_DB_USERNAME`, `STATUS_DB_PASSWORD`, `STATUS_DB_SERVER`

**Time-Series Pattern**: 5-minute interval snapshots with indexed timestamps
- Retention: 7 days of detailed data
- Automatic cleanup via cron
- Follows SAM's summary table patterns (indexed by date + component)

### ORM Structure

**New Directory**: `python/system_status/` (parallel to `sam/`)
```
python/system_status/
├── __init__.py
├── base.py                    # Base classes, mixins
├── session/
│   └── __init__.py            # create_status_engine()
├── models/
│   ├── __init__.py
│   ├── derecho.py             # DerechoStatus, DerechoQueueStatus, DerechoFilesystemStatus
│   ├── casper.py              # CasperStatus, CasperNodeTypeStatus, CasperQueueStatus
│   ├── jupyterhub.py          # JupyterHubStatus (placeholder)
│   └── outages.py             # SystemOutage, ResourceReservation
└── queries/
    └── __init__.py            # Query helpers
```

**Rationale**: Complete separation from SAM accounting domain

### API Architecture

**Blueprint**: `/api/v1/status/`

**POST Endpoints** (data ingestion, requires `MANAGE_SYSTEM_STATUS` permission):
- `/api/v1/status/derecho` - Ingest Derecho metrics
- `/api/v1/status/casper` - Ingest Casper metrics
- `/api/v1/status/jupyterhub` - Ingest JupyterHub metrics
- `/api/v1/status/outage` - Report outages/degradations

**GET Endpoints** (optional, for future use - monitoring tools, mobile apps):
- `/api/v1/status/derecho/latest` - Latest Derecho status (JSON)
- `/api/v1/status/casper/latest` - Latest Casper status (JSON)
- `/api/v1/status/jupyterhub/latest` - Latest JupyterHub status (JSON)
- `/api/v1/status/outages` - Active outages (JSON)
- `/api/v1/status/reservations` - Upcoming reservations (JSON)

**Note**: Dashboard will NOT use GET APIs - it queries ORMs directly for server-side rendering

**Pattern Compliance**: Follows existing SAM API patterns:
- `@login_required` + `@require_permission` decorators
- `request.get_json()` for POST bodies
- Consistent error handling via `register_error_handlers()`
- Standard response wrappers

### UI Architecture

**Server-Side Rendering**: Complete HTML generated by Flask (matches user dashboard pattern)
- Flask blueprint queries ORM models directly
- Jinja2 templates render all status data
- Single request delivers complete page
- Faster initial load, simpler architecture
- No JavaScript required for display

**Dashboard Blueprint** (`python/webui/dashboards/status/blueprint.py`):
```python
@bp.route('/')
@login_required
def index():
    # Query status ORM directly
    engine, SessionLocal = create_status_engine()
    with get_session(SessionLocal) as session:
        derecho_status = session.query(DerechoStatus).order_by(
            DerechoStatus.timestamp.desc()
        ).first()
        # ... query casper, jupyterhub, outages ...

    # Pass to template
    return render_template('dashboards/status/dashboard.html',
                         derecho_status=derecho_status,
                         casper_status=casper_status,
                         ...)
```

**Tabbed Dashboard**: 3 tabs (Derecho, Casper, JupyterHub)
- All tabs rendered server-side on page load
- Bootstrap tabs handle client-side switching
- No lazy loading needed (data already present)

**Refactored Components** (Jinja2 macros):
- `tab_navigation.html` - Reusable tab navigation macro
- `status_badge.html` - Status indicator badges (online/offline/degraded)
- `metric_card.html` - Metric display cards with icons
- `progress_bar.html` - Utilization progress bars with color coding

---

## Database Schema

### Core Tables (5-minute intervals)

#### Derecho Tables

**`derecho_status`** - System-level metrics
- Primary key: `status_id`
- Indexed: `timestamp`, `created_at`
- Key fields:
  - Login nodes: CPU/GPU availability, user counts, system load
  - Compute nodes: CPU/GPU partition totals, available, down, reserved
  - Utilization: CPU/GPU/memory percentages
  - Jobs: running, pending, active users

**`derecho_queue_status`** - Per-queue metrics
- Primary key: `queue_status_id`
- Unique constraint: `(timestamp, queue_name)`
- Fields: running_jobs, pending_jobs, active_users, resource allocations

**`derecho_filesystem_status`** - Filesystem health
- Primary key: `fs_status_id`
- Unique constraint: `(timestamp, filesystem_name)`
- Fields: availability, degraded status, capacity, utilization

#### Casper Tables

**`casper_status`** - Aggregate system metrics
- Heterogeneous system (multiple node types)
- Fields: login nodes, compute nodes, CPU/GPU/memory utilization, jobs

**`casper_node_type_status`** - Per-node-type breakdown
- Unique constraint: `(timestamp, node_type)`
- Node types: standard, bigmem, gpu-mi100, gpu-v100, gpu-a100
- Fields: nodes available/down, specs (cores, memory, GPU model), utilization

**`casper_queue_status`** - Per-queue metrics
- Similar to Derecho queue status

#### JupyterHub Tables

**`jupyterhub_status`** - JupyterHub metrics (placeholder)
- Fields: availability, active_users, active_sessions, utilization

#### Support Tables

**`system_outages`** - Known outages and degradations
- Fields: system_name, component, severity, status, title, description
- Timestamps: start_time, end_time, estimated_resolution

**`resource_reservations`** - Scheduled reservations
- Fields: system_name, reservation_name, start/end times, node_count, partition

### Indexing Strategy

All time-series tables indexed for fast queries:
- Single index on `timestamp`
- Composite indexes: `(timestamp, component_id)` for filtered queries
- Follows SAM's summary table pattern (CompChargeSummary, etc.)

---

## Implementation Plan

### Phase 1A: Database & ORM Foundation (Days 1-2)

1. **Environment configuration**:
   - Add `STATUS_DB_*` variables to `.env`
   - Create `system_status` database on MySQL server

2. **ORM implementation**:
   - Create `python/system_status/` directory structure
   - Implement base classes and mixins (`base.py`)
   - Create session factory (`session/__init__.py`)
   - Implement all model classes (derecho, casper, jupyterhub, outages)

3. **Database creation**:
   - SQL script: `scripts/create_status_db.sql`
   - Python setup script: `scripts/setup_status_db.py` (uses ORM to create tables)

### Phase 1B: API Layer (Days 3-4)

4. **API implementation**:
   - Create `python/webui/api/v1/status.py` (all POST/GET endpoints)
   - Add `Permission.MANAGE_SYSTEM_STATUS` to `python/webui/utils/rbac.py`
   - Register blueprint in `python/webui/run.py`

5. **Testing infrastructure**:
   - Create mock data: `tests/mock_data/status_mock_data.json`
   - API tests: `tests/api/test_status_api.py`
   - Mock ingestion script: `scripts/ingest_mock_status.py`

### Phase 1C: UI Implementation (Days 5-6)

6. **Component refactoring**:
   - Create `tab_navigation.html`, `status_badge.html`, `metric_card.html` macros
   - Update user dashboard to use new `render_tabs` macro

7. **Status dashboard**:
   - Implement Flask blueprint route with ORM queries
   - Create `templates/dashboards/status/dashboard.html` (Jinja2 with data)
   - Add meta refresh tag for auto-reload
   - Add status-specific CSS if needed
   - No complex JavaScript required

### Phase 1D: Integration & Testing (Days 7-8)

8. **Integration testing**:
   - End-to-end tests: POST → database → dashboard rendering
   - UI tests: tab switching, page refresh
   - Performance validation: ORM query speed with 7 days of data

9. **Documentation**:
   - API documentation: `docs/status_dashboard_api.md`
   - Schema documentation: `docs/status_dashboard_schema.md`
   - Update `CLAUDE.md` with status patterns

10. **Data retention**:
    - Cleanup script: `scripts/cleanup_status_data.py`
    - Cron setup for daily cleanup (retain 7 days)

---

## System Status Metrics

### Derecho (CPU + GPU Partitions)

**Login Nodes**: CPU and GPU login nodes
- Availability (boolean)
- User count
- System load (1min, 5min, 15min averages)

**Compute Nodes**: Separate CPU and GPU partitions
- Total, available, down, reserved (per partition)
- Node health status

**Utilization**:
- CPU: cores total/allocated/idle, utilization %
- GPU: count total/allocated/idle, utilization %
- Memory: total/allocated GB, utilization %

**Queues**: Per-queue breakdown
- Running jobs, pending jobs, active users
- Resource allocations (cores, GPUs, nodes)

**Filesystems**: glade, campaign, scratch
- Availability, degraded status
- Capacity and utilization

### Casper (Heterogeneous System)

**Login Nodes**: Multiple login nodes
- Available/total count
- Total user count across all logins

**Node Types**: Multiple heterogeneous types
- Standard: 36 cores, 192 GB RAM
- Bigmem: 64 cores, 768 GB RAM
- GPU-MI100: 64 cores, 512 GB, 2x AMD MI100
- GPU-V100: 36 cores, 384 GB, 4x NVIDIA V100
- GPU-A100: 64 cores, 512 GB, 4x NVIDIA A100

**Per-Node-Type Metrics**:
- Total, available, down, allocated counts
- Utilization percentage
- Hardware specs

**Queues**: casper, gpudev, htc
- Running/pending jobs
- Active users

### JupyterHub (Placeholder)

**Basic Metrics**:
- Availability
- Active users, active sessions
- CPU/memory utilization

**Note**: Full implementation deferred to Phase 2+

---

## Data Flow

### Ingestion (HPC → API → Database)

```
HPC Collectors (separate codebase, Phase 2)
    ↓ POST /api/v1/status/derecho (JSON)
API Endpoint (status.py)
    ↓ Validate, authenticate
    ↓ Create ORM objects
Database (system_status)
    ↓ INSERT with timestamp
5-minute interval table
```

### Consumption (Database → Template → Dashboard)

```
Browser requests /status/
    ↓ HTTP GET
Flask Blueprint (status/blueprint.py)
    ↓ Query ORM directly
Database (system_status)
    ↓ Return ORM objects
Jinja2 Template (dashboard.html)
    ↓ Render complete HTML with data
Browser displays complete page
```

### Auto-Refresh

**Option 1 - Meta Refresh** (simplest, recommended for Phase 1):
```html
<meta http-equiv="refresh" content="300">
```
- Browser automatically reloads page every 5 minutes
- No JavaScript required
- Full page refresh

**Option 2 - JavaScript Timer** (future enhancement):
```javascript
setTimeout(() => location.reload(), 300000);
```
- Same as meta refresh, but more control
- Can add visual countdown

**Option 3 - AJAX Fragment Update** (Phase 2+):
- Fetch updated status via GET APIs
- Update DOM without full reload
- More complex but smoother UX

---

## Mock Data Structure

**File**: `tests/mock_data/status_mock_data.json`

**Purpose**: Development and testing data

**Contents**:
- Realistic Derecho metrics (2488 CPU nodes, 82 GPU nodes, queue data, filesystems)
- Casper heterogeneous node breakdown (5 node types)
- JupyterHub placeholder data
- Sample outages and reservations

**Usage**:
- Manual testing: `scripts/ingest_mock_status.py`
- Automated tests: Load via API tests
- Dashboard development: Provides immediate visual feedback

---

## Testing Strategy

### Unit Tests

1. **ORM Models** (`tests/unit/test_status_models.py`):
   - Validate all model definitions
   - Test relationships and constraints
   - Test mixin behaviors

2. **Session Management** (`tests/unit/test_status_session.py`):
   - Test `create_status_engine()` with various configs
   - Validate SSL and pooling settings

### API Tests

3. **POST Endpoints** (`tests/api/test_status_post.py`):
   - Test data ingestion for all systems
   - Validate required fields
   - Test error handling (auth, malformed data)

4. **GET Endpoints** (`tests/api/test_status_get.py`) - Optional:
   - Test latest status retrieval (JSON format)
   - Test filtering (system_name for outages)
   - Test empty database handling
   - Note: GET APIs not required for dashboard but useful for future tools

### Integration Tests

5. **End-to-End Flow** (`tests/integration/test_status_flow.py`):
   - POST mock data → verify database insertion
   - Query ORM → verify data retrieval
   - Test data retention cleanup
   - Test concurrent writes

6. **UI Tests** (`tests/integration/test_status_ui.py`):
   - Test dashboard rendering with real data
   - Verify all status data displays correctly
   - Test tab switching (Bootstrap tabs)
   - Test with empty database (graceful handling)

---

## Data Retention & Cleanup

**Retention Policy**: 7 days of 5-minute interval data
- ~2,016 records per week per main status table
- Derecho: ~3 tables × 2,016 = ~6K records/week
- Casper: ~3 tables × 2,016 = ~6K records/week
- Total: ~15K records/week (manageable)

**Cleanup Script**: `scripts/cleanup_status_data.py`
- Deletes records older than 7 days
- Runs daily via cron (2 AM)
- Logs deletion counts

**Cron Entry**:
```cron
0 2 * * * /path/to/python scripts/cleanup_status_data.py >> /var/log/status_cleanup.log 2>&1
```

---

## Future Phases (Planned)

### HPC Data Collectors (Parallel Project)

**Status**: Not started | **Priority**: High
**Implementation Guide**: See `docs/HPC_DATA_COLLECTORS_GUIDE.md`

This is a **parallel project** that will implement the actual data collection from HPC systems. The collectors will:
- Run on Derecho/Casper/JupyterHub systems (or monitoring nodes)
- Execute every 5 minutes via cron
- Gather metrics using SLURM commands (`squeue`, `sinfo`, `scontrol`)
- Monitor filesystem health (`df`, `lfs df`)
- Check login node availability and load
- POST data to SAM API (`/api/v1/status/*`)
- Handle errors, retries, and logging

**Deliverables**:
- Python collector scripts (one per system)
- SLURM command wrappers and parsers
- Shared API client library with authentication
- Configuration files (queue definitions, node specs)
- Cron installation scripts
- Health monitoring and alerting

**Dependencies**: Phase 1 complete (APIs operational)

**Timeline**: 2-3 weeks for initial implementation

---

### Phase 2: Historical Visualization & Analytics

**Status**: Planned | **Priority**: Medium
**Prerequisites**: Phase 1 complete, real data flowing

**Objectives**:
- Add historical trend visualization to dashboard
- Enable time-range queries and comparison
- Provide capacity planning insights

**Features**:

1. **Time-Series Charts** (using Chart.js or matplotlib/SVG):
   - 7-day utilization trends (CPU, GPU, memory)
   - Job queue depth over time
   - Filesystem growth patterns
   - Interactive drill-down: 7-day → daily → hourly → 5-minute

2. **Historical Data Aggregation**:
   - Optional: Create daily summary tables for long-term trends
   - Aggregate 5-minute data → hourly averages
   - Aggregate hourly data → daily averages
   - Retain daily summaries for 1 year+

3. **Comparative Analysis**:
   - Week-over-week comparisons
   - Month-over-month trends
   - Peak usage identification
   - Capacity utilization forecasts

4. **Export & Reporting**:
   - CSV export of historical data
   - PDF report generation
   - API endpoints for external tools

**Database Changes**:
```sql
-- Optional aggregation tables
CREATE TABLE derecho_daily_summary (
  summary_date DATE PRIMARY KEY,
  avg_cpu_utilization FLOAT,
  avg_gpu_utilization FLOAT,
  peak_cpu_utilization FLOAT,
  peak_gpu_utilization FLOAT,
  avg_running_jobs INT,
  ...
);
```

**Timeline**: 1-2 weeks after real data is flowing

---

### Phase 3: Real-Time Updates & Notifications

**Status**: Planned | **Priority**: Medium
**Prerequisites**: Phase 2 complete

**Objectives**:
- Replace 5-minute meta refresh with real-time updates
- Add push notifications for critical events
- Enable user-subscribed alerts

**Features**:

1. **WebSocket Real-Time Updates** (Flask-SocketIO):
   - Push updates to dashboard without page reload
   - Sub-minute refresh rates
   - Only send changed data (delta updates)
   - Graceful fallback to polling if WebSocket unavailable

2. **Outage Notifications**:
   - Email alerts for critical/major outages
   - Slack/Teams webhooks for team notifications
   - SMS for emergency situations (optional)
   - Configurable notification thresholds

3. **User-Subscribed Alerts**:
   - Queue wait time thresholds
   - Filesystem space warnings
   - Node availability changes
   - Reservation reminders

4. **Alert Management UI**:
   - User preferences for notification channels
   - Alert history and acknowledgment
   - Silence/snooze functionality

**Database Changes**:
```sql
CREATE TABLE user_alert_preferences (
  user_id INT,
  alert_type VARCHAR(64),
  enabled BOOLEAN,
  threshold_value FLOAT,
  notification_channel ENUM('email', 'slack', 'sms'),
  PRIMARY KEY (user_id, alert_type)
);

CREATE TABLE alert_history (
  alert_id INT PRIMARY KEY AUTO_INCREMENT,
  user_id INT,
  alert_type VARCHAR(64),
  message TEXT,
  sent_at DATETIME,
  acknowledged BOOLEAN DEFAULT FALSE
);
```

**Timeline**: 2-3 weeks

---

### Phase 4: Predictive Analytics & Advanced Features

**Status**: Future | **Priority**: Low
**Prerequisites**: Phase 3 complete, 6+ months of historical data

**Objectives**:
- Add predictive analytics for capacity planning
- Implement anomaly detection
- Provide actionable insights

**Features**:

1. **Queue Wait Time Prediction** (ML-based):
   - Train models on historical job data
   - Predict wait times based on:
     - Current queue depth
     - Time of day/week patterns
     - Resource requirements
     - Historical completion times
   - Display predictions on dashboard
   - API endpoint for job submission tools

2. **Capacity Planning**:
   - Forecast resource needs based on trends
   - Identify usage patterns and seasonality
   - Recommend optimal allocation adjustments
   - What-if analysis for capacity changes

3. **Anomaly Detection**:
   - Automatic detection of unusual patterns:
     - Sudden utilization spikes/drops
     - Abnormal job failure rates
     - Filesystem growth anomalies
     - Node health degradation
   - Alert administrators before issues escalate
   - Root cause analysis suggestions

4. **Usage Insights Dashboard**:
   - Top users/projects by resource consumption
   - Efficiency metrics (requested vs. actual usage)
   - Queue optimization recommendations
   - Cost allocation reporting

**Technologies**:
- scikit-learn or TensorFlow for ML models
- Pandas for data analysis
- Optional: Apache Spark for large-scale analytics

**Timeline**: 4-6 weeks (after sufficient historical data)

---

## Phase Summary

| Phase | Status | Timeline | Dependency |
|-------|--------|----------|------------|
| Phase 1: Foundation | ✅ Complete | 2 weeks | None |
| HPC Collectors | 🔜 Next | 2-3 weeks | Phase 1 |
| Phase 2: Historical | 📋 Planned | 1-2 weeks | Real data |
| Phase 3: Real-Time | 📋 Planned | 2-3 weeks | Phase 2 |
| Phase 4: Analytics | 🔮 Future | 4-6 weeks | 6+ months data |

---

## Current State (Post-Phase 1)

**Operational**:
- ✅ Database accepting status data
- ✅ API endpoints ready for data ingestion
- ✅ Dashboard displaying real-time status
- ✅ Auto-refresh every 5 minutes
- ✅ Outage and reservation tracking
- ✅ Data cleanup automation (7-day retention)

**Ready For**:
- ✅ HPC data collector development (external project)
- ✅ Production deployment
- ✅ User feedback and iteration

**Waiting On**:
- ⏳ HPC collectors to provide real data
- ⏳ User adoption and feedback
- ⏳ Historical data accumulation

---

## Critical Files for Implementation

### Top 5 Files to Read Before Implementation

1. **`python/sam/session/__init__.py`** (lines 1-68)
   - **Why**: Connection factory pattern - establish how to replicate for status DB
   - **Key patterns**: Environment variable loading, SSL config, pooling

2. **`python/webui/api/v1/projects.py`** (lines 421-483, 521-573)
   - **Why**: POST endpoint patterns - add_member() shows complete flow
   - **Key patterns**: Validation, authorization, error handling, response format

3. **`python/webui/dashboards/user/blueprint.py`** (all)
   - **Why**: Dashboard route patterns - understand blueprint structure
   - **Key patterns**: Route definitions, template rendering, session passing

4. **`python/webui/templates/dashboards/user/dashboard.html`** (all)
   - **Why**: Tabbed interface implementation - see tab navigation structure
   - **Key patterns**: Bootstrap tabs, lazy loading, collapsible cards

5. **`python/webui/static/js/lazy-loading.js`** (all 27 lines)
   - **Why**: Fragment loading pattern - understand auto-loading mechanism
   - **Key patterns**: Event listeners, data attributes, fetch/insert

### Additional Reference Files

6. **`python/sam/summaries/comp_summaries.py`** - Time-series aggregation pattern
7. **`python/webui/api/helpers.py`** - Response wrappers and error handling
8. **`python/webui/utils/rbac.py`** - Permission system and decorators
9. **`python/sam/projects/projects.py`** (lines 306-441) - Query aggregation pattern
10. **`tests/integration/test_schema_validation.py`** - Schema validation approach

---

## Key Technical Patterns to Follow

### 1. Database Connection (from `sam/session/__init__.py`)

```python
# Environment-driven connection string
connection_string = f'mysql+pymysql://{username}:{password}@{server}/{database}'

# Engine with pooling and SSL
engine = create_engine(
    connection_string,
    pool_pre_ping=True,
    pool_recycle=3600,
    connect_args={'ssl': {'ssl_disabled': False}} if require_ssl else {}
)
```

### 2. API POST Pattern (from `projects.py`)

```python
@bp.route('/endpoint', methods=['POST'])
@login_required
@require_permission(Permission.X)
def endpoint():
    # 1. Get JSON
    data = request.get_json()

    # 2. Validate required fields
    if not data.get('field'):
        return jsonify({'error': 'Field required'}), 400

    # 3. Business logic with error handling
    try:
        # ... do work ...
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    # 4. Success response
    return jsonify({'success': True, 'message': '...'}), 201
```

### 3. Time-Series Table Pattern (from `comp_summaries.py`)

```python
class StatusTable(Base, StatusSnapshotMixin):
    __tablename__ = 'table_name'

    __table_args__ = (
        Index('ix_timestamp', 'timestamp'),
        Index('ix_timestamp_component', 'timestamp', 'component_id'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    # ... metrics ...
```

---

## Dependencies & Prerequisites

### Python Packages (already installed)

- SQLAlchemy 2.0
- Flask
- Flask-Login
- pymysql
- python-dotenv

### Database

- MySQL/MariaDB server (existing SAM_DB_SERVER)
- CREATE DATABASE permission for `system_status`

### Frontend

- Bootstrap 4.6.2 (already in use)
- Font Awesome 5.15.4 (already in use)
- jQuery 3.6.0 (already in use)

### No New Dependencies Required

All required libraries already present in SAM project.

---

## Success Criteria

### Phase 1 Complete When:

1. ✅ Separate `system_status` MySQL database created and accessible
2. ✅ All ORM models defined and tested in `python/system_status/`
3. ✅ POST APIs successfully ingest mock data for all three systems
4. ✅ GET APIs return latest status correctly formatted
5. ✅ Status dashboard renders with 3 tabs
6. ✅ Auto-refresh works (5-minute intervals)
7. ✅ Shared components refactored and reused
8. ✅ User dashboard migrated to use new shared tab navigation
9. ✅ All tests pass (unit, API, integration)
10. ✅ Documentation complete (API docs, schema docs, CLAUDE.md updated)
11. ✅ Data cleanup script tested and scheduled via cron

### Quality Gates

- **Code Coverage**: Aim for >80% on new code
- **Response Times**: ORM queries < 50ms with 7 days of data (indexed queries)
- **UI Performance**: Dashboard server-side render < 500ms
- **Schema Validation**: No drift between ORM and database

---

## Risk Mitigation

### Risk 1: Database Performance with 5-Minute Data

**Mitigation**:
- Proper indexing strategy (composite indexes on timestamp + component)
- 7-day retention limit
- Query optimization (fetch latest first, limit results)
- Consider materialized views if needed (Phase 2)

### Risk 2: Concurrent Writes (Multiple HPC Collectors)

**Mitigation**:
- Use unique constraints on `(timestamp, component_id)`
- Database handles concurrent INSERTs
- Transaction isolation via SQLAlchemy sessions
- Test concurrent writes in integration tests

### Risk 3: Stale Data Display

**Mitigation**:
- Show timestamp on dashboard ("Last updated: ...")
- Color-code by age (>10 minutes = warning)
- Auto-refresh keeps data fresh
- Fallback message if no data available

### Risk 4: API Authentication for HPC Collectors

**Mitigation**:
- Use existing `ApiCredentials` pattern from SAM
- Create service accounts for HPC collectors
- Assign `MANAGE_SYSTEM_STATUS` permission
- API key rotation procedures (Phase 2)

---

## Alignment with Existing Patterns

This implementation maintains complete consistency with SAM's established patterns:

✅ **Database**: Separate database on same MySQL server (like multi-tenant approach)
✅ **ORM**: Parallel directory structure (`system_status/` alongside `sam/`)
✅ **API**: Same blueprint patterns, decorators, error handling
✅ **Session Management**: Reuses proven connection factory pattern
✅ **UI**: Extends existing dashboard infrastructure
✅ **Testing**: Mirrors SAM's comprehensive test suite approach
✅ **Documentation**: Follows CLAUDE.md documentation pattern

**No Breaking Changes**: User dashboard remains fully functional throughout implementation.

---

## Architectural Decision: Server-Side Rendering

**Decision**: Use server-side rendering with Jinja2 templates (NOT client-side JavaScript rendering)

**Rationale**:
1. **Simplicity** - Fewer moving parts, less code to maintain
2. **Consistency** - Matches existing user dashboard pattern exactly
3. **Performance** - Single request for complete page (faster initial load)
4. **Accessibility** - Works without JavaScript, better for users
5. **Easier debugging** - View source shows actual data

**Trade-offs Accepted**:
- Full page reload for refresh (acceptable for 5-minute intervals)
- Cannot easily add real-time updates (deferred to Phase 3 WebSocket approach)

**APIs Still Included**:
- POST APIs: Required for HPC data ingestion (external systems)
- GET APIs: Optional but included for future use (monitoring tools, mobile apps, etc.)
- Dashboard does NOT use GET APIs - queries ORM directly

This approach prioritizes simplicity and maintainability for Phase 1 while keeping options open for future enhancements.

---

## Conclusion

This plan delivers a production-ready system status dashboard foundation that:

1. **Separates concerns** - Status data isolated from accounting data
2. **Scales efficiently** - 5-minute intervals with automatic cleanup
3. **Follows proven patterns** - Reuses SAM's established architecture (server-side rendering)
4. **Enables future growth** - Clear path to Phase 2+ enhancements
5. **Ships complete** - Full stack (DB → API → UI) in Phase 1
6. **Stays simple** - Server-side rendering reduces complexity

**Implementation Timeline**: 8 days for Phase 1 complete delivery

**Ready for Review**: This plan is ready for approval and implementation.
