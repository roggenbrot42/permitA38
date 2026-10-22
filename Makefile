PYTHON ?= python3
OPENAPI = api/openapi.json

.PHONY: test test-backend test-random test-web test-all
# fast backend suite (unit, API, repository contract, migrations) - what CI runs on every OS/Python
test: test-backend

test-backend:
	$(PYTHON) -m pytest tests/backend -q

# randomized API tests: hypothesis state machine + schema fuzzer (default "ci" profile, deterministic, ~1 min).
# HYPOTHESIS_PROFILE=thorough for a long non-deterministic run.
test-random:
	$(PYTHON) -m pytest tests/backend/randomized -q

# browser UI + live-server random user simulations (needs `playwright install chromium` or PERMITA38_CHROMIUM, ~3 min)
test-web:
	$(PYTHON) -m pytest tests/web -q

test-all: test-backend test-random test-web

.PHONY: lint
lint: ruff mypy

.PHONY: ruff
ruff:
	$(PYTHON) -m ruff check

.PHONY: mypy
mypy:
	$(PYTHON) -m mypy permita38

.PHONY: format
format:
	$(PYTHON) -m ruff format

.PHONY: check-format
check-format:
	$(PYTHON) -m ruff format --check

.PHONY: run
run:
	$(PYTHON) -m permita38 --no-browser serve

.PHONY: generate-openapi
generate-openapi:
	$(PYTHON) -m permita38 show-openapi > $(OPENAPI)

.PHONY: check-openapi
check-openapi:
	$(PYTHON) -m permita38 show-openapi | diff -u $(OPENAPI) - && echo "openapi.json is up to date"

DOCKER_IMAGE ?= permita38:local

.PHONY: docker-build
docker-build:
	docker build -t $(DOCKER_IMAGE) .

.PHONY: docker-run
docker-run: docker-build
	docker run --rm -it -p 127.0.0.1:8765:8765 -v permita38-data:/data --env-file .env $(DOCKER_IMAGE)

.PHONY: docker-up
docker-up:
	docker compose up -d --build

.PHONY: docker-lock
docker-lock:
	uv pip compile pyproject.toml --universal --python-version 3.12 --no-header -o docker/constraints.txt
