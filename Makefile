PYTHON_VERSION ?= 3.11
VENV           ?= .venv
RUFF           := $(VENV)/bin/ruff
MYPY           := $(VENV)/bin/mypy
PYTEST         := $(VENV)/bin/pytest

# Prefer uv when available - it's significantly faster than plain pip
# and it manages Python interpreter installation transparently. Fall back
# to a plain venv + pip otherwise.
HAS_UV := $(shell command -v uv 2>/dev/null)

.PHONY: help venv install format lint typecheck test test-unit test-integration coverage check ci clean

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
	@echo "  ci            Run the literal CI gate locally (mirrors .github/workflows/ci.yml)"
	@echo "  check         Alias for 'ci'"
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

# ``ci`` mirrors .github/workflows/ci.yml line-by-line.
#
# - Same flags (``--strict``, ``--cov-fail-under=90``)
# - Same scope (``nexus/ tests/``, NOT ``examples/``)
# - Uses ``uv run`` (CI's runner) instead of ``$(VENV)/bin/...`` so a
#   green local run guarantees a green CI run and there's no
#   "but it passed for me" gap.
#
# Run this before every push.  Coverage figures may differ slightly
# from CI (LSP integration tests skip without intelephense; doctor.py
# composer-check skips without composer) - see ci.yml for the
# environment shape CI provides.
ci:
	uv run ruff format --check nexus/ tests/
	uv run ruff check nexus/ tests/
	uv run mypy --strict nexus/
	uv run pytest --cov=nexus --cov-fail-under=90 --cov-report=term-missing -q
	uv run python scripts/smoke_check.py

# Backwards-compat alias.  Existing muscle memory (``make check``)
# still works and now resolves to the same canonical gate as ``ci``.
check: ci

clean:
	rm -rf $(VENV) build dist .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name "*.egg-info" -prune -exec rm -rf {} +
