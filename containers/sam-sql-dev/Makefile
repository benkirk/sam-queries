# full bash login shell requied for our complex make rules
#SHELL := /bin/bash --login

COMPOSE_FILE := ../../compose.yaml

.PHONY: all docker clone clean bootstrap db-up db-test db-down db-restart

all: clone

clone:
	@python3 bootstrap_clone.py
	@docker compose -f $(COMPOSE_FILE) exec -T mysql  sh -c 'mysql -uroot -proot -e "USE sam; TRUNCATE TABLE api_credentials;"'

clean:
	rm -rf dump/
	docker rm -f local-sam-mysql || true

%.sql.xz:
	mkdir -p $$(dirname $@)
	rm -f $@
	docker compose -f $(COMPOSE_FILE) exec -T mysql sh -c 'exec mysqldump --all-databases --lock-all-tables --triggers --routines --events -uroot -p"root"' | xz > $@

bootstrap:
	@echo "Starting clean: removing container and volume (persistent storage)..."
	@$(MAKE) clean >/dev/null
	@echo "Creating a local, subsetted database..."
	@$(MAKE) clone
	@echo "Backing up subsetted database..."
	@$(MAKE) backups/sam-local-dev.sql.xz
	@echo "Anonymizing (some warnings OK)..."
	@./run_anonymization_workflow.sh || true
	@echo "Backing up obfuscates database..."
	@$(MAKE) backups/sam-obfuscated.sql.xz

db-up:
	docker compose -f $(COMPOSE_FILE) up -d mysql
	@echo "Waiting for healthy status..."
	@timeout 300 bash -c 'until docker compose -f $(COMPOSE_FILE) ps | grep -q "healthy"; do sleep 5; done'
	@echo "✅ MySQL ready!"

db-test: db-up
	source ../../etc/config_env.sh && python3 check_username_leak.py
	source ../../etc/config_env.sh && python3 test_username_anonymization.py

db-down:
	docker compose -f $(COMPOSE_FILE) down -v

db-restart:
	@$(MAKE) db-down
	@$(MAKE) db-up
