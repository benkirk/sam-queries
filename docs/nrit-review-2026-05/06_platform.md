# Phase 6 — Platform / Cross-cutting

> The largest single phase. Everything that spans subsystems: install/deploy paths, CI/CD, secrets, supply chain, observability. The "deployment smell" that kicked off this audit lives here.

## Scope

### Install / deploy paths
- `Makefile`, `install.sh`, `install_local.sh`, `setup_local_db.sh`, `test_database.sh`
- `scripts/setup/switch_to_{local,production}_db.sh`
- `compose.yaml`, `conda-env.yaml`, `pyproject.toml`
- `containers/webapp/Dockerfile`, `containers/collectors/Dockerfile`, `containers/sam-sql-dev/`
- `helm/Chart.yaml`, `helm/values.yaml`, `helm/values-local.yaml`, `helm/local-secrets.sh`, `helm/templates/*.yaml`, `helm/tests/test-oidc-render.sh`
- `infrastructure/staging/` (Terraform: ECS, RDS, ALB, security groups)
- `etc/config_env.sh`

### CI/CD
- 10 workflows under `.github/workflows/`: `sam-ci-docker.yaml`, `sam-ci-conda_make.yaml`, `test-install.yaml`, `ci-staging.yaml`, `build-images-cirrus-deploy.yaml`, `deploy-staging.yaml`, `mega-linter.yaml`, `clean-ghcr.yaml`, `cron-clean-action-log.yaml`, `manually-clean-action-log.yaml`
- `.pre-commit-config.yaml`, `.mega-linter.yml`, `.trivy.yaml`, `.trivyignore`, `.cspell.json`, `.jscpd.json`

### Secrets / supply chain
- `.env.example`, `helm/local-secrets.sh`, `helm/templates/external_secret.yaml`
- `scripts/gen_api_key.py`
- `pyproject.toml`, `conda-env.yaml`, `collectors/requirements.txt`
- `containers/sam-sql-dev/.gitattributes` (LFS tracking)

### Observability
- `src/webapp/logging_config.py`, `src/webapp/run.py:158-185`
- `src/webapp/audit/logger.py`
- `src/webapp/api/v1/health.py`

## Method

Four parallel deep-dives along independent verticals:

1. **CI/CD workflows** — triggers, secrets exposure, action pinning, fail-open behavior, deploy gating, TruffleHog placement, build provenance.
2. **Deployment surfaces** — image artifact across 3 environments, helm chart structure, ExternalSecret integration, resource limits/scaling, Redis as stateful dep, ECS/Terraform staging, ingress/TLS.
3. **Secrets + supply chain** — `.env.example` completeness, helm local-secrets, ExternalSecret backend, gen_api_key cryptography, committed-secret scan, rotation procedures, Python dep pinning, Trivy posture, LFS, pre-commit + mega-linter.
4. **Install paths + observability** — `install.sh` curl|bash, `install_local.sh`, `Makefile`, DB switch scripts, `etc/config_env.sh`, structured logs, metrics, error ingestion, incident detection.

## Lenses applied

- Architecture (deployment topology)
- Security (secrets + supply chain — primary)
- Operability (primary)
- Testing (CI fitness)

---

## Findings

### Headline

Phase 6 is **the largest haul of the audit by finding count, and the most operationally consequential.** The architecture is competent across all four verticals — separation of three environments (Compose / ECS / k8s) with a shared Dockerfile target, three-tier secret injection (Docker `.env` / SSM / OpenBao), Helm chart that templates cleanly, CI that gates deploys behind TruffleHog. But the operating posture has the same shape as Phase 5's collector findings: **when things go wrong, ops won't know.**

The standout findings:

- **Helm webapp Deployment has zero liveness/readiness probes** despite the webapp exposing well-designed `/api/v1/health/{live,ready,db}` endpoints (and `run.py` even special-cases logging for them). On CIRRUS k8s, an unhealthy pod is never restarted. The infrastructure is built; it's just not connected.
- **No error ingestion** anywhere — no Sentry/Rollbar/equivalent. A 500 in production produces a stderr line that lands in CloudWatch (staging) or pod logs (prod) and stops there. `DESIGN.md` lists this as a TODO and the TODO is still open.
- **No metrics** — no Prometheus client, no `/metrics`, no histograms. The only "metric" is `run.py:180-184`'s hardcoded `elapsed_ms > 5000` warning.
- **Audit log written to ephemeral container path with no shipping** (`/var/log/sam/model_audit.log` inside the container). ECS Fargate writes to writable layer, destroyed on redeploy. The audit log Phase 2 admired is non-durable in practice.
- **3 High-severity CI/CD findings** — `trufflesecurity/trufflehog@main` (branch pin = supply-chain risk on the deploy path), `deploy-staging.yaml` uses long-lived AWS keys with no environment-gate / no required reviewers, `update-helm` force-pushes the prod-image refs to `cirrus` branch without server-side protection visible.
- **Staging RDS is `publicly_accessible=true`** with `skip_final_snapshot=true` — mitigated only by the UCAR `128.117.0.0/16` security group CIDR, but defense-in-depth-wise the staging DB should not be in public subnets at all.
- **Webapp gunicorn runs as root** in all three environments (no `USER` directive in `containers/webapp/Dockerfile`, no `securityContext: runAsNonRoot` in `deployment.yaml`).
- **0 committed secrets** (clean scan). **No Python lockfile anywhere** (no `poetry.lock` / `conda-lock.yml` / pinned `requirements.txt`); ~24 unpinned deps in root `pyproject.toml`.
- **No documented rotation procedure** for `STATUS_API_KEY` or `JUPYTERHUB_API_TOKEN`. OIDC rotation is best-in-class (`docs/AUTHENTICATION.md:322-360`); other secrets are operator-tribal-knowledge.

### CI / CD

**High**

- **CI1 [High] `trufflesecurity/trufflehog@main` is the only secrets gate on the deploy path** (`deploy-staging.yaml:38`, `ci-staging.yaml:34`). Pinning to `@main` means a TruffleHog repo compromise executes attacker code with `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` on the next push to `staging`. The skip-CI exclusion CLAUDE.md documents (TruffleHog runs unconditionally) is moot if the action itself is mutable. Fix: pin to release tag or SHA.

- **CI2 [High] `deploy-staging.yaml` uses long-lived AWS access keys with no approval gate.** (`deploy-staging.yaml:56-57`) No `environment:` gating, no required reviewers, no `permissions:` block. Every push to `staging` → build → ECS deploy in <15 min, no human in the loop. Fix: OIDC + IAM role-to-assume (`aws-actions/configure-aws-credentials@v4` + `permissions: id-token: write`) + protected `environment: staging` with reviewers.

- **CI3 [High] `update-helm` job force-pushes `cirrus` deploy branch** (`build-images-cirrus-deploy.yaml:294-301`). `git push origin cirrus --force` from `github-actions[bot]` is the production CIRRUS deploy trigger; the commit message includes `[skip ci]` and the branch has no visible server-side protection. Compromise of any contributor PAT lands prod-image refs on the deploy branch without review.

**Medium**

- **CI4 [Med] No third-party action SHA-pinned.** All 30+ `uses:` lines are tag-pinned (`@v4`, `@v8`, `@main`). First-party (`actions/*`) tag-pinning is acceptable but `peter-evans/create-pull-request@v7`, `stefanzweifel/git-auto-commit-action@v5`, `yanovation/delete-old-actions@v1`, `oxsecurity/megalinter/flavors/cupcake@v8`, `conda-incubator/setup-miniconda@v3`, TruffleHog are all third-party. Fix: SHA-pin third-party at minimum; add Dependabot for actions.

- **CI5 [Med] `BENKIRK_GITHUB_TOKEN` is a personal PAT in 3 maintenance workflows** (`clean-ghcr.yaml:29`, `cron-clean-action-log.yaml:17`, `manually-clean-action-log.yaml:19`). Bus factor 1: when Ben rotates / leaves CISL, log cleanup + GHCR pruning silently stop. `clean-ghcr.yaml` even has a fallback to `GITHUB_TOKEN` that should be the default. Pairs with the cron-from-Glade finding in Phase 5 — both single-points-of-failure on `benkirk`.

- **CI6 [Med] No `permissions:` block on `deploy-staging`, `sam-ci-docker`, `sam-ci-conda_make`, `ci-staging`, `test-install`.** Jobs inherit org/repo default `GITHUB_TOKEN` permissions. If the default is legacy "write all," CI has more authority than needed. Fix: `permissions: contents: read` at workflow top.

- **CI7 [Med] `mega-linter.yaml` grants `contents:write, issues:write, pull-requests:write`** but `APPLY_FIXES: none` means none of the apply-fixes blocks fire. Dormant code = misconfig surface. Delete until needed.

- **CI8 [Med] `DISABLE_ERRORS: true` makes mega-linter non-blocking** (`.mega-linter.yml:29`). KICS only fails on HIGH+ (`.mega-linter.yml:36`). Combined with `.trivy.yaml`'s HIGH/CRITICAL gate, the project is **blind to MEDIUM-severity IaC + dep findings by configuration**.

**Low / Informational**

- **CI9 [Low] `paths-ignore: ['docs/**', '**.md']`** on 4 CI workflows including `ci-staging.yaml` (the secret-scan job). A doc-only PR could carry a leaked credential and merge cleanly. Drop `paths-ignore` from the secret-scan path.
- **CI10 [Low] `yanovation/delete-old-actions@v1`** — low-star community action with `actions: write` scope. Tiny attack surface.

**Strengths (CI)**
- **Concurrency groups everywhere** — every CI workflow has `concurrency: group: ...; cancel-in-progress: true`. No double-runs.
- **Skip-CI semantics implemented as explicit `if:` checks** (not GitHub's native, which isn't trigger-universal). Asymmetric and documented in CLAUDE.md.
- **`fail-fast: false` on build matrix** — one image's failure doesn't block others.
- **`permissions:` blocks where they matter most** (`build-images-cirrus-deploy.yaml:129, 273`).
- **Health-gated install smoke test** (`test-install.yaml`) exercises the user-facing `curl|bash` install against fresh + update branches.
- **Build provenance wired correctly** — `GIT_SHA` + `BUILD_DATE` passed as build args; `containers/webapp/Dockerfile:36-39` places them after `pip install` so layer caching survives.

### Deployment

**High**

- **D1 [High] Helm webapp Deployment has no probes whatsoever** (`helm/templates/deployment.yaml:103`) — no `livenessProbe`, no `readinessProbe`, no `startupProbe`. Redis sibling has both (`redis-deployment.yaml:41-50`), Compose probes `/api/v1/health/ready` (`compose.yaml:72`), ECS target group probes `/api/v1/health/` (`alb.tf:21`). **On CIRRUS k8s, an unhealthy webapp is never restarted, and rolling updates serve traffic before gunicorn workers are ready.** The endpoints exist (`src/webapp/api/v1/health.py:4-7`); they just aren't wired up. Same fail-open pattern as the [XC: prod-config-hardening] theme.

- **D2 [High] Staging RDS is `publicly_accessible=true`** (`infrastructure/staging/rds.tf:26`) with `skip_final_snapshot=true` (`:29`). Mitigated by `security_groups.tf:71-77` UCAR CIDR `128.117.0.0/16`, but a public-IP RDS instance is a defense-in-depth regression vs the ECS tasks (private subnets). `skip_final_snapshot=true` discards data on `terraform destroy` despite `deletion_protection`.

**Medium**

- **D3 [Med] Image tag `:main` + `imagePullPolicy: IfNotPresent` is a non-updating combo** (`helm/values.yaml:31`, `deployment.yaml:22`). CI workflow `build-images-cirrus-deploy.yaml:284-301` rewrites this to `sha-<short>` on the `cirrus` branch — so prod is mutable-tagged on `main`, immutable-tagged on `cirrus`. Anyone deploying from `main` gets stale cache.

- **D4 [Med] ECS task pinned to `:latest`** (`infrastructure/staging/ecs.tf:30`) while `deploy-staging.yaml:79-80` pushes both `:<sha>` and `:latest`. The deploy workflow overrides via `amazon-ecs-render-task-definition`, but `lifecycle.ignore_changes = [task_definition]` (`ecs.tf:111`) means a future `terraform apply` will drift back to `:latest`. Two sources of truth.

- **D5 [Med] No HPA, no PDB, no `topologySpreadConstraints`.** `replicaCount: 2` hardcoded (`values.yaml:1`). Voluntary disruption (node drain) on 2 replicas can take both down. Phase 2's rate-limiter `memory://` fallback is mitigated *only* because Redis is wired — there's no autoscaling story for the webapp itself.

- **D6 [Med] Webapp container runs as root.** `containers/webapp/Dockerfile` has no `USER` directive; `gunicorn` runs as UID 0 in all 3 environments. No `securityContext: runAsNonRoot: true` in `deployment.yaml`. Same pattern as Phase 5's collector Dockerfile.

- **D7 [Med] Bcrypt-hashed API key committed in `values.yaml`** (`helm/values.yaml:61`) — `API_KEYS_COLLECTOR: "$2b$12$..."`. Defensible (it's a hash, not a key) but inconsistent with the OIDC pattern (everything else via ExternalSecret). Rotating this key requires chart commit + redeploy.

**Low**

- **D8 [Low] `local-secrets.sh` consumes `${SAM_DB_USERNAME}` unguarded** (`helm/local-secrets.sh:32-33`) — errors with `unbound variable` (`set -u` at line 9) if `.env` omits it. Other vars use `${VAR:-default}` (line 25). Inconsistent.
- **D9 [Low] Collector deployment not in Helm chart.** `containers/collectors/Dockerfile` built and pushed to GHCR by `build-images-cirrus-deploy.yaml`, but no CronJob/Deployment in `helm/templates/`. Confirms Phase 5's "two parallel models" finding: the container is built but the actual prod collector path is `collectors/cron_scripts/crontab` on Glade.
- **D10 [Low] `Chart.yaml` version stuck at `0.0.1`** (`helm/Chart.yaml:5-6`). Never bumped; loses chart-level diffing.
- **D11 [Low] `helm/tests/test-oidc-render.sh` not invoked by CI.** The `dev-only-insecure-key` guard at `:75` is a strong protection — but neither `deploy-staging.yaml` nor `build-images-cirrus-deploy.yaml` runs it. Guardrail exists, not enforced.

**Strengths (Deploy)**
- **Three-tier secret injection is well-thought-out and tested** — `external_secret.yaml` cleanly maps 4 OpenBao paths; `helm/tests/test-oidc-render.sh` asserts the prod/local rendering contract.
- **`docs/README-k8s.md` is unusually good operator documentation** — local-vs-prod matrix at lines 117-129 + per-env auth/OIDC matrix at 209-217. The kind of runbook on-call needs.
- **Gunicorn worker sizing is cgroup-aware** (`deployment.yaml:29-34`) — computes `workers = 2·cpu_limit + 1` from chart value rather than letting `multiprocessing.cpu_count()` see the node's 64 cores.
- **Compose + ECS + k8s share one Dockerfile target** — `containers/webapp/Dockerfile` stage `production` is the artifact in all three environments. True single-source-of-truth at build layer.

### Secrets + Supply chain

**Medium**

- **SS1 [Med] No documented rotation for `STATUS_API_KEY` or `JUPYTERHUB_API_TOKEN`.** AUTHENTICATION.md §Operations covers Entra OIDC client_secret rotation thoroughly. `FLASK_SECRET_KEY` is per-env. **No** documented procedure for: STATUS_API_KEY pair rotation (where the new hash flows through compose/Helm/SSM and the corresponding collector .env), JUPYTERHUB_API_TOKEN rotation, or leak response for `API_KEYS_*`. Phase 5 raised the JH token cleartext-handling concern; rotation cadence is the other half.

- **SS2 [Med] Python dependency pinning is loose, no lockfile.** Root `pyproject.toml:22-49` has **24 unpinned deps** (`flask`, `sqlalchemy`, `pymysql`, `cryptography`, `requests`, `bcrypt` — all unpinned). Only `authlib>=1.3.0,<2.0.0` and `redis>=5.0` have any bound. **No `poetry.lock` / `conda-lock.yml` / `requirements.lock` / pip-tools output anywhere.** Tomorrow's CI image and yesterday's CI image can diverge silently. `collectors/requirements.txt` is loose-bounded.

**Low / Informational**

- **SS3 [Low] `.env.example` incomplete vs code** — ~30 env vars consumed by the code aren't enumerated (`FLASK_CONFIG`, `FLASK_ENV`, `CACHE_REDIS_URL`, `RATELIMIT_*` × 8, `ALLOCATION_USAGE_CACHE_*`, `AUDIT_ENABLED`, `AUDIT_LOG_PATH`, `LOG_LEVEL`, `LOG_FILE`, `STATUS_DISPLAY_TZ`, `GUNICORN_WORKERS`, etc.). None secrets, but the "what knobs exist" gap is real.
- **SS4 [Low] `helm/local-secrets.sh` silently defaults to `root/root`** for DB creds — missing env still produces a working k8s Secret. No guardrail asserting `kubectl context` matches `docker-desktop`.
- **SS5 [Low] `.trivyignore` is bounded but immortal** — 6 entries (`.trivyignore:1-17`), all Dockerfile AVD rules with rationale comments but no expiry. `AVD-DS-0002` (non-root USER) globally ignored is the one worth revisiting — pairs with D6.
- **SS6 [Low] `conda-env.yaml` is fully unpinned** — only `postgresql=18.*` pinned. `mysql`, `python`, `mysql-connector-python`, `gh`, `pipdeptree` all float.

**Strengths (Secrets/SC)**
- **0 committed secrets in the scan.** Clean.
- **Defense-in-depth secret scanning** — TruffleHog (CI + deploy), GitGuardian (pre-push), `detect-private-key` (pre-commit). Three independent layers (modulo CI1's `@main` pin).
- **OIDC rotation procedure is best-in-class** (`docs/AUTHENTICATION.md:322-360`) — the kind of runbook every secret should have.
- **`gen_api_key.py` cryptography is sound** — `secrets.token_urlsafe(32)` CSPRNG, bcrypt rounds=12 default, allows `--rounds 14` for prod hardening.
- **Three-secret-store discipline for prod**: AWS SSM (Fargate), OpenBao (k8s), Compose env (local). No cross-pollination.
- **LFS for the dev DB dump** — 133-byte pointer committed; obfuscation pipeline documented in `ANONYMIZATION_PROCESS.md`. `backups/.gitignore` correctly excludes real-data local snapshots.
- **Pre-commit hooks pinned to tagged versions** — includes `detect-private-key`, `no-commit-to-branch` (main/staging), `ggshield` at pre-push.

### Install paths

**Medium**

- **I1 [Med] DB switch scripts silently no-op against the canonical `.env`.** `scripts/setup/switch_to_local_db.sh:30-33` patches `s/^#SAM_DB_USERNAME=root/.../`, but `.env.example:43-46` ships LOCAL as `#SAM_DB_USERNAME=${LOCAL_SAM_DB_USERNAME}` (variable-indirection form). A developer who copied `.env.example` and runs `switch_to_local_db.sh` gets no error and no change to LOCAL lines — the sed patterns target a legacy raw-value format. The script does succeed at commenting out prod/staging blocks, leaving `.env` with no `SAM_DB_*` set at all.

- **I2 [Med] `install_local.sh` is a thin wrapper around `make conda-env` that duplicates and diverges** — emits its own messaging, hardcodes incoherent Apple Silicon advice (`install_local.sh:19`: "Intel x86 64-bit (or Apple Silicon if M1/M2/M3)"), ends with a suggestion to run the broken `switch_to_production_db.sh`. `install.sh` doesn't call this; the two install scripts have no clear demarcation.

- **I3 [Med] `install.sh` does not pin or verify what it pulls.** `install.sh:21` defaults to `REPO_BRANCH=main` (moving target); `:189` does `git clone --branch main` with no SHA pin or signature verification. README documents `curl … | bash` style installs. No checksum, no signed-commit verification; `.env` cp at `:204` doesn't `chmod 600`. **Strength inline:** in-place detection at `:86-103` is genuinely thoughtful.

- **I4 [Med] `etc/config_env.sh` has no `set -e`/`set -u` and `make` runs unconditionally on every source.** Run on every shell session per CLAUDE.md; `:35` shells out to `make --silent -C ${ROOT_DIR} conda-env` unconditionally. No strict-mode pragma; `exit 1` paths (`:43-63`) kill the user's terminal session because the file is sourced.

- **I5 [Med] `setup_local_db.sh:90` runs `docker volume rm sam-queries_samuel-mysql-data` unconditionally** if the container is in any non-healthy state. A developer with local data they care about loses it silently. The healthy-container check at `:68-76` is good; the failure path is destructive without confirmation.

**Low**

- **I6 [Low] Setup-doc cluster overlap (matches Phase 1 finding).** 8 files describing overlapping subsets of "how to install": SETUP_SUMMARY, LOCAL_SETUP, GETTING_STARTED, SCRIPTS, SCRIPT_ORGANIZATION, DATABASE_SWITCHING, CREDENTIALS, WEBAPP_SETUP. Combined with I1's drift, docs and code haven't been cross-checked recently.
- **I7 [Low] `Makefile fixperms` uses NCAR-specific group ACLs** (`Makefile:65-76`) — `setfacl -m g:csgteam:r .env` won't exist on a fresh laptop. Documented `make help` text doesn't warn this target is server-only.

### Observability

**High**

- **O1 [High] No error ingestion path for unhandled exceptions.** Only `404`, `400`, `401`, `429` errorhandlers exist; no `@app.errorhandler(500)` or `@app.errorhandler(Exception)`. Unhandled exceptions land in CloudWatch (staging) via gunicorn stderr or pod logs (prod). `DESIGN.md:422` lists "Set up monitoring (Sentry, New Relic, etc.)" as an open item — gap is real and current.

- **O2 [High] Audit log written to ephemeral container path with no shipping.** `RotatingFileHandler('/var/log/sam/model_audit.log', maxBytes=10MB, backupCount=5)`. In ECS Fargate this writes to the container's writable layer — restarts/redeploys lose unrotated lines, only stdout reaches CloudWatch. The audit log Phase 2 admired is **non-durable in practice**. This is the operational realization of Phase 2's Q11.

**Medium**

- **O3 [Med] No structured logging.** Human format only (`logging_config.py:22-25`: `'%(asctime)s %(levelname)-8s %(name)s — %(message)s'`). CloudWatch Insights / Loki structured queries impossible.

- **O4 [Med] `request_id` ends at the first log line.** `run.py:161` mints `g.request_id`, embeds `rid=…` in exactly one log line (`:176`). Slow-request warning at `:181-184` doesn't include `rid`, so a slow request in CloudWatch can't be correlated to the summary. Grep confirms zero downstream code reads `g.request_id`.

- **O5 [Med] No metrics endpoint or backend.** No `prometheus_client` in deps, no `/metrics` route. Only "metric" is `run.py:180-184`'s hardcoded `elapsed_ms > 5000` warning. Hardcoded threshold; no histogram, no percentile counter. ECS Container Insights gives CPU/memory but nothing app-level.

- **O6 [Med] Healthcheck-failure path is invisible.** `run.py:170-179` suppresses logs for health probes returning <400. A failing healthcheck appears as a normal `INFO 503` line — same severity, same channel. No CloudWatch alarms in `ecs.tf`. A DB outage produces nothing humans see.

**Strengths (Observability)**
- **Health endpoint design** (`src/webapp/api/v1/health.py:4-7`) cleanly separates `/live` (no DB call) from `/ready` (DB ping). Code is correct — just unused by Helm (D1).
- **Healthcheck noise suppression** in `run.py:169-179` is sensible.
- **Audit log handler is rotating + excludes ApiCredentials + system_status writes** — design sound; only storage location and lack of shipping let it down.

---

## Cross-cutting tags raised

- `[XC: prod-config-hardening]` — D1 (no probes despite endpoints existing), D6 (root container), CI1 (TruffleHog `@main`), CI2 (long-lived AWS keys, no approval), SS5 (`AVD-DS-0002` non-root ignore). Now **14 footguns** following the same "fall back / fall open / accept the easier path" pattern. Phase 2 had 5; Phase 5 added 4; Phase 6 adds 5 more.
- `[XC: ops]` — Phase 6 is the biggest ops-finding concentration: no probes, no metrics, no error ingestion, audit log non-durable, healthcheck failures invisible, structured logs absent, `request_id` doesn't propagate. **The system is operationally observable only via log-grep, and the log destination is partly ephemeral.**
- `[XC: prod-config-hardening] + [XC: ops] combined:** *the system is built defensively where it counts (RBAC, audit, retry, span coalescing, OIDC rotation) but configured to fail open at the boundaries that ops would notice.*
- `[XC: testing]` — Mega-linter non-blocking (CI8); test-oidc-render.sh exists but not invoked by CI (D11); MEDIUM-severity IaC + dep findings filtered out by configuration (Trivy + KICS).
- `[XC: docs-drift]` — DB switch scripts target obsolete `.env` format (I1), `install_local.sh` Apple Silicon advice incoherent (I2), 8 setup docs with overlap (I6), `helm/values.yaml:31` `:main` tag misleading vs CI's `:sha-<short>` rewrite on `cirrus` (D3).
- `[XC: bus-factor]` — *new theme.* `BENKIRK_GITHUB_TOKEN` is a personal PAT in 3 maintenance workflows (CI5); collectors run from `/glade/work/benkirk/repos/...` per Phase 5 (P1-37); `helm/values.yaml:31` image is `ghcr.io/benkirk/sam-queries/webapp:main`. Three independent single-points-of-failure on `benkirk`. Worth a roll-up.

## Open questions for Ben

1. **CIRRUS k8s probes (D1)** — was the missing `livenessProbe`/`readinessProbe` intentional (ingress does its own?) or oversight? The endpoints exist and are well-designed.
2. **Long-lived AWS keys (CI2)** — why not OIDC + IAM role-to-assume? Migration is ~1 hour. Any CISL-side constraint (AWS account doesn't trust GitHub's OIDC issuer)?
3. **`cirrus` branch protection (CI3)** — is it protected server-side? The force-push works either because protection is off or `github-actions[bot]` bypasses it.
4. **`BENKIRK_GITHUB_TOKEN` ownership (CI5)** — if you're hit by a bus, does anyone else have the PAT scopes to keep GHCR/log cleanup running?
5. **Staging RDS `publicly_accessible=true` (D2)** — intentional (for VPN-based queries via `query-staging-db.sh`)? If so, worth a comment in `rds.tf`; if not, moving to private subnets is a 5-line change.
6. **Image tag in CIRRUS today** — `:main` (per `values.yaml`) or the CI-rewritten `:sha-<short>` on the `cirrus` branch? Determines whether D3 is a doc fix or a real issue.
7. **Collector deployment plan (D9 + Phase 5 Q29)** — container CronJob via Helm, or stay on Glade cron? The container is built and published but never used in Helm.
8. **Lockfile policy (SS2)** — deliberate reason no `conda-lock.yml` or `pip-compile` artifact in-repo? Even a manually-regenerated `requirements/` snapshot would help.
9. **`STATUS_API_KEY` rotation runbook (SS1)** — would you welcome an issue spec'd up to mirror the AUTHENTICATION.md OIDC pattern for the collector key pair + JH token?
10. **TruffleHog `@main` (CI1)** — any objection to SHA-pinning with Renovate/Dependabot to keep it current?
11. **`switch_to_local_db.sh` sed mismatch (I1)** — is the canonical `.env` in current developer use actually based on `.env.example`, or do you maintain a different template internally that uses raw `SAM_DB_USERNAME=root` lines?
12. **Staging error ingestion (O1)** — CloudWatch catches gunicorn stderr; is anyone alerting on `ERROR`-level lines, or is it diagnostic-on-demand?
13. **Audit log durability (O2)** — is the ephemeral `/var/log/sam/model_audit.log` accepted (stdout-via-CloudWatch covers most needs) or worth fixing? Pairs with Phase 2 Q11.
