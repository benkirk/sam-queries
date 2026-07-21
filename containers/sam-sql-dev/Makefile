# full bash login shell requied for our complex make rules
#SHELL := /bin/bash --login

COMPOSE_FILE := ../../compose.yaml

.PHONY: all docker clone clean bootstrap db-up db-test db-down db-restart

all: clone

clone:
	@python bootstrap_clone.py

clean:
	rm -rf dump/ *.sql.xz *~

# system_status tables holding real identities/usage from local collector runs.
# anonymize_sam_db.py only scrubs the `sam` database, so these must be dumped
# schema-only or the "obfuscated" blob leaks real usernames + per-user activity.
SENSITIVE_STATUS_TABLES := status_users project_codes user_proj_queue_status

# The committed blob: everything, EXCEPT the sensitive status tables which are
# appended schema-only (restore keeps working — FK checks are disabled inside
# mysqldump output, and the mysql-test healthcheck only needs the DB present).
backups/sam-obfuscated.sql.xz:
	mkdir -p $$(dirname $@)
	rm -f $@
	{ docker compose -f $(COMPOSE_FILE) exec -T mysql sh -c 'exec mysqldump --all-databases --lock-all-tables --triggers --routines --events --set-gtid-purged=OFF $(foreach t,$(SENSITIVE_STATUS_TABLES),--ignore-table=system_status.$(t)) -uroot -p"root"'; \
	  echo 'USE `system_status`;'; \
	  docker compose -f $(COMPOSE_FILE) exec -T mysql sh -c 'exec mysqldump --no-data --skip-lock-tables --set-gtid-purged=OFF system_status $(SENSITIVE_STATUS_TABLES) -uroot -p"root"'; \
	} | xz > $@

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
