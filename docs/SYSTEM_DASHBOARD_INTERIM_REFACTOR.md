# System Status Dashboard Refactoring Plan

## Overview
Refactor Phase 1 system status dashboard implementation with four key improvements:
1. Add marshmallow schemas for API serialization
2. Implement per-login-node tracking with new database tables
3. Split monolithic dashboard template into modular components
4. Add comprehensive test coverage

**User Preferences:**
- Login nodes: New LoginNodeStatus tables (detailed per-node tracking)
- Schemas: Basic single-tier schemas (can refactor to three-tier later)
- Tests: API endpoints + Schema serialization + Integration tests (skip UI tests)

---

## Task 1: Add Marshmallow Schemas for Status API

### Rationale
Current `/api/v1/status.py` uses manual dictionary construction. SAM project uses marshmallow-sqlalchemy for all other APIs. Benefits:
- Automatic datetime serialization (ISO format)
- Type safety and validation
- Consistent with project patterns
- Easier to maintain and extend

---

## Task 2: Implement Per-Login-Node Tracking

### Rationale
Current implementation tracks login nodes as aggregates (boolean flags or counts). User wants per-node detail:
- Derecho: 8 login nodes (currently binary CPU/GPU available flags)
- Casper: 2 login nodes (currently just available/total counts)

### Database Schema Changes

**Create two new tables:**

**derecho_login_node_status:**

**casper_login_node_status:**

### Mock Data Updates

**Update:** `tests/mock_data/status_mock_data.json`

Replace aggregate login fields with arrays:
```json
{
    "derecho": {
        "timestamp": "2025-11-25T14:30:00",
        "login_nodes": [
            {"node_name": "derecho-login1", "node_type": "cpu", "available": true, "user_count": 12, "load_1min": 2.3},
            {"node_name": "derecho-login2", "node_type": "cpu", "available": true, "user_count": 15, "load_1min": 3.1},
            {"node_name": "derecho-login3", "node_type": "cpu", "available": true, "user_count": 8, "load_1min": 1.8},
            {"node_name": "derecho-login4", "node_type": "cpu", "available": false, "user_count": 0, "load_1min": null},
            {"node_name": "derecho-gpu1", "node_type": "gpu", "available": true, "user_count": 5, "load_1min": 0.9},
            {"node_name": "derecho-gpu2", "node_type": "gpu", "available": true, "user_count": 7, "load_1min": 1.2},
            {"node_name": "derecho-gpu3", "node_type": "gpu", "available": true, "user_count": 3, "load_1min": 0.5},
            {"node_name": "derecho-gpu4", "node_type": "gpu", "available": true, "user_count": 4, "load_1min": 0.8}
        ],
        "cpu_nodes_total": 2488,
        // ... rest of fields
    },
    "casper": {
        "timestamp": "2025-11-25T14:30:00",
        "login_nodes": [
            {"node_name": "casper-login1", "available": true, "user_count": 38, "load_1min": 1.5},
            {"node_name": "casper-login2", "available": true, "user_count": 40, "load_1min": 1.8}
        ],
        "compute_nodes_total": 260,
        // ... rest of fields
    }
}
```

---

## Task 3: Implement Comprehensive Tests

### Test Files to Create

**1. API Endpoint Tests:** `tests/api/test_status_endpoints.py` (~400 lines)

**2. Schema Tests:** `tests/api/test_status_schemas.py` (~200 lines)

**3. Integration Tests:** `tests/integration/test_status_flow.py` (~150 lines)

---

## Task 4: Split Dashboard Template into Modular Components

### Rationale
Current `dashboard.html` is 656 lines - hard to maintain. Split into:
- Main container (100 lines)
- System-specific files (80-120 lines each)
- Reusable macros (20-40 lines each)

### Directory Structure

**Create directories:**
```
python/webui/templates/dashboards/status/
├── dashboard.html              (main container)
├── derecho.html                (Derecho tab content)
├── casper.html                 (Casper tab content)
├── jupyterhub.html             (JupyterHub tab content)
├── partials/
│   ├── system_header.html      (card header macro)
│   ├── metric_card.html        (metric display macro)
│   ├── login_nodes_table.html  (login node table macro - NEW)
│   ├── node_status.html        (compute node status macro)
│   ├── utilization_metrics.html (CPU/GPU/Memory bars)
│   ├── job_statistics.html     (job stats cards)
│   ├── queue_table.html        (queue table macro)
│   ├── filesystem_table.html   (filesystem table macro)
│   ├── nodetype_table.html     (Casper node types)
│   └── no_data_message.html    (empty state macro)
└── fragments/
    └── reservations.html       (reservations tab - optional lazy load)
```

### Dashboard Blueprint Changes

**Update:** `python/webui/dashboards/status/blueprint.py`

Query login nodes along with main status:
```python
@bp.route('/')
@login_required
def index():
    engine, SessionLocal = create_status_engine()
    with get_session(SessionLocal) as session:
        # Existing queries
        derecho_status = session.query(DerechoStatus).order_by(
            DerechoStatus.timestamp.desc()
        ).first()

        # NEW: Query login nodes for same timestamp
        derecho_login_nodes = []
        if derecho_status:
            derecho_login_nodes = session.query(DerechoLoginNodeStatus).filter_by(
                timestamp=derecho_status.timestamp
            ).all()

        casper_login_nodes = []
        if casper_status:
            casper_login_nodes = session.query(CasperLoginNodeStatus).filter_by(
                timestamp=casper_status.timestamp
            ).all()

        return render_template(
            'dashboards/status/dashboard.html',
            derecho_status=derecho_status,
            derecho_login_nodes=derecho_login_nodes,  # NEW
            casper_login_nodes=casper_login_nodes,    # NEW
            # ... other data
        )
```

### Main Dashboard Template

**Refactor:** `python/webui/templates/dashboards/status/dashboard.html` (~100 lines)

```jinja2
{% extends 'dashboards/base.html' %}

{% block title %}System Status Dashboard{% endblock %}

{% block extra_css %}
<style>
    /* Global status styles */
    .status-badge { ... }
    .metric-card { ... }
    .outage-card { ... }
    .last-updated { ... }
    .no-data-message { ... }
</style>
{% endblock %}

{% block content %}
<meta http-equiv="refresh" content="300">

<!-- Page Header -->
<div class="d-flex justify-content-between align-items-center mb-4">
    <h2><i class="fas fa-server"></i> System Status Dashboard</h2>
    <div class="last-updated">
        <i class="fas fa-clock"></i> Auto-refresh every 5 minutes
    </div>
</div>

<!-- Active Outages Banner -->
{% if outages %}
<div class="alert alert-warning mb-4">
    <!-- Outage banner content (keep inline, only shown when issues exist) -->
</div>
{% endif %}

<!-- Tab Navigation -->
<ul class="nav nav-tabs mb-4" id="statusTabs" role="tablist">
    <li class="nav-item">
        <a class="nav-link active" href="#derecho" data-toggle="tab">
            <i class="fas fa-server"></i> Derecho
        </a>
    </li>
    <li class="nav-item">
        <a class="nav-link" href="#casper" data-toggle="tab">
            <i class="fas fa-hdd"></i> Casper
        </a>
    </li>
    <li class="nav-item">
        <a class="nav-link" href="#jupyterhub" data-toggle="tab">
            <i class="fas fa-book"></i> JupyterHub
        </a>
    </li>
    {% if reservations %}
    <li class="nav-item">
        <a class="nav-link" href="#reservations" data-toggle="tab">
            <i class="fas fa-calendar-alt"></i> Reservations ({{ reservations|length }})
        </a>
    </li>
    {% endif %}
</ul>

<!-- Tab Content -->
<div class="tab-content">
    <div class="tab-pane fade show active" id="derecho">
        {% include 'dashboards/status/derecho.html' %}
    </div>
    <div class="tab-pane fade" id="casper">
        {% include 'dashboards/status/casper.html' %}
    </div>
    <div class="tab-pane fade" id="jupyterhub">
        {% include 'dashboards/status/jupyterhub.html' %}
    </div>
    {% if reservations %}
    <div class="tab-pane fade" id="reservations">
        {% include 'dashboards/status/fragments/reservations.html' %}
    </div>
    {% endif %}
</div>
{% endblock %}
```

### System-Specific Templates

**Create:** `derecho.html`, `casper.html`, `jupyterhub.html`

Each uses macros from partials/ for consistent rendering. Example structure:

```jinja2
{# derecho.html #}
{% import 'dashboards/status/partials/system_header.html' as header %}
{% import 'dashboards/status/partials/login_nodes_table.html' as login %}
{% import 'dashboards/status/partials/node_status.html' as nodes %}
{% import 'dashboards/status/partials/utilization_metrics.html' as util %}
{% import 'dashboards/status/partials/job_statistics.html' as jobs %}
{% import 'dashboards/status/partials/queue_table.html' as queues %}
{% import 'dashboards/status/partials/filesystem_table.html' as fs %}
{% import 'dashboards/status/partials/no_data_message.html' as empty %}

{% if derecho_status %}
<div class="card mb-3">
    {{ header.system_header('Derecho System Status', derecho_status.timestamp) }}
    <div class="card-body">
        <!-- Login Nodes Detail (NEW) -->
        {% if derecho_login_nodes %}
        <h6 class="mb-3"><i class="fas fa-desktop"></i> Login Nodes</h6>
        {{ login.login_nodes_table(derecho_login_nodes, has_node_type=true) }}
        {% endif %}

        <!-- Compute Nodes -->
        <h6 class="mb-3"><i class="fas fa-server"></i> Compute Nodes</h6>
        {{ nodes.node_status_table(derecho_status) }}

        <!-- Utilization -->
        <h6 class="mb-3"><i class="fas fa-chart-bar"></i> Resource Utilization</h6>
        {{ util.utilization_metrics(derecho_status) }}

        <!-- Job Stats -->
        <h6 class="mb-3"><i class="fas fa-tasks"></i> Job Statistics</h6>
        {{ jobs.job_statistics(derecho_status) }}

        <!-- Queues -->
        {% if derecho_queues %}
        <h6 class="mb-3"><i class="fas fa-list"></i> Queue Status</h6>
        {{ queues.queue_table(derecho_queues, show_gpus=true) }}
        {% endif %}

        <!-- Filesystems -->
        {% if derecho_filesystems %}
        <h6 class="mb-3 mt-4"><i class="fas fa-database"></i> Filesystem Status</h6>
        {{ fs.filesystem_table(derecho_filesystems) }}
        {% endif %}
    </div>
</div>
{% else %}
{{ empty.no_data_message('No Derecho status data available.') }}
{% endif %}
```

### New Login Nodes Table Macro

**Create:** `partials/login_nodes_table.html`

```jinja2
{% macro login_nodes_table(login_nodes, has_node_type=false) %}
{% if has_node_type %}
    {# Derecho: Group by node type #}
    <div class="row mb-4">
        <div class="col-md-6">
            <h6 class="text-primary">CPU Login Nodes</h6>
            {{ _render_login_table(login_nodes | selectattr('node_type', 'equalto', 'cpu') | list) }}
        </div>
        <div class="col-md-6">
            <h6 class="text-primary">GPU Login Nodes</h6>
            {{ _render_login_table(login_nodes | selectattr('node_type', 'equalto', 'gpu') | list) }}
        </div>
    </div>
{% else %}
    {# Casper: Single table #}
    {{ _render_login_table(login_nodes) }}
{% endif %}
{% endmacro %}

{% macro _render_login_table(nodes) %}
<table class="table table-sm table-bordered">
    <thead class="thead-light">
        <tr>
            <th>Node</th>
            <th>Status</th>
            <th>Users</th>
            <th>Load (1min)</th>
        </tr>
    </thead>
    <tbody>
        {% for node in nodes %}
        <tr>
            <td><code>{{ node.node_name }}</code></td>
            <td>
                <span class="status-badge status-{{ 'online' if node.available else 'offline' }}">
                    {{ 'Online' if node.available else 'Offline' }}
                </span>
                {% if node.degraded %}
                <span class="badge badge-warning">Degraded</span>
                {% endif %}
            </td>
            <td>{{ node.user_count if node.user_count is not none else 'N/A' }}</td>
            <td>{{ '%.2f'|format(node.load_1min) if node.load_1min is not none else 'N/A' }}</td>
        </tr>
        {% endfor %}
    </tbody>
</table>
{% endmacro %}
```

### Other Macros to Extract

Create one file per macro in `partials/`:
- `system_header.html` - Card header with last updated
- `metric_card.html` - Single metric display with optional progress bar
- `node_status.html` - Compute node breakdown (CPU/GPU partitions)
- `utilization_metrics.html` - CPU/GPU/Memory with progress bars
- `job_statistics.html` - Running/pending/active users cards
- `queue_table.html` - Queue status table
- `filesystem_table.html` - Filesystem table with progress bars
- `nodetype_table.html` - Casper node types table
- `no_data_message.html` - Empty state message

**Files to create:**
- 10 macro files in `partials/`
- 3 system templates (derecho.html, casper.html, jupyterhub.html)
- 1 fragment (fragments/reservations.html)

**Files to modify:**
- `dashboard.html` (reduce from 656 to ~100 lines)

---

## Implementation Order

Execute tasks in this sequence for minimal disruption:

### Phase A: Database & ORM (Foundation)
1. Create SQL schema updates (`create_status_db.sql`)
2. Create ORM models (`login_nodes.py`)
3. Update model exports
4. Run `setup_status_db.py` to create tables
5. Verify tables exist in database

### Phase B: Schemas (API Layer)
1. Create `webui/schemas/status.py` with all schemas
2. Update `webui/schemas/__init__.py` exports
3. Refactor GET endpoints in `api/v1/status.py` to use schemas
4. Test manually with existing data

### Phase C: Login Nodes (New Data)
1. Update POST endpoints to accept login_nodes arrays
2. Update mock data JSON with login_nodes
3. Update ingest script to use new format
4. Update blueprint to query login nodes
5. Update HPC collectors guide documentation

### Phase E: Testing (Validation)
1. Create test fixtures in conftest.py
2. Implement API endpoint tests
3. Implement schema tests
4. Implement integration tests
5. Run full test suite: `cd tests && pytest -v api/ integration/`

### Phase D: Templates (UI Layer)
1. Create `partials/` directory and all macro files
2. Create `fragments/` directory and reservations.html
3. Create system-specific templates (derecho.html, casper.html, jupyterhub.html)
4. Refactor main dashboard.html to use includes
5. Test dashboard rendering manually

---

## Files Summary

### Files to Create (28 new files)
**Database:**
- None (SQL updates only)

**ORM Models:**
- `python/system_status/models/login_nodes.py`

**Schemas:**
- `python/webui/schemas/status.py`

**Templates - Partials (10 files):**
- `python/webui/templates/dashboards/status/partials/system_header.html`
- `python/webui/templates/dashboards/status/partials/metric_card.html`
- `python/webui/templates/dashboards/status/partials/login_nodes_table.html`
- `python/webui/templates/dashboards/status/partials/node_status.html`
- `python/webui/templates/dashboards/status/partials/utilization_metrics.html`
- `python/webui/templates/dashboards/status/partials/job_statistics.html`
- `python/webui/templates/dashboards/status/partials/queue_table.html`
- `python/webui/templates/dashboards/status/partials/filesystem_table.html`
- `python/webui/templates/dashboards/status/partials/nodetype_table.html`
- `python/webui/templates/dashboards/status/partials/no_data_message.html`

**Templates - System Files (3 files):**
- `python/webui/templates/dashboards/status/derecho.html`
- `python/webui/templates/dashboards/status/casper.html`
- `python/webui/templates/dashboards/status/jupyterhub.html`

**Templates - Fragments (1 file):**
- `python/webui/templates/dashboards/status/fragments/reservations.html`

**Tests (3 files):**
- `tests/api/test_status_endpoints.py`
- `tests/api/test_status_schemas.py`
- `tests/integration/test_status_flow.py`

### Files to Modify (11 files)
**Database:**
- `scripts/create_status_db.sql` (add CREATE TABLE statements)

**ORM:**
- `python/system_status/models/__init__.py` (export new models)
- `python/system_status/__init__.py` (export new models)

**Schemas:**
- `python/webui/schemas/__init__.py` (export status schemas)

**API:**
- `python/webui/api/v1/status.py` (refactor all endpoints)

**Blueprint:**
- `python/webui/dashboards/status/blueprint.py` (query login nodes)

**Templates:**
- `python/webui/templates/dashboards/status/dashboard.html` (refactor to ~100 lines)

**Scripts:**
- `scripts/setup_status_db.py` (import new models)
- `scripts/ingest_mock_status.py` (use new data format)

**Data:**
- `tests/mock_data/status_mock_data.json` (add login_nodes arrays)

**Documentation:**
- `docs/HPC_DATA_COLLECTORS_GUIDE.md` (update API examples)

**Tests:**
- `tests/api/conftest.py` (add fixtures)

---

## Testing Plan

After implementation, validate with:

1. **Database tests:**
   ```bash
   python scripts/setup_status_db.py  # Create tables
   python scripts/test_status_db.py   # Verify connections
   ```

2. **Mock data ingestion:**
   ```bash
   python scripts/ingest_mock_status.py  # Ingest test data
   ```

3. **Unit/API tests:**
   ```bash
   cd tests
   pytest -v api/test_status_endpoints.py
   pytest -v api/test_status_schemas.py
   ```

4. **Integration tests:**
   ```bash
   cd tests
   pytest -v integration/test_status_flow.py
   ```

5. **Manual UI testing:**
   ```bash
   ./utils/run-webui-dbg.sh
   # Visit http://localhost:5050/status/
   # Verify all tabs render
   # Check login nodes table displays
   # Verify no JavaScript errors
   ```

6. **Full test suite:**
   ```bash
   cd tests
   pytest -v  # All tests
   ```

---

## Success Criteria

✅ **Task 1 - Schemas:**
- All 11 schemas created
- GET endpoints use schemas (no manual dicts)
- Datetimes serialize to ISO format

✅ **Task 2 - Login Nodes:**
- Two new tables in database
- 8 Derecho nodes tracked individually
- 2 Casper nodes tracked individually
- API accepts login_nodes arrays
- Dashboard displays per-node tables

✅ **Task 3 - Templates:**
- Main dashboard reduced to ~100 lines
- 10 reusable macros created
- 3 system-specific templates
- No code duplication
- All tabs render correctly

✅ **Task 4 - Tests:**
- 40+ API endpoint tests
- 20+ schema tests
- 10+ integration tests
- All tests passing
- >80% code coverage on new code

---

## Rollback Plan

If issues arise, rollback is straightforward:

1. **Schemas:** Just don't import/use them - manual dicts still work
2. **Login nodes:** Tables are optional - old aggregate fields remain
3. **Templates:** Keep old dashboard.html as `dashboard.html.backup`
4. **Tests:** Don't run new test files

Tables and schemas are additive changes - won't break existing functionality.

---

## Post-Implementation

After all tasks complete:

1. Update `docs/SYSTEM_DASHBOARD_PLAN.md` - mark Phase 1 refactoring complete
2. Create git commit with detailed message
3. Consider PR to main branch
4. Update README.md if needed
5. Deploy to test environment for validation
