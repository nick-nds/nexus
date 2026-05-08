---
date: 2026-05-08
status: approved-for-implementation
phase: 5.5
tier: OSS (free)
author: brainstormed with Claude (Opus 4.7) and Niku Nitin
---

# Package Indexing — Design Spec

## Summary

Add the ability to index a third-party Composer package as a first-class Nexus project. The user provides a path to a cloned package; Nexus uses Orchestra Testbench to boot a Laravel app with the package's service providers registered, runs the existing extractor against the booted app scoped to the target package, and ingests the resulting `reflection.json` through the existing pipeline. The package becomes a regular Nexus project at `~/.nexus/projects/<vendor>--<name>/`, queryable through every existing tool.

The feature ships as Phase 5.5, OSS/free, gated by one hard requirement: the target package must ship a `testbench.yaml`. Packages without one are out of scope for v1 and emit a clear error.

## Context

### What's true today

- Phases 1–4 are signed off (`internal_docs/STATUS.md`). The PHP extractor at `packages/nexus-extractor-php/` ships with 76 tests, golden fixtures, and is validated against three real Laravel projects. The Python pipeline (graph builder, chunker, embedder, query engine) handles ~46k-node enterprise codebases.
- Phase 5 (OSS v1.0 interface layer) is in progress.
- The extractor's design assumes a host Laravel app: `php artisan nexus:extract` runs inside the user's `bootstrap/app.php` boot (D1.4). It has no first-class path to index a standalone Composer package.
- Two adjacent paths exist in design but neither solves the consumer-of-a-third-party-package use case:
  - `--vendor-allowlist` (already implemented in `ExtractCommand.php`) scopes class discovery to a named vendor package, but only when run *inside* a host app where that package is installed.
  - Phase 7 PRO plans a hosted package library with server-side sandbox builds (`PHASE-7-pro-package-library.md`). Future paid feature, not a free-tier path.

### What's missing

A free-tier consumer who has a third-party package cloned locally — internal/private package, package-under-development, or just a public package they want to query without a host app — has no way to point Nexus at it.

### Why Testbench

Orchestra Testbench is the de-facto Laravel-ecosystem solution to "boot a Laravel app inside a package's repo." It's already a transitive dev dependency of the Nexus extractor (vendored at `packages/nexus-extractor-php/vendor/orchestra/testbench-core/`), used by the extractor's own test suite. Its `vendor/bin/testbench <artisan-command>` CLI runs any artisan command against a fully booted Laravel skeleton with the package's service providers registered — which means `Route::getRoutes()`, `app()->getBindings()`, `Event::getRawListeners()`, `Gate::policies()`, and `ClassLoader::getClassMap()` all return data scoped to Laravel core + the target package, not random vendor noise.

Testbench is what Phase 7's server-side build pipeline *should* be using internally too; this design uses it locally and makes the Phase 7 free-tier fallback (D7.8) concrete.

## Locked-in decisions

The brainstorm produced six design decisions. Each is recorded here as a future decision-log entry candidate.

| # | Decision | Rationale |
|---|---|---|
| 1 | Primary user is a consumer of third-party packages | Author and Phase-7-server-side use cases are out of scope for v1 |
| 2 | Two invocation modes: in-repo (user `cd`'s into the package) and Nexus-driven (path arg from elsewhere) | Both have legitimate workflows; both share most code |
| 3 | Indexes live at `~/.nexus/projects/<slug>/` as regular projects (not a separate `packages/` namespace) | Simplest MVP; existing CLI/MCP/query layer works unchanged. Phase 7 may migrate to `packages/<vendor>/<name>/<version>/` later when multi-version coexistence becomes a requirement |
| 4 | `testbench.yaml` is required; missing → clean error, exit 2 | Honest scope boundary. Excludes some packages but is unambiguous |
| 5 | Single Python CLI surface: `nexus package index <path>` orchestrates both modes | One UX, one mental model, one set of docs |
| 6 | Scratch dirs cached per (package, version) at `~/.nexus/cache/package-builds/<vendor>--<name>/<version>/`, invalidated by content fingerprint | First run pays composer install cost (~30–90 s); subsequent runs are fast (~5–15 s) |

Implementation choice: **Approach B** — a sibling artisan command `nexus:extract-package`, sharing pipeline wiring with the existing `nexus:extract` via an extracted `ExtractionRunner`. Chosen over Approach A (one command with a `--as-package` flag) because it leaves the signed-off Phase 1 command untouched, eliminates mutual-exclusion logic in the CLI surface, and forces a clean refactor that's net-positive regardless. Approach C (Python-only with vendor-allowlist + post-filtering) was rejected because runtime registry data can't be cleanly scoped by namespace post-hoc.

## Architecture

The feature spans both halves of the codebase. The boundary is unchanged: PHP does extraction, Python does orchestration + ingest, JSON contract between them — same as today, plus two new metadata fields.

### PHP side (`packages/nexus-extractor-php/`)

| Module | Status | Responsibility |
|---|---|---|
| `src/Console/ExtractCommand.php` | unchanged | Existing project-mode command; behavior preserved bit-for-bit |
| `src/Console/ExtractPackageCommand.php` | new | `nexus:extract-package` artisan command. Single signature: `--package=<vendor>/<name>` (auto-detect from composer.json if omitted), `--output`, `--quiet-progress` |
| `src/Extraction/ExtractionRunner.php` | new (extracted) | Pure refactor pulling pipeline wiring out of `ExtractCommand::handle()`. Both commands consume it. Owns: error collector, fatal handler installation, pipeline construction, JSON write, exit-code mapping |
| `src/Extraction/Support/PackageScope.php` | new | Immutable value object: `vendor`, `name`, `version`, `vendor_path`, `namespaces` (resolved from package's `composer.json` `autoload.psr-4`) |
| `src/Extraction/ExtractionContext.php` | extended | Adds optional `?PackageScope $package` field |
| `src/Extraction/PhaseB/ClassMapWalker.php` | extended | When `PackageScope` is set, filters classmap entries to those under `vendor_path` |
| `src/Extraction/PhaseC/StaticAnalysisExtractor.php` | extended | When `PackageScope` is set, scans only files under `vendor_path` (specifically the package's `src/` per its own `composer.json`) |
| `src/Output/ReflectionDocument.php` | extended | Meta gains `kind: "project" | "package"` and optional `package: { vendor, name, version }` |
| `src/NexusExtractorServiceProvider.php` | extended | Registers the new command alongside the existing one |

Phase A registries (routes, bindings, events, policies, etc.) are still captured raw — the booted Testbench skeleton has only Laravel core + the target package, so registry data is naturally tight. We don't filter Phase A in PHP.

### Python side (`nexus/`)

| Module | Status | Responsibility |
|---|---|---|
| `nexus/interfaces/cli/commands/package/__init__.py` | new | Click subcommand group `nexus package` |
| `nexus/interfaces/cli/commands/package/index.py` | new | `nexus package index <path>` entry point. Validates input, calls orchestrator |
| `nexus/pipeline/package_indexer.py` | new | Orchestrator. Detects in-repo vs Nexus-driven mode, manages scratch (when needed), invokes Testbench, hands the resulting reflection.json to the existing pipeline |
| `nexus/adapters/package/__init__.py` | new | |
| `nexus/adapters/package/composer_metadata.py` | new | Reads target package's `composer.json`, resolves `vendor`, `name`, `version` (composer.json `version` → git tag → `dev-<branch>`), validates `testbench.yaml` exists |
| `nexus/adapters/package/scratch_builder.py` | new | Owns `~/.nexus/cache/package-builds/<vendor>--<name>/<version>/`. Generates scratch `composer.json`, copies `testbench.yaml`, symlinks `workbench/` if present, runs `composer install`, writes `manifest.json` only on full success |
| `nexus/core/reflection/document.py` | extended | `ReflectionDocument` Pydantic model gains `kind` and `package` fields, back-compatible (default `kind="project"`) |
| `nexus/adapters/storage/project_storage.py` | extended | `ProjectMeta` gains `kind`, `package`, `build_mode`, `source_path` fields |

### Storage

`~/.nexus/projects/<slug>/`, slug = `<vendor>--<name>` (double-dash because `/` is illegal in filesystem paths). Version recorded in `meta.json`, not the slug. Re-indexing the same package at a new version overwrites in place — single slug per package, latest wins. Multi-version coexistence is explicit non-goal for v1; the Phase 7 `packages/<vendor>/<name>/<version>/` layout is the right place for that, when Phase 7 lands.

## Data flow

Both modes use the same Python entry point: `nexus package index <path> [--name=<vendor>/<name>] [--version=<v>] [--timeout=<s>] [--verbose]`. The orchestrator decides which mode to run based on the state of `<path>`.

### Mode detection (first 50 ms of every run)

```
1. read <path>/composer.json → resolve vendor, name, version
2. assert <path>/testbench.yaml exists; else exit 2 with help text
3. probe <path>/vendor/bin/testbench AND <path>/vendor/nexus/extractor-php/
   ├── both present  → in-repo mode
   └── either absent → nexus-driven mode
```

### In-repo mode (fast path)

```
4. cd <path>
5. exec vendor/bin/testbench nexus:extract-package
       --package=<vendor>/<name>
       --output=<temp>/reflection.json
6. await; check exit code; capture stdout/stderr
7. read+validate reflection.json (schema_version, kind="package")
8. ingest → existing pipeline
```

No scratch dir, no composer install. Total ~5–15 seconds.

### Nexus-driven mode (orchestrated path)

```
4. resolve scratch dir: ~/.nexus/cache/package-builds/<vendor>--<name>/<version>/
5. compute scratch fingerprint:
     hash of (target composer.json, testbench.yaml, nexus-extractor-php composer.json)
6. if scratch exists AND scratch/manifest.json.fingerprint == fingerprint:
       skip to step 11 (cache hit)
7. otherwise build/refresh scratch:
     7a. mkdir -p scratch
     7b. write scratch/composer.json:
           {
             "repositories": [
               {"type": "path", "url": "<abs path to target>",
                "options": {"symlink": true}},
               {"type": "path", "url": "<abs path to nexus/extractor-php>",
                "options": {"symlink": true}}
             ],
             "require": {
               "<vendor>/<name>": "*",
               "nexus/extractor-php": "*",
               "orchestra/testbench": "^8.0|^9.0|^10.0|^11.0"
             },
             "minimum-stability": "dev",
             "prefer-stable": true
           }
     7c. cp <path>/testbench.yaml scratch/testbench.yaml
     7d. if <path>/workbench exists: ln -s <path>/workbench scratch/workbench
     7e. composer install --no-interaction --prefer-dist (in scratch)
     7f. write scratch/manifest.json with fingerprint + timestamps
8. cd scratch
9. exec vendor/bin/testbench nexus:extract-package
       --package=<vendor>/<name>
       --output=scratch/reflection.json
10. await + check exit code
11. read+validate scratch/reflection.json
12. ingest → existing pipeline
```

First run on a new package: ~30–90 seconds dominated by composer install. Cache hit on subsequent re-indexes: ~5–15 seconds.

### Ingest (shared between both modes)

```
A. compute slug = "<vendor>--<name>"  (one slug per package, latest version wins)
B. resolve project_dir = ~/.nexus/projects/<slug>/
C. hand reflection.json to existing pipeline (unchanged):
     ReflectionLoader → GraphBuilder → ChunkPass → EmbedAndPersistPass
D. write/update ProjectMeta:
     {
       "kind": "package",
       "package": {"vendor": ..., "name": ..., "version": ...},
       "indexed_at": <timestamp>,
       "indexed_commit": null,
       "source_path": "<path>",
       "build_mode": "in-repo" | "nexus-driven"
     }
E. report: "Indexed package <vendor>/<name>@<version> as project <slug> (<n> chunks, <m> nodes)"
```

The query layer doesn't care this is a package — `nexus query`, `nexus mcp serve`, every tool from Phase 4 works because the index is a regular project on disk. The "package-ness" surfaces as `ProjectMeta.kind`, useful for future filters and for `nexus project list` rendering.

### Two key choices baked in

1. **Symlink, not copy, for path repos.** Faster, reflects edits instantly during package-author dev-loops, trusts Composer's path-repo handling.
2. **Cache invalidation by fingerprint, not TTL.** Composer install is deterministic given inputs; no point re-running on a clock. `nexus cache clean` clears manually.

## Error handling

Every error gets a stable `error_code` per D31 (the public taxonomy contract). User-facing messages stay terse and end with a remediation hint. Exit codes follow project convention: `0` success, `1` runtime/system fault, `2` usage error.

### Pre-flight failures (user-fixable, exit 2)

| `error_code` | Trigger | Remediation hint |
|---|---|---|
| `package_path_missing` | `<path>` doesn't exist or isn't a dir | "No such directory: <path>" |
| `package_composer_missing` | `<path>/composer.json` not found | "Path is not a Composer package — composer.json is missing." |
| `package_composer_invalid` | composer.json unparseable or no `name` field | "composer.json is missing the 'name' field." |
| `package_name_mismatch` | `--name` flag disagrees with composer.json `name` | shows both values, asks user to pick |
| `package_version_unresolvable` | no `version` in composer.json AND no git tag AND no HEAD | "Pass --version=<x>, or tag the repo, or set 'version' in composer.json." |
| `package_testbench_yaml_missing` | locked-in case | "Nexus requires a testbench.yaml at the package root. See: <link to docs/package-indexing.md, written during implementation>." |
| `package_testbench_yaml_invalid` | YAML parse error | shows yaml.YAMLError line/column |

### Environment failures (user-fixable, exit 1)

| `error_code` | Trigger | Remediation |
|---|---|---|
| `package_composer_binary_missing` | `composer` not on PATH (Nexus-driven mode only) | "Install composer: https://getcomposer.org". Code is named `_binary_missing` to distinguish from the pre-flight `package_composer_missing` (which means *target package's* composer.json is missing). |
| `package_php_missing` | `php` not on PATH | same |
| `package_php_too_old` | PHP < 8.2 | "Nexus's extractor requires PHP 8.2+; found <version>." |
| `package_scratch_permission` | can't write to `~/.nexus/cache/package-builds/...` | actual `errno`-derived message |

### Build/extraction failures (often the package's fault, exit 1)

| `error_code` | Trigger | Behavior |
|---|---|---|
| `package_composer_install_failed` | `composer install` non-zero | capture last 50 lines of composer's output; surface them; mark scratch dirty so next run retries |
| `package_extractor_install_missing` | composer install succeeds but `vendor/nexus/extractor-php` absent | internal bug — surface a stable error code with a "report at the project's issue tracker" hint (URL set during implementation when public repo is finalized) |
| `package_testbench_boot_failed` | `vendor/bin/testbench` exits non-zero before extraction logs anything | surface stderr verbatim with framing: "the target package's service providers couldn't boot" |
| `package_extraction_failed` | testbench runs, extractor throws | inherits the existing extractor's structured `errors` array — show first 5 |
| `package_extraction_timeout` | extraction exceeds timeout (default 300s, overridable via `--timeout`) | "Extraction took longer than <N>s. Pass --timeout=<seconds>, or open an issue if the package is well-formed and you think this is a Nexus bug." |
| `package_reflection_invalid` | reflection.json has wrong schema_version, missing `kind`, or `kind != "package"` | internal bug — surface a stable error code with a "report at the project's issue tracker" hint (URL set during implementation when public repo is finalized) |

### Atomicity / partial-state guarantees

Two state-mutation points are atomic:

1. **Scratch manifest.** `scratch/manifest.json` (the cache-hit signal) is written *only after* the full sequence succeeds: composer install + testbench extraction + JSON validation. On failure, manifest doesn't exist; next run treats scratch as stale and rebuilds (composer install is incremental).
2. **Project ingest.** Existing pipeline writes via temp + atomic rename. Reused as-is.

Ctrl-C during composer install propagates as SIGINT to the subprocess; manifest is never written; user re-runs and resumes.

### Existing pipeline errors (passed through unchanged)

Once we hand off to `ReflectionLoader → GraphBuilder → ChunkPass → EmbedAndPersistPass`, all error codes from those layers (`reflection_load_failed`, `graph_build_failed`, `embedder_connection_error`, etc.) fire normally. No new codes added there.

## Testing strategy

### Test fixture: a synthetic package

Add `tests/fixtures/sample-package/` — a hand-crafted minimal Laravel package that exercises the primitives the extractor cares about:

```
tests/fixtures/sample-package/
├── composer.json          # name: "nexus-fixtures/sample", version: "1.2.0", psr-4 autoload
├── testbench.yaml         # providers: [NexusFixtures\Sample\SamplePackageServiceProvider]
├── src/
│   ├── SamplePackageServiceProvider.php   # loads routes, binds interface, registers event listener, schedules a task
│   ├── Contracts/SampleService.php
│   ├── Services/DefaultSampleService.php
│   ├── Http/Controllers/SampleController.php
│   ├── Models/SampleModel.php             # with relationship + cast
│   ├── Events/SampleEvent.php
│   ├── Listeners/SampleListener.php       # dispatches a job
│   ├── Jobs/SampleJob.php
│   ├── Policies/SampleModelPolicy.php
│   └── Console/Commands/SampleCommand.php
└── routes/
    └── package.php        # Route::get('/sample', SampleController@show)->name('sample.show')
```

Reproducible, no external deps, ~250 LOC, exercises route + binding + event + listener + scheduled task + class walk + AST analysis in one fixture. Real-package smoke tests against pinned versions of `spatie/laravel-permission`, `laravel/scout`, `laravel/telescope` are part of acceptance validation, not the inner-loop fixture.

### PHP-side tests (extends `packages/nexus-extractor-php/tests/`)

| Test | Layer | Why |
|---|---|---|
| `PackageScopeTest` | Unit | Value object construction, namespace resolution from `composer.json` autoload.psr-4 |
| `ClassMapWalkerScopedTest` | Unit | When `PackageScope` is set, only `vendor/<vendor>/<name>/` entries are walked |
| `StaticAnalysisExtractorScopedTest` | Unit | When `PackageScope` is set, only files under the package's `src/` are AST-scanned |
| `ExtractionRunnerTest` | Unit | Pure-refactor coverage: both commands produce expected pipelines |
| `ExtractPackageCommandTest` | Feature (Testbench) | Boots `tests/fixtures/sample-package` via Testbench, runs the new command, asserts: `kind="package"`, package metadata correct, sample's route present, sample's listener registered, sample's class in classes section, no Laravel core noise |
| Existing `ExtractCommandTest::*` | Feature | **Must pass unchanged** — the `ExtractionRunner` extraction is a pure refactor with regression-guard semantics |

PHP coverage: `PackageScope` 100%, scoped extractors ≥ 95%, `ExtractPackageCommand` ≥ 95%.

### Python-side tests

**Unit (`tests/unit/package/`)**
- `test_composer_metadata.py` — parse fixture composer.json, version resolution from each source, error paths for each `package_*` pre-flight code
- `test_scratch_builder.py` — fingerprint determinism, composer.json generation, manifest read/write round-trip, manifest-only-on-success guarantee
- `test_package_indexer.py` — mode detection for every combination of `vendor/bin/testbench` and `vendor/nexus/extractor-php` presence
- `test_cli_package_commands.py` — Click parsing, `--name` and `--version` overrides, exit codes per error class

**Integration (`tests/integration/package/`)** — gated behind `RUN_PACKAGE_INTEGRATION=1`
- `test_inrepo_mode_end_to_end.py` — sample-package pre-set-up, run `nexus package index .` from inside, assert ingest, ProjectMeta, reflection.json
- `test_nexus_driven_mode_end_to_end.py` — same fixture, scratch is fresh; assert composer install runs, scratch manifest written, project dir populated
- `test_cache_hit_path.py` — second run after first; assert composer install does *not* run; assert fast-path produces correct output
- `test_scratch_invalidation.py` — bump fixture version, re-run, assert composer install runs again

**E2E (`tests/e2e/package/`)** — gated behind `RUN_E2E=1` and a real embedder
- `test_query_package_index.py` — full pipeline: index sample-package → run `list_routes` → assert `/sample` appears → run `find_listeners SampleEvent` → assert `SampleListener` returned

**Architecture tests (`tests/architecture/`)** — extends existing layering test
- `nexus.adapters.package` doesn't import from `nexus.interfaces`
- `nexus.pipeline.package_indexer` doesn't import from `nexus.interfaces`
- New modules pass `mypy --strict`

### CI matrix additions

- `RUN_PACKAGE_INTEGRATION=1` enabled in CI on a single combination (Linux + Python 3.12) to keep CI runtime sane while catching regressions
- PHP test job picks up the new `ExtractPackageCommand` automatically (`phpunit` driven)
- E2E with real embedder stays gated; matches existing convention

### Coverage targets

| Module | Layer | Line | Branch |
|---|---|---|---|
| `nexus.adapters.package.*` | Adapter | ≥ 85% | ≥ 75% |
| `nexus.pipeline.package_indexer` | Glue | ≥ 90% | ≥ 80% |
| `nexus.interfaces.cli.commands.package.*` | Interface | ≥ 80% | ≥ 70% |
| PHP `PackageScope` / scoped extractors | Domain | ≥ 95% | ≥ 90% |
| Project overall | — | ≥ 90% | ≥ 80% |

### Idempotency guarantee

Two consecutive `nexus package index <path>` runs against an unchanged fixture must produce byte-identical reflection JSON (modulo `extracted_at` timestamps), byte-identical scratch manifest fingerprint, byte-identical ProjectMeta (modulo `indexed_at`). Phase 1 already enforces deterministic ordering for the existing extractor; we extend that guarantee to package mode. This catches whole categories of subtle bugs (non-deterministic class iteration, unstable composer.lock, env leakage into output).

## Acceptance criteria

A check (`[x]`) means the criterion is met. The phase exits when all are checked.

### Functionality

- [ ] `nexus package index <path>` exists, accepts `--name`, `--version`, `--timeout`, `--verbose` flags, prints clear help.
- [ ] **In-repo mode** works: against a sample package with `vendor/bin/testbench` and `vendor/nexus/extractor-php` already installed, indexing completes in ≤ 15 s on a typical dev laptop.
- [ ] **Nexus-driven mode** works: against a sample package with only `composer.json` and `testbench.yaml`, indexing completes from cold cache (composer install runs) and warm cache (composer install skipped) successfully.
- [ ] Cache hit on warm run completes in ≤ 30 s (vs ≤ 90 s cold).
- [ ] Reflection.json has `meta.kind = "package"` and `meta.package = {vendor, name, version}` populated correctly.
- [ ] ProjectMeta on disk has `kind = "package"`, `package = {...}`, `build_mode`, `source_path`, `indexed_at`.
- [ ] Slug computation: `<vendor>--<name>` (verified for vendors and names containing dots, dashes, digits).
- [ ] Re-indexing the same package at a new version overwrites in place (latest wins, single slug per package).
- [ ] All existing query tools work against a package index unchanged: `list_routes`, `describe_class`, `find_listeners`, `find_dispatchers`, `get_request_flow`, `semantic_search` all return data from the package's contributions.
- [ ] `nexus project list` renders package-kind projects with their version next to the slug.
- [ ] **Phase 1 regression: existing `nexus:extract` command behavior is byte-identical to before.** Verified by re-running PHASE-1's golden snapshots against `momskitchen.json`, `crm.json`, `helm-v7.json` and asserting zero diff.

### Quality

- [ ] `ruff format --check` and `ruff check` clean on all new Python code.
- [ ] `mypy --strict nexus/` clean.
- [ ] `pint --test` clean on all new PHP code.
- [ ] `phpstan analyse` at level 8 clean on the extractor package.
- [ ] PHPUnit suite green; existing 76 tests still pass + new tests for `ExtractPackageCommand`, `PackageScope`, scoped extractors, `ExtractionRunner`.
- [ ] Pytest suite green; coverage targets met per the Section 4 table; overall ≥ 90% line / ≥ 80% branch held.
- [ ] No `print`, `os.path`, relative imports, or untyped function signatures in new code.
- [ ] No outbound network calls in any new code path **except** the composer install in Nexus-driven mode (auditable, called out in docs).
- [ ] Zero `dump`, `dd`, `var_dump`, `print_r` in new PHP code.

### Robustness

- [ ] Every `error_code` from the error-handling section is triggered by at least one test, with the correct exit code.
- [ ] Ctrl-C during composer install propagates to the subprocess; manifest is not written; next run resumes cleanly.
- [ ] Atomic ingest: a failure during `EmbedAndPersistPass` does not leave a dirty `~/.nexus/projects/<slug>/`. Verified by killing the process mid-pipeline in an integration test.
- [ ] Idempotency: two consecutive runs against the unchanged fixture produce byte-identical reflection JSON (modulo timestamps), identical fingerprint, identical ProjectMeta (modulo `indexed_at`).
- [ ] Cache invalidation: bumping fixture version, or modifying its `composer.json` or `testbench.yaml`, triggers a rebuild.
- [ ] `nexus package index --timeout=2 <slow-fixture>` exits with `package_extraction_timeout`, exit code 1.

### Documentation

- [ ] `internal_docs/PHASE-5.5-package-indexing.md` written, listing scope, decisions, deliverables, acceptance, risks (acceptance section here becomes its acceptance block).
- [ ] `internal_docs/13-decision-log.md` gains entries for: D32 ("Package indexing requires testbench.yaml — no scaffolding"), D33 ("Cache scratch dirs by fingerprint at `~/.nexus/cache/package-builds/`"), D34 ("Single slug per package, latest-version-wins").
- [ ] `internal_docs/03-feature-matrix.md` gains a row for `nexus package index` under OSS.
- [ ] `internal_docs/15-non-goals.md` notes that scaffolding `testbench.yaml` and supporting non-Testbench packages are explicit non-goals for v1.
- [ ] `internal_docs/MASTER-PLAN.md` lists Phase 5.5 in the timeline.
- [ ] `internal_docs/PHASE-7-pro-package-library.md` updated to reference Phase 5.5 as the local-fallback foundation (D7.8 was previously vague — Phase 5.5 makes it concrete).
- [ ] `docs/error-codes.md` audited via `scripts/list_error_codes.py --strict` includes every new `package_*` code.
- [ ] User-facing README has an "Indexing a Composer package" section with both modes + the "requires testbench.yaml" note linked to Testbench docs.

### External validation

- [ ] Index three real Laravel packages end-to-end and verify queries return useful answers:
  - **`spatie/laravel-permission`** (well-maintained, widely used, ships testbench.yaml)
  - **`laravel/scout`** (official Laravel package, ships testbench.yaml)
  - **`laravel/telescope`** (official, more complex, more event/listener machinery — stresses the extractor)
  - For each: assert `list_routes` returns the package's routes, `describe_class` works on a known class, `find_listeners` works on a known event.
- [ ] One internal/private package indexed by the user (path-based, never published) — proves the consumer path with non-public packages works.
- [ ] Project owner signs off on output quality across all three external + one internal validation.

## Explicit non-goals (v1)

- Scaffolding `testbench.yaml` when missing (deferred; user must request).
- Indexing packages without `testbench.yaml` (out of scope; clear error).
- Multi-version coexistence in storage (single slug; Phase 7 is the right layer).
- Git URL or remote package input (use `git clone` first; we operate on filesystem paths).
- Cross-package federation queries (Phase 6 territory).
- Hosted catalog / signed package indexes (Phase 7 territory).
- Pruning the scratch cache by age/size (manual `nexus cache clean` only; auto-prune is a follow-up).
- Mutation-style testing (Stryker/Infection); not in project's existing toolchain.

## Risks & mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Composer install in Nexus-driven mode is brittle (network, conflicts, install scripts) | High | Medium | Cache aggressively; surface composer's stderr verbatim on failure; document the in-repo mode as the faster, more reliable path |
| Symlinked path repos break in some Composer/PHP versions | Low | Medium | Easy fallback to `symlink: false` (copy mode) gated by a flag; document |
| `testbench.yaml` parsing diverges between Testbench v8/v9/v10/v11 | Medium | Low | We don't parse it ourselves — Testbench reads it, we just check existence and copy it. Decoupled from Testbench's internals |
| Phase 1 golden snapshot drift if `ExtractionRunner` refactor isn't perfectly behavior-preserving | Medium | High | Mandatory acceptance criterion: byte-identical golden snapshots on `momskitchen`, `crm`, `helm-v7` post-refactor, before any new feature work merges |
| Cache fingerprint misses an input we should have hashed | Medium | Low | Conservative: hash composer.json + testbench.yaml + extractor's composer.json at minimum. Document the inputs; add `nexus cache clean --force` as an escape hatch |
| Real-world packages have edge cases the synthetic fixture doesn't (closure listeners, custom kernels, deferred providers) | High | Medium | External validation criterion forces three real packages through before sign-off; synthetic fixture is the inner loop, real packages are the truth |

## References

- `internal_docs/05-extraction-layer.md` — extractor design (read first for context)
- `internal_docs/PHASE-1-extraction-layer.md` — Phase 1 plan, signed off; what we're building on
- `internal_docs/PHASE-7-pro-package-library.md` — Phase 7 PRO feature this complements
- `internal_docs/13-decision-log.md` — D1 through D31; D32–D34 to be added
- `internal_docs/15-non-goals.md` — explicit-non-goals discipline
- `internal_docs/STATUS.md` — current phase status
- [Orchestra Testbench docs](https://packages.tools/testbench)
- [Testbench CLI reference](https://packages.tools/testbench/cli)
- [Testbench configuration reference](https://packages.tools/getting-started/configuration.html)
- [Reference testbench.yaml in testbench-core 11.x](https://github.com/orchestral/testbench-core/blob/11.x/testbench.yaml)
- [Laravel Scout's testbench.yaml (real-world example)](https://github.com/laravel/scout/blob/10.x/testbench.yaml)
