# Documentation Index

Complete index of all documentation in the SAM Queries project.

## 🚀 Getting Started

- **[README.md](../README.md)** - Project overview and quick start
- **[SETUP_SUMMARY.md](SETUP_SUMMARY.md)** - Quick reference for setup (3-step guide)
- **[GETTING_STARTED.md](GETTING_STARTED.md)** - Onboarding + technology primer
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

- **[AUTHENTICATION.md](AUTHENTICATION.md)** - Authentication & SSO guide
  - How users sign in (UX walkthrough for non-engineers)
  - Technical OIDC flow with Microsoft Entra
  - Per-environment deployment matrix (local, staging, k8s)
  - Operations: secret rotation, troubleshooting, common failures
  - Security model and future plans
  - Glossary of terms

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

## 🚢 Deployment

- **[CIRRUS_PUBLISHING.md](CIRRUS_PUBLISHING.md)** - CIRRUS publishing & deployment
  - How `main` → image → `cirrus` branch → k8s rollout flows
  - GitHub App + ruleset that lock the `cirrus` branch
  - Operating the workflow (manual dispatch, audit, negative test)
  - Failure modes and rollback

- **[README-k8s.md](README-k8s.md)** - Kubernetes/Helm deployment guide
  (local Docker Desktop + CIRRUS production; see also [helm/README.md](../helm/README.md))
- **[k8s.md](k8s.md)** - kubectl / OIDC cheat sheet for the nwc1 cluster

- **[STAGING.md](STAGING.md)** - AWS ECS staging environment
  - Separate from CIRRUS — runs on ECS/RDS for VPN-gated test access

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

- **[TESTING.md](TESTING.md)** - Testing guide
  - Suite size, tiers, and timings (single source of truth)
  - Isolation model (SAVEPOINT rollback, mysql-test container)
  - Writing tests (fixtures vs factories)

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
- **Understand login / SSO?** → [AUTHENTICATION.md](AUTHENTICATION.md)
- **Rotate the OIDC client secret?** → [AUTHENTICATION.md#operations](AUTHENTICATION.md#operations)
- **Switch databases?** → [DATABASE_SWITCHING.md](DATABASE_SWITCHING.md)
- **Use setup scripts?** → [SCRIPTS.md](SCRIPTS.md) or [scripts/setup/README.md](../scripts/setup/README.md)
- **Fix Docker issues?** → [DOCKER_TROUBLESHOOTING.md](DOCKER_TROUBLESHOOTING.md)
- **Start webapp?** → [WEBAPP_SETUP.md](WEBAPP_SETUP.md)
- **Understand ORM?** → [CLAUDE.md](../CLAUDE.md)
- **Write tests?** → [TESTING.md](TESTING.md)
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
├── GETTING_STARTED.md         # Onboarding + technology primer
├── LOCAL_SETUP.md             # Complete setup guide
├── CREDENTIALS.md             # Credential configuration
├── AUTHENTICATION.md          # OIDC/SSO flow + local auth modes
├── DATABASE_SWITCHING.md      # Database switching guide
├── TESTING.md                 # Test suite guide (counts live here)
├── SCRIPTS.md                 # Script reference
├── DOCKER_TROUBLESHOOTING.md  # Docker issues
├── WEBAPP_SETUP.md            # Webapp setup
├── CIRRUS_PUBLISHING.md       # Image build + CIRRUS GitOps deploy
├── README-k8s.md / k8s.md     # Kubernetes deployment + cheat sheet
├── STAGING.md                 # AWS ECS staging environment
├── apis/
│   ├── SYSTEMS_INTEGRATION_APIs.md   # Directory access, project access, fairshare tree
│   ├── CHARGING_INTEGRATION.md       # HPC charge ingest integration
│   └── HPC_DATA_COLLECTORS_GUIDE.md  # Collector implementation guide
├── plans/                     # Active plans (implemented/ holds shipped ones)
└── presentations/             # Quarto → pptx presentation infra

../
├── README.md                  # Project overview
├── CONTRIBUTING.md            # Development guide
├── CLAUDE.md                  # Technical reference (agent-facing)
├── src/webapp/README.md       # Web UI & API docs
├── src/cli/README.md          # CLI architecture guide
└── helm/README.md             # Kubernetes chart overview
```

## 🆘 Need Help?

1. Check this index for the right document
2. Use search in your editor to find specific topics
3. Check troubleshooting sections in relevant docs
4. Review code examples in test files
5. Contact CISL USS team for access/credentials
