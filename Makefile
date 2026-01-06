.ONESHELL:
SHELL := /bin/bash
CONDA_ROOT := $(shell conda info --base)

# Common way to initialize environment across various types of systems
config_env := module load conda >/dev/null 2>&1 || true && . $(CONDA_ROOT)/etc/profile.d/conda.sh

.PHONY: help clean clobber distclean fixperms check docker-build docker-up docker-down docker-restart

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
	$(config_env) && source etc/config_env.sh && python3 tests/tools/orm_inventory.py
	$(config_env) && source etc/config_env.sh && python3 -m pytest -v -n auto

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
