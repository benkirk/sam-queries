# Documentation Index

Complete index of all documentation in the SAM Queries project.

## 🚀 Getting Started

- **[README.md](../README.md)** - Project overview and quick start
- **[SETUP_SUMMARY.md](SETUP_SUMMARY.md)** - Quick reference for setup (3-step guide)
- **[LOCAL_SETUP.md](LOCAL_SETUP.md)** - Complete local development setup guide
  - Prerequisites
  - Step-by-step installation
  - Troubleshooting
  - Daily usage

## 🔐 Configuration

- **[CREDENTIALS.md](CREDENTIALS.md)** - Credential configuration guide
  - Database credentials (production and local)
  - GitHub Personal Access Token
  - AWS credentials
  - Security best practices

- **[DATABASE_SWITCHING.md](DATABASE_SWITCHING.md)** - Switching between databases
  - Local vs production
  - Switch scripts
  - What works with read-only access

## 🛠️ Tools & Scripts

- **[SCRIPTS.md](SCRIPTS.md)** - Setup script reference
  - Essential scripts
  - Utility scripts
  - Script dependencies
  - Error handling

- **[DOCKER_TROUBLESHOOTING.md](DOCKER_TROUBLESHOOTING.md)** - Docker issues and solutions
  - Permission denied errors
  - Container issues
  - Socket problems

- **[WEBAPP_SETUP.md](WEBAPP_SETUP.md)** - Web application setup
  - Starting the webapp
  - Development mode
  - Troubleshooting

## 💻 Development

- **[CONTRIBUTING.md](../CONTRIBUTING.md)** - Development guide
  - Code style
  - Testing
  - Git workflow
  - Best practices

- **[CLAUDE.md](../CLAUDE.md)** - Technical reference
  - ORM models
  - Database patterns
  - API schemas
  - Query examples
  - Known issues

- **[src/webapp/README.md](../src/webapp/README.md)** - Web UI and REST API
  - API endpoints
  - Authentication
  - Role-based access control

- **[tests/docs/README.md](../tests/docs/README.md)** - Testing guide
  - Running tests
  - Writing tests
  - Coverage

## 🔌 API Reference

- **[apis/SYSTEMS_INTEGRATION_APIs.md](apis/SYSTEMS_INTEGRATION_APIs.md)** —
  Directory access, project access & fairshare tree APIs (LDAP provisioning + PBS scheduler integration)
- **[apis/CHARGING_INTEGRATION.md](apis/CHARGING_INTEGRATION.md)** —
  HPC charge ingest integration
- **[src/webapp/README.md](../src/webapp/README.md)** — Full REST API endpoint reference

## 📚 Quick Reference

### Setup Flow

```
1. ./install_local.sh          → Install Python environment
2. ./setup_local_db.sh          → Set up local database
3. ./test_database.sh           → Test installation
4. Edit .env with credentials   → Add production credentials (optional)
5. source etc/config_env.sh      → Activate environment
```

### Common Commands

```bash
# Environment
source etc/config_env.sh

# Database
docker compose up -d mysql
docker compose down mysql

# Testing
pytest tests/ --no-cov

# CLI
sam-search user benkirk
sam-search project SCSG0001

# Switch databases
./scripts/setup/switch_to_production_db.sh
./scripts/setup/switch_to_local_db.sh
```

## 🔍 Finding What You Need

### "How do I..."

- **Set up locally?** → [LOCAL_SETUP.md](LOCAL_SETUP.md)
- **Configure credentials?** → [CREDENTIALS.md](CREDENTIALS.md)
- **Switch databases?** → [DATABASE_SWITCHING.md](DATABASE_SWITCHING.md)
- **Use setup scripts?** → [SCRIPTS.md](SCRIPTS.md) or [scripts/setup/README.md](../scripts/setup/README.md)
- **Fix Docker issues?** → [DOCKER_TROUBLESHOOTING.md](DOCKER_TROUBLESHOOTING.md)
- **Start webapp?** → [WEBAPP_SETUP.md](WEBAPP_SETUP.md)
- **Understand ORM?** → [CLAUDE.md](../CLAUDE.md)
- **Write tests?** → [tests/docs/README.md](../tests/docs/README.md)
- **Use the API?** → [src/webapp/README.md](../src/webapp/README.md)
- **Use systems integration APIs?** → [apis/SYSTEMS_INTEGRATION_APIs.md](apis/SYSTEMS_INTEGRATION_APIs.md)

### "I'm getting..."

- **Permission denied (Docker)** → [DOCKER_TROUBLESHOOTING.md](DOCKER_TROUBLESHOOTING.md)
- **Database connection error** → [LOCAL_SETUP.md](LOCAL_SETUP.md#common-issues--solutions)
- **Unknown database 'sam'** → [LOCAL_SETUP.md](LOCAL_SETUP.md#common-issues--solutions)
- **Access denied (production)** → [DATABASE_SWITCHING.md](DATABASE_SWITCHING.md#troubleshooting)
- **Git LFS pointer** → [LOCAL_SETUP.md](LOCAL_SETUP.md#common-issues--solutions)

## 📖 Documentation Structure

```
docs/
├── INDEX.md                    # This file
├── SETUP_SUMMARY.md           # Quick reference
├── LOCAL_SETUP.md             # Complete setup guide
├── CREDENTIALS.md             # Credential configuration
├── DATABASE_SWITCHING.md      # Database switching guide
├── SCRIPTS.md                 # Script reference
├── DOCKER_TROUBLESHOOTING.md  # Docker issues
├── WEBAPP_SETUP.md            # Webapp setup
└── apis/
    ├── SYSTEMS_INTEGRATION_APIs.md   # Directory access, project access, fairshare tree
    └── CHARGING_INTEGRATION.md       # HPC charge ingest integration

../
├── README.md                  # Project overview
├── CONTRIBUTING.md            # Development guide
├── CLAUDE.md                  # Technical reference
├── src/webapp/README.md       # Web UI & API docs
└── tests/docs/README.md       # Testing guide
```

## 🆘 Need Help?

1. Check this index for the right document
2. Use search in your editor to find specific topics
3. Check troubleshooting sections in relevant docs
4. Review code examples in test files
5. Contact CISL USS team for access/credentials
