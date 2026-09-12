COMPOSE_FILE := ../../compose.yaml

.PHONY: all clone clean bootstrap verify db-up db-test db-down db-restart db-reset FORCE \
        pg-up pg-down pg-reset clone-pg clone-pg-local clone-pg-test pg-test-up pg-test-reset

all: clone

# Subset production into the local container (reads prod as hpc-reader).
# The result holds REAL PII until `bootstrap` anonymizes it.
# SAM_CLONE_DAYS=<n> deepens every `recent` window in config.yaml.
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
	@timeout 600 bash -c 'until docker compose -f $(COMPOSE_FILE) ps mysql | grep -q "healthy"; do sleep 5; done'
	@echo "✅ MySQL ready!"

db-test: db-up verify
	python test_username_anonymization.py

db-down:
	docker compose -f $(COMPOSE_FILE) stop mysql

db-restart:
	@$(MAKE) db-down
	@$(MAKE) db-up

# Drop ONLY the mysql container and its volume so the next db-up restores the
# blob. `down -v` would also remove every other project volume; never use it.
db-reset:
	docker compose -f $(COMPOSE_FILE) rm -sf mysql
	-docker volume rm project-samuel_mysql-data

# ---- Postgres copy (load_postgres.py) ------------------------------------
# clone-pg targets SAM_DEV_PG_* from .env (the csg-postgres CNPG cluster by
# default); clone-pg-local keeps the same user/password/db and points at the
# compose `postgres` service on 5433. Both build <db>_next from the local
# MySQL clone and rename it over <db>.
clone-pg:
	@python load_postgres.py --config config.yaml $(CLONE_PG_ARGS)

clone-pg-local: export SAM_DEV_PG_HOST = 127.0.0.1
clone-pg-local: export SAM_DEV_PG_PORT = 5433
clone-pg-local: export SAM_DEV_PG_REQUIRE_SSL = false
clone-pg-local:
	@python load_postgres.py --config config.yaml $(CLONE_PG_ARGS)

# The test suite's Postgres target: `sam` on the compose `postgres-test` service,
# built from mysql-test (3307). Fixed test credentials, never .env's; the
# PG_TEST_* overrides let CI run it inside the webapp container by service name.
clone-pg-test: export SAM_DEV_PG_HOST = $(or $(PG_TEST_HOST),127.0.0.1)
clone-pg-test: export SAM_DEV_PG_PORT = $(or $(PG_TEST_PORT),5434)
clone-pg-test: export SAM_DEV_PG_USER = sam_test
clone-pg-test: export SAM_DEV_PG_PASSWORD = sam_test
clone-pg-test: export SAM_DEV_PG_DB = sam
clone-pg-test: export SAM_DEV_PG_REQUIRE_SSL = false
clone-pg-test:
	@python load_postgres.py --config config.yaml \
	    --source '$(or $(PG_TEST_SOURCE_URL),mysql+pymysql://root:root@127.0.0.1:3307/sam)' $(CLONE_PG_ARGS)

pg-test-up:
	docker compose -f $(COMPOSE_FILE) --profile test up -d postgres-test
	@timeout 120 bash -c 'until docker compose -f $(COMPOSE_FILE) --profile test ps postgres-test | grep -q "healthy"; do sleep 3; done'
	@echo "✅ postgres-test ready on 127.0.0.1:5434"

pg-up:
	docker compose -f $(COMPOSE_FILE) --profile pg up -d postgres
	@echo "Waiting for healthy status..."
	@timeout 120 bash -c 'until docker compose -f $(COMPOSE_FILE) --profile pg ps postgres | grep -q "healthy"; do sleep 3; done'
	@echo "✅ Postgres ready on 127.0.0.1:5433"

pg-down:
	docker compose -f $(COMPOSE_FILE) --profile pg stop postgres

# Drop ONLY the postgres container and its volume (a credential change needs
# a fresh initdb). `down -v` would also remove mysql-data; never use it here.
pg-reset:
	docker compose -f $(COMPOSE_FILE) --profile pg rm -sf postgres
	-docker volume rm project-samuel_postgres-data

pg-test-reset:
	docker compose -f $(COMPOSE_FILE) --profile test rm -sf postgres-test
	-docker volume rm project-samuel_postgres-test-data

# Dump targets are real files but must always rebuild: a stale blob from the
# last run would otherwise be "up to date" and silently kept.
FORCE:
