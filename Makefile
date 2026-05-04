PYTHON_VERSION ?= 3.11
VENV           ?= .venv
RUFF           := $(VENV)/bin/ruff
MYPY           := $(VENV)/bin/mypy
PYTEST         := $(VENV)/bin/pytest

# Prefer uv when available — it's significantly faster than plain pip
# and it manages Python interpreter installation transparently. Fall back
# to a plain venv + pip otherwise.
HAS_UV := $(shell command -v uv 2>/dev/null)

.PHONY: help venv install format lint typecheck test test-unit test-integration coverage check clean

help:
	@echo "Targets:"
	@echo "  venv          Create a Python virtual environment in $(VENV)"
	@echo "  install       Install nexus in editable mode with dev extras"
	@echo "  format        Apply ruff format"
	@echo "  lint          Run ruff format --check + ruff check"
	@echo "  typecheck     Run mypy --strict"
	@echo "  test          Run the full pytest suite"
	@echo "  test-unit     Run only unit tests"
	@echo "  test-integration  Run only integration tests"
	@echo "  coverage      Run pytest with coverage report"
	@echo "  check         Run lint + typecheck + test (the CI gate)"
	@echo "  clean         Remove venv and build artefacts"

$(VENV)/bin/python:
ifdef HAS_UV
	uv venv --python $(PYTHON_VERSION) $(VENV)
else
	python$(PYTHON_VERSION) -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip
endif

venv: $(VENV)/bin/python

install: venv
ifdef HAS_UV
	uv pip install --python $(VENV)/bin/python -e ".[dev]"
else
	$(VENV)/bin/pip install -e ".[dev]"
endif

format: venv
	$(RUFF) format nexus tests examples

lint: venv
	$(RUFF) format --check nexus tests examples
	$(RUFF) check nexus tests examples

typecheck: venv
	$(MYPY) nexus

test: venv
	$(PYTEST)

test-unit: venv
	$(PYTEST) tests/unit

test-integration: venv
	$(PYTEST) tests/integration -m integration

coverage: venv
	$(PYTEST) --cov=nexus --cov-branch --cov-report=term --cov-report=html

check: lint typecheck test

clean:
	rm -rf $(VENV) build dist .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name "*.egg-info" -prune -exec rm -rf {} +
