PYTHON ?= python3
OPENAPI = api/openapi.json

# worker processes for the randomized and browser suites (they are independent and CPU bound): make test-random XDIST="-n 1"
XDIST ?= -n 4 --dist worksteal

.PHONY: test test-backend test-random test-web test-all test-summary
# fast backend suite (unit, API, repository contract, migrations) - what CI runs on every OS/Python
test: test-backend

test-backend:
	$(PYTHON) -m pytest tests/backend -q

# randomized API tests: hypothesis state machine + schema fuzzer (default "ci" profile, deterministic, ~50 s on 4 workers,
# ~90 s with XDIST="-n 1").
# HYPOTHESIS_PROFILE=thorough for a long non-deterministic run.
test-random:
	$(PYTHON) -m pytest tests/backend/randomized -q $(XDIST)

# browser UI + live-server random user simulations (needs `playwright install chromium` or PERMITA38_CHROMIUM, ~1.5 min on 4 workers)
test-web:
	$(PYTHON) -m pytest tests/web -q $(XDIST)

test-all: test-backend test-random test-web

# all suites one after the other, ONE line per suite (full output in test-artifacts/<suite>.log); waits if another
# run is active. Prefer this to `test-all` for anything that is read by a person or a model:
#   make test-summary                   # backend, random, web
#   make test-summary SUITES="backend lint"
test-summary:
	$(PYTHON) scripts/run_suites.py $(SUITES)

.PHONY: test-release
test-release:
	$(PYTHON) -m pytest tests/release -q

# cut a release tag:  make release VERSION=0.4.0-alpha.1  [RELEASE_FLAGS="--dry-run --skip-checks"]  (see RELEASING.md)
.PHONY: release
release:
	@test -n "$(VERSION)" || { echo "usage: make release VERSION=0.4.0-alpha.1 [RELEASE_FLAGS=--dry-run]"; exit 2; }
	$(PYTHON) scripts/release.py tag $(VERSION) $(RELEASE_FLAGS)

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
