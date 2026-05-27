# Nexus

> Laravel-specific code intelligence: a typed semantic graph + vector RAG, exposed via MCP and CLI for AI agents.

Nexus indexes a Laravel application into a typed knowledge graph (routes, models, controllers, jobs, events, listeners, policies, bindings, middleware) plus a vector store of code chunks. It exposes 28 structural and semantic query tools so agents like Claude Code, Cursor, and Copilot can answer questions about your codebase with less hallucination and fewer wasted tokens.

## The problem

When an AI agent needs to answer "how does the checkout flow work?" it reads dozens of files, loses track of the chain halfway through, and fills the gaps with plausible-sounding nonsense. A controller calls a service, which dispatches a job, which fires an event, which triggers three listeners - the agent reading files linearly can't follow this.

## The Nexus answer

Each of those relationships is a typed edge in the graph. `trace_route POST /checkout` follows `ROUTES_TO -> VALIDATES_WITH -> DISPATCHES -> FIRES -> LISTENS_TO` edges in a single traversal. The agent gets the complete, accurate chain - not a guess.

## How it works

```
Your Laravel app
      |
      v
PHP extractor (Artisan command)        -- boots your app, emits reflection.json
      |
      v
Graph builder                          -- typed nodes and edges (SQLite)
      |
      v
Chunker (tree-sitter) + Embedder       -- vector store (LanceDB)
      |
      v
LSP enrichment (optional)              -- CALLS edges via intelephense/phpactor
      |
      v
Query engine  ---------> MCP tools  ---------> Claude Code / Cursor / Copilot
                          CLI tools
```

1. **Extraction** - the Composer package `nick-nds/nexus-extractor` boots your Laravel app and emits a `reflection.json` capturing every route, class, event, job, middleware, binding, policy, scheduled task, and dispatch call.
2. **Graph build** - the Python builder creates typed nodes and edges: `ROUTES_TO`, `VALIDATES_WITH`, `FIRES`, `DISPATCHES`, `LISTENS_TO`, `EXTENDS`, `BOUND_TO`, and more.
3. **Chunk + embed** - source files are chunked at class/method boundaries by tree-sitter and embedded into a LanceDB vector store using your chosen backend.
4. **LSP enrichment** - optionally, intelephense or phpactor populates `CALLS` edges so the graph knows who-calls-whom across files.
5. **Query** - 28 tools traverse the graph and search the vector store. An agent calls `trace_route`, then `find_event_chains`, then `get_model_context` - each call follows edges, not file reads.

## Nexus and Laravel Boost

[Laravel Boost](https://laravel.com/ai/boost) is the official Laravel MCP server. Nexus and Boost solve different problems and work well together.

**Boost** gives agents runtime context: live database schema, application logs, browser errors, and semantic search over 17,000+ pieces of Laravel ecosystem documentation. It runs inside your Laravel process with zero indexing step.

**Nexus** gives agents structural context: how your codebase is wired together. It builds a persistent, typed graph of your application's routes, controllers, models, events, jobs, listeners, policies, and bindings - with cross-file call graph edges from LSP analysis and semantic search over your actual code.

| Capability | Boost | Nexus |
|---|---|---|
| Database schema and live queries | Yes | No |
| Laravel ecosystem docs (17k+ entries) | Yes | No |
| Application and browser logs | Yes | No |
| AI coding guidelines per package | Yes | No |
| Code structure graph (15+ edge types) | No | Yes |
| Cross-file call graph (LSP) | No | Yes |
| Request flow tracing (route to listeners) | No | Yes |
| Semantic search over your code | No | Yes |
| Event/job/listener chain traversal | No | Yes |
| Container binding resolution | No | Yes |
| Incremental indexing with change detection | No | Yes |

An agent using both gets: Boost for "what does the database look like?" and "what do the Laravel docs say about X?" - Nexus for "how does this codebase work?" and "trace the request flow for POST /orders."

## Installation

```bash
pip install nexus-php
# or with uv
uv pip install nexus-php
```

PHP extractor (required for indexing):

```bash
# in your Laravel project
composer require --dev nick-nds/nexus-extractor
```

PHP language server (recommended - provides `CALLS` edges):

```bash
npm install -g intelephense
```

Without an LSP, indexing still succeeds but the graph contains no `CALLS` edges - `find_callers`, `expand_call_tree`, and the call-graph side of `get_request_flow` will return empty results. The pipeline emits a clear warning when no server is found.

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
nexus query trace_route --method POST --uri /api/orders
nexus query get_model_context --fqn "App\\Models\\Order"
```

## MCP configuration

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

## MCP tools

Nexus exposes 28 query tools via MCP (and identically via CLI):

### Structural primitives

| Tool | Description |
|---|---|
| `list_routes` | List routes, optionally filtered by method or URI pattern |
| `list_scheduled_tasks` | List scheduled cron jobs |
| `list_by_kind` | List classes by type (models, controllers, events, jobs, etc.) |
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
| `find_dispatchers` | Code that dispatches a given event or job, with file/line |
| `find_event_chains` | Transitive event-listener chains (multi-hop) |
| `find_jobs_dispatching` | Jobs dispatched by a class |
| `get_policy_for` | Policy class governing a model |

### Dependency and call graph

| Tool | Description |
|---|---|
| `resolve_binding` | Resolve a service container binding (concrete, singleton, contextual) |
| `find_implementations` | Concrete implementations of an interface or abstract class |
| `find_callers` | All call sites for a method (requires LSP) |
| `expand_call_tree` | BFS through call graph upstream or downstream (requires LSP) |
| `find_cache_users` | Cache read/write sites |

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
    "indexed_at": "2026-05-03T12:13:34+00:00",
    "project_path": "/path/to/your-laravel-app"
  }
}
```

| Field | Meaning |
|---|---|
| `calls_indexed` | `true` when LSP ran and `CALLS` edges were populated. `find_callers` and `expand_call_tree` only return meaningful results when this is `true`. |
| `lsp_server` | Path or name of the LSP binary used. `null` when no LSP ran. |
| `embedder_id` | Embedder identifier. Score distributions vary by model. |
| `indexed_at` | ISO-8601 timestamp of when the index was last built. |
| `project_path` | Host-side project root the index was built from. |

### Error codes

Tools surface failures via `error` and `error_code` fields:

| Code | Meaning |
|---|---|
| `method_not_found` | The supplied method FQN doesn't exist in the graph |
| `class_not_found` | The supplied class FQN doesn't exist |
| `route_not_found` | No indexed route matches the supplied method/URI pair |
| `event_not_found` | The supplied event FQN isn't in the graph |
| `no_embedder` | `semantic_search` was called against a graph indexed without an embedder |
| `no_confident_match` | (`ask` only) no rule matched and semantic fallback scored below threshold |

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
| `rebuild` | Drop existing index and run the full pipeline |
| `sync` | Incremental re-index, reusing the embedding cache |
| `status` | Print stored metadata |
| `clear` | Delete the project's index |

```
nexus index rebuild [--project-path PATH] [--include-tests]
                    [--php CMD] [--container-project-path PATH]
                    [--lsp auto|none|intelephense|phpactor|PATH]
nexus index sync    [--project-path PATH] [--full]
```

`--lsp` selects the language server for `CALLS` edge population:

| Value | Behaviour |
|---|---|
| `auto` (default) | Detect intelephense or phpactor on PATH. Continue without LSP if none found. |
| `none` | Skip LSP enrichment entirely. |
| `intelephense` / `phpactor` / path | Use this server explicitly. Exit 2 if not found. |

### `nexus query`

Run any tool directly from the command line:

```
nexus query <tool-name> [OPTIONS]
```

All 28 tools listed above are available as subcommands with typed options.

### `nexus ask`

Classifier-routed free-text query - Nexus picks the best tool automatically:

```
nexus ask "how does checkout work?"
nexus ask --explain "which jobs does PlaceOrderAction dispatch?"
```

Returns the routing decision alongside the result so the calling agent can see why a particular tool ran.

### `nexus profile`

| Subcommand | Description |
|---|---|
| `list` | List all built-in profiles |
| `detect` | Auto-detect the best profile for a directory |
| `show NAME` | Show full definition of a profile |

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

Default transport is `stdio`. Use `sse` or `http` for shared server deployments.

## Docker support

If PHP runs inside a Docker container, use the `--php` flag:

```bash
# Docker Compose
nexus index rebuild \
  --project-path /path/to/your-laravel-app \
  --php "docker compose exec -T app php"

# Laravel Sail
nexus index rebuild \
  --project-path /path/to/your-laravel-app \
  --php "/path/to/your-laravel-app/vendor/bin/sail php"
```

The Laravel project files must be volume-mounted at the same absolute path inside the container as on the host (most Sail and Compose setups do this by default). Use `--container-project-path` if the paths differ.

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
| `laravel-ddd-cqrs` | DDD + CQRS with commands and queries |
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

```yaml
schema_version: '1.0'

embedder:
  provider: fastembed          # fastembed | ollama | voyage | openai
  model: all-MiniLM-L6-v2

cost:
  confirm_above_usd: 0.50

ask:
  semantic_confidence_floor: 0.65
```

The confidence floor controls when `nexus ask` returns a structured refusal instead of weak semantic hits. Tune per embedder:

| Embedder | Suggested floor |
|---|---|
| fastembed / nomic-embed-text | 0.65 |
| voyage-code-3 | 0.70 |
| openai/text-embedding-3-large | 0.55 - 0.60 |

## Architecture

```
nexus/
├── core/           # Pure domain: graph builder, query engine, classifiers, chunkers
├── adapters/       # I/O: SQLite, LanceDB, embedder backends, LSP clients
├── pipeline/       # Indexing pipeline (5 passes)
├── profiles/       # Built-in YAML profiles + auto-detection
├── config/         # Pydantic configuration models
└── interfaces/
    ├── cli/        # Click commands
    └── mcp/        # FastMCP server adapter
```

The pure core never imports adapter modules. All dependencies are injected at the edges.

## Development

### Prerequisites

- Python 3.11+
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

## Requirements

- Python 3.11+
- PHP 8.2+, Laravel 10 / 11 / 12
- Linux or macOS

## License

[Business Source License 1.1](LICENSE) - free to use for any purpose except building a competing commercial product. Converts to Apache 2.0 on 2030-05-27.

For alternative licensing, contact nitin.niku97@gmail.com.
