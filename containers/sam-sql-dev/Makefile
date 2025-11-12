.PHONY: all docker clone clean

all: docker clone

docker:
	@./docker_start.sh

clone:
	@python3 bootstrap_clone.py

clean:
	rm -rf dump/
	docker rm -f local-sam-mysql || true
