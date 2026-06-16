# Scripts Directory

Organized scripts for SAM Queries project setup, maintenance, and utilities.

## Directory Structure

```
scripts/
├── README.md                    # This file
├── cirrus_healthcheck.sh        # CIRRUS/k8s health probe (samuel release)
├── cirrus_weblog_audit.sh       # CIRRUS/k8s traffic + rate-limit + abuse audit
├── zap_probe_docker.sh          # Dockerized OWASP ZAP scan of the webapp
├── lib/                         # Sourceable shell helpers (see below)
│   ├── common.sh               # Generic: colors, log/verdict helpers, usage
│   ├── cirrus_common.sh        # CIRRUS layer: release names, KCTL, arg parse
│   └── prereqs.sh              # Dependency checks (require_cmd, check_docker…)
├── setup/                       # Setup and utility scripts
│   ├── README.md               # Setup scripts documentation
│   ├── switch_to_production_db.sh
│   ├── switch_to_local_db.sh
│   ├── check_docker.sh
│   ├── fix_mysql_permissions.sh
│   └── download_backup.sh
├── infra/                       # AWS infrastructure scripts
│   ├── ssh-staging.sh          # SSH into staging ECS container
│   ├── query-staging-db.sh     # Connect to staging RDS MySQL
│   └── deploy-staging.sh       # Manual staging deployment
├── gen_api_key.py              # Generate API key + bcrypt hash for collector auth
├── setup_status_db.py          # System status database setup
├── test_status_db.py           # System status database testing
├── cleanup_status_data.py      # System status data cleanup
├── ingest_mock_status.py       # Mock status data ingestion
└── create_status_db.sql        # System status database creation SQL
```

## Script Categories

### Cluster Operations (CIRRUS / `nwc1`)

Read-only operator tools for the public `samuel` release on the `nwc1`
cluster. All use the same idioms: colored PASS/WARN/FAIL output, exit codes
`0` (all pass) / `1` (≥1 warn) / `2` (≥1 fail), and the shared `--no-color`,
`-n/--namespace`, `-r/--release`, `--context`, `-v/--verbose`, `-h/--help`
flags.

- **`cirrus_healthcheck.sh`** — "is the cluster healthy?" 11-section probe of
  the Helm release: pods, rollout safety, Redis, resource usage, ingress/TLS,
  edge security headers, ExternalSecrets, the in-pod health endpoint, recent
  logs, and events.

- **`cirrus_weblog_audit.sh`** — "who's hitting the public site, and is anything
  abusive getting through?" Harvests the webapp's stdout (and the Redis
  `ratelimit:events` set) and reports traffic volume + status mix, top talkers,
  vulnerability-probe path signatures, rate-limit (429) offenders, and
  auth/CSRF failures. Built for the public-exposure cutover.

  ```bash
  scripts/cirrus_weblog_audit.sh --since 6h          # last 6 hours
  scripts/cirrus_weblog_audit.sh --since 24h --top 25 -v
  ```

  Its `--help` also lists hardening recommendations (R1–R5: edge rate limiting,
  ProxyFix/X-Forwarded-For, scheduling, CSP reporting, durable log shipping)
  that the audit surfaces but does not enforce.

- **`zap_probe_docker.sh`** — Dockerized OWASP ZAP passive/active scan of the
  webapp (local throwaway target by default). See its `--help`.

### Shared Library (`lib/`)

Sourceable helpers so the cluster scripts stop duplicating boilerplate. Two
layers:

- **`lib/common.sh`** — generic, no Kubernetes knowledge: `setup_colors`
  (TTY/`NO_COLOR`-aware), plain log primitives (`info`/`ok`/`die`), verdict
  primitives with PASS/WARN/FAIL counters (`section`/`pass`/`warn`/`fail`/`run`)
  + `verdict_exit`, `usage_from_header`, and `repo_paths`.
- **`lib/cirrus_common.sh`** — the CIRRUS layer (sources `common.sh`): baked-in
  release/object names, `build_kctl` (KCTL/KCTL_NS arrays), `handle_common_arg`
  (shared flag parsing), and K8s resource-unit converters.

`cirrus_healthcheck.sh` and `cirrus_weblog_audit.sh` source
`cirrus_common.sh`; `zap_probe_docker.sh` sources only `common.sh`.
`lib/prereqs.sh` (`require_cmd`, `check_vpn`, `check_docker`, `check_aws_cli`)
remains the dependency-check helper used by the setup/infra scripts.

### Setup Scripts (`setup/`)

Utility scripts for database setup, switching, and troubleshooting:

- **Database Switching:** Switch between local and production databases
- **Troubleshooting:** Docker diagnostics, MySQL permissions fixes
- **Git LFS:** Download database backup files

See [setup/README.md](setup/README.md) for detailed documentation.

### Infrastructure Scripts (`infra/`)

AWS staging environment management:

- `ssh-staging.sh` - SSH into staging ECS container (via ECS Exec)
- `query-staging-db.sh` - Connect to staging RDS MySQL (requires VPN)
- `deploy-staging.sh` - Manual build and deploy to staging

See [infra scripts documentation](../docs/SCRIPTS.md#infrastructure-scripts-scriptsinfra) for details.

### API Key Management

- `gen_api_key.py` - Generate a new API key and its bcrypt hash for machine-to-machine auth (e.g., HPC status collectors)

```bash
# Generate a new key for the default 'collector' username
python scripts/gen_api_key.py

# Generate for a named service
python scripts/gen_api_key.py --username my_service

# Output:
#   API Key  → set as STATUS_API_KEY in collectors/.env
#   Hash     → add to API_KEYS dict in src/webapp/config.py
```

Run this whenever you create or rotate collector credentials.

### System Status Scripts

Python scripts for managing the `system_status` database:

- `setup_status_db.py` - Create system status database tables
- `test_status_db.py` - Test system status database connection
- `cleanup_status_data.py` - Clean up old status snapshots
- `ingest_mock_status.py` - Ingest mock status data for testing
- `create_status_db.sql` - SQL script for database creation

## Main Entry Point Scripts

These scripts remain in the project root for easy access:

- `install.sh` - Bootstrap script (clones repo, tested in CI)
- `install_local.sh` - Main installation script (Python environment)
- `setup_local_db.sh` - Main database setup script
- `test_database.sh` - Main testing script

**Why in root:** These are primary workflow entry points, frequently used, and `install.sh` is designed for `curl | bash` execution.

## Usage

### Setup Utilities

```bash
# Switch databases
./scripts/setup/switch_to_production_db.sh
./scripts/setup/switch_to_local_db.sh

# Troubleshooting
./scripts/setup/check_docker.sh
./scripts/setup/fix_mysql_permissions.sh

# Git LFS
./scripts/setup/download_backup.sh
```

### System Status Scripts

```bash
# Setup system status database
python scripts/setup_status_db.py

# Test connection
python scripts/test_status_db.py

# Cleanup old data
python scripts/cleanup_status_data.py
```

## See Also

- **[setup/README.md](setup/README.md)** - Setup utility scripts
- **[../docs/SCRIPTS.md](../docs/SCRIPTS.md)** - Complete script reference
- **[../docs/STAGING.md](../docs/STAGING.md)** - Staging environment guide
- **[../infrastructure/README.md](../infrastructure/README.md)** - Infrastructure documentation
- **[../docs/LOCAL_SETUP.md](../docs/LOCAL_SETUP.md)** - Setup guide
