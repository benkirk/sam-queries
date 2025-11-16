.PHONY: all docker clone clean bootstrap

all: docker clone

docker:
	@./docker_start.sh

clone:
	@python3 bootstrap_clone.py

clean:
	rm -rf dump/
	docker rm -f local-sam-mysql || true

%.sql.xz:
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
	@$(MAKE) sam-local-dev.sql.gz
	@./run_anonymization_workflow.sh
	@echo "Backing up obfuscates database..."
	@$(MAKE) sam-obfuscated.sql.gz
