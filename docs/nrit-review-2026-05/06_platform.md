# Phase 6 — Platform / Cross-cutting

> Everything that spans subsystems: install/deploy paths, CI/CD, secrets, supply chain, observability. The deployment smell that kicked off this review lives here.

## Scope

### Install / deploy paths
- `Makefile` (conda env + pip install)
- `install.sh` (curl|bash bootstrap, clones repo)
- `install_local.sh`, `setup_local_db.sh`, `test_database.sh`
- `scripts/setup/` (switch_to_{local,production}_db.sh, etc.)
- `compose.yaml` (webapp, webdev, mysql, mysql-test)
- `conda-env.yaml`, `pyproject.toml`
- `containers/` (Dockerfiles)
- `helm/` (Chart + values + local-secrets.sh)
- `infrastructure/` (staging configs, scripts)

### CI/CD
- `.github/workflows/` — 10 workflows
- `.github/scripts/`
- `.pre-commit-config.yaml`
- `.mega-linter.yml`, `.trivy.yaml`, `.trivyignore`, `.cspell.json`, `.jscpd.json`

### Secrets
- `.env.example`, helm `local-secrets.sh`
- Where secrets live in each environment (local, staging, k8s)
- Rotation procedures (per AUTHENTICATION.md "Operations")

### Supply chain
- Trivy posture, `.trivyignore` (what's intentionally ignored)
- Pinned vs. floating deps in `pyproject.toml` + `conda-env.yaml`
- LFS-tracked binary backup

### Observability
- Logging strategy (`src/webapp/logging_config.py`)
- Metrics / dashboards (if any)
- How a prod incident is detected

## Method

1. Trace every install path end-to-end on paper. Mark overlap / redundancy / dead paths.
2. Trace one CI run end-to-end (e.g., `sam-ci-docker.yaml`).
3. Trace staging deploy (`deploy-staging.yaml`) and image build (`build-images-cirrus-deploy.yaml`).
4. Trace secret flow: dev → staging → k8s.
5. Trivy scan: what's flagged, what's ignored, why.
6. Logging: structured? Levels? Where do they ship?

## Lenses applied

- Architecture (deployment topology)
- Security (secrets + supply chain — primary)
- Operability (primary)
- Testing (CI fitness)

## Findings

### Install/deploy paths

*TBD*

### CI/CD

*TBD*

### Secrets

*TBD*

### Supply chain

*TBD*

### Observability

*TBD*

## Cross-cutting tags raised

*TBD*

## Open questions for Ben

*TBD*
