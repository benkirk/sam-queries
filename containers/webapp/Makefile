.PHONY: build

TAG ?= samuel-webapp:latest
DOCKER_BUILD_ARGS ?= --progress=plain #--no-cache

build:
	docker buildx build $(DOCKER_BUILD_ARGS) --tag $(TAG) -f Dockerfile ../..

run:
	docker run -it $(TAG)
