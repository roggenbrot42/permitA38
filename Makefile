PYTHON ?= python3
OPENAPI = api/openapi.json

.PHONY: test
test:
	$(PYTHON) -m pytest -q

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
