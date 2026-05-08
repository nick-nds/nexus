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

The brainstorm + self-review produced nine design decisions. Each is recorded here as a future decision-log entry candidate.

| # | Decision | Rationale |
|---|---|---|
| 1 | Primary user is a consumer of third-party packages | Author and Phase-7-server-side use cases are out of scope for v1 |
| 2 | Two invocation modes: in-repo (user `cd`'s into the package) and Nexus-driven (path arg from elsewhere) | Both have legitimate workflows; both share most code |
| 3 | Indexes live at `~/.nexus/projects/<slug>/` as regular projects (not a separate `packages/` namespace) | Simplest MVP; existing CLI/MCP/query layer works unchanged. Phase 7 may migrate to `packages/<vendor>/<name>/<version>/` later when multi-version coexistence becomes a requirement |
| 4 | `testbench.yaml` is required; missing → clean error, exit 2 | Honest scope boundary. Excludes some packages but is unambiguous |
| 5 | Single Python CLI surface: `nexus package index <path>` orchestrates both modes | One UX, one mental model, one set of docs |
| 6 | Scratch dirs cached per (package, version) at `~/.nexus/cache/package-builds/<vendor>--<name>/<version>/`, invalidated by content fingerprint that includes target source state | First run pays composer install cost (~30–90 s); subsequent runs are fast (~5–15 s); source edits invalidate cache so we never serve a stale index |
| 7 | Workbench/Testbench/Orchestra namespaces are filtered from the package index by the `ExtractPackageCommand` itself | Without this, fixture routes/factories/providers that Workbench registers to make the booted skeleton usable would pollute the package's index. Filter at PHP side because it knows it's running under Testbench |
| 8 | File paths in `reflection.json` are normalized to be `<package_root>`-relative on Python ingest | Without this, file paths reference `/some/scratch/.../vendor/orchestra/testbench-core/laravel/vendor/<vendor>/<name>/...` — useless for "where does this code live" UX, and different between in-repo and Nexus-driven modes (breaks idempotency) |
| 9 | `kind` and `package` are new top-level fields on `ReflectionDocument` (not nested under `project`); schema bumps from 2.0.0 → 2.1.0 (additive minor) | `project` describes the booted-app identity (which is "Testbench" in package mode); `package` describes the indexed target. They're orthogonal. Minor bump matches the existing schema-version policy (`document.py` SCHEMA_MAJOR comment) |

Implementation choice: **Approach B** — a sibling artisan command `nexus:extract-package`, sharing pipeline wiring with the existing `nexus:extract` via an extracted `ExtractionRunner`. Chosen over Approach A (one command with a `--as-package` flag) because it leaves the signed-off Phase 1 command untouched, eliminates mutual-exclusion logic in the CLI surface, and forces a clean refactor that's net-positive regardless. Approach C (Python-only with vendor-allowlist + post-filtering) was rejected because runtime registry data can't be cleanly scoped by namespace post-hoc.

## Architecture

The feature spans both halves of the codebase. The boundary is unchanged: PHP does extraction, Python does orchestration + ingest, JSON contract between them — same as today, plus two new metadata fields.

### PHP side (`packages/nexus-extractor-php/`)

| Module | Status | Responsibility |
|---|---|---|
| `src/Console/ExtractCommand.php` | unchanged | Existing project-mode command; behavior preserved bit-for-bit |
| `src/Console/ExtractPackageCommand.php` | new | `nexus:extract-package` artisan command. Single signature: `--package=<vendor>/<name>` (auto-detect from composer.json if omitted), `--output` (**required** — no default; the orchestrator always passes one), `--quiet-progress`. After extraction, applies the namespace-exclusion filter (decision #7) before writing JSON |
| `src/Extraction/ExtractionRunner.php` | new (extracted) | Pulled out of `ExtractCommand::handle()`. Both commands consume it. Owns: error collector, fatal-handler installation, pipeline construction, JSON write, exit-code mapping. Caveat: the existing handler captures Symfony Console output and registers a global shutdown function — keeping behavior bit-for-bit identical requires the runner to accept these as constructor deps and to deregister cleanly between runs (matters for tests, not prod) |
| `src/Extraction/Support/PackageScope.php` | new | Immutable value object: `vendor`, `name`, `version`, `vendor_path`, `namespaces` (resolved from package's `composer.json` `autoload.psr-4`) |
| `src/Extraction/Support/NamespaceExclusionFilter.php` | new | Hardcoded prefix list (`Workbench\`, `Orchestra\Testbench\`, `Orchestra\Workbench\`, `Orchestra\Sidekick\`, `Orchestra\Canvas\`). Filters: classes by namespace, bindings/aliases by concrete class, listeners by listener class, routes by handler class, gate callbacks by class, static-analysis findings by enclosing class. Applied in `ExtractPackageCommand` after Phase A/B/C complete, before JSON write |
| `src/Extraction/ExtractionContext.php` | extended | Adds optional `?PackageScope $package` field |
| `src/Extraction/PhaseB/ClassMapWalker.php` | extended | When `PackageScope` is set, filters classmap entries to those under `vendor_path` |
| `src/Extraction/PhaseC/StaticAnalysisExtractor.php` | extended | When `PackageScope` is set, scans only files under `vendor_path` (specifically the package's `src/` per its own `composer.json`) |
| `src/Output/ReflectionDocument.php` | extended | **Top-level** adds `kind: "project" | "package"` (default `"project"`) and optional `package: { vendor, name, version }`. Mirrors Python-side decision #9 |
| `src/Output/SchemaVersion.php` | extended | Bump constant from `"2.0.0"` to `"2.1.0"` (additive minor; old consumers ignore unknown fields, new consumers see the new fields with sensible defaults) |
| `src/NexusExtractorServiceProvider.php` | extended | Registers the new command alongside the existing one |

Phase A registries are captured raw inside `ExtractPackageCommand`'s pipeline. The post-extraction filter step (`NamespaceExclusionFilter`) drops Workbench/Testbench/Orchestra noise before serialization. We do this in PHP rather than Python because the PHP side already has full structured access to every section and can filter precisely; doing it in Python would mean re-walking every section after `ReflectionDocument.model_validate`.

### Python side (`nexus/`)

| Module | Status | Responsibility |
|---|---|---|
| `nexus/interfaces/cli/commands/package/__init__.py` | new | Click subcommand group `nexus package`. Subpackage rather than flat module is a small divergence from the existing flat layout (`ask.py`, `cache.py`, etc.); justified because we expect future siblings (`list`, `remove`, `inspect`) |
| `nexus/interfaces/cli/commands/package/index.py` | new | `nexus package index <path>` entry point. Validates input, calls orchestrator |
| `nexus/pipeline/package_indexer.py` | new | Orchestrator. Detects in-repo vs Nexus-driven mode, manages scratch (when needed), invokes Testbench, normalizes paths, hands the resulting reflection.json to the existing pipeline |
| `nexus/adapters/package/__init__.py` | new | |
| `nexus/adapters/package/composer_metadata.py` | new | Reads target package's `composer.json`, resolves `vendor`, `name`, `version` (composer.json `version` → git tag → `dev-<branch>`), validates `testbench.yaml` exists, resolves `<package_root>` and `<vendor>/<name>/src/` paths |
| `nexus/adapters/package/scratch_builder.py` | new | Owns `~/.nexus/cache/package-builds/<vendor>--<name>/<version>/`. Generates scratch `composer.json`, copies `testbench.yaml`, symlinks `workbench/` if present, runs `composer install`, writes `manifest.json` only on full success |
| `nexus/adapters/package/fingerprint.py` | new | Computes scratch fingerprint per decision #6 — see "Cache fingerprint shape" below |
| `nexus/adapters/package/path_normalizer.py` | new | Rewrites file paths in a loaded `ReflectionDocument` to be `<package_root>`-relative (per decision #8). Touches: `classes.items[].reflection.file`, `static_analysis.findings[].file`, `routes.items[].action.file`, `bindings.bindings[].concrete.file`, `events.listeners[].listeners[].file`, `gates_policies.gates[].callback.file`. Also rewrites `project.base_path` to `<package_root>` for downstream consumers |
| `nexus/core/reflection/document.py` | extended | Top-level `ReflectionDocument` Pydantic model gains `kind: Literal["project", "package"] = "project"` and `package: PackageMetadata \| None = None`. New `PackageMetadata` model with `vendor`, `name`, `version`. Model validator: `kind == "package"` requires `package` set; `kind == "project"` requires `package` is `None`. `SCHEMA_MAJOR` stays `2`; the loader now accepts `2.1.0` documents |
| `nexus/adapters/storage/project_storage.py` | extended | `ProjectMeta` gains `kind: Literal["project", "package"] = "project"`, `package: PackageMetadata \| None = None`, `build_mode: Literal["in-repo", "nexus-driven"] \| None = None`, `source_path: str \| None = None`. Schema bumps from `"1.0"` → `"1.1"` (additive). Existing meta files load fine — defaults fill in missing keys |

### Storage

`~/.nexus/projects/<slug>/`, slug = `<vendor>--<name>` (double-dash because `/` is illegal in filesystem paths). The `<vendor>--<name>` separator is intentional: composer's name regex permits `php-imap/php-imap` → slug `php-imap--php-imap` (no realistic collision since composer doesn't allow triple-dash inside vendor or name). Version is recorded in `meta.json`, not the slug. Re-indexing the same package at a new version overwrites in place — single slug per package, latest wins. Multi-version coexistence is explicit non-goal for v1; the Phase 7 `packages/<vendor>/<name>/<version>/` layout is the right place for that.

`ProjectMeta` for a package index has `kind = "package"`, `package = {vendor, name, version}`, `build_mode = "in-repo" | "nexus-driven"`, `source_path = <absolute path the user passed>`. UIs that render or query projects should prefer `package.name@package.version` over `project_slug` when `kind == "package"`, because `project.name` will contain `"Testbench"` (the booted skeleton's app name) — that's expected, not a bug. See "Project metadata expectations for package mode" below.

### Project metadata expectations for package mode

When `vendor/bin/testbench` boots the skeleton, the `app.name` config value reads `"Testbench"` (or whatever `testbench.yaml`'s `env.APP_NAME` overrides it to). The existing `describeProject()` method in `ExtractCommand.php` returns this verbatim. So in package-mode reflection JSON, you'll see:

```json
{
  "kind": "package",
  "project": {
    "name": "Testbench",
    "environment": "testing",
    "laravel_version": "11.50.0",
    "php_version": "8.3.x",
    "base_path": "<package_root>",
    "profile_hint": null
  },
  "package": {
    "vendor": "spatie",
    "name": "laravel-permission",
    "version": "v6.18.0"
  }
}
```

The `project` block describes the *boot context* (Testbench skeleton); `package` describes the *indexed target*. They're orthogonal. Note that `project.base_path` is rewritten to `<package_root>` by the Python-side path normalizer (decision #8), so downstream consumers can resolve relative file paths back to the user's filesystem.

`nexus project list`, `nexus query`, and any future tools that render package context should branch on `kind`:

```python
display_name = (
    f"{meta.package.vendor}/{meta.package.name}@{meta.package.version}"
    if meta.kind == "package" and meta.package
    else meta.project_slug
)
```

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
5. compute scratch fingerprint (see "Cache fingerprint shape" below)
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
     7e. composer install --no-interaction (in scratch)
     7f. write scratch/manifest.json with fingerprint + timestamps
8. cd scratch
9. exec vendor/bin/testbench nexus:extract-package
       --package=<vendor>/<name>
       --output=scratch/reflection.json
10. await + check exit code
11. read+validate scratch/reflection.json
12. ingest → existing pipeline
```

#### Cache fingerprint shape

```
fingerprint = sha256(
    target composer.json content,
    target testbench.yaml content,
    extractor-php composer.json content,
    target source state:
        if <path> is a git repo:
            git rev-parse HEAD       # commit hash
            git status --porcelain   # working-tree dirty marker
        else:
            recursive sha256 of <path>/<psr-4 src dir>/  # falls back to content hash
)
```

Including target source state (decision #6) is the difference between "the cache is honest" and "the cache silently serves stale indexes when the user `git pull`s the target package." For dirty git working trees, the porcelain output ensures uncommitted edits also bust the cache.

**Tradeoff:** for non-git targets, computing a recursive content hash is O(file count) per run. Acceptable: package-sized trees are small (typically < 1000 files), hashing is fast (~50–200 ms), and the alternative (cache lying) is a real correctness bug.

First run on a new package: ~30–90 seconds dominated by composer install. Cache hit on subsequent re-indexes: ~5–15 seconds.

### Ingest (shared between both modes)

```
A. compute slug = "<vendor>--<name>"  (one slug per package, latest version wins)
B. resolve project_dir = ~/.nexus/projects/<slug>/
C. load reflection.json via ReflectionLoader (existing — handles 2.0.0 and 2.1.0)
D. PathNormalizer rewrites every file-path field to be <package_root>-relative
   (decision #8). Also rewrites project.base_path → <package_root>.
E. hand the normalized document to the existing pipeline (unchanged):
     GraphBuilder → ChunkPass → EmbedAndPersistPass
F. write/update ProjectMeta:
     {
       "schema_version": "1.1",
       "project_slug": "<slug>",
       "project_path": "<package_root>",
       "kind": "package",
       "package": {"vendor": ..., "name": ..., "version": ...},
       "build_mode": "in-repo" | "nexus-driven",
       "source_path": "<path>",
       "indexed_at": <ISO-8601 UTC>,
       "last_indexed_commit": <git HEAD if <path> is a git repo, else null>,
       ...other existing ProjectMeta fields...
     }
G. report: "Indexed package <vendor>/<name>@<version> as project <slug> (<n> chunks, <m> nodes)"
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
| `package_path_missing` | `not Path(<path>).is_dir()` (covers both "doesn't exist" and "is a file") | "No such directory: <path>" or "Path is not a directory: <path>" depending on `Path.exists()` |
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
| `package_extractor_install_missing` | composer install succeeds (exit 0) but `vendor/nexus/extractor-php/` is absent post-install | Either an internal bug (path-repo URL wrong) or an env conflict (PHP version mismatch with target's constraints, dependency conflict resolved by composer dropping our extractor). Suggest: "run `composer why-not nexus/extractor-php` in the scratch dir to see why; report the output at the project's issue tracker if the error is unclear" |
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
├── testbench.yaml         # providers: [
│                          #   NexusFixtures\Sample\SamplePackageServiceProvider,
│                          #   Workbench\App\Providers\WorkbenchServiceProvider,
│                          # ]
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
├── workbench/
│   └── app/Providers/WorkbenchServiceProvider.php   # registers a fixture route — must NOT appear in the index
└── routes/
    └── package.php        # Route::get('/sample', SampleController@show)->name('sample.show')
```

Reproducible, no external deps, ~280 LOC. Exercises route + binding + event + listener + scheduled task + class walk + AST analysis. **The `workbench/` directory + Workbench provider are intentional**: they let us test that the `NamespaceExclusionFilter` (decision #7) actually drops Workbench-registered routes/classes from the index.

Real-package smoke tests against pinned versions of `spatie/laravel-permission`, `laravel/scout`, `laravel/telescope` are part of acceptance validation, not the inner-loop fixture.

### PHP-side tests (extends `packages/nexus-extractor-php/tests/`)

| Test | Layer | Why |
|---|---|---|
| `PackageScopeTest` | Unit | Value object construction, namespace resolution from `composer.json` autoload.psr-4 |
| `ClassMapWalkerScopedTest` | Unit | When `PackageScope` is set, only `vendor/<vendor>/<name>/` entries are walked |
| `StaticAnalysisExtractorScopedTest` | Unit | When `PackageScope` is set, only files under the package's `src/` are AST-scanned |
| `NamespaceExclusionFilterTest` | Unit | Each section type (classes, bindings, aliases, listeners, routes, gates, static-analysis findings) is filtered correctly when entries match `Workbench\`, `Orchestra\Testbench\`, `Orchestra\Workbench\`, `Orchestra\Sidekick\`, `Orchestra\Canvas\` prefixes; entries not matching are preserved |
| `ExtractionRunnerTest` | Unit | Refactor coverage: both commands produce expected pipelines, fatal-handler registration is idempotent across runs (matters because `register_shutdown_function` is global state) |
| `ExtractPackageCommandTest` | Feature (Testbench) | Boots `tests/fixtures/sample-package` via Testbench, runs the new command, asserts: top-level `kind="package"`, top-level `package` metadata correct, sample's route `/sample` present, sample's listener registered, sample's class in classes section, **Workbench-registered fixture route is NOT in the index, `Workbench\App\Providers\WorkbenchServiceProvider` is NOT in classes**, no Laravel core noise |
| Existing `ExtractCommandTest::*` | Feature | **Must pass unchanged** — the `ExtractionRunner` extraction must be behavior-preserving. Caveat: PHP's `register_shutdown_function` is global; the runner must accept-and-restore that state cleanly between consecutive test cases or one test's handler fires during the next test's execution |

PHP coverage: `PackageScope` 100%, scoped extractors ≥ 95%, `ExtractPackageCommand` ≥ 95%.

### Python-side tests

**Unit (`tests/unit/package/`)**
- `test_composer_metadata.py` — parse fixture composer.json, version resolution from each source, error paths for each `package_*` pre-flight code
- `test_fingerprint.py` — fingerprint determinism, hash inclusion of git HEAD, hash inclusion of source content for non-git targets, dirty-working-tree busts the hash, composer.json edits bust the hash, testbench.yaml edits bust the hash
- `test_scratch_builder.py` — composer.json generation, testbench.yaml + workbench symlink setup, manifest read/write round-trip, manifest-only-on-success guarantee
- `test_path_normalizer.py` — file paths in classes/static-analysis/routes/bindings/events/gates are rewritten relative to `<package_root>`, paths NOT under `<vendor_path>` are passed through unchanged, idempotent
- `test_package_indexer.py` — mode detection for every combination of `vendor/bin/testbench` and `vendor/nexus/extractor-php` presence
- `test_cli_package_commands.py` — Click parsing, `--name` and `--version` overrides, exit codes per error class
- `test_slug.py` — `<vendor>--<name>` computed correctly for edge-case names (`php-imap/php-imap`, `pestphp/pest`, names with digits)
- `test_reflection_document_kind_validation.py` — `kind="package"` requires `package` field set; `kind="project"` requires `package=None`; cross-validation rejects mismatched docs

**Integration (`tests/integration/package/`)** — gated behind `RUN_PACKAGE_INTEGRATION=1`
- `test_inrepo_mode_end_to_end.py` — sample-package pre-set-up, run `nexus package index .` from inside, assert ingest, ProjectMeta (with kind/package/build_mode/source_path), reflection.json (with kind/package), file paths normalized to package-relative
- `test_nexus_driven_mode_end_to_end.py` — same fixture, scratch is fresh; assert composer install runs, scratch manifest written, project dir populated, **same reflection.json output as in-repo mode** (same fingerprint after normalization → idempotency across modes)
- `test_workbench_filter.py` — fixture has Workbench provider + fixture route; assert resulting index has neither the Workbench provider class nor the fixture route
- `test_cache_hit_path.py` — second run after first; assert composer install does *not* run; assert fast-path produces correct output
- `test_scratch_invalidation_version.py` — bump fixture version, re-run, assert composer install runs again
- `test_scratch_invalidation_source.py` — modify a `.php` file under fixture's `src/`, re-run, assert composer install runs again (cache fingerprint catches source changes)
- `test_dirty_working_tree.py` — fixture is a git repo, `git status` is dirty, fingerprint reflects that, edits to the dirty tree bust the cache

**E2E (`tests/e2e/package/`)** — gated behind `RUN_E2E=1` and a real embedder
- `test_query_package_index.py` — full pipeline: index sample-package → run `list_routes` → assert `/sample` appears (and `/workbench-fixture` does NOT) → run `find_listeners SampleEvent` → assert `SampleListener` returned

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

### Prerequisites

- [ ] **Phase 5 (OSS v1.0 interface layer) is signed off.** Phase 5.5 depends on the `nexus` Click CLI infrastructure being stable. Per CLAUDE.md's phase discipline, Phase 5.5 does not start until Phase 5's acceptance criteria are met.

### Functionality

- [ ] `nexus package index <path>` exists, accepts `--name`, `--version`, `--timeout`, `--verbose` flags, prints clear help.
- [ ] **In-repo mode** works: against a sample package with `vendor/bin/testbench` and `vendor/nexus/extractor-php` already installed, indexing completes in ≤ 15 s on a typical dev laptop.
- [ ] **Nexus-driven mode** works: against a sample package with only `composer.json` and `testbench.yaml`, indexing completes from cold cache (composer install runs) and warm cache (composer install skipped) successfully.
- [ ] Cache hit on warm run completes in ≤ 30 s (vs ≤ 90 s cold).
- [ ] Reflection.json has top-level `kind = "package"` and top-level `package = {vendor, name, version}` populated correctly.
- [ ] Reflection.json `schema_version` is `"2.1.0"` for package-mode documents; `"2.0.0"` documents still load (back-compat).
- [ ] `ProjectMeta` on disk has `schema_version = "1.1"`, `kind = "package"`, `package = {...}`, `build_mode`, `source_path`, `indexed_at`. Existing `1.0` meta files load fine after this change.
- [ ] **All file paths in reflection.json are `<package_root>`-relative** (no `vendor/orchestra/...` paths leaking through). Verified across both modes — same package, same fingerprint, same paths.
- [ ] **Workbench/Testbench/Orchestra noise is filtered**. Sample-package fixture has a Workbench-registered fixture route + WorkbenchServiceProvider; neither appears in the resulting index.
- [ ] Slug computation: `<vendor>--<name>` (verified for vendors and names containing dots, dashes, digits, including `php-imap/php-imap`, `pestphp/pest`).
- [ ] Re-indexing the same package at a new version overwrites in place (latest wins, single slug per package).
- [ ] All existing query tools work against a package index unchanged: `list_routes`, `describe_class`, `find_listeners`, `find_dispatchers`, `get_request_flow`, `semantic_search` all return data from the package's contributions.
- [ ] `nexus project list` renders package-kind projects as `<vendor>/<name>@<version>` (not `Testbench`).
- [ ] **Phase 1 regression: existing `nexus:extract` command behavior is byte-identical to before.** Verified by re-running PHASE-1's golden snapshots against `momskitchen.json`, `crm.json`, `helm-v7.json` and asserting zero diff. Includes the `register_shutdown_function` lifecycle being equivalent across consecutive runs.

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
- [ ] **Idempotency across modes**: in-repo and Nexus-driven runs against the same fixture produce byte-identical reflection JSON (modulo `generated_at`), identical fingerprint, identical ProjectMeta (modulo `indexed_at`/`updated_at`). Path normalization (decision #8) is what makes this possible.
- [ ] **Cache invalidation by source**: modifying any `.php` file under fixture's `src/` triggers a rebuild on next run, even though `composer.json` is unchanged.
- [ ] **Cache invalidation by metadata**: bumping fixture version, or modifying `composer.json` or `testbench.yaml`, triggers a rebuild.
- [ ] **Cache invalidation by working-tree state** (git fixtures): an uncommitted edit to fixture's `src/` busts the cache. Reverting the edit restores the prior fingerprint.
- [ ] `nexus package index --timeout=2 <slow-fixture>` exits with `package_extraction_timeout`, exit code 1.

### Documentation

- [ ] `internal_docs/PHASE-5.5-package-indexing.md` written, listing scope, decisions, deliverables, acceptance, risks (acceptance section here becomes its acceptance block).
- [ ] `internal_docs/13-decision-log.md` gains entries for: D32 ("Package indexing requires testbench.yaml — no scaffolding"), D33 ("Cache scratch dirs by fingerprint that includes target source state"), D34 ("Single slug per package, latest-version-wins"), D35 ("Workbench/Testbench/Orchestra namespaces filtered from package indexes"), D36 ("Reflection.json paths normalized to `<package_root>`-relative on Python ingest"), D37 ("`kind` and `package` are top-level fields on `ReflectionDocument`; schema bump 2.0.0 → 2.1.0").
- [ ] `internal_docs/03-feature-matrix.md` gains a row for `nexus package index` under OSS.
- [ ] `internal_docs/15-non-goals.md` notes that scaffolding `testbench.yaml` and supporting non-Testbench packages are explicit non-goals for v1.
- [ ] `internal_docs/MASTER-PLAN.md` lists Phase 5.5 in the timeline.
- [ ] `internal_docs/PHASE-7-pro-package-library.md` updated to reference Phase 5.5 as the local-fallback foundation (D7.8 was previously vague — Phase 5.5 makes it concrete).
- [ ] `internal_docs/05-extraction-layer.md` updated: schema-version policy clarified (2.0.0 → 2.1.0 is an additive minor bump; old documents still load).
- [ ] `internal_docs/07-storage-layer.md` updated: `ProjectMeta` schema-version bump (1.0 → 1.1) documented; new `kind`/`package`/`build_mode`/`source_path` fields described.
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
| First Nexus-driven run requires network (composer install hits packagist for testbench's transitive deps) | High | Low | Document explicitly; in-repo mode is offline-friendly; subsequent cache-hit runs are also offline-friendly |
| Symlinked path repos break in some Composer/PHP versions | Low | Medium | Easy fallback to `symlink: false` (copy mode) gated by a flag; document. Path-repo with symlinks also has a known quirk: composer respects the source dir's autoload, which can cause double-installation if the source has a populated vendor/. Validate during implementation; add a `--no-symlink` escape hatch |
| `testbench.yaml` parsing diverges between Testbench v8/v9/v10/v11 | Medium | Low | We don't parse it ourselves — Testbench reads it, we just check existence and copy it. Decoupled from Testbench's internals |
| Phase 1 golden snapshot drift if `ExtractionRunner` refactor isn't perfectly behavior-preserving | Medium | High | Mandatory acceptance criterion: byte-identical golden snapshots on `momskitchen`, `crm`, `helm-v7` post-refactor, before any new feature work merges. Watch for: `ProgressReporter` constructor coupling to Symfony Console, `register_shutdown_function` lifecycle |
| Cache fingerprint misses an input we should have hashed (e.g., target source changes) | Low | Medium | **Mitigated by decision #6**: fingerprint includes git HEAD or `src/` content hash. Add `nexus cache clean` and `nexus cache clean --force` as escape hatches; document the inputs |
| Workbench/Testbench/Orchestra namespace filter accidentally drops legit user code that uses one of those namespaces | Low | Medium | Filter operates on namespace prefix only — a user package in `App\` or any custom namespace is unaffected. The risk only exists if a target package literally extends `Orchestra\Testbench\TestCase` from production code (which would be an unusual pattern). Document the prefix list; allow override via `--no-filter-orchestra` if it ever matters |
| Path normalization fails on edge cases (absolute paths in testbench.yaml, custom workbench locations, files outside `<vendor_path>`) | Medium | Low | Idempotent normalizer: paths NOT under `<vendor_path>` pass through unchanged. Test coverage for: absolute paths, relative paths already package-relative, paths with `..` components |
| Real-world packages have edge cases the synthetic fixture doesn't (closure listeners, custom kernels, deferred providers, paid-license packages) | High | Medium | External validation criterion forces three real packages through before sign-off; synthetic fixture is the inner loop, real packages are the truth |
| Refactoring `ExtractionRunner` reveals hidden coupling that breaks Phase 1 tests (e.g., shutdown handlers leaking across test cases) | Medium | High | Treat the refactor as its own milestone; merge it green against the existing Phase 1 test suite *before* adding `ExtractPackageCommand`. Don't let the new feature ride along on a flaky refactor |

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
