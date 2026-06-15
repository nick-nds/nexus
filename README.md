# Nexus

> Laravel code intelligence: a typed graph plus vector search over your codebase, exposed to AI agents over MCP and CLI.

Nexus indexes a Laravel application into a typed graph (routes, models, controllers, jobs, events, listeners, policies, bindings, middleware) and a vector store of code chunks. It exposes 25 structural and semantic query tools so agents like Claude Code, Cursor, and Copilot answer questions about your codebase with less hallucination and fewer tokens.

## The problem

Ask an agent "how does the checkout flow work?" and it reads dozens of files, loses the thread halfway through, and fills the gaps with guesses. A controller calls a service, the service dispatches a job, the job fires an event, the event triggers three listeners. An agent reading files top to bottom cannot follow that chain reliably.

## The Nexus answer

Each of those relationships is a typed edge. `trace_route POST /checkout` walks `ROUTES_TO`, `VALIDATES_WITH`, `DISPATCHES`, `FIRES`, and `LISTENS_TO` edges in one traversal and returns the actual chain, not a guess.

## How it works

```
Your Laravel app
      |
      v
PHP extractor (Artisan command)        boots your app, emits reflection.json
      |
      v
Graph builder                          typed nodes and edges (SQLite)
      |
      v
Chunker (tree-sitter) + embedder       vector store (LanceDB)
      |
      v
LSP enrichment (optional)              CALLS edges via intelephense/phpactor
      |
      v
Query engine  ------>  MCP tools  ------>  Claude Code / Cursor / Copilot
                       CLI tools
```

1. **Extraction.** The Composer package `nick-nds/nexus-extractor` boots your Laravel app and writes a `reflection.json` capturing every route, class, event, job, middleware, binding, policy, scheduled task, and dispatch call.
2. **Graph build.** The Python builder creates typed nodes and edges: `ROUTES_TO`, `VALIDATES_WITH`, `FIRES`, `DISPATCHES`, `LISTENS_TO`, `EXTENDS`, `BOUND_TO`, and more.
3. **Chunk and embed.** Source files are chunked at class and method boundaries by tree-sitter, then embedded into a LanceDB vector store using your chosen backend.
4. **LSP enrichment.** Optionally, intelephense or phpactor populates `CALLS` edges so the graph knows who calls whom across files.
5. **Query.** 25 tools traverse the graph and search the vector store. An agent calls `trace_route`, then `find_event_chains`, then `get_model_context`, following edges instead of reading files.

## Nexus and Laravel Boost

[Laravel Boost](https://laravel.com/ai/boost) is the official Laravel MCP server. Nexus and Boost solve different problems and work well together.

Boost gives agents runtime context: live database schema, application logs, browser errors, and semantic search over 17,000+ pieces of Laravel ecosystem documentation. It runs inside your Laravel process with no indexing step.

Nexus gives agents structural context: how your codebase is wired. It builds a persistent, typed graph of your routes, controllers, models, events, jobs, listeners, policies, and bindings, with cross-file call edges from LSP analysis and semantic search over your own code.

| Capability | Boost | Nexus |
|---|---|---|
| Database schema and live queries | Yes | No |
| Laravel ecosystem docs (17k+ entries) | Yes | No |
| Application and browser logs | Yes | No |
| AI coding guidelines per package | Yes | No |
| Code structure graph (20+ edge types) | No | Yes |
| Cross-file call graph (LSP) | No | Yes |
| Request flow tracing (route to listeners) | No | Yes |
| Semantic search over your code | No | Yes |
| Event/job/listener chain traversal | No | Yes |
| Container binding resolution | No | Yes |
| Incremental indexing with change detection | No | Yes |

Use Boost for "what does the database look like?" and "what do the Laravel docs say about X?". Use Nexus for "how does this codebase work?" and "trace the request flow for POST /orders".

## Installation

Nexus needs Python 3.11, 3.12, or 3.13. Python 3.14 is not supported yet: `pyarrow` (pulled in by LanceDB) has no prebuilt 3.14 wheels, so the install tries to compile it from source and fails without a full CMake and Arrow toolchain. If your system Python is 3.14, create the environment with an explicit interpreter: `uv venv --python 3.13`.

```bash
# [local-embeddings] adds the default fastembed backend. Indexing needs an
# embedder, and the bare nexus-php install ships none.
pip install 'nexus-php[local-embeddings]'
# or with uv (pin the interpreter if your system Python is 3.14)
uv venv --python 3.13
uv pip install 'nexus-php[local-embeddings]'
```

Indexing requires an embedder backend, and `nexus-php` ships none on its own. `[local-embeddings]` is the zero-setup default (fastembed, CPU). For the faster local Ollama backend or a hosted API, install `[ollama]`, `[openai]`, or `[voyage]` instead; see [Embedder backends](#embedder-backends). `nexus init` records your chosen backend in `~/.nexus/config.yml`; a project can override it via `nexus.yml` (see [Configuration](#configuration)).

PHP extractor (required for indexing), run inside your Laravel project:

```bash
composer require --dev nick-nds/nexus-extractor
```

PHP language server (recommended, provides `CALLS` edges):

```bash
npm install -g intelephense
```

Without an LSP, indexing still succeeds but the graph has no `CALLS` edges, so `find_callers`, `expand_call_tree`, and the call-graph part of `get_request_flow` return empty. The pipeline warns when no server is found.

## Quick start

```bash
# 1. Create nexus.yml in your Laravel project
cd /path/to/your/laravel-app
nexus init

# 2. Check your environment
nexus doctor

# 3. Build the index. --slug names this project's index namespace;
#    reuse the same slug for every sync, query, and MCP server.
nexus --slug my-app index rebuild

# 4. Query it (same --slug)
nexus --slug my-app ask "how does user authentication work?"
nexus --slug my-app query trace_route --method POST --uri /api/orders
nexus --slug my-app query get_model_context --fqn "App\\Models\\Order"
```

`--slug` is a **global** flag, so it goes **before** the subcommand. It selects the index namespace at `~/.nexus/projects/<slug>/`, and the same slug must be used to index, `sync`, `query`, and `mcp serve` a given project. Omit it and everything uses the `default` namespace, which is fine for a single project but collides once you index more than one.

## MCP configuration

Register the server with your agent, and pass your project's slug so the server knows which index to serve.

```json
{
  "mcpServers": {
    "nexus": {
      "command": "nexus",
      "args": ["--slug", "your-project-slug", "mcp", "serve"]
    }
  }
}
```

Or from the Claude Code CLI. Everything after `--` is the launch command, so global flags like `--slug` go there, before `mcp serve`:

```bash
claude mcp add nexus -- /abs/path/to/.venv/bin/nexus --slug your-project-slug mcp serve
```

**Project slug.** The server serves one index, stored at `~/.nexus/projects/<slug>/`. `--slug` is a global flag, so it must come before `mcp serve`. The slug is set at `nexus init`; it defaults to the slugified project-directory name and is saved in `nexus.yml`. The agent launches the server in its own working directory, so `nexus.yml` is not read automatically and the slug has to be explicit here. List your indexes with `ls ~/.nexus/projects/`. Pass `--storage-root` as well if your index lives outside the default `~/.nexus`.

**Virtualenv installs.** The agent does not run your shell activation, so `"command": "nexus"` will not be on its `PATH`. Use the absolute path to the venv binary instead, for example `/abs/path/to/.venv/bin/nexus` (the agent does not expand `~`, so a full path is required). The console-script shebang pins it to the venv interpreter, so no activation is needed. Set any environment the server needs, such as `OLLAMA_HOST`, in an `"env"` block next to `command` and `args`.

Then ask Claude: *"How does the checkout flow work?"* or *"Which jobs does PlaceOrderAction dispatch?"*

## MCP tools

Nexus exposes 25 query tools over MCP, and identically over the CLI.

### Structural primitives

| Tool | Description |
|---|---|
| `list_routes` | List routes, optionally filtered by method or URI pattern |
| `list_scheduled_tasks` | List scheduled cron jobs |
| `list_by_kind` | List classes by type (models, controllers, events, jobs, and so on) |
| `list_modules` | List DDD modules |
| `describe_module` | Describe a module's classes and imports |
| `explore_entity` | Fuzzy-find classes by name |
| `describe_class` | Full class view: kind, methods, related routes, events, jobs, policies |
| `describe_flow` | High-level description of a route flow |
| `get_model_context` | Eloquent model context: relations, scopes, observers, policies, usage sites |

### Route and request flow

| Tool | Description |
|---|---|
| `trace_route` | Full handler trace: middleware, controller, form request, policy |
| `get_request_flow` | Complete request lifecycle with event fan-out and job dispatches |
| `find_handlers` | Find route handlers matching a URI pattern |

### Events, jobs, and policies

| Tool | Description |
|---|---|
| `find_listeners` | All listeners for an event, with queue status |
| `find_dispatchers` | Code that dispatches a given event or job, with file and line |
| `find_event_chains` | Transitive event-listener chains (multi-hop) |
| `find_jobs_dispatching` | Jobs dispatched by a class |
| `get_policy_for` | Policy class governing a model |

### Dependency and call graph

| Tool | Description |
|---|---|
| `resolve_binding` | Resolve a service container binding (concrete, singleton, contextual) |
| `find_implementations` | Concrete implementations of an interface or abstract class |
| `find_callers` | All call sites for a method (requires LSP) |
| `expand_call_tree` | BFS through the call graph upstream or downstream (requires LSP) |
| `find_cache_users` | Cache read and write sites |

### Semantic retrieval

| Tool | Description |
|---|---|
| `semantic_search` | Vector similarity search over code chunks with graph-aware re-ranking |

### Source code access

| Tool | Description |
|---|---|
| `get_full_block` | Source code by file path and line range |
| `get_node_body` | Source code by graph node ID |

## Tool response contract

Every tool response carries a `coverage` block:

```json
{
  "coverage": {
    "calls_indexed": true,
    "lsp_server": "/usr/local/bin/intelephense",
    "embedder_id": "ollama:nomic-embed-text",
    "semantic_search_available": true,
    "indexed_at": "2026-05-03T12:13:34+00:00",
    "project_path": "/path/to/your-laravel-app"
  }
}
```

| Field | Meaning |
|---|---|
| `calls_indexed` | `true` when LSP ran and `CALLS` edges were populated. `find_callers` and `expand_call_tree` only return results when this is `true`. |
| `lsp_server` | Path or name of the LSP binary used, or `null` when none ran. |
| `embedder_id` | Embedder identifier. Score distributions vary by model. |
| `semantic_search_available` | `true` when the embedder answered a probe. `false`, with `semantic_search_unavailable_reason`, when it is configured but unreachable or no vectors were indexed. `null` when no embedder is wired in. |
| `indexed_at` | ISO-8601 timestamp of the last build. |
| `project_path` | Project root the index was built from. |

### Error codes

Tools report failures through `error` and `error_code` fields:

| Code | Meaning |
|---|---|
| `method_not_found` | The supplied method FQN does not exist in the graph |
| `class_not_found` | The supplied class FQN does not exist |
| `route_not_found` | No indexed route matches the supplied method and URI |
| `event_not_found` | The supplied event FQN is not in the graph |
| `no_embedder` | `semantic_search` ran against a graph indexed without an embedder |
| `no_confident_match` | (`ask` only) no rule matched and the semantic fallback scored below the threshold |

## CLI reference

### `nexus init`

Create `nexus.yml` in a project directory.

```
nexus init [--project-path PATH] [--slug SLUG] [--profile PROFILE]
           [--embedder EMBEDDER] [--non-interactive]
```

### `nexus doctor`

Run environment diagnostics: Python version, PHP, Composer, `nexus.yml` validity, extractor installation, data directory writability, and LSP responsiveness.

```
nexus doctor [--project-path PATH]
```

### `nexus index`

| Subcommand | Description |
|---|---|
| `rebuild` | Drop the existing index and run the full pipeline |
| `sync` | Incremental re-index, reusing the embedding cache |
| `status` | Print stored metadata |
| `clear` | Delete the project's index |

```
nexus --slug SLUG index rebuild [--project-path PATH] [--include-tests]
                                [--php CMD] [--container-project-path PATH]
                                [--lsp auto|none|intelephense|phpactor|PATH]
nexus --slug SLUG index sync    [--project-path PATH] [--full]
```

`--slug` is the global flag (before `index`) that picks the project namespace at `~/.nexus/projects/<SLUG>/`. Use the same slug you intend to `query` and `mcp serve` with; it defaults to `default` when omitted.

`--lsp` selects the language server for `CALLS` edge population:

| Value | Behaviour |
|---|---|
| `auto` (default) | Detect intelephense or phpactor on PATH. Continue without LSP if none is found. |
| `none` | Skip LSP enrichment entirely. |
| `intelephense` / `phpactor` / path | Use this server explicitly. Exit 2 if not found. |

### `nexus query`

Run any tool directly from the command line:

```
nexus query <tool-name> [OPTIONS]
```

All 25 tools above are available as subcommands with typed options.

### `nexus ask`

Classifier-routed free-text query. Nexus picks the tool for you:

```
nexus ask "how does checkout work?"
nexus ask --explain "which jobs does PlaceOrderAction dispatch?"
```

It returns the routing decision alongside the result so the calling agent can see why a tool ran.

### `nexus profile`

| Subcommand | Description |
|---|---|
| `list` | List all built-in profiles |
| `detect` | Auto-detect the best profile for a directory |
| `show NAME` | Show the full definition of a profile |

### `nexus cache`

| Subcommand | Description |
|---|---|
| `size` | Report embedding cache disk usage |
| `clear` | Delete all cached embeddings |

### `nexus install-hooks`

Install a Git post-commit hook that runs `nexus index sync` after each commit.

### `nexus mcp serve`

Start the MCP server:

```
nexus mcp serve [--transport stdio|sse|http] [--host HOST] [--port PORT]
```

The default transport is `stdio`. Use `sse` or `http` for shared server deployments.

## Docker support

When PHP runs inside a container (`docker exec`, Laravel Sail), point `--php` at the in-container PHP **and** pass `--container-project-path` with the path where the project is mounted inside the container. Host paths are not valid inside the container, so Nexus uses it to resolve `artisan` and to route the extractor's output file through the mounted project directory (`~/.nexus` is not mounted, so the output can't be written there directly). The host `--project-path` is still used for local checks and is what your queries reference.

```bash
# Docker Compose (project mounted at /var/www inside the container)
nexus --slug my-app index rebuild \
  --project-path /home/me/projects/my-app \
  --php "docker compose exec -T app php" \
  --container-project-path /var/www

# docker exec against a named container
nexus --slug my-app index rebuild \
  --project-path /home/me/projects/my-app \
  --php "docker exec my-app-container php" \
  --container-project-path /var/www

# Laravel Sail (Sail mounts the project at /var/www/html)
nexus --slug my-app index rebuild \
  --project-path /path/to/your-laravel-app \
  --php "./vendor/bin/sail php" \
  --container-project-path /var/www/html
```

Find the mount path with `docker inspect` or your `docker-compose.yml` `volumes:` entry (the right-hand side of `host:container`). Only when PHP runs directly on the host (no container) do you omit both `--php` and `--container-project-path`.

## Embedder backends

| Backend | Install | Speed | Notes |
|---|---|---|---|
| `fastembed` (default) | `pip install nexus-php[local-embeddings]` | ~5 chunks/s | No API key, no daemon, runs on CPU |
| `ollama` | `pip install nexus-php[ollama]` | ~76 chunks/s | Requires `ollama serve` running |
| `openai` | `pip install nexus-php[openai]` | API-bound | Requires `OPENAI_API_KEY` |
| `voyage` | `pip install nexus-php[voyage]` | API-bound | Requires `VOYAGE_API_KEY` |

## Built-in profiles

| Name | Description |
|---|---|
| `laravel-default` | Vanilla Laravel |
| `laravel-api` | API-only (JSON responses, no Blade) |
| `laravel-actions` | Action-based architecture |
| `laravel-ddd` | Domain-Driven Design module layout |
| `laravel-ddd-cqrs` | DDD with CQRS commands and queries |
| `laravel-filament` | Filament admin panel |
| `laravel-repository` | Repository pattern over Eloquent |

Run `nexus profile detect` to auto-detect your project's style.

## Configuration

### `nexus.yml` (project-level, committed to git)

```yaml
schema_version: '1.0'

project:
  slug: my-laravel-app

profile: laravel-default    # auto-detected if omitted

indexing:
  include_tests: false
  exclude_paths:
    - storage/
    - bootstrap/cache/
```

### `~/.nexus/config.yml` (user-level)

`nexus init` writes this file with the embedder you choose, so a fresh setup has a working backend. It's also where you change the embedder later.

```yaml
schema_version: '1.0'

embedder:
  provider: fastembed          # fastembed | ollama | voyage | openai
  model: BAAI/bge-small-en-v1.5
  # dimensions: 384            # pin for non-default model widths (Ollama needs this)

cost:
  confirm_above_usd: 0.50

ask:
  semantic_confidence_floor: 0.65
```

**Embedder precedence.** The index pipeline resolves the embedder as: the project `nexus.yml` `embedder:` block (if present) **overrides** this global `config.yml`, which is the user/machine default. A team can commit an `embedder:` block to `nexus.yml` to standardise on one backend for comparable vectors; otherwise the global default is used. If neither configures an embedder, indexing runs graph-only and `semantic_search` is unavailable (the `coverage.embedder_id` comes back `null`).

The confidence floor controls when `nexus ask` returns a structured refusal instead of weak semantic hits. Tune it per embedder:

| Embedder | Suggested floor |
|---|---|
| fastembed / nomic-embed-text | 0.65 |
| voyage-code-3 | 0.70 |
| openai/text-embedding-3-large | 0.55 to 0.60 |

## Architecture

```
nexus/
├── core/           # Pure domain: graph builder, query engine, classifiers, chunkers
├── adapters/       # I/O: SQLite, LanceDB, embedder backends, LSP clients
├── pipeline/       # Indexing pipeline (5 passes)
├── profiles/       # Built-in YAML profiles and auto-detection
├── config/         # Pydantic configuration models
└── interfaces/
    ├── cli/        # Click commands
    └── mcp/        # FastMCP server adapter
```

The pure core never imports adapter modules. Dependencies are injected at the edges.

## Development

### Prerequisites

- Python 3.11 to 3.13 (3.14 not supported yet)
- [uv](https://docs.astral.sh/uv/) (recommended)
- PHP 8.2+ and Composer (only for the extractor package)

### Setup

```bash
git clone https://github.com/nick-nds/nexus.git
cd nexus
uv sync --all-extras
nexus --version
```

### Quality gate

```bash
# Python
uv run ruff format nexus/ tests/
uv run ruff check nexus/ tests/
uv run mypy --strict nexus/
uv run pytest tests/ -q

# PHP (from packages/nexus-extractor-php/)
composer check
```

### Test layers

```bash
uv run pytest tests/unit/          # Fast pure tests
uv run pytest tests/integration/   # Real SQLite, LanceDB
uv run pytest tests/contract/      # Protocol contract suites
uv run pytest tests/golden/        # Snapshot tests against fixture app
uv run pytest tests/e2e/           # Full pipeline end-to-end
```

### How this is built

Nexus is developed with heavy use of AI coding assistants, under human review
and an enforced quality gate. We mention this because we'd rather be upfront
about it than have you wonder. The bar for what merges is the same regardless of
who or what wrote a line: `ruff`, `mypy --strict`, 90%+ enforced test coverage,
and the contract, golden, and end-to-end suites above all run in CI, and nothing
lands that doesn't pass them.

## Requirements

- Python 3.11 to 3.13 (3.14 not supported yet)
- PHP 8.2+, Laravel 10 / 11 / 12 / 13
- Linux or macOS

## License

[Business Source License 1.1](LICENSE). Free to use for any purpose except building a competing commercial product. Converts to Apache 2.0 on 2030-05-27.

For alternative licensing, contact nitin.niku97@gmail.com.
