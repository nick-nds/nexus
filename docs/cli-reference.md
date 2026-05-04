# CLI Reference

Complete reference for every `nexus` command, subcommand, option, argument, and exit code.

---

## Global options

These options apply to the `nexus` root command and are inherited by every subcommand.

```
nexus [OPTIONS] COMMAND [ARGS]...
```

| Option | Type | Default | Description |
|---|---|---|---|
| `--storage-root PATH` | Path | `~/.nexus/` | Root directory for all Nexus project data. |
| `--slug TEXT` | str | _(from `nexus.yml`)_ | Project slug; overrides the value in `nexus.yml`. |
| `--format [auto\|json\|pretty]` | choice | `auto` | Output format. `auto` uses rich/pretty on a TTY; JSON lines otherwise. |
| `--color / --no-color` | flag | `--color` | Enable or suppress ANSI colour codes. |
| `-v, --verbose` | flag | off | Emit debug-level log output. |
| `-y, --yes` | flag | off | Answer yes to all confirmation prompts (non-interactive mode). |
| `-V, --version` | flag | — | Print the installed Nexus version and exit. |

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Success. |
| `1` | Internal error or unrecoverable failure. |
| `2` | User action required (missing dependency, config not found, etc.). |

---

## `nexus init`

Create a `nexus.yml` configuration file interactively or non-interactively.

```
nexus init [OPTIONS]
```

| Option | Type | Default | Description |
|---|---|---|---|
| `--project-path PATH` | Path | `.` (current directory) | Root of the Laravel project to configure. |
| `--slug TEXT` | str | _(directory name)_ | Short identifier for this project. |
| `--profile TEXT` | str | _(auto-detected)_ | Profile to use (e.g. `laravel`, `laravel-ddd`). |
| `--embedder [fastembed\|ollama\|voyage\|openai]` | choice | `fastembed` | Embedding backend to configure. |
| `--non-interactive` | flag | off | Skip all prompts; use auto-detected/default values. |
| `--overwrite / --no-overwrite` | flag | `--no-overwrite` | Overwrite an existing `nexus.yml` without prompting. |

**What it does:**

1. Auto-detects the project profile (same logic as `nexus profile detect`).
2. Prompts for confirmation of the detected profile unless `--non-interactive`.
3. Prompts for embedder choice (skipped if `--non-interactive`).
4. Writes `nexus.yml` to `--project-path`.
5. Prints next-step instructions.

**Exit codes:** `0` success · `1` error · `2` project path not found.

---

## `nexus profile`

Inspect and query the profile system.

```
nexus profile COMMAND [ARGS]...
```

### `nexus profile list`

List all available built-in and custom profiles.

```
nexus profile list
```

No options. Outputs a table of profile names with short descriptions.

**Exit codes:** `0` always.

---

### `nexus profile detect`

Auto-detect the best-matching profile for a Laravel project.

```
nexus profile detect [OPTIONS]
```

| Option | Type | Default | Description |
|---|---|---|---|
| `--project-path PATH` | Path | `.` | Root of the Laravel project to inspect. |
| `--top INT` | int | `3` | Number of top-scoring candidate profiles to show. |

**Exit codes:** `0` success · `1` project path not found or detection failed.

---

### `nexus profile show`

Show the full contents of a named profile.

```
nexus profile show NAME
```

| Argument | Description |
|---|---|
| `NAME` | Profile name (e.g. `laravel`, `laravel-ddd`). |

**Exit codes:** `0` success · `1` profile not found.

---

## `nexus index`

Drive the indexing pipeline.

```
nexus index COMMAND [ARGS]...
```

### `nexus index rebuild`

Perform a full re-index of the project (all six pipeline passes).

```
nexus index rebuild [OPTIONS]
```

| Option | Type | Default | Description |
|---|---|---|---|
| `--project-path PATH` | Path | `.` | Laravel project root. |
| `--include-tests` | flag | off | Also index files under `tests/`. |

**Behaviour on missing dependencies:**

- If the Composer extractor package is not installed, the command exits with code `2` and prints the exact `composer require --dev` command to run.
- If an LSP server is not installed, the command continues, prints a one-time warning, and points to install instructions.

**Exit codes:** `0` success · `1` pipeline error · `2` user action required (missing extractor, missing `nexus.yml`).

---

### `nexus index sync`

Incrementally sync changed files since the last index run.

```
nexus index sync [OPTIONS]
```

| Option | Type | Default | Description |
|---|---|---|---|
| `--project-path PATH` | Path | `.` | Laravel project root. |
| `--include-tests` | flag | off | Also sync files under `tests/`. |

**Exit codes:** `0` success · `1` pipeline error · `2` user action required.

---

### `nexus index status`

Show the current index state (last run time, file counts, staleness).

```
nexus index status
```

No options.

**Exit codes:** `0` success · `1` storage not initialised (run `nexus index rebuild` first).

---

### `nexus index clear`

Delete all indexed data for the current project.

```
nexus index clear [OPTIONS]
```

| Option | Type | Default | Description |
|---|---|---|---|
| `--force` | flag | off | Skip the confirmation prompt. |

**Exit codes:** `0` success · `1` error.

---

## `nexus query`

Invoke a query tool directly. Subcommands are **auto-generated** at startup from the `ToolRegistry`; one subcommand per registered tool.

```
nexus query TOOL [OPTIONS]
```

Output is JSON by default. Add `--pretty` (via the root `--format pretty` flag) for human-readable output.

The following tools are registered in the default build:

---

### `nexus query describe_class`

Describe a PHP class: properties, methods, relationships, and where it fits in the graph.

```
nexus query describe_class [OPTIONS]
```

| Option | Type | Required | Default | Description |
|---|---|---|---|---|
| `--fqn TEXT` | str | yes | — | Fully-qualified class name (e.g. `App\Models\User`). |

---

### `nexus query find_callers`

Find all call sites of a method across the codebase.

```
nexus query find_callers [OPTIONS]
```

| Option | Type | Required | Default | Description |
|---|---|---|---|---|
| `--method-fqn TEXT` | str | yes | — | Fully-qualified method name (e.g. `App\Services\Auth::login`). |

---

### `nexus query find_dispatchers`

Find all places that dispatch a given event.

```
nexus query find_dispatchers [OPTIONS]
```

| Option | Type | Required | Default | Description |
|---|---|---|---|---|
| `--event TEXT` | str | yes | — | Event class FQN or short name (e.g. `App\Events\UserRegistered`). |

---

### `nexus query find_event_chains`

Trace the full listener/subscriber chain triggered by an event.

```
nexus query find_event_chains [OPTIONS]
```

| Option | Type | Required | Default | Description |
|---|---|---|---|---|
| `--event TEXT` | str | yes | — | Event class FQN or short name. |
| `--max-depth INT` | int | no | `3` | Maximum chain depth to traverse. |

---

### `nexus query find_handlers`

Find route handlers matching an optional URI glob, HTTP method, or handler FQN.

```
nexus query find_handlers [OPTIONS]
```

| Option | Type | Required | Default | Description |
|---|---|---|---|---|
| `--uri-glob TEXT` | str\|None | no | — | Shell-style glob for the URI (e.g. `/api/users*`). |
| `--method TEXT` | str\|None | no | — | HTTP method filter (e.g. `GET`, `POST`). |
| `--handler-fqn TEXT` | str\|None | no | — | Handler class/method FQN filter. |

---

### `nexus query find_implementations`

Find all concrete implementations of an interface or abstract class.

```
nexus query find_implementations [OPTIONS]
```

| Option | Type | Required | Default | Description |
|---|---|---|---|---|
| `--interface-fqn TEXT` | str | yes | — | Fully-qualified interface or abstract class name. |
| `--include-subclasses` | bool | no | `False` | Also include subclasses (not just direct implementations). |

---

### `nexus query find_jobs_dispatching`

Find all dispatch sites for a given job class.

```
nexus query find_jobs_dispatching [OPTIONS]
```

| Option | Type | Required | Default | Description |
|---|---|---|---|---|
| `--job TEXT` | str | yes | — | Job class FQN or short name (e.g. `App\Jobs\SendWelcomeEmail`). |

---

### `nexus query find_listeners`

List all listeners registered for an event.

```
nexus query find_listeners [OPTIONS]
```

| Option | Type | Required | Default | Description |
|---|---|---|---|---|
| `--event TEXT` | str | yes | — | Event class FQN or short name. |

---

### `nexus query get_model_context`

Return full context for an Eloquent model: relationships, observers, policies, events, and jobs.

```
nexus query get_model_context [OPTIONS]
```

| Option | Type | Required | Default | Description |
|---|---|---|---|---|
| `--fqn TEXT` | str | yes | — | Fully-qualified model class name (e.g. `App\Models\Order`). |

---

### `nexus query get_policy_for`

Return the Gate policy bound to a given model.

```
nexus query get_policy_for [OPTIONS]
```

| Option | Type | Required | Default | Description |
|---|---|---|---|---|
| `--model-fqn TEXT` | str | yes | — | Fully-qualified model class name. |

---

### `nexus query get_request_flow`

Show the complete request flow for a route: middleware, form request, controller, service, jobs, and events.

```
nexus query get_request_flow [OPTIONS]
```

| Option | Type | Required | Default | Description |
|---|---|---|---|---|
| `--route-id TEXT` | str\|None | no | — | Internal route identifier (from `list_routes`). |
| `--method TEXT` | str\|None | no | — | HTTP method (e.g. `POST`). |
| `--uri TEXT` | str\|None | no | — | Exact URI path (e.g. `/api/orders`). |

At least one of `--route-id`, `--method`/`--uri` must be supplied.

---

### `nexus query list_routes`

List registered routes with optional filters.

```
nexus query list_routes [OPTIONS]
```

| Option | Type | Required | Default | Description |
|---|---|---|---|---|
| `--method TEXT` | str\|None | no | — | HTTP method filter. |
| `--uri-glob TEXT` | str\|None | no | — | Shell-style glob for the URI. |
| `--name-glob TEXT` | str\|None | no | — | Shell-style glob for the route name. |
| `--middleware TEXT` | str\|None | no | — | Filter to routes that apply this middleware. |

---

### `nexus query resolve_binding`

Resolve a service-container binding to its concrete implementation.

```
nexus query resolve_binding [OPTIONS]
```

| Option | Type | Required | Default | Description |
|---|---|---|---|---|
| `--abstract TEXT` | str | yes | — | Abstract class or interface FQN registered in the container. |

---

### `nexus query semantic_search`

Search the codebase semantically using vector similarity.

```
nexus query semantic_search [OPTIONS]
```

| Option | Type | Required | Default | Description |
|---|---|---|---|---|
| `--query TEXT` | str | yes | — | Natural-language query string. |
| `--top-k INT` | int | no | `30` | Candidate results to retrieve from the vector store. |
| `--final-k INT` | int | no | `10` | Results to return after re-ranking. |

---

### `nexus query trace_route`

Trace all code touched by a specific route, depth-first.

```
nexus query trace_route [OPTIONS]
```

| Option | Type | Required | Default | Description |
|---|---|---|---|---|
| `--route-id TEXT` | str\|None | no | — | Internal route identifier (from `list_routes`). |
| `--method TEXT` | str\|None | no | — | HTTP method (e.g. `GET`). |
| `--uri TEXT` | str\|None | no | — | Exact URI path (e.g. `/api/users/{id}`). |

At least one of `--route-id`, `--method`/`--uri` must be supplied.

---

## `nexus ask`

Ask a free-text question about the codebase. Nexus classifies the question and routes it to the best-matching query tool automatically.

```
nexus ask [OPTIONS] TEXT...
```

| Argument/Option | Type | Description |
|---|---|---|
| `TEXT...` | str (nargs=-1) | The question, as a space-separated string or quoted. |
| `--explain` | flag | Print the classification plan (which tool would be called and why) without executing it. |

**Example:**

```bash
nexus ask "how does the login flow work"
nexus ask --explain "where is UserRegistered dispatched"
```

**Exit codes:** `0` success · `1` no suitable tool found or execution failed · `2` project not initialised.

---

## `nexus mcp`

Manage the MCP server.

```
nexus mcp COMMAND [ARGS]...
```

### `nexus mcp serve`

Start the FastMCP server. All tools from `ToolRegistry` are exposed automatically.

```
nexus mcp serve [OPTIONS]
```

| Option | Type | Default | Description |
|---|---|---|---|
| `--transport [stdio\|sse\|http]` | choice | `stdio` | Transport to use. `stdio` is recommended for Claude Code and Cursor. |
| `--host TEXT` | str | `127.0.0.1` | Bind host (SSE/HTTP only). |
| `--port INT` | int | `8000` | Bind port (SSE/HTTP only). |
| `--log-level [debug\|info\|warning\|error]` | choice | `info` | Log verbosity (SSE/HTTP only; stdio is always silent). |

**Stdio transport (default):** spawned directly by the agent. No host/port needed. Add to your agent config:

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

**SSE transport:**

```bash
nexus mcp serve --transport sse --host 0.0.0.0 --port 9000
```

**Exit codes:** `0` clean shutdown · `1` startup error.

---

## `nexus doctor`

Run environment diagnostics and print a structured health report.

```
nexus doctor [OPTIONS]
```

| Option | Type | Default | Description |
|---|---|---|---|
| `--project-path PATH` | Path | `.` | Laravel project root to check. |
| `--json-summary` | flag | off | Print the summary block as JSON (for tooling). |

**Checks performed:**

| Check | What is verified |
|---|---|
| `python_version` | Python ≥ 3.11 |
| `nexus_version` | Installed Nexus version |
| `data_directory` | `~/.nexus/` exists and is writable |
| `php` | `php` is on `PATH` and is version 8.2+ |
| `composer` | `composer` is on `PATH` |
| `nexus_yml` | `nexus.yml` present and valid in `--project-path` |
| `extractor` | Composer package `nexus/extractor-php` installed in project |
| `lsp` | At least one supported LSP (Intelephense or phpactor) is available |
| `embedder` | Configured embedder is reachable |

Each check prints `OK`, `WARN`, or `FAIL` with a remediation hint when not OK.

The report ends with a copy-pasteable **bug report summary block** for filing issues.

**Exit codes:** `0` all checks passed · `1` one or more `FAIL` checks.

---

## `nexus cache`

Manage the embedding cache.

```
nexus cache COMMAND [ARGS]...
```

### `nexus cache size`

Print the current size of the embedding cache on disk.

```
nexus cache size
```

No options.

**Exit codes:** `0` always.

---

### `nexus cache clear`

Delete all cached embeddings.

```
nexus cache clear [OPTIONS]
```

| Option | Type | Default | Description |
|---|---|---|---|
| `--force` | flag | off | Skip the confirmation prompt. |

**Exit codes:** `0` success · `1` error.

---

## `nexus install-hooks`

Install a Git post-commit hook that runs `nexus index sync` automatically after each commit.

```
nexus install-hooks [OPTIONS]
```

| Option | Type | Default | Description |
|---|---|---|---|
| `--project-path PATH` | Path | `.` | Laravel project root (must contain `.git/`). |
| `--force` | flag | off | Overwrite an existing post-commit hook. |

The hook is written to `.git/hooks/post-commit` and runs `nexus index sync --quiet` in the background so it does not block the commit.

**Exit codes:** `0` success · `1` `.git/` not found or hook already exists without `--force`.
