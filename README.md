# Nexus

> Laravel-specific code intelligence: a typed semantic graph + vector RAG over a Laravel codebase, exposed via MCP and CLI for AI agents.

Nexus indexes a Laravel application into a typed knowledge graph (routes, models, controllers, jobs, events, listeners, policies, ...) plus a vector store of code chunks, then exposes structural and semantic queries that help agents like Claude Code, Cursor, and Codex answer questions about the codebase **with less hallucination and fewer wasted tokens**.

## Why Nexus?

When an AI agent needs to answer "how does the checkout flow work?" it typically reads dozens of files, hallucinates missing pieces, and burns tokens on boilerplate. Nexus gives the agent a graph-aware, semantically-searchable index of the codebase so it can answer in one or two tool calls instead of twenty file reads.

**The problem with file reading:** A controller method calls a service, which dispatches a job, which fires an event, which triggers three listeners. An agent reading files linearly loses track of the chain halfway through and fills the gaps with plausible-sounding nonsense.

**The Nexus answer:** Each of those relationships is a typed edge in the graph. `trace_route POST /checkout` follows `ROUTES_TO → VALIDATES_WITH → DISPATCHES → FIRES → LISTENS_TO` edges in a single traversal. The agent gets the complete, accurate chain — not a guess.

## How it works

```
Your Laravel app
      │
      ▼
PHP extractor (Artisan command)
      │  reflection.json
      ▼
Graph builder
      │  typed graph: routes, classes, events, jobs, policies, ...
      ▼
Chunker + Embedder
      │  vector store (LanceDB)
      ▼
Query engine  ─────────►  MCP tools  ─────────►  Claude / Cursor
                           CLI tools
```

1. **Extraction** — the Composer package `nexus/extractor-php` boots your Laravel app and emits a `reflection.json` that captures every route, class, event, job, middleware binding, and dispatch call.
2. **Graph build** — the Python builder walks the reflection and creates typed nodes and edges: `ROUTES_TO`, `VALIDATES_WITH`, `FIRES`, `DISPATCHES`, `LISTENS_TO`, `EXTENDS`, `BOUND_TO`, and more.
3. **Chunk + embed** — source files are chunked by class/method and embedded into a LanceDB vector store using your chosen backend (local `fastembed`, Ollama, Voyage, or OpenAI).
4. **Query** — 15 query tools traverse the graph and search the vector store. An agent asking about the checkout flow calls `trace_route`, then `find_event_chains`, then `get_model_context` — each call follows edges, not file reads.

## Installation

```bash
pip install nexus
# or
uv pip install nexus
```

PHP extractor (required for indexing):

```bash
# in your Laravel project
composer require --dev nexus/extractor-php
```

PHP language server (strongly recommended — provides the `CALLS` edges the call-graph queries depend on):

```bash
# Recommended: intelephense — fast bulk indexing, persistent index
npm install -g intelephense

# Alternative: phpactor — works for editor use, but slow at indexing scale
# (https://phpactor.readthedocs.io/en/master/usage/standalone.html)
```

Without an LSP, indexing still succeeds but the graph contains no CALLS
edges — `find_callers`, `get_request_flow`'s caller chain, and other
call-graph tools will return empty results. The pipeline emits a clear
warning when no server is found; see the `--lsp` option for explicit
control.

## Quick start

```bash
# 1. Create nexus.yml in your Laravel project
cd /path/to/your/laravel-app
nexus init

# 2. Check your environment
nexus doctor

# 3. Build the index
nexus index rebuild

# 4. Query it
nexus ask "how does user authentication work?"
nexus query list_routes --method POST
nexus query trace_route --method GET --uri /api/v1/users
```

## MCP configuration (Claude Code / Cursor)

Add to your Claude Code or Cursor MCP configuration:

```json
{
  "mcpServers": {
    "nexus": {
      "command": "nexus",
      "args": ["mcp", "serve"]
    }
  }
}
```

Then ask Claude: *"How does the checkout flow work?"* or *"Which jobs does PlaceOrderAction dispatch?"*

## CLI reference

### `nexus init`

Create `nexus.yml` in a project directory (interactive or `--non-interactive`).

```
nexus init [--project-path PATH] [--slug SLUG] [--profile PROFILE]
           [--embedder EMBEDDER] [--non-interactive]
```

### `nexus index`

| Subcommand | Description |
|---|---|
| `rebuild` | Drop existing index and run the full pipeline |
| `sync` | Re-run pipeline, reusing the embedding cache |
| `status` | Print stored `meta.json` |
| `clear` | Delete the project's index |

```
nexus index rebuild [--project-path PATH] [--include-tests]
                    [--php CMD] [--container-project-path PATH]
                    [--lsp CHOICE]
nexus index sync    [--project-path PATH] [--include-tests]
                    [--php CMD] [--container-project-path PATH]
                    [--lsp CHOICE]
nexus index status
nexus index clear   [--force]
```

`--lsp` selects the language server used to populate `CALLS` edges:

| Value | Behaviour |
|---|---|
| `auto` (default) | Detect `intelephense` or `phpactor` on PATH or in Mason. Continue without LSP if none found (with warning). |
| `none` | Skip LSP enrichment entirely. Pipeline produces a structural-only graph. |
| `intelephense` / `phpactor` / absolute path | Use this server explicitly. Exit code 2 if not found. |

`--container-project-path` is needed when `--php` invokes a Docker container so artisan and the output file resolve to in-container paths. Example:

```bash
nexus index rebuild \
  --php "docker exec my-app php" \
  --container-project-path /var/www \
  --lsp auto
```

### `nexus query`

Run any registered tool directly:

```
nexus query <tool-name> [OPTIONS]

Tools:
  describe_class        --fqn <FQN>
  find_callers          --method-fqn <FQN>
  find_dispatchers      --event <FQN>
  find_event_chains     --event <FQN> [--max-depth N]
  find_handlers         [--uri-glob GLOB] [--method METHOD]
  find_implementations  --interface-fqn <FQN>
  find_jobs_dispatching --job <FQN>
  find_listeners        --event <FQN>
  get_model_context     --fqn <FQN>
  get_policy_for        --model-fqn <FQN>
  get_request_flow      --method METHOD --uri URI
  list_routes           [--method METHOD] [--uri-glob GLOB]
  resolve_binding       --abstract <CLASS>
  semantic_search       --query <TEXT> [--top-k N] [--final-k N]
  trace_route           --method METHOD --uri URI
```

### `nexus ask`

Classifier-routed free-text query — Nexus picks the best tool automatically:

```
nexus ask "how does checkout work?"
nexus ask --explain "which jobs does PlaceOrderAction dispatch?"
```

The response is wrapped with the routing decision so the calling agent can see why a particular tool ran:

```json
{
  "tool": "find_handlers",
  "confidence": 0.85,
  "reason": "detected 'handles X' phrasing (uri_glob='*login*')",
  "alternatives_tried": [],
  "result": { ... actual tool output ... }
}
```

When the classifier cannot match a structural rule **and** the best semantic-search hit scores below the confidence floor (vector_score < 0.65), `ask` returns a structured refusal instead of weak hits:

```json
{
  "tool": "semantic_search",
  "result": {
    "error_code": "no_confident_match",
    "error": "No confident match for 'make me a sandwich'. ... best semantic hit scored 0.44 (threshold 0.65).",
    "best_vector_score": 0.44,
    "weak_hits_count": 10,
    "suggested_tools": ["list_routes", "find_handlers", "describe_class", ...]
  }
}
```

This is the explicit guardrail against the kind of hallucination where an agent trusts the noisy semantic-fallback as an answer.

### `nexus profile`

| Subcommand | Description |
|---|---|
| `list` | List all built-in profiles |
| `detect` | Auto-detect the best profile for a directory |
| `show NAME` | Show full definition of one profile |

### `nexus doctor`

Run environment diagnostics:

```
nexus doctor [--project-path PATH]
```

Checks Python version, PHP, Composer, `nexus.yml` validity, extractor installation, data directory writability, and **LSP responsiveness** (sends `initialize` to the resolved server with a 5s timeout). The LSP check distinguishes three states: `ok`, `not_found` (warning), and `found_but_unresponsive` (error).

### `nexus cache`

| Subcommand | Description |
|---|---|
| `size` | Report embedding cache disk usage |
| `clear` | Delete all cached embeddings |

### `nexus install-hooks`

Install a Git post-commit hook that runs `nexus index sync` automatically after each commit:

```
nexus install-hooks [--project-path PATH] [--force]
```

### `nexus mcp serve`

Start the MCP server:

```
nexus mcp serve [--transport stdio|sse|http] [--host HOST] [--port PORT]
```

Default transport is `stdio` (for agent spawning). Use `--transport sse` or `--transport http` for shared server deployments.

## MCP tool reference

All tools are also available as MCP tools with identical names and schemas:

| Tool | Description |
|---|---|
| `describe_class` | Full view of a class: kind, methods, related routes, events, jobs, policy |
| `find_callers` | Find all call sites for a method or function |
| `find_dispatchers` | Find code that dispatches a given event or job |
| `find_event_chains` | Trace event listener chains |
| `find_handlers` | Find route handler for a URI pattern |
| `find_implementations` | Find concrete implementations of an interface or abstract class |
| `find_jobs_dispatching` | Find all jobs dispatched by a class |
| `find_listeners` | Find all listeners for an event |
| `get_model_context` | Full Eloquent model context: relations, scopes, observers, policies |
| `get_policy_for` | Get the policy class governing a model |
| `get_request_flow` | Trace middleware, controller, and form request for a route |
| `list_routes` | List routes, optionally filtered by method/URI |
| `resolve_binding` | Resolve a service container binding |
| `semantic_search` | Vector similarity search over code chunks (returns a `snippet` of the source per hit) |
| `trace_route` | Full handler trace for a route: middleware, controller, relations |

## Tool response contract

Every tool response — CLI, MCP, or Python — carries a `coverage` block at the top level:

```json
{
  "coverage": {
    "calls_indexed": true,
    "lsp_server": "/usr/local/bin/intelephense",
    "embedder_id": "ollama:nomic-embed-text",
    "indexed_at": "2026-05-03T12:13:34+00:00",
    "project_path": "/path/to/your-laravel-app"
  },
  ...
}
```

| Field | Meaning |
|---|---|
| `calls_indexed` | `true` only when an LSP ran during indexing and `CALLS` edges were populated. `find_callers` and the call-graph side of `get_request_flow` only return meaningful results when this is `true`. |
| `lsp_server` | Path or name of the LSP binary used. `null` when no LSP ran. |
| `embedder_id` | Identifier of the embedder. Vector_score distributions vary by model, so an agent comparing scores across projects should weight by this id. |
| `indexed_at` | ISO-8601 UTC timestamp of when the index was last built. Useful for detecting stale indexes. |
| `project_path` | Host-side project root the index was built from. |

This is the explicit signal that turns a `total: 0` response from "no matches" into "no matches **because** this feature isn't indexed." Agents should always check `coverage.calls_indexed` before trusting a `find_callers: []` result.

### Error codes

Tools surface failures via two fields:

```json
{ "error": "human-readable description", "error_code": "stable_machine_code" }
```

Common codes an agent should handle:

| Code | Meaning |
|---|---|
| `method_not_found` | The supplied `method_fqn` doesn't exist in the graph |
| `class_not_found` | The supplied class FQN doesn't exist |
| `route_not_found` | The supplied route id or `(method, uri)` pair didn't match any indexed route |
| `event_not_found` | The supplied event FQN isn't in the graph |
| `no_embedder` / `no_vector_dimensions` | `semantic_search` was called against a graph indexed without an embedder |
| `no_confident_match` | (returned only by `ask`) no rule matched and the semantic fallback's top hit was below threshold; see `suggested_tools` for next steps |

### Semantic search snippets

`semantic_search` returns each hit with a `snippet` field containing real source code (default 30 lines, configurable via `--snippet-lines` 0–100). Pass `--snippet-lines 0` for metadata-only responses when running in tight token budgets.

## Built-in profiles

| Name | Description |
|---|---|
| `laravel-default` | Vanilla Laravel (no special pattern) |
| `laravel-api` | API-only Laravel (JSON responses, no Blade) |
| `laravel-actions` | Action-based architecture |
| `laravel-ddd` | Domain-Driven Design module layout |
| `laravel-ddd-cqrs` | DDD + CQRS with commands and queries |
| `laravel-filament` | Filament admin panel |
| `laravel-repository` | Repository pattern over Eloquent |

Run `nexus profile list` for the complete list, `nexus profile detect` to auto-detect your project's profile.

## Configuration

### `nexus.yml` (project-level)

```yaml
schema_version: '1.0'

project:
  slug: my-laravel-app   # Used for storage namespace

profile: laravel-default  # Built-in profile name (auto-detected if omitted)

indexing:
  include_tests: false
  exclude_paths:
    - storage/
    - bootstrap/cache/
```

### `~/.nexus/config.yml` (user-level)

```yaml
schema_version: '1.0'

embedder:
  provider: fastembed          # fastembed | ollama | voyage | openai
  model: all-MiniLM-L6-v2

cost:
  confirm_above_usd: 0.50      # Gate paid-embedder runs

ask:
  semantic_confidence_floor: 0.65  # Tune for your embedder
```

The `ask.semantic_confidence_floor` is the vector_score threshold below which `nexus ask` returns a `no_confident_match` refusal instead of low-quality semantic hits. Different embedders produce different score distributions:

- `nomic-embed-text` (Ollama default) — 0.65 is sensible
- `voyage-code-3` — relevant code typically scores higher; consider 0.70
- `openai/text-embedding-3-large` — sits a bit lower; consider 0.55–0.60

Lower it if `ask` refuses too eagerly on borderline questions; raise it for stricter behaviour.

## Architecture

```
nexus/
├── core/           # Pure domain: graph builder, query engine, classifiers, chunkers
├── adapters/       # I/O: SQLite, LanceDB, embedder backends, PHP extractor
├── pipeline/       # Indexing pipeline (6 passes)
├── profiles/       # Built-in YAML profiles + auto-detection
└── interfaces/
    ├── cli/        # Click commands
    └── mcp/        # FastMCP server adapter
```

The pure core never imports adapter modules. All dependencies are injected at the edges.

## Development

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or plain pip
- PHP 8.2+ and Composer (only needed if working on the extractor package)

### Clone and install

```bash
git clone https://github.com/nexus-tools/nexus.git
cd nexus

# Install the package in editable mode with all dev + embedder extras
uv sync --all-extras
```

This installs the `nexus` CLI directly from your working tree. Any source change is reflected immediately — no reinstall needed.

```bash
nexus --version   # should print the version from nexus/version.py
```

### Running against a real Laravel project

Point Nexus at a local Laravel project to exercise the full pipeline from your dev build:

```bash
# Initialise (creates nexus.yml in the Laravel project)
uv run nexus init --project-path /path/to/your-laravel-app

# Build the index
uv run nexus index rebuild --project-path /path/to/your-laravel-app

# Run a query
uv run nexus query list_routes --project-path /path/to/your-laravel-app
```

Alternatively, install the extractor into the Laravel project first (required for `index rebuild`):

```bash
cd /path/to/your-laravel-app
composer require --dev nexus/extractor-php
```

#### Laravel project running in Docker

Nexus runs `php artisan nexus:extract` as a subprocess. If PHP lives inside a Docker container rather than on the host, use the `--php` flag to provide a wrapper command. Nexus shell-splits the value, so multi-word commands work correctly:

```bash
# Docker Compose
uv run nexus index rebuild \
  --project-path /path/to/your-laravel-app \
  --php "docker compose exec -T app php"

# Laravel Sail
uv run nexus index rebuild \
  --project-path /path/to/your-laravel-app \
  --php "/path/to/your-laravel-app/vendor/bin/sail php"
```

**Requirements for this to work:**

1. The Laravel project files must be volume-mounted at the **same absolute path** inside the container as on the host. Most Sail and Compose setups do this by default. If the paths differ, use a wrapper script instead (see below).
2. Nexus writes `reflection.json` to `~/.nexus/projects/<slug>/reflection.json` on the host. The container does not need to access that path.

**Wrapper script approach (most flexible):**

Create a small script on your host `PATH`:

```bash
#!/usr/bin/env bash
# ~/bin/php-myapp — routes php through the project's Docker container
exec docker compose -f /path/to/your-laravel-app/docker-compose.yml \
     exec -T app php "$@"
```

Make it executable and use it:

```bash
chmod +x ~/bin/php-myapp
uv run nexus index rebuild --project-path /path/to/your-laravel-app --php php-myapp
```

This also works with `nexus index sync` and carries across all future runs.

### Working on the PHP extractor

The Composer package lives under `packages/nexus-extractor-php/`. Its vendor directory is pre-installed; you do not need to run `composer install` unless you are adding a new dependency.

```bash
cd packages/nexus-extractor-php

composer lint        # Pint formatting check
composer format      # Apply Pint formatting
composer stan        # PHPStan static analysis
composer test        # PHPUnit test suite
composer check       # All of the above in one command
```

#### Using your local extractor in a Laravel project (optional)

When working on the PHP package and the Python pipeline simultaneously, you can point Composer at your local checkout instead of pulling from Packagist. This means every change to `packages/nexus-extractor-php/src/` is live immediately — no `composer update` needed between edits.

Add a `repositories` entry to the Laravel project's `composer.json`:

```json
{
    "repositories": [
        {
            "type": "path",
            "url": "/path/to/nexus-v2/packages/nexus-extractor-php",
            "options": {
                "symlink": true
            }
        }
    ],
    "require-dev": {
        "nexus/extractor-php": "@dev"
    }
}
```

Then install:

```bash
cd /path/to/your-laravel-app
composer update nexus/extractor-php
```

Composer creates a symlink from `vendor/nexus/extractor-php` to your local `packages/nexus-extractor-php/` directory. Verify it:

```bash
ls -la vendor/nexus/extractor-php
# → should point to /path/to/nexus-v2/packages/nexus-extractor-php
```

To switch back to the published Packagist version, remove the `repositories` entry and run:

```bash
composer require --dev nexus/extractor-php:^1.0
```

### Quality gate

Run the full quality gate before opening a PR:

```bash
# Python
uv run ruff format nexus/ tests/
uv run ruff check nexus/ tests/
uv run mypy --strict nexus/
uv run pytest tests/ -q

# Or via Make
make check

# PHP (from packages/nexus-extractor-php/)
composer check
```

### Running specific test layers

```bash
uv run pytest tests/unit/          # Fast pure unit tests only
uv run pytest tests/integration/   # Integration tests (real SQLite, LanceDB)
uv run pytest tests/contract/      # Protocol contract suites
uv run pytest tests/golden/        # Snapshot tests against the fixture Laravel app
```

### Build

```bash
uv build           # Produces dist/nexus-*.whl and dist/nexus-*.tar.gz
```

See `CLAUDE.md` for architectural principles, design patterns, and test discipline.

## Repository layout

```
nexus-v2/
├── nexus/                         # Python package
├── packages/nexus-extractor-php/  # Composer package (PHP)
├── tests/                         # Unit, integration, contract, golden, e2e
├── profiles/                      # Built-in YAML profiles
└── CLAUDE.md                      # Architecture and coding standards
```

## License

MIT
