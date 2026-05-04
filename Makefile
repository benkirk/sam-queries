.ONESHELL:
SHELL := /bin/bash
CONDA_ROOT := $(shell conda info --base)

# Common way to initialize environment across various types of systems
config_env := module load conda >/dev/null 2>&1 || true && . $(CONDA_ROOT)/etc/profile.d/conda.sh

.PHONY: help clean clobber distclean fixperms check perf check-db-vs-orms docker-build docker-up docker-down docker-restart \
        migrate-status-current migrate-status-up migrate-status-down migrate-status-history migrate-status-revision migrate-status-stamp-head

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
	@echo -e "\033[1mPattern rules:\033[0m"
	@echo -e "  \033[32mmake <name>\033[0m              Create conda environment from <name>.yaml"
	@echo -e "  \033[32mmake solve-<name>\033[0m        Dry-run solve for <name>.yaml (no install)"
	@echo ""
	@echo -e "\033[1mExample:\033[0m"
	@echo -e "  \033[33mmake conda-env\033[0m           Create conda environment from conda-env.yaml"
	@echo ""

clean: ## Remove temporary files (*~)
	rm *~

clobber: ## Git clean except .env and conda-env/
	git clean -xdf --exclude ".env" --exclude "conda-env/"

distclean: ## Clean everything including conda-env/
	$(MAKE) clobber
	rm -rf conda-env/

%.py : %.ipynb Makefile ## Convert Jupyter notebook to Python script
	jupyter nbconvert --clear-output $<
	jupyter nbconvert --to=python $< >/dev/null
	chmod +x $@
	git add $@ $<

%: %.yaml pyproject.toml ## Create conda environment from YAML file
	[ -d $@ ] && mv $@ $@.old && rm -rf $@.old & \
	$(config_env) && \
	conda env create --file $< --prefix $@ && \
	conda activate ./$@ && \
	pip install -e ".[test]" && \
	pip install 'hpc-usage-queries[postgres] @ git+https://github.com/benkirk/hpc-usage-queries.git' && \
	pipdeptree --all 2>/dev/null || true

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

docker-build: ## Build docker containers
	@docker compose build

docker-up: ## Start docker containers
	@docker compose up --detach
	@echo "Waiting for healthy status..."
	@timeout 300 bash -c 'until docker compose ps | grep -q "healthy"; do sleep 5; done'
	@echo "✅ Containers ready!"

docker-down: ## Stop docker containers
	docker compose down

docker-restart: ## Restart docker containers
	@$(MAKE) docker-down
	@$(MAKE) docker-build
	@$(MAKE) docker-up

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
