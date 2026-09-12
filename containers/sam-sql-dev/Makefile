COMPOSE_FILE := ../../compose.yaml

.PHONY: all clone clean bootstrap verify db-up db-test db-down db-restart FORCE

all: clone

# Subset production into the local container (reads prod as hpc-reader).
# The result holds REAL PII until `bootstrap` anonymizes it.
clone:
	@python bootstrap_clone.py --config config.yaml

clean:
	rm -rf dump/ *.sql.xz *~

# Exit non-zero if any username column still holds a real name. Gates the
# obfuscated blob below, so a failed or skipped anonymization cannot ship.
verify:
	@python check_username_leak.py --config config.yaml

# system_status tables holding real identities/usage from local collector runs.
# anonymize_sam_db.py only scrubs the `sam` database, so these must be dumped
# schema-only or the "obfuscated" blob leaks real usernames + per-user activity.
SENSITIVE_STATUS_TABLES := status_users project_codes user_proj_queue_status

# The committed blob: everything, EXCEPT the sensitive status tables which are
# appended schema-only (restore keeps working — FK checks are disabled inside
# mysqldump output, and the mysql-test healthcheck only needs the DB present).
backups/sam-obfuscated.sql.xz: verify FORCE
	mkdir -p $$(dirname $@)
	rm -f $@
	{ docker compose -f $(COMPOSE_FILE) exec -T -e MYSQL_PWD=root mysql sh -c 'exec mysqldump --all-databases --lock-all-tables --triggers --routines --events --set-gtid-purged=OFF $(foreach t,$(SENSITIVE_STATUS_TABLES),--ignore-table=system_status.$(t)) -uroot'; \
	  echo 'USE `system_status`;'; \
	  docker compose -f $(COMPOSE_FILE) exec -T -e MYSQL_PWD=root mysql sh -c 'exec mysqldump --no-data --skip-lock-tables --set-gtid-purged=OFF system_status $(SENSITIVE_STATUS_TABLES) -uroot'; \
	} | xz > $@

# Any other dump (e.g. backups/sam-local-dev.sql.xz): unverified, never commit.
%.sql.xz: FORCE
	mkdir -p $$(dirname $@)
	rm -f $@
	docker compose -f $(COMPOSE_FILE) exec -T -e MYSQL_PWD=root mysql sh -c 'exec mysqldump --all-databases --lock-all-tables --triggers --routines --events -uroot' | xz > $@

# clone -> raw backup -> anonymize -> verified obfuscated blob. Every step must
# succeed: a swallowed anonymizer failure once staged a near-raw prod dump.
bootstrap:
	@echo "Starting clean: removing dumps and backups..."
	@$(MAKE) clean >/dev/null
	rm -f backups/*.*
	@echo "Creating a local, subsetted database..."
	@$(MAKE) clone
	@echo "Backing up the raw subset..."
	@$(MAKE) backups/sam-local-dev.sql.xz
	@echo "Anonymizing..."
	./run_anonymization_workflow.sh --yes
	@echo "Verifying and backing up the obfuscated database..."
	@$(MAKE) backups/sam-obfuscated.sql.xz

db-up:
	docker compose -f $(COMPOSE_FILE) up -d mysql
	@echo "Waiting for healthy status..."
	@timeout 300 bash -c 'until docker compose -f $(COMPOSE_FILE) ps | grep -q "healthy"; do sleep 5; done'
	@echo "✅ MySQL ready!"

db-test: db-up verify
	python test_username_anonymization.py

db-down:
	docker compose -f $(COMPOSE_FILE) down -v

db-restart:
	@$(MAKE) db-down
	@$(MAKE) db-up

# Dump targets are real files but must always rebuild: a stale blob from the
# last run would otherwise be "up to date" and silently kept.
FORCE:
