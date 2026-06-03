# CLAUDE.md - Nexus v2

Project-level instructions for Claude Code working in this repo. These rules apply to **every** change. Read this file first; then read the relevant document under `internal_docs/` before touching code.

## What this project is

Nexus is a **Laravel-specific code intelligence tool**. It indexes a Laravel codebase into a typed semantic graph + vector store and exposes structural and semantic queries over MCP and CLI so AI agents (Claude Code, Cursor, etc.) can answer questions about the code with less hallucination and fewer tokens.

The full design lives in `internal_docs/`. Start with `internal_docs/README.md`.

**Non-negotiables:**
- Laravel-only. We do not support Symfony, plain PHP, or non-PHP languages. See `internal_docs/15-non-goals.md`.
- Code quality is the product. Better to ship fewer excellent features than many half-built ones.
- Free tier must be complete on its own. Pro tier extends, never gates.

## Source of truth

| Topic | Document |
|---|---|
| Vision, audience, value prop | `internal_docs/01-vision-and-strategy.md` |
| Open core / monetization | `internal_docs/02-monetization-and-open-core.md` |
| Feature inventory by tier | `internal_docs/03-feature-matrix.md` |
| System architecture | `internal_docs/04-architecture-overview.md` |
| PHP extraction layer | `internal_docs/05-extraction-layer.md` |
| Indexing pipeline | `internal_docs/06-indexing-pipeline.md` |
| Storage (SQLite + LanceDB) | `internal_docs/07-storage-layer.md` |
| Embeddings & chunking | `internal_docs/08-embedding-and-chunking.md` |
| Query engine & tools | `internal_docs/09-query-engine.md` |
| MCP + CLI interfaces | `internal_docs/10-interface-layer.md` |
| Profile system | `internal_docs/11-profile-system.md` |
| Federation (PRO) | `internal_docs/12-federation-multi-project.md` |
| Decision log (D1–D30) | `internal_docs/13-decision-log.md` |
| v1 learnings | `internal_docs/14-v1-learnings.md` |
| Explicit non-goals | `internal_docs/15-non-goals.md` |
| Master implementation plan | `internal_docs/MASTER-PLAN.md` |
| Per-phase plans | `internal_docs/PHASE-*.md` |

If a design doc and code disagree, the code is wrong unless an entry in `13-decision-log.md` says otherwise. Do not silently change a design - update the doc and reference the change in your commit.

## Architectural principles

These are the principles that govern every line of code in this repo. They are derived from the design docs and v1 lessons.

### 1. Protocol-first, implementation-second

Every cross-boundary type is a `typing.Protocol` (or `abc.ABC` when runtime checks are needed). Concrete implementations are wired at the edges, not imported by callers.

```python
class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...
    @property
    def dimensions(self) -> int: ...
    @property
    def model_id(self) -> str: ...
```

**Why:** swappable backends (Ollama / Voyage / OpenAI / sentence-transformers), pro-tier plugins via entry points, trivial test doubles.

### 2. Pure core, impure shell

Domain logic (graph builder, query engine, classifiers, chunkers) is pure: it takes data in and returns data out. I/O (SQLite, HTTP, filesystem, subprocess) lives in adapter classes at the edges. The pure core never imports adapter modules.

**Why:** unit tests need no fixtures, no temp dirs, no network. Adapters get a small number of integration tests; the bulk of coverage is fast pure tests.

### 3. Composition over inheritance

Inheritance only when there is a true is-a relationship and a stable hierarchy (e.g., `BaseExtractor` for the PHP package's extractors). For everything else, use composition + protocols.

### 4. Explicit dependency injection

No global state. No singletons. No service locators. Every class receives its collaborators in `__init__`. The wiring happens once, in the CLI/MCP entry point or in tests.

**Counter-example to avoid:** v1's `set_active_profile` global. v2 passes the profile down explicitly.

### 5. Errors are values where it matters

For pipeline steps that can partially fail (extraction warnings, chunking errors, LSP timeouts), return a `Result` / `Outcome` object with `.value` and `.warnings`. Reserve exceptions for truly exceptional, programmer-error cases.

**Why:** indexing must continue past one bad file. Users see a structured warning, not a crash.

### 6. Stable data contracts, versioned

Every schema (reflection JSON, SQLite tables, profile YAML, federation config) has a `schema_version`. Migrations are explicit and tested. Breaking schema changes bump the major version and ship a migration.

### 7. Determinism

Same input → same output. No timestamps embedded in stored content (only in metadata columns). Sort orderings are explicit. Random IDs are seeded in tests.

**Why:** reproducible indexes, diffable outputs, debuggable cascades.

### 8. Performance is a feature, but correctness comes first

No micro-optimization without a benchmark. Profile before optimizing. The performance targets in `internal_docs/06-indexing-pipeline.md` and `09-query-engine.md` are the contract; meet them with the simplest code that works.

### 9. Single responsibility, narrow modules

A module exports one thing (or a small cluster of cohesive things). Files over ~400 lines are a smell - split them. Functions over ~50 lines are a smell - extract.

### 10. No speculative abstraction

Build for the requirements in the current phase. Resist creating "extension points" for hypothetical future needs. The plugin entry-point system exists because pro tier is a *concrete*, planned requirement - not because it might be useful.

## Design patterns we use

| Pattern | Where | Why |
|---|---|---|
| **Protocol / Strategy** | Embedder, VectorStore, GraphStore, Retriever, Extractor | Pluggable backends, testability |
| **Repository** | Project storage, federation storage | Hide SQLite/LanceDB details from query engine |
| **Pipeline** | Indexing (six passes) | Composable, observable, restartable steps |
| **Builder** | Graph construction from reflection JSON | Incremental assembly, validation at end |
| **Adapter** | LSP client (Intelephense, phpactor), embedder backends | Unify divergent third-party APIs |
| **Visitor** | AST walking (tree-sitter, nikic/php-parser) | Type-driven traversal |
| **Command** | CLI commands (Click), MCP tools | One-class-per-action keeps interfaces thin |
| **Factory** | Embedder/store construction from config | Centralize wiring decisions |
| **Result / Outcome** | Pipeline steps with partial failure | Errors as values, structured warnings |
| **Plugin registry (entry points)** | Pro tier extensions | Open core boundary |

## Coding standards

### Python

- **Version:** Python 3.11+ (we use `Self`, `LiteralString`, exception groups, faster startup).
- **Formatter:** `ruff format` (line length 100).
- **Linter:** `ruff check` with rules: `E,F,I,N,UP,B,SIM,RUF,PT,TID,PL`.
- **Type checker:** `mypy --strict` on `nexus/` (excluding test fixtures). No `Any` unless justified in a comment.
- **Imports:** absolute only. No relative imports (`from .foo import ...`).
- **Docstrings:** Google style. Public functions/classes only. Don't docstring obvious things.
- **Naming:** `snake_case` for functions/variables, `PascalCase` for classes, `UPPER_SNAKE` for constants. Protocols end in a noun (e.g., `Embedder`, not `IEmbedder`).
- **No `print`** in library code. Use `structlog` via the project logger.
- **Pathlib only.** No `os.path`.
- **Dataclasses / Pydantic:** dataclasses for internal types, Pydantic for external boundaries (config files, MCP requests, reflection JSON).

### PHP (Composer package)

- **Version:** PHP 8.2+, Laravel 10/11/12/13.
- **Standard:** PSR-12, enforced by Pint.
- **Static analysis:** PHPStan level 8.
- **Naming:** PSR-4 autoload, namespaced under `Nexus\Extractor\`.
- **No global state.** All extractors are services injected via the container.
- **Graceful degradation.** A failing extractor records a warning and continues; it never throws past the command boundary.

### YAML configs

- 2-space indent, no tabs.
- Every top-level config has a `schema_version`.
- Validated by Pydantic models on load.

## Testing standards

Testing is **not optional** and **not negotiable**. Every PR includes tests for the code it touches.

### Coverage targets

| Layer | Minimum line coverage | Minimum branch coverage |
|---|---|---|
| Pure domain (graph builder, classifiers, chunkers, query planner) | **95%** | **90%** |
| Adapters (storage, embedder backends, LSP client) | **85%** | **75%** |
| Interface layer (CLI, MCP) | **80%** | **70%** |
| **Overall project** | **≥ 90%** | **≥ 80%** |

Coverage is measured by `pytest-cov` and **enforced in CI**. A PR that drops coverage below threshold is blocked.

### Test taxonomy

1. **Unit tests** (`tests/unit/`): pure, no I/O, run in < 100ms each. The vast majority of tests live here.
2. **Integration tests** (`tests/integration/`): real SQLite, real LanceDB, real subprocess calls. Use `tmp_path` fixtures, never share state.
3. **Contract tests** (`tests/contract/`): every protocol has a contract test suite that all implementations must pass. New backends just plug into the suite.
4. **Golden tests** (`tests/golden/`): snapshot the output of the indexing pipeline against a fixture Laravel project. Catches regressions in extraction quality.
5. **End-to-end tests** (`tests/e2e/`): full pipeline against a sample Laravel app, asserting that specific MCP queries return expected results.

### TDD where it makes sense

- **Required** for: graph builder, classifiers, query planners, change detection - anything with branchy logic and well-defined inputs/outputs. Write the test, watch it fail, write the code, watch it pass, refactor.
- **Encouraged** for: adapters, where you can write the integration test against a fake fixture first.
- **Pragmatic** for: glue code, CLI plumbing - write tests after, but write them.

### Test quality rules

- **One concept per test.** Test names describe the behavior, not the method (e.g., `test_blade_chunks_are_linked_to_returning_controllers`, not `test_chunker`).
- **Arrange / Act / Assert** with blank lines between sections.
- **No mocks for things you own.** Use real implementations of your own protocols. Mock only external services (HTTP, LLM APIs).
- **Fixtures live next to tests** that use them, unless shared. Big fixture files go in `tests/fixtures/`.
- **Property-based tests** (`hypothesis`) for parsers and chunkers, where input space is large.
- **No flaky tests.** A flaky test is a broken test. Quarantine and fix; never retry-loop.

### Test fixture: the sample Laravel app

A real, hand-crafted Laravel 11 project lives at `tests/fixtures/sample-laravel-app/`. It exercises every primitive Nexus extracts (routes, models, jobs, events, listeners, policies, form requests, blade templates, observers, service bindings, DDD modules, CQRS handlers). Golden and e2e tests run against it.

## Quality gates (CI)

A PR is mergeable when **all** of the following pass:

1. `ruff format --check` - formatting.
2. `ruff check` - linting.
3. `mypy --strict nexus/` - types.
4. `pytest` - unit + integration + contract + golden tests.
5. `pytest --cov=nexus --cov-fail-under=90` - coverage threshold.
6. `pytest tests/e2e/` - end-to-end against the sample Laravel app.
7. PHP package: `pint --test`, `phpstan analyse`, `phpunit`.
8. Docs: every new public symbol has a docstring; every changed schema bumps `schema_version`.

CI runs on Linux + macOS. Windows is not supported (see `internal_docs/15-non-goals.md`).

## Repository layout

```
nexus-v2/
├── CLAUDE.md                    # this file
├── pyproject.toml
├── README.md
├── internal_docs/               # design + plans (gitignored)
├── nexus/                       # Python package
│   ├── core/                    # pure domain
│   │   ├── graph/
│   │   ├── chunking/
│   │   ├── classification/
│   │   └── query/
│   ├── adapters/                # I/O implementations
│   │   ├── storage/             # SQLite, LanceDB
│   │   ├── embedders/           # ollama, openai, voyage, ...
│   │   ├── lsp/                 # intelephense, phpactor
│   │   └── extractor/           # subprocess wrapper for PHP package
│   ├── pipeline/                # indexing pipeline orchestration
│   ├── profiles/                # YAML profile loader + auto-detect
│   ├── interfaces/
│   │   ├── cli/                 # Click commands
│   │   └── mcp/                 # FastMCP server
│   ├── plugins/                 # entry-point plugin loader
│   └── config/                  # Pydantic config models
├── packages/
│   └── nexus-extractor-php/     # Composer package (PHP)
│       ├── composer.json
│       ├── src/
│       └── tests/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── contract/
│   ├── golden/
│   ├── e2e/
│   └── fixtures/
│       └── sample-laravel-app/
└── profiles/                    # built-in YAML profiles
```

## How to work in this repo

When asked to implement or change something:

1. **Read the relevant `internal_docs/` document** for context. Don't guess at design.
2. **Read the active phase plan** in `internal_docs/PHASE-N-*.md` and confirm the work is in scope for the current phase. If it isn't, ask.
3. **Write the test first** for pure code; write the integration test first for adapters.
4. **Implement the smallest thing that makes the test pass.**
5. **Run the full quality gate locally** before declaring done: `make check` (or the equivalent task list).
6. **Update the phase plan's acceptance criteria checklist** if your change advances it.
7. **Update the relevant design doc** if the implementation revealed something the doc got wrong.

When in doubt, prefer:
- Smaller PRs over larger ones.
- Deleting code over adding code.
- A failing test that documents a known gap over silently shipping a hole.
- Asking the user over guessing on a design decision.

## What not to do

- **Do not** add features outside the current phase scope. Park them in `internal_docs/MASTER-PLAN.md` under "deferred."
- **Do not** add Laravel version-specific shims for versions older than 10.
- **Do not** introduce new dependencies without justification in the PR description and a check against `internal_docs/13-decision-log.md`.
- **Do not** use `print`, `os.path`, relative imports, or untyped function signatures.
- **Do not** mock your own code in tests.
- **Do not** add telemetry, analytics, or "phone home" calls. Ever. The tool is local-first by design.
- **Do not** silently catch and ignore exceptions. Either handle them with a structured warning or let them propagate.
- **Do not** commit anything in `internal_docs/`, `data/`, `.nexus/`, or `~/.nexus/` to git history. They are gitignored for a reason.

## Phase discipline

We build in phases. **Do not start work on phase N+1 until phase N's acceptance criteria are met.** Each phase has a dedicated plan in `internal_docs/PHASE-N-*.md`. The master overview is `internal_docs/MASTER-PLAN.md`.

This is the single most important rule for shipping. v1 failed because too many things were half-built simultaneously. v2 finishes one phase before opening the next.
