.ONESHELL:
SHELL := /bin/bash
CONDA_ROOT := $(shell conda info --base)

# Common way to initialize environment across various types of systems
config_env := module load conda >/dev/null 2>&1 || true && . $(CONDA_ROOT)/etc/profile.d/conda.sh

.PHONY: help clean clobber distclean fixperms check perf check-db-vs-orms docker-build docker-up docker-down docker-restart docker-watch docker-pytest \
        conda-env prune-old-envs print-env-hash migrate-legacy-env \
        migrate-status-current migrate-status-up migrate-status-down migrate-status-history migrate-status-revision migrate-status-stamp-head

# -------------------------------------------------------------------
# Conda env: content-addressed hashed dir + ./conda-env symlink swap.
# See etc/config_env.sh — the user-facing entry point sources this rule.
#
# HPC_USAGE_QUERIES_REF is the branch / tag / sha of hpc-usage-queries
# to pip install (mirrors compose.yaml + containers/webapp/Dockerfile).
# It is part of the hash, so changing it triggers a rebuild into a new
# ./conda-env-<sha>/ and an atomic symlink swap.
# -------------------------------------------------------------------
HPC_USAGE_QUERIES_REF ?= main

# 12-char content hash of: env spec + python deps + hpc ref.
# Drives both the build dir name and the cache-hit decision.
ENV_HASH := $(shell { cat conda-env.yaml pyproject.toml; \
                      echo "HPC_USAGE_QUERIES_REF=$(HPC_USAGE_QUERIES_REF)"; \
                    } | shasum -a 256 | cut -c1-12)
ENV_PREFIX := conda-env-$(ENV_HASH)
ENV_STAMP  := $(ENV_PREFIX)/.sam-env-ready

# -------------------------------------------------------------------
# Alembic — system_status database
# -------------------------------------------------------------------
ALEMBIC_STATUS := alembic -c migrations/system_status/alembic.ini

# -------------------------------------------------------------------
# Default target: help
# -------------------------------------------------------------------
help: ## Show this help message
	@echo ""
	@echo -e "\033[1;36mSAM Queries - Makefile Help\033[0m"
	@echo -e "\033[1;36m============================\033[0m"
	@echo ""
	@echo -e "\033[1mAvailable targets:\033[0m"
	@echo ""
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[32m%-20s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo -e "\033[1mConda env:\033[0m"
	@echo -e "  \033[32mmake conda-env\033[0m           Build (if needed) ./conda-env-<hash>, symlink ./conda-env"
	@echo -e "  \033[32mmake solve-<name>\033[0m        Dry-run solve for <name>.yaml (no install)"
	@echo -e "  \033[32mmake print-env-hash\033[0m      Show the hash that drives env caching (debug)"
	@echo ""
	@echo -e "\033[1mExample (override hpc-usage-queries ref, triggers rebuild):\033[0m"
	@echo -e "  \033[33mHPC_USAGE_QUERIES_REF=my-branch source etc/config_env.sh\033[0m"
	@echo ""

clean: ## Remove temporary files (*~)
	rm *~

clobber: ## Git clean except .env and conda-env* (symlink + hashed dirs)
	git clean -xdf --exclude ".env" --exclude "conda-env" --exclude "conda-env-*"

distclean: ## Clean everything including the conda-env symlink and all hashed envs
	$(MAKE) clobber
	rm -rf conda-env conda-env-*

%.py : %.ipynb Makefile ## Convert Jupyter notebook to Python script
	jupyter nbconvert --clear-output $<
	jupyter nbconvert --to=python $< >/dev/null
	chmod +x $@
	git add $@ $<

conda-env: migrate-legacy-env $(ENV_STAMP) ## Build (if needed) and symlink ./conda-env -> ./conda-env-<hash>
	@# Swap + prune inlined here. We deliberately do NOT recursively
	@# invoke make for the prune, because any recipe line referencing the
	@# special MAKE variable is force-executed even under dry-run, which
	@# would clobber the symlink during `make -n`.
	@current=$$(readlink conda-env 2>/dev/null || true); \
	if [ "$$current" != "$(ENV_PREFIX)" ]; then \
	    ln -snfv $(ENV_PREFIX) conda-env; \
	    ( ls -1dt conda-env-* 2>/dev/null \
	        | grep -v "^$(ENV_PREFIX)$$" \
	        | tail -n +2 \
	        | xargs -r rm -rf ) & \
	fi

# Pre-refactor checkouts have ./conda-env as a real directory rather than a
# symlink. Rename it aside (one-shot) so the symlink-based flow can take
# over. We use a `.legacy.<ts>` suffix (NOT matching conda-env-*) so
# prune-old-envs never touches it — the user can rm it manually once happy.
migrate-legacy-env:
	@if [ -d conda-env ] && [ ! -L conda-env ]; then \
	    legacy="conda-env.legacy.$$(date +%Y%m%d%H%M%S)"; \
	    echo ">>> Migrating pre-refactor real dir ./conda-env -> ./$$legacy"; \
	    echo ">>> (safe to 'rm -rf ./$$legacy' once the new env is verified)"; \
	    mv conda-env "$$legacy"; \
	fi

# The stamp file is content-addressed (its path encodes the hash), so it
# has NO mtime-based prereqs. A `touch conda-env.yaml` doesn't change the
# hash → stamp still present → no rebuild. A real edit (new dep, ref
# change) produces a different hash → new ENV_PREFIX → stamp absent →
# rebuild. The `rm -rf` guards against a half-built dir from a prior
# interrupted run that shares this hash.
$(ENV_STAMP):
	@echo ">>> Building $(ENV_PREFIX) (hpc-usage-queries ref: $(HPC_USAGE_QUERIES_REF))"
	@rm -rf $(ENV_PREFIX)
	$(config_env) && \
	    conda env create --file conda-env.yaml --prefix $(ENV_PREFIX) && \
	    conda activate ./$(ENV_PREFIX) && \
	    pip install -e ".[test]" && \
	    pip install "hpc-usage-queries[postgres] @ git+https://github.com/benkirk/hpc-usage-queries.git@$(HPC_USAGE_QUERIES_REF)" && \
	    (pipdeptree --all 2>/dev/null || true)
	@touch $(ENV_STAMP)

prune-old-envs: ## Keep current + most-recent-previous conda-env-*; remove older
	@current=$$(readlink conda-env 2>/dev/null); \
	ls -1dt conda-env-* 2>/dev/null \
	    | grep -v "^$$current$$" \
	    | tail -n +2 \
	    | xargs -r rm -rf

print-env-hash: ## Print the computed ENV_HASH / ENV_PREFIX (debug)
	@echo "HPC_USAGE_QUERIES_REF=$(HPC_USAGE_QUERIES_REF)"
	@echo "ENV_HASH=$(ENV_HASH)"
	@echo "ENV_PREFIX=$(ENV_PREFIX)"
	@echo "ENV_STAMP=$(ENV_STAMP)"

solve-%: %.yaml ## Dry-run solve for conda environment
	$(config_env) && conda env create --file $< --prefix $@ --dry-run

fixperms: ## Fix file permissions for .env
	for file in .env; do \
	  setfacl --remove-all $${file} ; \
	  for group in csgteam csg hdt ssg; do \
	    setfacl -m g:$${group}:r $${file} ; \
	  done ;\
	  for user in bdobbins; do \
	    getent passwd $${user} 2>&1 >/dev/null || continue ; \
	    setfacl -m u:$${user}:r $${file} ; \
	  done ;\
	  getfacl $${file} ;\
	done

check: ## Run tests
	$(config_env) && source etc/config_env.sh && python3 scripts/orm_inventory.py
	$(config_env) && source etc/config_env.sh && python3 -m pytest -v -n auto

perf: ## Run perf regression + benchmark suite (serial)
	$(config_env) && source etc/config_env.sh && \
	    python3 -m pytest -m perf -n 0 -v

check-db-vs-orms: ## Audit prod DB schema vs ORM models — runs check_db_drift + orm_inventory (skips if PROD_* env unset / VPN unreachable)
	$(config_env) && source etc/config_env.sh && \
	    python3 scripts/check_db_drift.py

validate_user_proj_usage: ## Reconcile + benchmark get_user_proj_usage against local docker MySQL (overrides STATUS_DB_DRIVER/SERVER for host access)
	$(config_env) && source etc/config_env.sh && \
	    STATUS_DB_DRIVER=mysql STATUS_DB_SERVER=127.0.0.1 \
	    python3 scripts/validate_user_proj_usage.py

docker-build: ## Build docker containers
	@docker compose build

docker-up: ## Start docker containers (waits until every service reports healthy)
	@# `--wait` blocks until every service with a healthcheck is healthy and
	@# exits non-zero if any becomes unhealthy. Replaces the older
	@# `grep -q healthy` loop, which returned as soon as the first container
	@# (usually cache, in 5s) reported healthy — well before mysql had
	@# finished restoring the backup.
	@docker compose --profile test up --detach --wait
	@echo "✅ Containers ready!"

docker-down: ## Stop docker containers
	docker compose --profile test down

docker-restart: ## Rebuild and restart docker containers
	@$(MAKE) docker-down
	@$(MAKE) docker-build
	@$(MAKE) docker-up

docker-watch: ## Live-sync host → /code in webdev (foreground; Ctrl-C to stop)
	@# Runs `docker compose watch` against an already-up stack so the syncer
	@# stays in the foreground without blocking other rules. `docker-up`
	@# brings the stack up first if it isn't already, and `--wait` ensures
	@# we don't start syncing into containers that are still booting.
	@$(MAKE) docker-up
	@echo "👀 Watching for source changes — Ctrl-C to stop"
	@docker compose watch

docker-pytest: ## Run pytest with coverage inside the webapp container against mysql-test (parity with CI)
	@# Brings up the `test` profile, which adds the isolated `mysql-test`
	@# service on its own volume + port (see compose.yaml). `--wait` gates on
	@# every service being healthy — including mysql-test verifying both
	@# `sam` and `system_status` are queryable — so pytest never starts
	@# against a half-restored DB. Mirrors `.github/workflows/sam-ci-docker.yaml`.
	@docker compose --profile test up --detach --wait
	@docker compose exec -T \
	    -e SAM_TEST_DB_URL='mysql+pymysql://root:root@mysql-test:3306/sam' \
	    webapp bash -c "cd /code && pytest --cov=src --cov-report=term-missing --cov-report=html"
	@docker compose cp webapp:/code/htmlcov ./htmlcov >/dev/null 2>&1 || echo "⚠️  No coverage report copied"
	@echo "📊 HTML coverage report: ./htmlcov/index.html"

# -------------------------------------------------------------------
# Alembic — system_status database (per-bind env)
# -------------------------------------------------------------------
migrate-status-current: ## Show current alembic revision (system_status)
	$(config_env) && source etc/config_env.sh && $(ALEMBIC_STATUS) current

migrate-status-up: ## Upgrade system_status DB to head
	$(config_env) && source etc/config_env.sh && $(ALEMBIC_STATUS) upgrade head

migrate-status-down: ## Downgrade system_status DB by one revision
	$(config_env) && source etc/config_env.sh && $(ALEMBIC_STATUS) downgrade -1

migrate-status-history: ## Show system_status revision history
	$(config_env) && source etc/config_env.sh && $(ALEMBIC_STATUS) history --verbose

migrate-status-revision: ## Autogenerate a new system_status revision (use MSG="…")
	@if [ -z "$(MSG)" ]; then echo 'usage: make migrate-status-revision MSG="describe change"'; exit 2; fi
	$(config_env) && source etc/config_env.sh && $(ALEMBIC_STATUS) revision --autogenerate -m "$(MSG)"

migrate-status-stamp-head: ## Stamp existing system_status DB at head without running DDL (prod bootstrap)
	$(config_env) && source etc/config_env.sh && $(ALEMBIC_STATUS) stamp head
