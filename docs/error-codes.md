# Error code reference

Every Nexus query tool returns a structured response. When something
goes wrong, the response carries `error_code` (a stable short string)
and `error` (a human-readable message). Agents should branch on
`error_code`, not on the message text - the message can change with
each Nexus release; the code is part of the v1.0 contract.

This page is the authoritative list. The CI script
`scripts/list_error_codes.py` greps the codebase and asserts that
every code emitted in source is documented here.

## How to read this table

Each error code is grouped by **what an agent should do next**:

| Group | Meaning | Recovery |
|---|---|---|
| **Input invalid** | The call would never succeed regardless of indexed state - the args are wrong. | Fix the args and retry. |
| **Target not found** | The arg shape is fine, but no node/edge in this graph matches. | Try `explore_entity` / `list_routes` / `list_modules` to discover the right name, then retry. |
| **Feature not indexed** | This question requires data the index doesn't have (e.g. embeddings). | Re-index with the missing capability, or use a structural alternative. |
| **Ambiguous / no confident match** | A discovery tool found nothing or found too little to act on. | Broaden or narrow the query. |

## Tool errors

### Input invalid

| Code | Tools | What it means | Recovery |
|---|---|---|---|
| `invalid_kind` | `list_by_kind` | The `kind` argument is not a known `NodeKind` value. | Pass one of the values listed in the error message. |
| `non_listable_kind` | `list_by_kind` | The `kind` is a real value but has a dedicated tool (e.g. `route`, `middleware`, `scheduled_task`). | Use the dedicated tool the error names. |
| `invalid_mode` | `find_cache_users` | The `mode` argument is not `any`, `read`, or `write`. | Use one of the three. |
| `invalid_direction` | `expand_call_tree` | The `direction` argument is not `upstream` or `downstream`. | Use one of the two. |
| `missing_filter` | `find_handlers` | Neither `uri_glob` nor `controller` was provided. | Pass at least one filter. |

### Target not found

| Code | Tools | What it means | Recovery |
|---|---|---|---|
| `route_not_found` | `trace_route`, `get_request_flow` | No route matches the supplied `route_id` / `(method, uri)` pair. | Use `list_routes` or `describe_flow` to find the right URI. |
| `class_not_found` | `describe_class`, `find_implementations`, `get_model_context` | No class node has the supplied FQN. | Use `explore_entity` for short-name discovery; verify the FQN's slashes are escaped (`App\\Models\\User`). |
| `model_not_found` | `get_policy_for` | The supplied class FQN exists but isn't a `MODEL`-kind node. | Pass a model FQN (or use `describe_class` to confirm the node's kind). |
| `policy_not_found` | `get_policy_for` | The model exists but no `Policy` is registered for it. | Confirm with `list_by_kind kind=policy` - sometimes the policy is registered under a different model. |
| `method_not_found` | `find_callers`, `expand_call_tree` | No method node matches `Class::method`. | Confirm via `describe_class` first that the method exists. |
| `event_not_found` | `find_listeners`, `find_dispatchers`, `find_event_chains` | No event node matches the supplied class FQN/name. | Use `list_by_kind kind=event` to enumerate. |
| `job_not_found` | `find_jobs_dispatching` | No job node matches the supplied class. | Use `list_by_kind kind=job`. |
| `binding_not_found` | `resolve_binding` | The container does not have a binding for the supplied abstract. | Container bindings are extracted from `app/Providers/*ServiceProvider.php` - confirm the binding is declared there, or pass the concrete class FQN directly. |
| `key_not_found` | `find_cache_users` | No `cache_key` node matches the literal/glob/substring. | Re-check that the index was built with the static analyser (see `response.coverage.cache_indexed`). |
| `empty_module` | `describe_module` | No classes exist under the supplied `prefix`. | Use `list_modules` to see the prefixes the index actually contains. |
| `no_matches` | `explore_entity`, `describe_flow` | The fuzzy matcher found nothing. | Broaden the query (try a shorter fragment, different casing) or use a kind-specific list tool. |

### Feature not indexed

These errors mean the call shape was correct but the index was built
without the data the tool needs.

| Code | Tools | What it means | Recovery |
|---|---|---|---|
| `no_embedder` | `semantic_search` | The query context has no embedder configured. | Configure an embedder backend (Ollama / Voyage / OpenAI) and re-index, or use a structural alternative tool. |
| `no_vector_dimensions` | `semantic_search` | The vector store has no recorded dimensionality (likely empty or never written). | Re-index with embedding enabled. |
| `calls_not_indexed` | `find_callers`, `expand_call_tree` | The index was built without an LSP server, so `CALLS` edges were never populated. | Re-index with `--lsp auto` (or `--lsp intelephense`); `response.coverage.calls_indexed` is the canary. |

The richer "feature flags" live on `response.coverage` - when that
block exists, check `coverage.calls_indexed`, `coverage.cache_indexed`,
etc. before treating an empty result as "no match." The error
codes above are emitted only when the tool fundamentally cannot run.

### Ambiguous / no confident match

| Code | Tools | What it means | Recovery |
|---|---|---|---|
| `low_relevance` | `semantic_search` | Candidates were fetched but none crossed the relevance threshold (`min_vector_score`); all were filtered out. | Use a more specific query, or lower `min_vector_score` (e.g. `0.3`) to inspect weak matches. |

## Source retrieval

These tools read raw source by node id or by file path and line
range. They can fail on lookup, path safety, or I/O - distinct from a
"target not found" because the arg shape was plausible.

| Code | Tools | What it means | Recovery |
|---|---|---|---|
| `node_not_found` | `get_node_body` | No node has the supplied `node_id`. | Discover a valid id via `explore_entity`, `list_by_kind`, or a `describe_*` tool, then retry. |
| `file_not_found` | `get_full_block` | `file_path` doesn't resolve to a regular file. | Pass a path a tool returned (e.g. `get_node_body`'s `file`), not a hand-typed guess. |
| `file_outside_project` | `get_full_block` | The resolved path lies outside the indexed project root; Nexus refuses to read it. | Pass a path within the indexed project. |
| `invalid_range` | `get_full_block` | `end_line` is less than `start_line`. | Pass a range where `end_line >= start_line`. |
| `range_out_of_bounds` | `get_full_block` | `start_line` is past end-of-file. | Check `total_file_lines` on the response and pass an in-range `start_line`. |
| `read_error` | `get_full_block` | The file exists but the OS read failed (permissions, transient I/O). | Check file permissions and retry. |

## `nexus ask` refusals

| Code | Where | What it means | Recovery |
|---|---|---|---|
| `no_confident_match` | `nexus ask` | The classifier didn't match a structural rule, and the best `semantic_search` hit scored below `ask.semantic_confidence_floor` (default 0.65). | Rephrase with a class FQN, route URI, or event name; or call a structural tool directly via `nexus query <tool>`. |

## Pipeline errors (indexing-time, not query-time)

These don't surface as `error_code` on a tool response; they're
appended to the pipeline `Outcome` and surfaced via
`nexus index status` and structlog warnings.

| Code | Where | What it means |
|---|---|---|
| `pass_crashed` | `nexus.pipeline.orchestrator` | A pipeline pass raised an exception. The orchestrator catches it, records a structured Error, and continues if subsequent passes are independent. |
| `embedder_failed` | `nexus.pipeline.passes.embed_and_persist` | The embedder backend raised on a batch. The batch is skipped; the rest of the pipeline continues. |

## When you see a code that's not in this table

That's a bug - file an issue. Run `python scripts/list_error_codes.py
--strict` locally to confirm; the script lists undocumented codes and
exits non-zero so it can run in CI.

The trace file (`nexus ask --trace <path>` or
`NEXUS_TRACE_DIR=...`) records the `error_code` of every tool
dispatch - useful for reproducing a failure and pasting it into the
issue.
