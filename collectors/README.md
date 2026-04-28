# HPC Status Collectors

Python-based data collectors for Derecho and Casper HPC systems that gather metrics via PBS and post to the SAM Status Dashboard API.

## Features

- **Intelligent Node Classification**: Automatically detects node types from PBS `resources_available` fields (cpu_type, gpu_type, Qlist)
- **Comprehensive Metrics**: Nodes, queues, login nodes, filesystems, jobs, and utilization
- **Robust Error Handling**: Partial collection continues on individual component failure
- **Multiple Run Modes**: Dry-run, JSON-only, verbose logging
- **Simple Deployment**: While-loop runner for continuous collection (cron deployment documented in plan)

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Credentials

```bash
cp .env.example .env
# Edit .env with your API credentials
```

### 3. Test Collectors

```bash
# Derecho - dry run (collect but don't post)
./derecho/collector.py --dry-run --verbose

# Casper - dry run
./casper/collector.py --dry-run --verbose

# JSON output only (no API call)
./derecho/collector.py --json-only

# Post to API
./derecho/collector.py --verbose
./casper/collector.py --verbose
```

### 4. Run Continuously

```bash
# Simple while loop runner (5 minute intervals)
./run_collectors.sh

# Logs written to collectors/logs/
```

## Directory Structure

```
collectors/
├── README.md                    # This file
├── requirements.txt             # Python dependencies
├── pyproject.toml               # Python project configuration
├── .env                         # API credentials (create from .env.example)
├── .env.example                 # Credential template
├── run_collectors.sh            # Simple while-loop runner
│
├── lib/                         # Shared library (90% code reuse)
│   ├── base_collector.py        # Abstract base collector class
│   ├── pbs_client.py            # PBS command execution
│   ├── api_client.py            # SAM API client with retry logic
│   ├── config.py                # Configuration management
│   ├── logging_utils.py         # Logging setup
│   ├── ssh_utils.py             # SSH operations for login nodes
│   ├── parallel_ssh.py          # Parallel SSH execution
│   ├── exceptions.py            # Custom exceptions
│   └── parsers/                 # Data parsing modules
│       ├── nodes.py             # Intelligent node type classification
│       ├── jobs.py              # Job statistics
│       ├── queues.py            # Queue statistics
│       └── filesystems.py       # Filesystem usage
│
├── derecho/                     # Derecho-specific
│   ├── collector.py             # Main executable
│   └── config.yaml              # Derecho configuration
│
├── casper/                      # Casper-specific
│   ├── collector.py             # Main executable
│   └── config.yaml              # Casper configuration
│
├── jupyterhub/                  # JupyterHub-specific
│   ├── collector.py             # Main executable
│   └── config.yaml              # JupyterHub configuration
│
├── logs/                        # Log files (created automatically)
│   ├── derecho.log
│   └── casper.log
│
└── docs/                        # Documentation
    ├── PBS_COLLECTORS_PLAN.md   # Original implementation plan
    └── PBS_COLLECTORS_ADD_RESERVATIONS_PLAN.md # Reservation tracking plan
```

## Collector Output

### Derecho Metrics

- **System-level**:
  - CPU nodes (total, available, down, reserved)
  - GPU nodes (total, available, down, reserved)
  - CPU/GPU cores and utilization
  - Memory allocation and utilization
  - Running/pending jobs, active users

- **Per-queue**: running jobs, pending jobs, active users, resource allocation
- **Per-login-node**: availability, user count, load averages (with cpu/gpu type)
- **Per-filesystem**: capacity, used space, utilization

### Casper Metrics

- **System-level**: compute nodes (total, available, down), utilization
- **Per-node-type**: Intelligently classified by PBS data:
  - `htc` - HTC nodes (Qlist contains "htc")
  - `largemem` - Large memory nodes (Qlist contains "largemem")
  - `gpu-a100` - NVIDIA A100 GPUs (gpu_type contains "a100")
  - `gpu-h100` - NVIDIA H100 GPUs (gpu_type contains "h100")
  - `gpu-l40` - NVIDIA L40 GPUs (gpu_type contains "l40")
  - `gpu-gp100` - NVIDIA GP100 GPUs (gpu_type contains "gp100")
  - `gpu` - Other GPU nodes (V100, etc.)
  - `standard` - Standard compute nodes

  Each type includes: nodes (total/available/down/allocated), cores/memory/GPUs per node, utilization

- **Per-queue**: running jobs, pending jobs, active users, resource allocation
- **Per-login-node**: availability, user count, load averages
- **Per-filesystem**: capacity, used space, utilization

## Node Type Classification

The collectors intelligently infer node types from PBS `pbsnodes -aj -F json` output:

**Derecho**:
- CPU nodes: `ngpus` absent or 0
- GPU nodes: `ngpus` > 0, `gpu_type` indicates A100

**Casper**:
- Checks `Qlist` field for htc, largemem, nvgpu, vis queues
- Checks `gpu_type` field for specific GPU models
- Checks memory size for bigmem classification
- Falls back to hostname patterns if needed

**NO hardcoded node type configs required** - everything is inferred from live PBS data!

## Configuration

### Environment Variables (.env)

```bash
# API Configuration
STATUS_API_URL=http://localhost:5050
STATUS_API_USER=collector
STATUS_API_KEY=your_password

# Optional timeouts (seconds)
PBS_COMMAND_TIMEOUT=30
SSH_TIMEOUT=10
API_TIMEOUT=30
```

### System Configuration (config.yaml)

**Derecho** (`derecho/config.yaml`):
```yaml
system_name: derecho
pbs_host: derecho

login_nodes:
  - name: derecho1
    type: cpu
  # ... derecho2-8 ...

filesystems:
  - glade
  - campaign
  - derecho_scratch

queues:
  - main
  - cpu
  - gpu
  # ...
```

**Casper** (`casper/config.yaml`):
```yaml
system_name: casper
pbs_host: casper

login_nodes:
  - name: casper-login1
  - name: casper-login2

filesystems:
  - glade
  - campaign

queues:
  - casper
  - htc
  - nvgpu
  # ...
```

## Time Zones

**The collectors must run with `TZ=UTC` set in the environment.** The
project-wide convention (see `CLAUDE.md`) is naive-UTC for every
datetime stored in the database; everything downstream — the webapp's
display layer, charge-summary windowing, the chart x-axes —
assumes that. Python's `datetime.now()` returns the process-local
time, so a collector running on a host in MDT/MST without `TZ=UTC`
will write timestamps 6–7 hours behind UTC and recent records will
silently fall outside short read windows on the dashboard.

`run_collectors.sh` already does `export TZ=UTC` at the top, so the
loop runner is correct by default. **If you invoke a collector
directly (e.g. `./derecho/collector.py --once`), set `TZ=UTC` first**:

```bash
TZ=UTC ./derecho/collector.py --verbose
```

A systemd unit, cron entry, or any other runner needs the same:

```ini
[Service]
Environment=TZ=UTC
ExecStart=/path/to/run_collectors.sh
```

### PBS reservations

PBS reports reservation start/end times in the cluster's local time
with no zone marker (e.g. `Wed Nov 12 12:00:00 2025`).
`ReservationParser` anchors that to a configurable `cluster_tz`
(default `'America/Denver'` — both Derecho and Casper sit at
NCAR-Wyoming) and converts to naive-UTC at parse time, so the
storage convention holds even though PBS itself is TZ-blind. If
this code is ever deployed at a non-Mountain site, override
`cluster_tz` when calling `ReservationParser.parse_reservations(...)`.

The dashboard converts back to a configurable display TZ at render
time (`STATUS_DISPLAY_TZ`, also defaulting to `America/Denver`), so
operators see times in cluster-local even though storage is UTC.

## Command-Line Options

```bash
./derecho/collector.py [OPTIONS]
./casper/collector.py [OPTIONS]

Options:
  --dry-run          Collect data but do not post to API
  --json-only        Output JSON to stdout and exit (no API call)
  --verbose, -v      Enable verbose logging
  --log-file PATH    Log file path (default: stdout only)
```

## Testing

```bash
# Test with dry-run mode
./derecho/collector.py --dry-run --verbose

# Get JSON output for inspection
./derecho/collector.py --json-only | jq .

# Test posting to local API server
# (Start dev server first: ../../utils/run-webapp-dbg.sh)
./derecho/collector.py --verbose
```

## Troubleshooting

### Check Logs

```bash
tail -f logs/derecho.log
tail -f logs/casper.log
```

### Test PBS Commands

```bash
ssh derecho "pbsnodes -aj -F json" | jq . | head -100
ssh casper "qstat -f -F json" | jq . | head -100
```

### Test API Endpoint

```bash
curl -X POST http://localhost:5050/api/v1/status/derecho \
  -H "Content-Type: application/json" \
  -d '{"cpu_nodes_total": 1}'
```

### Common Issues

1. **Authentication errors**: Check `.env` credentials
2. **SSH timeouts**: Increase `PBS_COMMAND_TIMEOUT` / `SSH_TIMEOUT` in `.env`
3. **Duplicate filesystem entries**: Handled automatically - only unique mountpoints tracked
4. **JSON parse errors**: Check PBS command output for format changes

## Next Steps (Deferred)

The following are documented in `docs/PBS_COLLECTORS_PLAN.md` but deferred for future implementation:

- Cron-based deployment
- Log rotation (logrotate)
- Health check monitoring
- Deployment automation (Makefile)
- Unit and integration tests

## Architecture

See `docs/PBS_COLLECTORS_PLAN.md` for detailed architecture documentation including:
- Data flow diagrams
- Error handling strategy
- API endpoint schemas
- Deployment procedures
- Monitoring & maintenance

## License

NCAR/CISL internal use.
