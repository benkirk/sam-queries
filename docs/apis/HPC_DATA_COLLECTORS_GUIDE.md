# HPC Data Collectors Implementation Guide

## Overview

This guide describes how to implement data collectors that gather system status from HPC resources (Derecho, Casper, JupyterHub) and post the data to the SAM System Status Dashboard API.

**Purpose**: Enable external collector scripts to run on HPC systems, gather metrics, and send data to the central dashboard via REST API.

**Target Audience**: Developers implementing data collectors in Python, Bash, or other languages.

---

## Architecture

```
HPC System (Derecho/Casper/JupyterHub)
    ↓ Collector Script (runs every 5 minutes via cron)
    ↓ Gathers metrics (squeue, sinfo, df, lfs, etc.)
    ↓ Formats as JSON
    ↓ HTTP POST to SAM API
SAM Status API (/api/v1/status/*)
    ↓ Validates & authenticates
    ↓ Inserts via ORM
system_status Database
    ↓ Stores 5-minute snapshots
Dashboard displays latest data
```

---

## Authentication

### API Credentials Setup

Data collectors require `MANAGE_SYSTEM_STATUS` permission.

**Create API credentials** (via SAM admin interface or database):
```sql
-- Example: Create service account for Derecho collector
INSERT INTO api_credentials (username, password_hash, description, active)
VALUES (
    'derecho_collector',
    '$2b$12$...', -- bcrypt hash of password
    'Derecho system status collector',
    1
);

-- Assign permission
INSERT INTO role_api_credentials (role_id, api_credentials_id)
SELECT r.role_id, ac.api_credentials_id
FROM role r, api_credentials ac
WHERE r.role_name = 'admin' AND ac.username = 'derecho_collector';
```

**Store credentials securely**:
```bash
# On HPC system, create .env file (mode 600)
echo "SAM_API_USER=derecho_collector" > ~/.sam_collector.env
echo "SAM_API_PASSWORD=your_secure_password" >> ~/.sam_collector.env
chmod 600 ~/.sam_collector.env
```

---

## API Endpoints

### Base URL
```
Production: https://sam.ucar.edu/api/v1/status/
Development: http://localhost:5050/api/v1/status/
```

### Authentication
```bash
# Basic Auth
curl -u username:password -X POST ...

# Or in Python
import requests
requests.post(url, json=data, auth=('username', 'password'))
```

---

## Derecho Data Collector

### Endpoint
```
POST /api/v1/status/derecho
```

### Required Permission
`MANAGE_SYSTEM_STATUS`

### Data Format

**Main system metrics**:
```json
{
  "timestamp": "2025-11-25T14:30:00",  // Optional, defaults to now

  // Compute Nodes - CPU Partition
  "cpu_nodes_total": 2488,
  "cpu_nodes_available": 1850,
  "cpu_nodes_down": 15,
  "cpu_nodes_reserved": 623,

  // Compute Nodes - GPU Partition
  "gpu_nodes_total": 82,
  "gpu_nodes_available": 45,
  "gpu_nodes_down": 2,
  "gpu_nodes_reserved": 35,

  // CPU Utilization
  "cpu_cores_total": 321536,
  "cpu_cores_allocated": 245000,
  "cpu_cores_idle": 76536,
  "cpu_utilization_percent": 76.2,

  // GPU Utilization
  "gpu_count_total": 656,
  "gpu_count_allocated": 485,
  "gpu_count_idle": 171,
  "gpu_utilization_percent": 73.9,

  // Memory
  "memory_total_gb": 650000.0,
  "memory_allocated_gb": 495000.0,
  "memory_utilization_percent": 76.2,

  // Jobs (system-wide)
  "running_jobs": 1245,
  "pending_jobs": 328,
  "active_users": 156,

  // Login Nodes - Per-node tracking (8 total: 4 CPU, 4 GPU)
  "login_nodes": [
    {
      "node_name": "derecho1",
      "node_type": "cpu",          // 'cpu' or 'gpu'
      "available": true,
      "degraded": false,
      "user_count": 12,
      "load_1min": 2.3,
      "load_5min": 2.5,
      "load_15min": 2.8
    },
    {
      "node_name": "derecho2",
      "node_type": "cpu",
      "available": true,
      "degraded": false,
      "user_count": 11,
      "load_1min": 2.1,
      "load_5min": 2.4,
      "load_15min": 2.7
    },
    {
      "node_name": "derecho5",
      "node_type": "gpu",
      "available": true,
      "degraded": false,
      "user_count": 3,
      "load_1min": 1.1,
      "load_5min": 1.3,
      "load_15min": 1.2
    }
    // ... up to 8 login nodes total
  ],

  // Optional: Queue-specific data
  "queues": [
    {
      "queue_name": "main",
      "running_jobs": 890,
      "pending_jobs": 245,
      "active_users": 98,
      "cores_allocated": 185000,
      "gpus_allocated": 0,
      "nodes_allocated": 1432
    },
    {
      "queue_name": "gpumain",
      "running_jobs": 167,
      "pending_jobs": 18,
      "active_users": 45,
      "cores_allocated": 18500,
      "gpus_allocated": 340,
      "nodes_allocated": 143
    }
  ],

  // Optional: Filesystem data
  "filesystems": [
    {
      "filesystem_name": "glade",
      "available": true,
      "degraded": false,
      "capacity_tb": 20000.0,
      "used_tb": 16500.0,
      "utilization_percent": 82.5
    },
    {
      "filesystem_name": "campaign",
      "available": true,
      "degraded": false,
      "capacity_tb": 30000.0,
      "used_tb": 24000.0,
      "utilization_percent": 80.0
    }
  ]
}
```

**Login Node Fields**:
- `node_name` (required): Hostname (e.g., "derecho1", "derecho5")
- `node_type` (required): Either "cpu" or "gpu"
- `available` (required): Boolean - node is accessible
- `degraded` (optional): Boolean - node is up but degraded, defaults to false
- `user_count` (optional): Number of logged-in users
- `load_1min`, `load_5min`, `load_15min` (optional): Load averages

### Data Collection Commands

**Node counts** (from `sinfo`):
```bash
# CPU nodes
sinfo -p main --noheader -o "%D,%A" | awk -F, '{print $1, $2}'
# Output: total_nodes allocated/idle/other/down

# GPU nodes
sinfo -p gpumain --noheader -o "%D,%A"
```

**CPU/GPU utilization** (from `sinfo` or monitoring):
```bash
# Allocated cores
sinfo -p main --noheader -o "%C" | awk -F/ '{print $1}'  # allocated
sinfo -p main --noheader -o "%C" | awk -F/ '{print $2}'  # idle
sinfo -p main --noheader -o "%C" | awk -F/ '{print $4}'  # total
```

**Job counts** (from `squeue`):
```bash
# Running jobs
squeue -t RUNNING --noheader | wc -l

# Pending jobs
squeue -t PENDING --noheader | wc -l

# Active users
squeue --noheader -o "%u" | sort -u | wc -l
```

**Queue-specific** (from `squeue`):
```bash
# Per-queue metrics
for queue in main preempt develop gpudev gpumain; do
  running=$(squeue -p $queue -t RUNNING --noheader | wc -l)
  pending=$(squeue -p $queue -t PENDING --noheader | wc -l)
  users=$(squeue -p $queue --noheader -o "%u" | sort -u | wc -l)
  echo "$queue,$running,$pending,$users"
done
```

**Filesystem status** (from `df` or `lfs df`):
```bash
# For Lustre filesystems
lfs df -h /glade | grep filesystem | awk '{print $2, $3, $5}'
# Output: total_size used_size utilization%
```

**Login node load** (from `uptime` or `/proc/loadavg`):
```bash
# On login node
ssh derecho-login1 "cat /proc/loadavg"
# Output: 2.5 2.8 3.1 (1min, 5min, 15min)

# User count
ssh derecho-login1 "who | wc -l"
```

### Example Python Collector

```python
#!/usr/bin/env python3
"""Derecho status collector."""

import subprocess
import requests
import json
from datetime import datetime
import os

# Load credentials
SAM_API_URL = os.getenv('SAM_API_URL', 'http://localhost:5050/api/v1/status')
SAM_API_USER = os.getenv('SAM_API_USER')
SAM_API_PASSWORD = os.getenv('SAM_API_PASSWORD')

def run_cmd(cmd):
    """Run shell command and return output."""
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return result.stdout.strip()

def get_node_counts():
    """Get node counts from sinfo."""
    # CPU nodes
    cpu_output = run_cmd("sinfo -p main --noheader -o '%D,%A'")
    cpu_total, cpu_alloc_info = cpu_output.split(',')
    cpu_alloc, cpu_idle, _, cpu_down = cpu_alloc_info.split('/')

    # GPU nodes
    gpu_output = run_cmd("sinfo -p gpumain --noheader -o '%D,%A'")
    gpu_total, gpu_alloc_info = gpu_output.split(',')
    gpu_alloc, gpu_idle, _, gpu_down = gpu_alloc_info.split('/')

    return {
        'cpu_nodes_total': int(cpu_total),
        'cpu_nodes_available': int(cpu_idle),
        'cpu_nodes_down': int(cpu_down),
        'gpu_nodes_total': int(gpu_total),
        'gpu_nodes_available': int(gpu_idle),
        'gpu_nodes_down': int(gpu_down),
    }

def get_job_counts():
    """Get job counts from squeue."""
    running = int(run_cmd("squeue -t RUNNING --noheader | wc -l"))
    pending = int(run_cmd("squeue -t PENDING --noheader | wc -l"))
    users = int(run_cmd("squeue --noheader -o '%u' | sort -u | wc -l"))

    return {
        'running_jobs': running,
        'pending_jobs': pending,
        'active_users': users,
    }

def collect_data():
    """Collect all Derecho metrics."""
    data = {
        'timestamp': datetime.now().isoformat(),
        **get_node_counts(),
        **get_job_counts(),
        # Add more data collection functions here
    }
    return data

def post_data(data):
    """Post data to SAM API."""
    url = f"{SAM_API_URL}/derecho"
    response = requests.post(
        url,
        json=data,
        auth=(SAM_API_USER, SAM_API_PASSWORD),
        timeout=30
    )
    response.raise_for_status()
    return response.json()

if __name__ == '__main__':
    try:
        data = collect_data()
        result = post_data(data)
        print(f"✓ Posted Derecho status: {result}")
    except Exception as e:
        print(f"✗ Error: {e}")
        exit(1)
```

### Cron Setup
```cron
# Run every 5 minutes
*/5 * * * * /path/to/derecho_collector.py >> /var/log/derecho_collector.log 2>&1
```

---

## Casper Data Collector

### Endpoint
```
POST /api/v1/status/casper
```

### Data Format

Casper is heterogeneous, so includes node type breakdown:

```json
{
  "timestamp": "2025-11-25T14:30:00",  // Optional

  // Compute Nodes (aggregate)
  "compute_nodes_total": 260,
  "compute_nodes_available": 185,
  "compute_nodes_down": 3,

  // Aggregate Utilization
  "cpu_utilization_percent": 68.5,
  "gpu_utilization_percent": 82.3,
  "memory_utilization_percent": 71.2,

  // Jobs (system-wide)
  "running_jobs": 456,
  "pending_jobs": 89,
  "active_users": 92,

  // Login Nodes - Per-node tracking (2 nodes)
  "login_nodes": [
    {
      "node_name": "casper1",
      "available": true,
      "degraded": false,
      "user_count": 39,
      "load_1min": 3.2,
      "load_5min": 3.4,
      "load_15min": 3.6
    },
    {
      "node_name": "casper2",
      "available": true,
      "degraded": false,
      "user_count": 39,
      "load_1min": 3.1,
      "load_5min": 3.3,
      "load_15min": 3.5
    }
  ],

  // Node Type Breakdown
  "node_types": [
    {
      "node_type": "standard",
      "nodes_total": 114,
      "nodes_available": 78,
      "nodes_down": 1,
      "nodes_allocated": 35,
      "cores_per_node": 36,
      "memory_gb_per_node": 192,
      "gpu_model": null,
      "gpus_per_node": null,
      "utilization_percent": 65.2
    },
    {
      "node_type": "gpu-v100",
      "nodes_total": 64,
      "nodes_available": 42,
      "nodes_down": 2,
      "nodes_allocated": 20,
      "cores_per_node": 36,
      "memory_gb_per_node": 384,
      "gpu_model": "NVIDIA V100",
      "gpus_per_node": 4,
      "utilization_percent": 82.7
    }
  ],

  // Queue Breakdown
  "queues": [
    {
      "queue_name": "casper",
      "running_jobs": 298,
      "pending_jobs": 56,
      "active_users": 67,
      "cores_allocated": 8500,
      "nodes_allocated": 158
    }
  ]
}
```

**Casper Login Node Fields**:
- `node_name` (required): Hostname (e.g., "casper-login1", "casper-login2")
- `available` (required): Boolean - node is accessible
- `degraded` (optional): Boolean - node is up but degraded, defaults to false
- `user_count` (optional): Number of logged-in users
- `load_1min`, `load_5min`, `load_15min` (optional): Load averages

**Note**: Casper login nodes do not have a `node_type` field (unlike Derecho) since all Casper login nodes are the same type.

### Node Type Detection

```bash
# Get node types from sinfo
sinfo --noheader -o "%N,%C,%m,%G" | while read line; do
  node=$(echo $line | cut -d, -f1)
  # Determine type from node name or features
  case $node in
    casper-v100*) type="gpu-v100" ;;
    casper-a100*) type="gpu-a100" ;;
    casper-bigmem*) type="bigmem" ;;
    *) type="standard" ;;
  esac
  echo "$type,$line"
done
```

---

## JupyterHub Data Collector

### Endpoint
```
POST /api/v1/status/jupyterhub
```

### Data Format

```json
{
  "timestamp": "2025-11-25T14:30:00",  // Optional
  "available": true,                   // Required - service is up
  "active_users": 68,                  // Required - number of active users
  "active_sessions": 72,               // Required - number of running servers
  "cpu_utilization_percent": 45.2,    // Optional - aggregate CPU usage
  "memory_utilization_percent": 52.8  // Optional - aggregate memory usage
}
```

**JupyterHub Fields**:
- `available` (required): Boolean - service is accessible
- `active_users` (required): Number of users with active sessions
- `active_sessions` (required): Number of running Jupyter servers
- `cpu_utilization_percent` (optional): Aggregate CPU utilization across all sessions
- `memory_utilization_percent` (optional): Aggregate memory utilization across all sessions

### Data Collection

**JupyterHub API** (requires admin token):
```python
import requests

JUPYTERHUB_URL = "https://jupyterhub.ucar.edu"
ADMIN_TOKEN = os.getenv('JUPYTERHUB_ADMIN_TOKEN')

headers = {'Authorization': f'token {ADMIN_TOKEN}'}

# Get active users
response = requests.get(f"{JUPYTERHUB_URL}/hub/api/users", headers=headers)
users = response.json()
active_users = sum(1 for u in users if u.get('server'))

# Get server stats (requires custom endpoint or monitoring)
stats = requests.get(f"{JUPYTERHUB_URL}/hub/api/metrics", headers=headers)
```

---

## Outage Reporting

### Endpoint
```
POST /api/v1/status/outage
```

### Data Format

```json
{
  "system_name": "Derecho",             // Required: system name (e.g., "Derecho", "Casper", "JupyterHub")
  "title": "Brief outage description",  // Required: short summary
  "severity": "major",                  // Required: critical, major, minor, maintenance
  "status": "investigating",            // Required: investigating, identified, monitoring, resolved
  "component": "Login nodes",           // Optional: affected component (e.g., "login nodes", "queue", "filesystem")
  "description": "Detailed description of issue and impact",  // Optional: longer explanation
  "start_time": "2025-11-25T08:15:00",  // Optional: when outage started, defaults to now
  "end_time": "2025-11-25T10:30:00",    // Optional: when resolved (set when status=resolved)
  "estimated_resolution": "2025-11-25T18:00:00"  // Optional: ETA for resolution
}
```

**Outage Fields**:
- `system_name` (required): System name - "Derecho", "Casper", "JupyterHub", etc.
- `title` (required): Brief description (appears in dashboard alerts)
- `severity` (required): One of "critical", "major", "minor", "maintenance"
- `status` (required): One of "investigating", "identified", "monitoring", "resolved"
- `component` (optional): Affected component or subsystem
- `description` (optional): Detailed explanation of issue and impact
- `start_time` (optional): ISO 8601 timestamp, defaults to current time
- `end_time` (optional): ISO 8601 timestamp, set when outage is resolved
- `estimated_resolution` (optional): ISO 8601 timestamp for expected resolution

### Example: Automated Outage Detection

```python
def check_login_nodes():
    """Check if login nodes are responsive."""
    nodes = ['derecho1', 'derecho2', 'derecho3']
    down_nodes = []

    for node in nodes:
        result = subprocess.run(
            f'ssh -o ConnectTimeout=5 {node} "echo ok"',
            shell=True, capture_output=True
        )
        if result.returncode != 0:
            down_nodes.append(node)

    if down_nodes:
        # Report outage
        outage_data = {
            'system_name': 'derecho',
            'title': f'Login nodes unavailable: {", ".join(down_nodes)}',
            'severity': 'major' if len(down_nodes) > 1 else 'minor',
            'component': 'Login nodes',
            'status': 'investigating'
        }
        post_outage(outage_data)
```

---

## Error Handling

### API Response Codes

- **201 Created**: Success
  ```json
  {
    "success": true,
    "message": "Derecho status ingested successfully",
    "status_id": 12345,
    "timestamp": "2025-11-25T14:30:00",
    "login_node_ids": [101, 102, 103, 104, 105, 106, 107, 108],  // If login_nodes provided
    "queue_ids": [50, 51],        // If queues provided
    "filesystem_ids": [10, 11]    // If filesystems provided
  }
  ```

- **400 Bad Request**: Invalid data
  ```json
  {
    "error": "Missing required fields: cpu_nodes_total"
  }
  ```

- **401 Unauthorized**: Authentication failed
  ```json
  {
    "error": "Invalid credentials"
  }
  ```

- **403 Forbidden**: Insufficient permissions
  ```json
  {
    "error": "MANAGE_SYSTEM_STATUS permission required"
  }
  ```

- **500 Internal Server Error**: Database error
  ```json
  {
    "error": "Database error: ..."
  }
  ```

### Retry Logic

```python
import time

def post_with_retry(url, data, max_retries=3):
    """Post data with exponential backoff."""
    for attempt in range(max_retries):
        try:
            response = requests.post(url, json=data, auth=AUTH, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            if attempt == max_retries - 1:
                raise
            wait = 2 ** attempt  # 1s, 2s, 4s
            print(f"Retry {attempt + 1}/{max_retries} after {wait}s: {e}")
            time.sleep(wait)
```

---

## Monitoring & Logging

### Collector Health Checks

```bash
# Check last successful post
tail -n 1 /var/log/derecho_collector.log

# Alert if no success in last 15 minutes
if ! grep -q "✓ Posted" /var/log/derecho_collector.log | tail -n 3; then
  echo "Collector may be failing" | mail -s "Alert" admin@ucar.edu
fi
```

### Dashboard Staleness Detection

The dashboard shows "Last updated: timestamp" for each system. If data is stale (>10 minutes), consider:
1. Checking collector cron job is running
2. Checking network connectivity to SAM API
3. Checking API credentials are valid
4. Reviewing collector logs for errors

---

## Testing

### Test with Mock Data

```bash
# Test Derecho endpoint
curl -X POST http://localhost:5050/api/v1/status/derecho \
  -H "Content-Type: application/json" \
  -u username:password \
  -d @tests/mock_data/derecho_sample.json
```

### Validate Data

```python
# Verify data was stored
from system_status import create_status_engine
from system_status.models import DerechoStatus

engine, SessionLocal = create_status_engine()
session = SessionLocal()

latest = session.query(DerechoStatus).order_by(
    DerechoStatus.timestamp.desc()
).first()

print(f"Latest Derecho data: {latest.timestamp}")
print(f"CPU utilization: {latest.cpu_utilization_percent}%")
```

---

## Security Best Practices

1. **Credentials**:
   - Store in environment variables or secure config files (mode 600)
   - Rotate passwords regularly
   - Use service accounts (not personal accounts)

2. **Network**:
   - Use HTTPS in production
   - Consider IP whitelisting for API endpoints
   - Use VPN if collectors are on external networks

3. **Data Validation**:
   - Collectors should validate data before posting
   - Reject obviously invalid values (e.g., >100% utilization)
   - Log suspicious metrics for review

4. **Error Handling**:
   - Don't expose sensitive info in error messages
   - Log errors locally, not in API responses
   - Implement rate limiting to prevent abuse

---

## Example Project Structure

```
hpc-status-collectors/
├── README.md
├── requirements.txt          # requests, python-dotenv
├── .env.example             # Template for credentials
├── collectors/
│   ├── derecho_collector.py
│   ├── casper_collector.py
│   └── jupyterhub_collector.py
├── lib/
│   ├── api_client.py        # Shared API posting logic
│   ├── slurm_utils.py       # SLURM command wrappers
│   └── monitoring.py        # Health check utilities
├── config/
│   ├── derecho_queues.yaml  # Queue definitions
│   └── casper_nodes.yaml    # Node type specs
└── cron/
    ├── install_cron.sh
    └── crontab.template
```

---

## Support & Troubleshooting

### Common Issues

**"401 Unauthorized"**:
- Check username/password in .env
- Verify API credentials exist in database
- Confirm credentials have `MANAGE_SYSTEM_STATUS` permission

**"Missing required fields"**:
- Review API documentation for required fields
- Check JSON formatting (trailing commas, quotes)
- Validate data types (numbers vs strings)

**"Connection timeout"**:
- Check SAM API is running
- Verify network connectivity from HPC system
- Check firewall rules

**Stale data in dashboard**:
- Verify collector cron job is running: `crontab -l`
- Check collector logs for errors
- Test manual collection: `python collector.py`

### Getting Help

- API Documentation: `/api/v1/status/` (this guide)
- SAM Issues: https://github.com/ncar/sam/issues
- Contact: SAM development team

---

## Changelog

- **2025-11-25**:
  - Initial release (Phase 1)
  - Updated to per-node login tracking (breaking change)
    - Derecho: `login_nodes` array replaces aggregate `cpu_login_*` and `gpu_login_*` fields
    - Casper: `login_nodes` array replaces aggregate `login_nodes_available`, `login_nodes_total`, `login_total_users` fields
    - Each login node tracked individually with availability, load, and user count
  - Updated outage schema with new severity/status fields
  - JupyterHub schema updated with `active_sessions` field
- Future: Add WebSocket push API, real-time streaming
