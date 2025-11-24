# full bash login shell requied for our complex make rules
#SHELL := /bin/bash --login


.PHONY: all docker clone clean bootstrap db-up db-test db-down db-restart

all: docker clone

docker:
	@./docker_start.sh

clone:
	@python3 bootstrap_clone.py
	@docker exec local-sam-mysql sh -c 'mysql -uroot -proot -e "USE sam; TRUNCATE TABLE api_credentials;"'

clean:
	rm -rf dump/
	docker rm -f local-sam-mysql || true

backups/%.sql.xz:
	mkdir -p $$(dirname $@)
	rm -f $@
	docker exec local-sam-mysql sh -c 'exec mysqldump --all-databases --lock-all-tables --triggers --routines --events -uroot -p"root"' | xz > $@

bootstrap:
	@echo "Starting clean: removing container and volume (persistent storage)..."
	@$(MAKE) clean >/dev/null
	@docker volume rm local-sam-mysql-vol >/dev/null 2>&1 || true
	@echo "Creatng a new docker container with empty storage..."
	@$(MAKE) docker
	@echo "Creating a local, subsetted database..."
	@$(MAKE) clone
	@echo "Backing up subsetted database..."
	@$(MAKE) backups/sam-local-dev.sql.xz
	@echo "Anonymizing (some warnings OK)..."
	@./run_anonymization_workflow.sh || true
	@echo "Backing up obfuscates database..."
	@$(MAKE) backups/sam-obfuscated.sql.xz

db-up:
	docker compose -f docker-compose.yaml -f docker-compose.ci.yaml up -d
	@echo "Waiting for healthy status..."
	@timeout 300 bash -c 'until docker compose ps | grep -q "healthy"; do sleep 5; done'
	@echo "✅ MySQL ready!"

db-test: db-up
	source ../../etc/config_env.sh && python3 check_username_leak.py
	source ../../etc/config_env.sh && python3 test_username_anonymization.py

db-down:
	docker compose -f docker-compose.yaml -f docker-compose.ci.yaml down -v

db-restart:
	@$(MAKE) db-down
	@$(MAKE) db-up
