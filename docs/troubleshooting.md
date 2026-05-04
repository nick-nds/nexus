# Troubleshooting

Common problems and how to fix them. If you're filing a bug, run `nexus doctor` first and paste the output into the issue.

---

## 1. `nexus doctor` shows PHP not found

**Symptom:** `nexus doctor` reports `php — not found on PATH` with status `error`.

**Fix:**

Install PHP 8.2+ and make sure it's on your PATH:

```bash
# macOS (Homebrew)
brew install php

# Ubuntu / Debian
sudo apt install php8.3 php8.3-cli

# Verify
php --version
```

---

## 2. `nexus index rebuild` exits with code 2 — extractor not found

**Symptom:**

```
Error: PHP extractor not available: ...
Hint: install it with `composer require --dev nexus/extractor-php`
```

**Fix:**

Inside your Laravel project:

```bash
composer require --dev nexus/extractor-php
```

If `composer` itself is missing, install it from [getcomposer.org](https://getcomposer.org/download/).

---

## 3. `nexus index rebuild` hangs or times out

**Symptom:** The extraction step hangs indefinitely (no progress after "Running extractor").

**Possible causes and fixes:**

1. **Very large project** — try passing `--project-path` to a subdirectory, or add `exclude_paths` to `nexus.yml`.
2. **Extractor subprocess crash** — run `php artisan nexus:extract --dry-run` directly to see the PHP error.
3. **Low PHP `memory_limit`** — set `memory_limit = 512M` (or higher) in your `php.ini`.

---

## 4. `nexus index rebuild` fails with "no_graph" error

**Symptom:**

```
[no_graph] EmbedAndPersistPass needs a graph.
```

**Fix:** This means `BuildGraphPass` produced no output. The most common cause is an extraction failure that was silently swallowed. Run with `--verbose` to see the full log:

```bash
nexus --verbose index rebuild
```

---

## 5. Semantic search returns no results

**Symptom:** `nexus query semantic_search --query "..."` returns an empty list.

**Possible causes:**

1. **No embedder configured** — the pipeline ran without embedding (you'll see a `no_embedder` warning in the index log). Check `~/.nexus/config.yml` for `embedder.provider`.
2. **fastembed model not downloaded** — fastembed downloads the model on first run. If the download failed (network error, disk space), the cache is empty.
3. **Index was not rebuilt after configuring the embedder** — run `nexus index rebuild` again.

---

## 6. `nexus mcp serve` doesn't appear in Claude Code / Cursor

**Symptom:** After adding Nexus to the MCP config, tools don't show up.

**Fix:**

1. Verify the config path is correct for your OS.
2. Run `nexus mcp serve` manually in a terminal to confirm it starts without error.
3. Check that the `nexus` executable is on the PATH used by Claude Code / Cursor (they may not inherit your shell's PATH on macOS).

**macOS PATH fix** — add to `~/.zshrc` (or `~/.bash_profile`):

```bash
export PATH="/usr/local/bin:$PATH"
```

Or use the full path in the MCP config:

```json
{
  "mcpServers": {
    "nexus": {
      "command": "/usr/local/bin/nexus",
      "args": ["mcp", "serve"]
    }
  }
}
```

---

## 7. `nexus query` returns `class_not_found` for a class I know exists

**Symptom:** Querying a class that definitely exists returns `"error_code": "class_not_found"`.

**Possible causes:**

1. **The index is stale** — run `nexus index sync` to pick up recent changes.
2. **Namespace escaping** — on the CLI, backslashes must be escaped: `"App\\Models\\User"` or use single quotes to avoid shell expansion.
3. **The class is excluded** — check `exclude_paths` in `nexus.yml`.

---

## 8. High memory usage during indexing

**Symptom:** `nexus index rebuild` uses more than 2 GB of RAM.

**Fix:** This is expected for large projects (50k+ lines). The embedding pass streams chunks in batches to cap memory. If you're hitting OOM, try:

```yaml
# nexus.yml
indexing:
  embed_batch_size: 64   # default 256 — lower to reduce peak RSS
```

---

## 9. Coverage check fails in CI

**Symptom:** `pytest --cov-fail-under=90` fails after adding a new module.

**Fix:** This is enforced by CLAUDE.md. Write tests for your new code. See the [CLAUDE.md coverage targets](../CLAUDE.md#testing-standards).

---

## 10. `nexus doctor` reports LSP not found

**Symptom:** `nexus doctor` shows `lsp — no LSP server found` with status `warning`.

**Effect:** The index still works. `find_callers` and CALLS-edge enrichment are simply skipped — all other tools work normally.

**Fix (optional):**

```bash
# Install Intelephense
npm install -g intelephense

# Or install phpactor
# See https://phpactor.readthedocs.io/en/master/usage/standalone.html
```

---

## Still stuck?

Run `nexus doctor` and file an issue at **https://github.com/nexus-php/nexus/issues** with the output. Include:

- Your OS and Python version (`python --version`)
- The `nexus doctor` JSON summary (`nexus doctor --json-summary`)
- The full error output with `nexus --verbose index rebuild`
