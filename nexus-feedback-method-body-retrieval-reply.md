# Re: Can't retrieve large method bodies for `CreateProductRequest::rules()`

Thanks for the writeup — the detail (the three rephrased queries, the side-by-side with `validateInventorySettings`, the HTTP-probe workaround) was exactly what we needed to diagnose this without re-running anything ourselves. Posting back what we found, what shipped, and what's still open.

## TL;DR

- **Your chunk wasn't dropped.** It's in LanceDB with all 11 sibling method chunks. Hypothesis 1 (chunker drops large methods) is refuted.
- **Your Hypothesis 2 is correct, with a sharper mechanism.** The embedding for the 88-line `rules()` body is *systematically less similar* to every query than its smaller siblings — including queries containing the literal FQN.
- **Two new MCP tools shipped today: `get_node_body` and `get_full_block`.** They close your immediate pain. No re-index needed; the existing index from 2026-05-13 works as-is. Just restart the MCP server.
- **The underlying retrieval bug isn't fully fixed.** Issue [#1](https://github.com/the-messie-company/nexus/issues/1) tracks the benchmarking work. The new tools are the escape hatch, not the cure.

## What we found

We opened your index at `/home/lockhart/.nexus/projects/synthesq-api/` (the on-disk home for `nexus-run`) and tested both hypotheses end-to-end.

### Chunk presence — Hypothesis 1 refuted

```
chunk id: c1b3c06c81396a48
node_id:  method:App\Modules\Operations\Presentation\Requests\CreateProductRequest::rules
kind:     method
lines:    38-125  (88-line body)
```

All 12 expected `CreateProductRequest` chunks present, including the target. The chunker has no size cap on method chunks; `_emit_method` writes the full body verbatim (`nexus/core/chunking/php_chunker.py:328-350`). Your line range of 38–131 in `describe_class` was actually 38–125 in the chunk metadata — the closing brace is at 125.

### Cosine layer — Hypothesis 2 confirmed

We re-ran your queries A/B/C/D plus three FQN-heavy probes, recording the target chunk's rank across all 20,238 chunks (cosine, not approximate — exhaustive):

| Query | Target cosine | Top-1 cosine | Target rank / 20238 |
|---|---|---|---|
| **A**: `CreateProductRequest rules ... max 255` | 0.5876 | 0.7069 (`UploadDocumentCommand::validate`) | **2,082** |
| **C**: `rules sku required string max 100 unique products` | 0.5538 | 0.6225 (`GenerateVariantsRequest::rules`) | **228** |
| **F** (we added): literal FQN `App\Modules\...\CreateProductRequest rules` | 0.6242 | 0.7463 | **264** |
| **G** (we added): `CreateProductRequest::rules` | 0.5862 | 0.7205 | **1,946** |

Even with the literal FQN in the query, the target ranks at #264 — far below the default `top_k=30` candidate window that `semantic_search` pulls before re-ranking. The kind-weight re-rank (`controller_method` gets 1.20×, the top tier) never gets a chance — the chunk isn't in the pool to begin with.

### Mechanism — embedding dilution by length

The enrichment template (`nexus/core/chunking/enrichment.py`) builds the embedding input as:

```
controller_method: rules
file: /.../CreateProductRequest.php:38-125
namespace: App\Modules\Operations\Presentation\Requests
in class: App\Modules\Operations\Presentation\Requests\CreateProductRequest

source:
<88 lines of $tenantConnection rules>
```

`nomic-embed-text` averages token representations across the whole input. For an 88-line body that's ~300 repetitions of `'required', 'string', 'max:100', 'sometimes', 'numeric'`, the distinctive tokens at the top (class FQN, namespace, in-class line) get crushed by the repetitive validation array.

`CreateVariantRequest::rules` (the sibling that *did* surface in your Query C, 25-line body) ships the same FQN-bearing header on a much smaller body — proportionally, the FQN signal survives. Same shape, different signal-to-noise ratio.

This isn't `rules`-specific: it's also why your queries on `messages()`, `getAttributes()`, `prepareForValidation()`, etc. came back empty. Methods with small-but-generic content hit the same cliff for a different reason — low content distinctiveness instead of dilution.

## What you can do now

Pull the latest `nexus` (commit `3d609da` on `main`), restart your MCP server, and you have two new tools:

### `mcp__nexus__get_node_body`

The exact tool you sketched in your "Option 1". Resolves a graph node id to its source body:

```json
{
  "node_id": "method:App\\Modules\\Operations\\Presentation\\Requests\\CreateProductRequest::rules"
}
```

Response includes `content` (the 88-line body), `file`, `start_line`, `end_line`, `node_kind`, `container_class`. End-line is derived from the chunk metadata, so it's accurate even for methods whose graph node only carries the start line.

Verified on your index: returns the full `rules()` body in one call. First call has a one-time ~3.5 s cost (we walk the LanceDB table to build a node→chunk index, cached per session); subsequent calls are sub-millisecond. A graph-attribute lookup that removes the scan is tracked as a follow-up under issue #1.

### `mcp__nexus__get_full_block`

The synthesq-nexus-shaped tool you referenced in "Option 2":

```json
{
  "file_path": "/home/lockhart/projects/crm/api.crm.test/nexus-run/app/Modules/Operations/Presentation/Requests/CreateProductRequest.php",
  "start_line": 38,
  "end_line": 131,
  "context_lines": 2
}
```

Takes absolute or project-relative paths. Path containment is enforced against the indexed project root (the resolved path must live inside `coverage.project_path`), so an MCP-exposed agent can't `cat /etc/passwd`. Optional `context_lines` adds ±N lines around the requested range; capped at 20.

### Suggested workflow for your wizard-scoping use case

Replace the HTTP-probe escape hatch with this two-call pattern:

1. `describe_class { fqn: "App\\Modules\\...\\CreateProductRequest" }` — gives you the line range plus the `node_id` for every method.
2. `get_node_body { node_id: "method:...\\::rules" }` — gives you the body.

For the wizard issue #9 you mentioned (the `sku` validation question), step 1 + 2 gets you the exact `Rule::requiredIf(...)` you'd been guessing about — no backend stack required, no 422-probing.

## What we did **not** fix

The underlying `semantic_search` retrieval bug is still there. Your queries A/B/C/D will still bury `rules()` even on the new build. The new tools route around the bug; they don't fix it.

We filed [issue #1](https://github.com/the-messie-company/nexus/issues/1) with the full diagnostic and four candidate fixes ranked by effort:

1. Boost class FQN in the enrichment header (cheap, re-index needed)
2. Split large method chunks into overlapping sub-chunks (structural fix, re-index needed)
3. Add `scope_class`/`scope_file` filter to `semantic_search` (your "Option 3", cheap, no re-index)
4. Replace the LanceDB scan in `get_node_body` with a node-attribute lookup (perf cleanup, re-index needed)

Best honest estimate: 1–2 days of empirical iteration (re-embed, measure, regress-test). On the schedule for after Phase 5 closes; we didn't want to bundle it into the unblock.

## Your adjacent observations

> `find_dispatchers` returns 0 results for `App\Modules\Operations\Domain\Events\ProductCreated` even though Eloquent models clearly fire this via `$dispatchesEvents` arrays.

Correct — the static analyzer only catches literal `event(new X(…))` and `dispatch(new X(…))` call sites. The `$dispatchesEvents` array property is a Laravel-idiomatic auto-fire mechanism we don't currently parse. Worth a separate ticket and a docstring note on `find_dispatchers`; we'll file it.

> The `indexed_at` timestamp on every response is great — saved me from acting on stale data several times this session.

Noted and appreciated. We'll keep it.

## Anything else

If you hit another "I can see the metadata but not the body" gap after the upgrade, we want to hear about it — the body-retrieval surface is brand new and there may be node kinds we haven't thought through (e.g., enum cases, traits, anonymous classes). Open a ticket or grab the same level of detail you put in this one.

Thanks again — this is exactly the kind of feedback that makes the diagnosis cheap.
