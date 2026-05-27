# Frequently Asked Questions

---

## Monetisation & pricing

### Is Nexus free?

Yes. The core tool - indexing, all 15 query tools, MCP server, CLI - is MIT-licensed and free forever.

A Pro tier is planned for teams (see [the open-core strategy](https://github.com/nexus-php/nexus)) but is not required to use any of the features described in the v1.0 documentation.

### Will I be charged for using the embedder?

It depends on which embedder you choose:

| Provider | Cost |
|---|---|
| `fastembed` (default) | Free - runs locally, no API calls |
| `ollama` | Free - runs locally via Ollama |
| `voyage` | Paid - [Voyage AI pricing](https://docs.voyageai.com/docs/pricing) |
| `openai` | Paid - [OpenAI pricing](https://openai.com/api/pricing/) |

When you configure a paid provider, `nexus index rebuild` shows an estimated cost and asks for confirmation before starting. The `--yes` flag bypasses this.

The default `fastembed` backend is free and works well for most projects.

---

## Privacy & telemetry

### Does Nexus send any data home?

No. Nexus is a local-first tool by design. It:

- Never phones home.
- Never sends your code, queries, or index to any server.
- Never collects analytics, crash reports, or usage statistics.

This is a non-negotiable design principle, not a configuration option. The source is open - you can verify it yourself.

### Does the embedder send my code to the internet?

Only if you explicitly configure a paid cloud embedder (Voyage or OpenAI). With the default `fastembed` backend, all embedding happens locally on your machine. No code leaves your network.

---

## Laravel version support

### Which Laravel versions does Nexus support?

Laravel 10, 11, and 12 on PHP 8.2+.

Laravel 9 is not supported - it reached end-of-life in February 2024 and would require PHP 8.0/8.1 shims that add maintenance cost with no upside.

### Does Nexus work with Lumen?

No. Nexus is specifically designed for full Laravel (not Lumen, Symfony, or plain PHP). See the [non-goals document](../internal_docs/15-non-goals.md) for the reasoning.

### Does Nexus support Laravel Sail / Docker?

Yes, with a caveat: `nexus index rebuild` runs `php artisan nexus:extract` as a local subprocess. If `php` is inside a Docker container (Sail), you need to either:
- Run `nexus` inside the container (`sail shell` → `nexus index rebuild`), or
- Configure Sail to expose PHP on your host PATH.

---

## Platform support

### Does Nexus run on Windows?

WSL2 only. Native Windows is not supported:

- LanceDB's Python wheels target Linux and macOS.
- The PHP extractor relies on Unix subprocess behaviour.

Use WSL2 with Ubuntu 22.04 or later. Everything works as documented once PHP and Python are installed in WSL.

### Does Nexus run on macOS?

Yes, on both Intel and Apple Silicon. The `fastembed` backend uses ONNX Runtime which has native ARM64 wheels.

### Does Nexus run on Apple Silicon (M1/M2/M3)?

Yes. All dependencies have ARM64 wheels available on PyPI. No Rosetta required.

---

## Usage with AI agents

### Which agents work with Nexus?

Any agent that supports the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/). Tested with:

- **Claude Code** (stdio transport - spawn directly)
- **Cursor** (stdio transport)
- **Continue.dev** (SSE transport with `--transport sse`)

### Can multiple agents connect to the same Nexus server?

Yes, with `--transport sse` or `--transport http`:

```bash
nexus mcp serve --transport sse --host 0.0.0.0 --port 9000
```

With the default stdio transport, each agent spawns its own server process - which is fine for single-user setups.

### Does Nexus work with Claude.ai (the web app)?

Not directly. Claude.ai does not currently support connecting to local MCP servers. Use Claude Code (the CLI) or Cursor instead.

---

## Indexing

### How long does indexing take?

On a midrange laptop (Apple M2 / Intel i7):

| Project size | Cold run | Warm run (cache hit) |
|---|---|---|
| ~5k lines | ~30 seconds | ~5 seconds |
| ~50k lines | ~3–5 minutes | ~20–30 seconds |
| ~200k lines | ~15–20 minutes | ~1–2 minutes |

Times vary by embedder. `fastembed` is the fastest local option.

### What happens when I change a file?

Run `nexus index sync` (or let the Git post-commit hook do it). Sync re-runs the pipeline with the embedding cache warm, so only new/changed chunks are re-embedded. For a single-file change this typically completes in under 10 seconds.

### Does Nexus index vendor/ (Composer dependencies)?

No. Vendor packages are excluded by default. If you need to query a specific package, you can temporarily add it to `include_paths` in `nexus.yml`.
