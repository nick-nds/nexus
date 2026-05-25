---
date: 2026-05-25
status: approved-for-implementation
phase: 5 (enhancement)
tier: OSS (free)
author: brainstormed with Claude (Opus 4.7) and Niku Nitin
---

# Incremental LSP Enrichment — Design Spec

## Summary

Make `nexus index sync` incremental by scoping the LSP enrichment pass to only query methods in files that changed since the last indexed commit. CALLS edges for unchanged methods are carried forward from the previous graph. This reduces a typical post-commit sync from ~17 minutes to ~60 seconds for a 22k-node project.

## Context

### What's true today

- `nexus index sync` runs the full five-pass pipeline (extract → build_graph → enrich_with_lsp → chunk → embed_and_persist) every time.
- The embedding cache already provides content-hash-based "unchanged → reuse" semantics for the embed pass (~18x speedup on unchanged chunks).
- The LSP enrichment pass (`EnrichWithLspPass`) queries every method node in the graph via `textDocument/references`, adding CALLS edges. For the user's CRM project (22,825 nodes, 19,560 methods), this takes ~12 minutes — 68% of the total pipeline time.
- `ProjectMeta` has a `last_indexed_commit` field but it's never written by the project pipeline.
- The LSP pass's algorithm: for each method M, ask "who references M?" and create edges FROM each caller TO M. Edges are discovered by querying the **target** (callee).
- `SqliteGraphStore.persist()` does a full DELETE + INSERT — always replaces the entire graph atomically.

### What's missing

No mechanism to skip LSP queries for methods whose source file hasn't changed. Every sync pays the full 12-minute LSP cost regardless of how many files actually changed.

### Why this works

The LSP `references` query for a method M returns the same result as long as:
- M's source file hasn't changed (M's position/signature is stable)
- No new references to M were added in other files

The second condition introduces a bounded imprecision (documented below), but for "commit frequently, sync after each" workflows the trade-off is acceptable.

## Design Decisions

| # | Decision | Rationale |
|---|---|---|
| 1 | Scope by target method's file | The pass discovers edges by querying the target (callee). If the target's file is unchanged, its references list is stable. Carry forward old edges targeting it. |
| 2 | Threshold fallback at >50% files changed | When most files changed (merge commits, large refactors), the overhead of loading/filtering old edges isn't worth it. Fall back to full enrichment. |
| 3 | Graceful degradation on git failures | If git isn't available, last_indexed_commit is missing, or the old commit is unreachable, fall back to full enrichment silently. Never fail the pipeline due to incremental logic. |
| 4 | No new storage schema | Carry-forward uses the existing graph's CALLS edges loaded from SQLite. No new tables, no new schema version. |
| 5 | `rebuild` always does full enrichment | Only `sync` attempts incremental. `rebuild` clears storage and starts fresh by definition. |
| 6 | Record `last_indexed_commit` for both `rebuild` and `sync` | Both commands leave a usable meta.json so the next `sync` has a baseline. |
| 7 | `--full` flag on `sync` forces full enrichment | Escape hatch when the user knows incremental would be imprecise (e.g., after rebasing). |
| 8 | Accept bounded imprecision for cross-file edges | New calls FROM changed files TO unchanged targets won't appear until the target file is touched or a full rebuild. Documented trade-off, not a bug. |

## Architecture

### PipelineContext extension

```python
@dataclass
class PipelineContext:
    ...
    changed_files: set[Path] | None = None  # None = full enrichment
```

`None` means "no incremental information available — run fully." An empty set means "nothing changed — carry forward everything."

### Changed-files computation (in `_index_helpers.py`)

```python
def _compute_changed_files(project_path: Path, storage: ProjectStorage) -> set[Path] | None:
    """Return PHP files changed since last indexed commit, or None for full mode."""
    meta = storage.read_meta()
    if meta is None or meta.last_indexed_commit is None:
        return None  # No baseline — full enrichment

    last_commit = meta.last_indexed_commit

    # Verify we're in a git repo and the old commit is reachable
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", last_commit],
            cwd=project_path, capture_output=True, timeout=10,
        )
        if result.returncode != 0:
            return None  # Commit unreachable (force-push, rebase)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None  # git not available

    # Compute diff
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{last_commit}..HEAD"],
            cwd=project_path, capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    # Filter to .php files, resolve to absolute paths
    changed: set[Path] = set()
    for line in result.stdout.strip().splitlines():
        path = project_path / line.strip()
        if path.suffix == ".php":
            changed.add(path.resolve())

    return changed
```

### Threshold check (in `_index_helpers.py`, after reflection is available)

The threshold check happens AFTER extraction + graph building (because we need the reflection to know total file count). It's applied just before passing the context to the pipeline, or inside the LSP pass itself.

Better: apply inside `EnrichWithLspPass` because only there do we have both `changed_files` and `method_nodes` with their file mappings. The pass can decide to ignore `changed_files` (treat as None) if the ratio exceeds 50%.

### EnrichWithLspPass incremental logic

```python
def run(self, ctx: PipelineContext) -> None:
    ...
    method_nodes = [n for n in ctx.graph.nodes if n.kind == NodeKind.METHOD]
    file_for_class = _build_class_file_map(ctx.graph)

    # Determine effective changed_files (apply threshold)
    changed_files = self._effective_changed_files(ctx, method_nodes, file_for_class)

    if changed_files is None:
        # Full enrichment (existing behavior)
        self._enrich_all(ctx, method_nodes, file_for_class)
    else:
        # Incremental enrichment
        self._enrich_incremental(ctx, method_nodes, file_for_class, changed_files)
```

#### `_effective_changed_files`

```python
def _effective_changed_files(self, ctx, method_nodes, file_for_class) -> set[Path] | None:
    if ctx.changed_files is None:
        return None

    # Count unique files in the method set
    all_files = {_file_for_method(m, file_for_class) for m in method_nodes} - {None}
    if not all_files:
        return None

    # Threshold: if >50% of files changed, fall back to full
    changed_php_files = ctx.changed_files & all_files
    if len(changed_php_files) > len(all_files) * 0.5:
        ctx.progress.emit(PassProgress(
            pass_name=self.name,
            message=f"Changed files ({len(changed_php_files)}) exceed 50% threshold "
                    f"({len(all_files)} total); using full enrichment.",
        ))
        return None

    return changed_php_files
```

#### `_enrich_incremental`

```python
def _enrich_incremental(self, ctx, method_nodes, file_for_class, changed_files):
    # 1. Load old CALLS edges from the previous graph
    old_graph = ctx.storage.graph().load()
    old_calls_edges = [e for e in old_graph.edges if e.kind == EdgeKind.CALLS]

    # 2. Partition methods
    new_node_ids = {n.id for n in ctx.graph.nodes}
    methods_to_query = []
    methods_to_skip = []
    for method in method_nodes:
        file = _file_for_method(method, file_for_class)
        if file is not None and file in changed_files:
            methods_to_query.append(method)
        else:
            methods_to_skip.append(method)

    # 3. Carry forward old CALLS edges targeting skipped methods
    skipped_ids = {m.id for m in methods_to_skip}
    carried = 0
    for edge in old_calls_edges:
        if edge.target in skipped_ids and edge.source in new_node_ids:
            ctx.graph.add_edge(edge)
            carried += 1

    # 4. Query LSP only for methods in changed files
    ...same loop as existing _enrich_all but over methods_to_query only...

    ctx.progress.emit(PassProgress(
        pass_name=self.name,
        message=f"Incremental: queried {len(methods_to_query)} methods, "
                f"carried forward {carried} edges for {len(methods_to_skip)} unchanged methods",
    ))
```

### Recording `last_indexed_commit`

In `_write_meta` (embed_and_persist.py):

```python
def _resolve_git_head(project_path: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_path, capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None
```

Called inside `_write_meta` and stored in the ProjectMeta.

### CLI changes

`nexus index sync` gains a `--full` flag:

```python
@index_group.command("sync")
@click.option("--full", is_flag=True, default=False,
              help="Force full LSP enrichment, ignoring incremental cache.")
```

When `--full` is set, `changed_files` is passed as `None` regardless of git state.

## Known Bounded Imprecision

**Scenario:** File A is modified to add a new call to method B (in an unchanged file).

**Result:** The CALLS edge A→B won't appear in the index until:
- B's file is also modified and indexed (next sync touching B)
- A full `nexus index rebuild` is run

**Why:** The pass discovers edges by querying the target (B). Since B's file didn't change, B is skipped, and the new reference from A isn't discovered.

**Impact:**
- Only affects CALLS edges (structural edges from extraction are always fresh)
- `find_callers` for B won't include A until corrected
- `expand_call_tree` from A won't show B as a callee
- Does NOT affect: routes, listeners, dispatchers, bindings, class relationships (all from extraction)

**Mitigation:**
- Self-corrects when B's file is eventually touched
- Threshold fallback ensures large changes get full enrichment
- `nexus index rebuild` or `nexus index sync --full` corrects immediately
- For "commit frequently" workflows, most files are touched within days

## Testing Strategy

### Unit tests

- `test_compute_changed_files.py` — git diff parsing, .php filtering, absolute path resolution, fallback on git failures, fallback on unreachable commit
- `test_enrich_incremental.py` — threshold logic (below/at/above 50%), carry-forward correctness (edges targeting unchanged methods, edges with deleted source nodes filtered out, edges targeting deleted methods filtered out), empty changed_files (carry all), all files changed (threshold triggers full)
- `test_last_indexed_commit.py` — written by both rebuild and sync, None when not a git repo

### Integration tests

- `test_sync_incremental_lsp.py` — fixture project, index fully, modify one file, sync, assert only that file's methods were queried (mock LSP tracking call count), assert carried-forward edges are present
- `test_sync_threshold_fallback.py` — modify >50% of files, assert full enrichment ran
- `test_sync_no_baseline.py` — first sync (no last_indexed_commit), assert full enrichment
- `test_sync_full_flag.py` — pass --full, assert full enrichment regardless of diff

### Contract

- After incremental sync, the graph has the same node count as a full rebuild (extraction + graph are always full)
- Edge count may differ from a full rebuild by the bounded imprecision (CALLS edges only)
- Structural edges (ROUTE_HANDLED_BY, LISTENS_TO, DISPATCHES, etc.) are always identical to a full rebuild

## Performance Expectations

| Scenario | Before | After |
|----------|--------|-------|
| Typical commit (1–5 files) | ~17 min | ~60–90s |
| Medium commit (20–50 files) | ~17 min | ~2–4 min |
| Large merge (>50% files) | ~17 min | ~17 min (threshold fallback) |
| First sync (no baseline) | ~17 min | ~17 min (no baseline) |

The savings come from LSP enrichment dropping from 19,560 method queries to 50–500 (proportional to changed files).

## Implementation Phases

### Phase A: Record last_indexed_commit (foundation)

Add `_resolve_git_head()` to `embed_and_persist.py`, wire it into `_write_meta`. Both `rebuild` and `sync` record the current HEAD after a successful pipeline run.

### Phase B: Compute changed_files in sync command

Add `_compute_changed_files()` to `_index_helpers.py`. Wire it into `run_pipeline` for sync (passing result to PipelineContext). Add `changed_files` field to PipelineContext. Add `--full` flag to sync command.

### Phase C: Incremental EnrichWithLspPass

The core change. Add threshold logic, carry-forward logic, and incremental query loop to `EnrichWithLspPass`. Full test coverage.

### Phase D: End-to-end validation

Run against the user's CRM project. Measure time savings. Verify correctness by comparing query results before/after incremental sync.
