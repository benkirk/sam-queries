# SAM Database Development Environment

Tools for creating and managing a local development copy of the SAM (System for Allocation Management) database.

## Directory Contents

### Core Scripts

| Script | Purpose |
|--------|---------|
| `bootstrap_clone.py` | Clone remote database to local MySQL (with sampling) |
| `anonymize_sam_db.py` | Anonymize sensitive data for development use |
| `preview_anonymization.py` | Preview anonymization transformations |
| `verify_anonymization.py` | Verify anonymization completed successfully |
| `cleanup_orphans.py` | Remove orphaned foreign key references |

### Workflows

| Script | Purpose |
|--------|---------|
| `run_anonymization_workflow.sh` | Complete guided anonymization workflow |
| `docker_start.sh` | Start local MySQL container |

### Configuration

- `config.yaml` - Database connection settings and anonymization options
- `docker-compose.yml` - Local MySQL container definition

## Documentation

- **[ANONYMIZATION_PROCESS.md](ANONYMIZATION_PROCESS.md)** - Complete technical guide to database anonymization

## Quick Start

### 1. Start Local MySQL Container
```bash
./docker_start.sh
```

### 2. Clone Remote Database (Optional)
```bash
python3 bootstrap_clone.py --config config.yaml
```

### 3. Anonymize Database
```bash
./run_anonymization_workflow.sh
```

Or manually:
```bash
# Preview transformations
python3 preview_anonymization.py

# Dry run (no changes)
python3 anonymize_sam_db.py --config config.yaml --dry-run

# Execute anonymization
python3 anonymize_sam_db.py --config config.yaml

# Verify results
python3 verify_anonymization.py
```

## Prerequisites

**Required:**
- Docker (for local MySQL)
- Python 3.8+
- Python packages: `sqlalchemy`, `pymysql`, `pyyaml`

**Install dependencies:**
```bash
pip install sqlalchemy pymysql pyyaml
```

## Database Connection

**Local MySQL** (via Docker):
```bash
mysql -u root -h 127.0.0.1 -proot sam
```

**Configuration** in `config.yaml`:
```yaml
local:
  host: localhost
  port: 3306
  user: root
  password: root
  database: sam
```

## Use Cases

1. **Development database** - Safe local copy for application development
2. **Testing** - Run integration tests against realistic data
3. **Debugging** - Investigate issues without accessing production
4. **Demonstrations** - Show features with anonymized data
5. **Schema exploration** - Understand database structure

## Security Note

Anonymized databases should not contain real PII. However:
- Preserved usernames (configured in `config.yaml`) retain real data
- Always verify no sensitive data remains before sharing
- Never use production credentials in development environments

## Support

For detailed information on:
- Anonymization process → See `ANONYMIZATION_PROCESS.md`
- Database cloning → See `config.yaml` comments
- Configuration options → See `config.yaml`
- Troubleshooting → See `ANONYMIZATION_PROCESS.md` § Troubleshooting
