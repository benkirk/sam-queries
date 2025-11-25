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

### Implementation

**Create new schema file:** `python/webui/schemas/status.py`

**Schemas to create (basic single-tier):**

1. **DerechoStatusSchema** - Main system metrics
2. **DerechoQueueStatusSchema** - Per-queue data
3. **DerechoFilesystemStatusSchema** - Filesystem health
4. **DerechoLoginNodeStatusSchema** - Per-login-node (new)
5. **CasperStatusSchema** - Main system metrics
6. **CasperNodeTypeStatusSchema** - Per-node-type
7. **CasperQueueStatusSchema** - Per-queue data
8. **CasperLoginNodeStatusSchema** - Per-login-node (new)
9. **JupyterHubStatusSchema** - JupyterHub metrics
10. **SystemOutageSchema** - Outages/degradations
11. **ResourceReservationSchema** - Scheduled reservations

**Pattern to follow:**
```python
from marshmallow import fields
from webui.schemas import BaseSchema
from system_status.models import DerechoStatus

class DerechoStatusSchema(BaseSchema):
    """Derecho system status serialization."""
    class Meta(BaseSchema.Meta):
        model = DerechoStatus
        fields = (
            'status_id', 'timestamp', 'created_at',
            'cpu_login_available', 'cpu_login_user_count',
            # ... all fields from model
        )

    # Timestamp auto-converts to ISO format via BaseSchema
```

**Update schema exports:** `python/webui/schemas/__init__.py`

**Refactor API endpoints:** `python/webui/api/v1/status.py`

Replace manual dict construction with:
```python
# Before (manual):
result = {
    'timestamp': status.timestamp.isoformat(),
    'cpu_login_available': status.cpu_login_available,
    # ... 30 more lines
}

# After (schema):
from webui.schemas import DerechoStatusSchema
result = DerechoStatusSchema().dump(status)
```

**Files to modify:**
- `python/webui/schemas/status.py` (create new)
- `python/webui/schemas/__init__.py` (add exports)
- `python/webui/api/v1/status.py` (refactor all GET endpoints)

---

## Task 2: Implement Per-Login-Node Tracking

### Rationale
Current implementation tracks login nodes as aggregates (boolean flags or counts). User wants per-node detail:
- Derecho: 8 login nodes (currently binary CPU/GPU available flags)
- Casper: 2 login nodes (currently just available/total counts)

### Database Schema Changes

**Create two new tables:**

**derecho_login_node_status:**
```sql
CREATE TABLE derecho_login_node_status (
    login_node_id INT PRIMARY KEY AUTO_INCREMENT,
    timestamp DATETIME NOT NULL,
    node_name VARCHAR(64) NOT NULL,
    node_type ENUM('cpu', 'gpu') NOT NULL,
    available BOOLEAN NOT NULL DEFAULT TRUE,
    degraded BOOLEAN NOT NULL DEFAULT FALSE,
    user_count INT DEFAULT NULL,
    load_1min FLOAT DEFAULT NULL,
    load_5min FLOAT DEFAULT NULL,
    load_15min FLOAT DEFAULT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_derecho_login_timestamp_name (timestamp, node_name),
    INDEX ix_derecho_login_timestamp (timestamp),
    INDEX ix_derecho_login_node_name (node_name)
);
```

**casper_login_node_status:**
```sql
CREATE TABLE casper_login_node_status (
    login_node_id INT PRIMARY KEY AUTO_INCREMENT,
    timestamp DATETIME NOT NULL,
    node_name VARCHAR(64) NOT NULL,
    available BOOLEAN NOT NULL DEFAULT TRUE,
    degraded BOOLEAN NOT NULL DEFAULT FALSE,
    user_count INT DEFAULT NULL,
    load_1min FLOAT DEFAULT NULL,
    load_5min FLOAT DEFAULT NULL,
    load_15min FLOAT DEFAULT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_casper_login_timestamp_name (timestamp, node_name),
    INDEX ix_casper_login_timestamp (timestamp),
    INDEX ix_casper_login_node_name (node_name)
);
```

### ORM Models

**Create:** `python/system_status/models/login_nodes.py`

```python
from sqlalchemy import Column, Integer, String, Float, Boolean, Enum, Index, UniqueConstraint
from ..base import StatusBase, StatusSnapshotMixin, AvailabilityMixin, SessionMixin

class DerechoLoginNodeStatus(StatusBase, StatusSnapshotMixin, AvailabilityMixin, SessionMixin):
    """Per-login-node metrics for Derecho (5-minute intervals)."""
    __tablename__ = 'derecho_login_node_status'

    __table_args__ = (
        UniqueConstraint('timestamp', 'node_name', name='uq_derecho_login_timestamp_name'),
        Index('ix_derecho_login_node_name', 'node_name'),
    )

    login_node_id = Column(Integer, primary_key=True, autoincrement=True)
    node_name = Column(String(64), nullable=False, index=True)
    node_type = Column(Enum('cpu', 'gpu', name='derecho_login_node_type'), nullable=False)

    # Status fields from AvailabilityMixin: available, degraded

    # Per-node metrics
    user_count = Column(Integer, nullable=True)
    load_1min = Column(Float, nullable=True)
    load_5min = Column(Float, nullable=True)
    load_15min = Column(Float, nullable=True)

class CasperLoginNodeStatus(StatusBase, StatusSnapshotMixin, AvailabilityMixin, SessionMixin):
    """Per-login-node metrics for Casper (5-minute intervals)."""
    __tablename__ = 'casper_login_node_status'

    __table_args__ = (
        UniqueConstraint('timestamp', 'node_name', name='uq_casper_login_timestamp_name'),
        Index('ix_casper_login_node_name', 'node_name'),
    )

    login_node_id = Column(Integer, primary_key=True, autoincrement=True)
    node_name = Column(String(64), nullable=False, index=True)

    # Status fields from AvailabilityMixin: available, degraded

    # Per-node metrics
    user_count = Column(Integer, nullable=True)
    load_1min = Column(Float, nullable=True)
    load_5min = Column(Float, nullable=True)
    load_15min = Column(Float, nullable=True)
```

**Update:** `python/system_status/models/__init__.py`
```python
from .login_nodes import DerechoLoginNodeStatus, CasperLoginNodeStatus
```

**Update:** `python/system_status/__init__.py`
```python
from .models import (
    # ... existing exports
    DerechoLoginNodeStatus,
    CasperLoginNodeStatus,
)
```

### API Changes

**Update POST endpoints** to accept login node arrays:

**Derecho API (`/api/v1/status/derecho`):**
```json
{
    "timestamp": "2025-11-25T14:30:00",
    "cpu_nodes_total": 2488,
    // ... existing fields ...

    "login_nodes": [
        {
            "node_name": "derecho-login1",
            "node_type": "cpu",
            "available": true,
            "user_count": 12,
            "load_1min": 2.3,
            "load_5min": 2.5,
            "load_15min": 2.7
        },
        {
            "node_name": "derecho-login2",
            "node_type": "cpu",
            "available": true,
            "user_count": 15,
            "load_1min": 3.1,
            "load_5min": 3.2,
            "load_15min": 3.0
        }
        // ... 6 more nodes
    ]
}
```

**Casper API (`/api/v1/status/casper`):**
```json
{
    "timestamp": "2025-11-25T14:30:00",
    "compute_nodes_total": 260,
    // ... existing fields ...

    "login_nodes": [
        {
            "node_name": "casper-login1",
            "available": true,
            "user_count": 38,
            "load_1min": 1.5
        },
        {
            "node_name": "casper-login2",
            "available": true,
            "user_count": 40,
            "load_1min": 1.8
        }
    ]
}
```

**Update ingestion logic:**
```python
# In ingest_derecho() and ingest_casper()
login_nodes = data.get('login_nodes', [])
if login_nodes:
    login_node_ids = []
    for node_data in login_nodes:
        node = DerechoLoginNodeStatus(
            timestamp=timestamp,
            node_name=node_data['node_name'],
            node_type=node_data.get('node_type'),  # Derecho only
            available=node_data.get('available', True),
            degraded=node_data.get('degraded', False),
            user_count=node_data.get('user_count'),
            load_1min=node_data.get('load_1min'),
            load_5min=node_data.get('load_5min'),
            load_15min=node_data.get('load_15min'),
        )
        session.add(node)
        session.flush()
        login_node_ids.append(node.login_node_id)
    result['login_node_ids'] = login_node_ids
```

**Update GET endpoints** to include login node arrays:
```python
# In get_derecho_latest()
login_nodes = session.query(DerechoLoginNodeStatus).filter_by(
    timestamp=status.timestamp
).all()

result['login_nodes'] = DerechoLoginNodeStatusSchema(many=True).dump(login_nodes)
```

**Keep aggregate fields** in DerechoStatus/CasperStatus for backward compatibility:
- `cpu_login_available`, `gpu_login_available` - computed from login_nodes array
- `login_nodes_available`, `login_nodes_total` - computed from login_nodes array

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

### Documentation Updates

**Update:** `docs/HPC_DATA_COLLECTORS_GUIDE.md`

Update API data format sections to show login_nodes arrays instead of aggregate fields.

**Files to modify:**
- `scripts/create_status_db.sql` (add CREATE TABLE statements)
- `python/system_status/models/login_nodes.py` (create new)
- `python/system_status/models/__init__.py` (export new models)
- `python/system_status/__init__.py` (export new models)
- `python/webui/api/v1/status.py` (update POST/GET endpoints)
- `python/webui/dashboards/status/blueprint.py` (query login nodes)
- `tests/mock_data/status_mock_data.json` (add login_nodes arrays)
- `scripts/ingest_mock_status.py` (update to use new format)
- `docs/HPC_DATA_COLLECTORS_GUIDE.md` (update API examples)

---

## Task 3: Split Dashboard Template into Modular Components

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

## Task 4: Implement Comprehensive Tests

### Test Files to Create

**1. API Endpoint Tests:** `tests/api/test_status_endpoints.py` (~400 lines)

**Test classes:**
- `TestDerechoIngestion` - POST /api/v1/status/derecho
- `TestCasperIngestion` - POST /api/v1/status/casper
- `TestJupyterHubIngestion` - POST /api/v1/status/jupyterhub
- `TestOutageReporting` - POST /api/v1/status/outage
- `TestStatusRetrieval` - GET endpoints (latest status)

**Test coverage:**
- Authentication (unauthenticated should fail)
- Authorization (requires MANAGE_SYSTEM_STATUS permission)
- Data validation (missing required fields, invalid formats)
- Timestamp parsing (ISO format, custom format, default to now)
- Login nodes array handling (new)
- Queue/filesystem/nodetype array handling
- Response structure validation
- Error handling (400, 401, 403, 404, 500)

**Example tests:**
```python
class TestDerechoIngestion:
    def test_ingest_derecho_success(self, auth_client, mock_status_data):
        """Test successful Derecho status ingestion."""
        response = auth_client.post(
            '/api/v1/status/derecho',
            json=mock_status_data['derecho']
        )
        assert response.status_code == 201
        data = response.get_json()
        assert data['success'] is True
        assert 'status_id' in data
        assert 'login_node_ids' in data  # NEW

    def test_ingest_missing_login_nodes(self, auth_client):
        """Test ingestion without login_nodes array (optional)."""
        minimal_data = {'cpu_nodes_total': 2488, ...}
        response = auth_client.post('/api/v1/status/derecho', json=minimal_data)
        assert response.status_code == 201

    def test_ingest_unauthenticated(self, client):
        """POST requires authentication."""
        response = client.post('/api/v1/status/derecho', json={})
        assert response.status_code in [302, 401]
```

**2. Schema Tests:** `tests/api/test_status_schemas.py` (~200 lines)

**Test classes:**
- `TestDerechoSchemas` - All Derecho-related schemas
- `TestCasperSchemas` - All Casper-related schemas
- `TestJupyterHubSchemas` - JupyterHub schema
- `TestOutageSchemas` - Outage and reservation schemas

**Test coverage:**
- Field presence validation
- Type validation (datetime → ISO string, float → number)
- Nested serialization (login_nodes, queues, filesystems)
- Many=True serialization (arrays)
- Missing data handling (None vs empty list)

**Example tests:**
```python
from webui.schemas import DerechoStatusSchema, DerechoLoginNodeStatusSchema

class TestDerechoSchemas:
    def test_derecho_status_schema(self, session):
        """Test DerechoStatus serialization."""
        from system_status import DerechoStatus

        status = session.query(DerechoStatus).first()
        result = DerechoStatusSchema().dump(status)

        # Validate field presence
        assert 'status_id' in result
        assert 'timestamp' in result
        assert 'cpu_nodes_total' in result

        # Validate types
        assert isinstance(result['timestamp'], str)  # ISO format
        assert isinstance(result['cpu_nodes_total'], int)

    def test_login_node_schema_many(self, session):
        """Test login nodes array serialization."""
        from system_status import DerechoLoginNodeStatus

        nodes = session.query(DerechoLoginNodeStatus).limit(5).all()
        result = DerechoLoginNodeStatusSchema(many=True).dump(nodes)

        assert isinstance(result, list)
        assert len(result) <= 5
        for node in result:
            assert 'node_name' in node
            assert 'available' in node
```

**3. Integration Tests:** `tests/integration/test_status_flow.py` (~150 lines)

**Test classes:**
- `TestStatusIngestionFlow` - POST → DB → GET flow
- `TestDataRetention` - Cleanup script behavior
- `TestConcurrentWrites` - Multiple simultaneous ingestions

**Test coverage:**
- End-to-end flow: POST data → verify DB storage → GET retrieval
- Login nodes persist correctly
- Timestamp-based queries work
- Cleanup script deletes old data
- Concurrent writes don't conflict

**Example tests:**
```python
class TestStatusIngestionFlow:
    def test_derecho_full_flow(self, auth_client, session):
        """Test POST → Database → GET for Derecho."""
        # Step 1: POST status data
        post_data = {...}  # From mock_status_data.json
        response = auth_client.post('/api/v1/status/derecho', json=post_data)
        assert response.status_code == 201
        status_id = response.get_json()['status_id']

        # Step 2: Verify database storage
        from system_status import DerechoStatus, DerechoLoginNodeStatus
        status = session.query(DerechoStatus).get(status_id)
        assert status is not None
        assert status.cpu_nodes_total == post_data['cpu_nodes_total']

        login_nodes = session.query(DerechoLoginNodeStatus).filter_by(
            timestamp=status.timestamp
        ).all()
        assert len(login_nodes) == len(post_data['login_nodes'])

        # Step 3: GET latest status
        response = auth_client.get('/api/v1/status/derecho/latest')
        assert response.status_code == 200
        data = response.get_json()
        assert data['status_id'] == status_id
        assert 'login_nodes' in data
        assert len(data['login_nodes']) == len(post_data['login_nodes'])
```

### Test Fixtures

**Update:** `tests/api/conftest.py`

Add status-specific fixtures:
```python
@pytest.fixture
def mock_status_data():
    """Load mock status data from JSON file."""
    import json
    with open('tests/mock_data/status_mock_data.json') as f:
        return json.load(f)

@pytest.fixture
def status_session():
    """System status database session."""
    from system_status import create_status_engine, get_session
    engine, SessionLocal = create_status_engine()
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()
```

### Database Setup Script

**Update:** `scripts/setup_status_db.py`

Add creation of login_node_status tables:
```python
from system_status import (
    DerechoStatus, DerechoLoginNodeStatus,  # Add new model
    CasperStatus, CasperLoginNodeStatus,    # Add new model
    # ... other models
)

# Tables will auto-create via Base.metadata.create_all()
```

**Files to create:**
- `tests/api/test_status_endpoints.py` (~400 lines)
- `tests/api/test_status_schemas.py` (~200 lines)
- `tests/integration/test_status_flow.py` (~150 lines)

**Files to modify:**
- `tests/api/conftest.py` (add fixtures)

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

### Phase D: Templates (UI Layer)
1. Create `partials/` directory and all macro files
2. Create `fragments/` directory and reservations.html
3. Create system-specific templates (derecho.html, casper.html, jupyterhub.html)
4. Refactor main dashboard.html to use includes
5. Test dashboard rendering manually

### Phase E: Testing (Validation)
1. Create test fixtures in conftest.py
2. Implement API endpoint tests
3. Implement schema tests
4. Implement integration tests
5. Run full test suite: `cd tests && pytest -v api/ integration/`

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
