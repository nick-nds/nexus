# Getting Started

Go from `pip install` to your first query in under 10 minutes.

## Prerequisites

- Python 3.11 or newer
- PHP 8.2+ with Composer (for the extractor package)
- A Laravel 10, 11, or 12 project

## 1. Install Nexus

```bash
pip install nexus
nexus --version   # should print 1.0.0
```

Run a quick sanity check:

```bash
nexus doctor
```

All checks should be `ok` or `warning`. A `warning` on the LSP check is normal — CALLS-edge enrichment is optional.

## 2. Install the PHP extractor

Inside your Laravel project:

```bash
composer require --dev nexus/extractor-php
```

This installs the `nexus:extract` Artisan command that Nexus uses to read your codebase.

## 3. Initialise Nexus in your project

```bash
cd /path/to/your-laravel-project
nexus init
```

The `init` wizard:
1. Auto-detects your project profile (MVC, DDD, API-only, etc.)
2. Asks you to confirm the detected profile or pick another.
3. Asks which embedding backend to use.
4. Writes `nexus.yml` to the project root.

For a non-interactive environment:

```bash
nexus init --non-interactive
```

## 4. Index the project

```bash
nexus index rebuild
```

This runs the full indexing pipeline:

1. **Extraction** — runs `php artisan nexus:extract` to dump a `reflection.json`
2. **Graph build** — constructs the semantic graph (routes, models, events, jobs, etc.)
3. **Chunking** — splits the graph into embedding chunks
4. **Embed & persist** — embeds chunks and writes them to `~/.nexus/<project>/`

On a mid-range laptop, a fresh 50k-line Laravel project typically finishes in 2–5 minutes.

## 5. Run your first query

```bash
nexus query list_routes
```

You should see a JSON list of every registered route.

Try a few more:

```bash
# Describe a specific controller
nexus query describe_class --fqn "App\Http\Controllers\UserController"

# Trace a route end-to-end
nexus query trace_route --method GET --uri /api/v1/users

# Ask a natural-language question
nexus ask "how does the password reset flow work"
```

## 6. Connect to Claude Code or Cursor

Add Nexus to your agent's MCP server list.

**Claude Code** (`~/.claude/config.json` or per-project `.claude/config.json`):

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

**Cursor** (`.cursor/mcp.json`):

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

Restart Claude Code or Cursor. You should see all Nexus tools listed under the MCP provider.

## 7. Keep the index fresh (optional)

Install the Git post-commit hook to automatically sync the index after every commit:

```bash
nexus install-hooks
```

Or sync manually whenever you want:

```bash
nexus index sync
```

## What's next?

- [CLI Reference](cli-reference.md) — full option reference for every command
- [MCP Reference](mcp-reference.md) — all 15 query tools with example inputs and outputs
- [Profiles](profiles/) — what each built-in profile detects and when to use it
- [Troubleshooting](troubleshooting.md) — common problems and how to fix them
- [FAQ](faq.md) — privacy, telemetry, monetisation, platform support
- [Upgrading](upgrading.md) — version policy and migration guide
