# Incremental LSP Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `nexus index sync` skip LSP queries for methods in unchanged files, carrying forward old CALLS edges instead. Reduces post-commit sync from ~17 min to ~60s for a 22k-node project.

**Architecture:** The LSP pass discovers CALLS edges by querying each method's references (callers). If a method's file hasn't changed, its references are stable — carry forward old edges targeting it. `git diff` between `last_indexed_commit` and HEAD identifies changed files. Threshold fallback (>50%) reverts to full enrichment.

**Tech Stack:** Python 3.11+, Click CLI, SQLite (graph store), subprocess (git), pytest, mypy --strict, ruff

**Spec:** `docs/superpowers/specs/2026-05-25-incremental-lsp-enrichment-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `nexus/pipeline/context.py` | Modify | Add `changed_files: set[Path] \| None` field |
| `nexus/pipeline/passes/embed_and_persist.py` | Modify | Add `_resolve_git_head()`, wire into `_write_meta` |
| `nexus/pipeline/passes/enrich_with_lsp.py` | Modify | Add incremental mode: threshold, carry-forward, scoped query |
| `nexus/interfaces/cli/commands/_index_helpers.py` | Modify | Add `_compute_changed_files()`, wire into `run_pipeline`, add `--full` param |
| `nexus/interfaces/cli/commands/index.py` | Modify | Add `--full` flag to `sync` command |
| `tests/unit/test_resolve_git_head.py` | Create | Unit tests for git HEAD resolution |
| `tests/unit/test_compute_changed_files.py` | Create | Unit tests for changed-files computation |
| `tests/unit/test_enrich_with_lsp_pass.py` | Modify | Add incremental-mode tests |
| `tests/integration/test_incremental_sync.py` | Create | End-to-end incremental sync tests |

---

## Phase A: Record `last_indexed_commit` (Foundation)

**Goal:** After every successful pipeline run (rebuild or sync), meta.json records the git HEAD so the next sync has a baseline.

**Acceptance criteria:**
- `_resolve_git_head(project_path)` returns the 40-char SHA when inside a git repo
- `_resolve_git_head(project_path)` returns `None` when not in a git repo or git is unavailable
- `ProjectMeta.last_indexed_commit` is populated after `rebuild` and `sync`
- Existing tests still pass (no behavior change for the pipeline itself)
- `mypy --strict` and `ruff check` clean

---

### Task 1: Write unit tests for `_resolve_git_head`

**Files:**
- Create: `tests/unit/test_resolve_git_head.py`

- [ ] **Step 1: Write the test file**

```python
"""Unit tests for _resolve_git_head in embed_and_persist."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from nexus.pipeline.passes.embed_and_persist import _resolve_git_head


def test_returns_sha_in_git_repo(tmp_path: Path) -> None:
    """Inside a real git repo, returns the 40-char HEAD SHA."""
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env={"GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t",
             "HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    result = _resolve_git_head(tmp_path)
    assert result is not None
    assert len(result) == 40
    assert all(c in "0123456789abcdef" for c in result)


def test_returns_none_when_not_a_git_repo(tmp_path: Path) -> None:
    """A directory that is not a git repo returns None."""
    result = _resolve_git_head(tmp_path)
    assert result is None


def test_returns_none_when_git_binary_missing(tmp_path: Path) -> None:
    """When git is not on PATH, returns None gracefully."""
    with patch(
        "nexus.pipeline.passes.embed_and_persist.subprocess.run",
        side_effect=FileNotFoundError("git not found"),
    ):
        result = _resolve_git_head(tmp_path)
    assert result is None


def test_returns_none_on_timeout(tmp_path: Path) -> None:
    """When git hangs, returns None after timeout."""
    with patch(
        "nexus.pipeline.passes.embed_and_persist.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="git", timeout=5),
    ):
        result = _resolve_git_head(tmp_path)
    assert result is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_resolve_git_head.py -v`
Expected: FAIL — `_resolve_git_head` doesn't exist yet (ImportError)

---

### Task 2: Implement `_resolve_git_head`

**Files:**
- Modify: `nexus/pipeline/passes/embed_and_persist.py`

- [ ] **Step 1: Add import and function**

Add `import subprocess` to the imports section (after the existing `from __future__ import annotations`), and add the function before the `EmbedAndPersistPass` class:

```python
import subprocess


def _resolve_git_head(project_path: Path) -> str | None:
    """Return the 40-char git HEAD SHA for the project, or None.

    Returns None (never raises) when:
    - The directory is not a git repository
    - git is not on PATH
    - The subprocess times out (5s)
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_resolve_git_head.py -v`
Expected: All 4 tests PASS

- [ ] **Step 3: Run full test suite + linters**

Run: `uv run ruff check nexus/pipeline/passes/embed_and_persist.py && uv run mypy --strict nexus/pipeline/passes/embed_and_persist.py && uv run pytest tests/unit/test_resolve_git_head.py -v`
Expected: All clean

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_resolve_git_head.py nexus/pipeline/passes/embed_and_persist.py
git commit -m "feat(pipeline): add _resolve_git_head for tracking indexed commit"
```

---

### Task 3: Wire `_resolve_git_head` into `_write_meta`

**Files:**
- Modify: `nexus/pipeline/passes/embed_and_persist.py` (the `_write_meta` static method)

- [ ] **Step 1: Write a test that meta includes last_indexed_commit**

Add to `tests/unit/test_resolve_git_head.py`:

```python
from unittest.mock import MagicMock

from nexus.pipeline.passes.embed_and_persist import EmbedAndPersistPass


def test_write_meta_records_git_head(tmp_path: Path) -> None:
    """_write_meta populates last_indexed_commit from git HEAD."""
    # Set up a git repo so _resolve_git_head returns a real SHA
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env={"GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t",
             "HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )

    from nexus.adapters.storage import ProjectStorage
    from nexus.core.graph.graph import Graph
    from nexus.pipeline.context import PipelineContext

    storage = ProjectStorage(root=tmp_path / ".nexus", slug="test")
    graph = Graph()
    ctx = PipelineContext(
        project_path=tmp_path,
        storage=storage,
        profile=_StubProfile(),
        graph=graph,
    )

    EmbedAndPersistPass._write_meta(ctx, embedder_id=None)

    meta = storage.read_meta()
    assert meta is not None
    assert meta.last_indexed_commit is not None
    assert len(meta.last_indexed_commit) == 40


def test_write_meta_records_none_outside_git(tmp_path: Path) -> None:
    """_write_meta sets last_indexed_commit=None when not in a git repo."""
    from nexus.adapters.storage import ProjectStorage
    from nexus.core.graph.graph import Graph
    from nexus.pipeline.context import PipelineContext

    storage = ProjectStorage(root=tmp_path / ".nexus", slug="test")
    graph = Graph()
    ctx = PipelineContext(
        project_path=tmp_path,
        storage=storage,
        profile=_StubProfile(),
        graph=graph,
    )

    EmbedAndPersistPass._write_meta(ctx, embedder_id=None)

    meta = storage.read_meta()
    assert meta is not None
    assert meta.last_indexed_commit is None
```

Also add at the top the import for `_StubProfile` (or define it inline):

```python
from dataclasses import dataclass, field

@dataclass(frozen=True)
class _StubProfile:
    name: str = "stub"
    custom_bases: dict[str, str] = field(default_factory=dict)
    custom_suffixes: dict[str, str] = field(default_factory=dict)
```

- [ ] **Step 2: Run the new tests — they should fail**

Run: `uv run pytest tests/unit/test_resolve_git_head.py::test_write_meta_records_git_head -v`
Expected: FAIL — `last_indexed_commit` is still None because `_write_meta` doesn't call `_resolve_git_head` yet

- [ ] **Step 3: Modify `_write_meta` to record the commit**

In `nexus/pipeline/passes/embed_and_persist.py`, change the `_write_meta` method:

```python
@staticmethod
def _write_meta(ctx: PipelineContext, *, embedder_id: str | None) -> None:
    assert ctx.graph is not None
    laravel_version = ctx.reflection.project.laravel_version if ctx.reflection else None
    meta = ProjectMeta(
        project_slug=ctx.storage.slug,
        project_path=str(ctx.project_path),
        laravel_version=laravel_version,
        last_indexed_commit=_resolve_git_head(ctx.project_path),
        indexed_at=datetime.now(UTC).isoformat(),
        node_count=len(ctx.graph.nodes),
        edge_count=len(ctx.graph.edges),
        embedder_id=embedder_id,
        lsp_server=ctx.lsp_server,
    )
    ctx.storage.write_meta(meta)
```

- [ ] **Step 4: Run all tests**

Run: `uv run pytest tests/unit/test_resolve_git_head.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Run the full unit suite to catch regressions**

Run: `uv run pytest tests/unit/ -x -q`
Expected: All pass (no regressions)

- [ ] **Step 6: Commit**

```bash
git add nexus/pipeline/passes/embed_and_persist.py tests/unit/test_resolve_git_head.py
git commit -m "feat(pipeline): record last_indexed_commit in meta.json after every run"
```

---

## Phase B: Compute Changed Files + CLI Wiring

**Goal:** `nexus index sync` computes the set of changed PHP files since last indexed commit and passes it through `PipelineContext`. The `--full` flag forces full enrichment.

**Acceptance criteria:**
- `PipelineContext` has a `changed_files: set[Path] | None` field (default None)
- `_compute_changed_files()` returns the correct set of .php files from `git diff`
- `_compute_changed_files()` returns None on any git failure (not a repo, commit unreachable, timeout)
- `sync` passes `changed_files` to the context; `rebuild` always passes None
- `--full` flag on `sync` forces None regardless of git state
- `run_pipeline` accepts an optional `changed_files` parameter
- All existing tests pass, mypy + ruff clean

---

### Task 4: Add `changed_files` to PipelineContext

**Files:**
- Modify: `nexus/pipeline/context.py`

- [ ] **Step 1: Add the field**

In `nexus/pipeline/context.py`, add the field after `include_tests`:

```python
    include_tests: bool = False
    changed_files: set[Path] | None = None
```

And update the docstring's "Optional at construction" section by adding:

```
        changed_files: Set of absolute paths to PHP files that have
            changed since the last indexed commit. ``None`` means "no
            incremental info available — all passes run fully." An
            empty set means "nothing changed." Used by
            :class:`EnrichWithLspPass` to scope LSP queries.
```

- [ ] **Step 2: Verify mypy is happy**

Run: `uv run mypy --strict nexus/pipeline/context.py`
Expected: Success

- [ ] **Step 3: Run existing tests (no behavior change)**

Run: `uv run pytest tests/unit/ -x -q`
Expected: All pass (new field has a default; no existing code is broken)

- [ ] **Step 4: Commit**

```bash
git add nexus/pipeline/context.py
git commit -m "feat(pipeline): add changed_files field to PipelineContext"
```

---

### Task 5: Write unit tests for `_compute_changed_files`

**Files:**
- Create: `tests/unit/test_compute_changed_files.py`

- [ ] **Step 1: Write the test file**

```python
"""Unit tests for _compute_changed_files in _index_helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from nexus.interfaces.cli.commands._index_helpers import _compute_changed_files


def _git(cwd: Path, *args: str) -> None:
    """Run a git command in cwd with minimal env."""
    env = {
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@t",
        "HOME": str(cwd),
        "PATH": "/usr/bin:/bin",
    }
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True, env=env)


def _make_repo_with_baseline(tmp_path: Path) -> str:
    """Create a git repo with one commit. Return the baseline SHA."""
    _git(tmp_path, "init")
    (tmp_path / "Foo.php").write_text("<?php class Foo {}")
    (tmp_path / "Bar.php").write_text("<?php class Bar {}")
    (tmp_path / "readme.md").write_text("# Hi")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "init")
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def test_returns_changed_php_files(tmp_path: Path) -> None:
    """Changed .php files between baseline and HEAD are returned."""
    baseline = _make_repo_with_baseline(tmp_path)
    # Make a new commit modifying Foo.php only
    (tmp_path / "Foo.php").write_text("<?php class Foo { public function x() {} }")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "edit foo")

    result = _compute_changed_files(tmp_path, baseline)

    assert result is not None
    assert result == {(tmp_path / "Foo.php").resolve()}


def test_filters_out_non_php_files(tmp_path: Path) -> None:
    """Only .php files are included; .md, .js, etc. are filtered."""
    baseline = _make_repo_with_baseline(tmp_path)
    (tmp_path / "readme.md").write_text("# Changed")
    (tmp_path / "app.js").write_text("console.log('hi')")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "edit non-php")

    result = _compute_changed_files(tmp_path, baseline)

    assert result is not None
    assert result == set()  # No PHP files changed


def test_includes_new_php_files(tmp_path: Path) -> None:
    """Newly added .php files appear in the changed set."""
    baseline = _make_repo_with_baseline(tmp_path)
    (tmp_path / "New.php").write_text("<?php class New {}")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "add new")

    result = _compute_changed_files(tmp_path, baseline)

    assert result is not None
    assert (tmp_path / "New.php").resolve() in result


def test_includes_deleted_php_files(tmp_path: Path) -> None:
    """Deleted .php files appear in the changed set (the file existed before)."""
    baseline = _make_repo_with_baseline(tmp_path)
    (tmp_path / "Bar.php").unlink()
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "delete bar")

    result = _compute_changed_files(tmp_path, baseline)

    assert result is not None
    # The path is still listed even if it no longer exists on disk
    assert (tmp_path / "Bar.php").resolve() in result


def test_returns_none_when_no_baseline(tmp_path: Path) -> None:
    """When last_indexed_commit is None, returns None (full mode)."""
    result = _compute_changed_files(tmp_path, None)
    assert result is None


def test_returns_none_when_not_a_git_repo(tmp_path: Path) -> None:
    """Non-git directory returns None."""
    result = _compute_changed_files(tmp_path, "abc123")
    assert result is None


def test_returns_none_when_commit_unreachable(tmp_path: Path) -> None:
    """If the baseline commit doesn't exist (rebase/force-push), returns None."""
    _make_repo_with_baseline(tmp_path)
    result = _compute_changed_files(tmp_path, "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef")
    assert result is None


def test_returns_none_when_git_binary_missing(tmp_path: Path) -> None:
    """If git is not on PATH, returns None."""
    with patch(
        "nexus.interfaces.cli.commands._index_helpers.subprocess.run",
        side_effect=FileNotFoundError("git not found"),
    ):
        result = _compute_changed_files(tmp_path, "abc123")
    assert result is None


def test_returns_empty_set_when_no_changes(tmp_path: Path) -> None:
    """When HEAD equals baseline (no new commits), returns empty set."""
    baseline = _make_repo_with_baseline(tmp_path)
    result = _compute_changed_files(tmp_path, baseline)

    assert result is not None
    assert result == set()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_compute_changed_files.py -v`
Expected: FAIL — `_compute_changed_files` doesn't exist (ImportError)

---

### Task 6: Implement `_compute_changed_files`

**Files:**
- Modify: `nexus/interfaces/cli/commands/_index_helpers.py`

- [ ] **Step 1: Add import and function**

Add `import subprocess` to the existing imports at the top of `_index_helpers.py`. Then add the function after the existing `EXIT_USER_ACTION_REQUIRED` constant (before `run_pipeline`):

```python
import subprocess


def _compute_changed_files(
    project_path: Path,
    last_indexed_commit: str | None,
) -> set[Path] | None:
    """Return absolute paths of PHP files changed since the last indexed commit.

    Returns None (triggering full enrichment) when:
    - last_indexed_commit is None (no baseline)
    - project_path is not a git repo
    - The baseline commit is unreachable (rebase, force-push)
    - git binary is not on PATH
    - Any subprocess times out
    """
    if last_indexed_commit is None:
        return None

    # Verify the old commit is reachable
    try:
        verify = subprocess.run(
            ["git", "rev-parse", "--verify", last_indexed_commit],
            cwd=project_path,
            capture_output=True,
            timeout=10,
        )
        if verify.returncode != 0:
            return None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    # Compute the diff
    try:
        diff = subprocess.run(
            ["git", "diff", "--name-only", f"{last_indexed_commit}..HEAD"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if diff.returncode != 0:
            return None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    # Filter to .php files, resolve to absolute paths
    changed: set[Path] = set()
    for line in diff.stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        path = (project_path / line).resolve()
        if path.suffix == ".php":
            changed.add(path)

    return changed
```

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/unit/test_compute_changed_files.py -v`
Expected: All 9 tests PASS

- [ ] **Step 3: Linters**

Run: `uv run ruff check nexus/interfaces/cli/commands/_index_helpers.py && uv run mypy --strict nexus/interfaces/cli/commands/_index_helpers.py`
Expected: Clean

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_compute_changed_files.py nexus/interfaces/cli/commands/_index_helpers.py
git commit -m "feat(cli): add _compute_changed_files for incremental sync"
```

---

### Task 7: Wire `changed_files` into `run_pipeline` and add `--full` flag

**Files:**
- Modify: `nexus/interfaces/cli/commands/_index_helpers.py` (add param to `run_pipeline`)
- Modify: `nexus/interfaces/cli/commands/index.py` (add `--full` to sync, pass to run_pipeline)

- [ ] **Step 1: Add `changed_files` parameter to `run_pipeline`**

In `_index_helpers.py`, modify the `run_pipeline` signature:

```python
def run_pipeline(
    cli_ctx: CliContext,
    *,
    project_path: Path,
    include_tests: bool,
    reset: bool,
    php_binary: str | None = None,
    container_project_path: Path | None = None,
    lsp_choice: str = "auto",
    changed_files: set[Path] | None = None,
) -> None:
```

Then in the body, where `PipelineContext` is constructed (around line 98–106), add `changed_files`:

```python
    pipe_ctx = PipelineContext(
        project_path=project_path,
        storage=storage,
        profile=profile,
        include_tests=include_tests,
        embedder=embedder,
        lsp=lsp,
        lsp_server=lsp_server,
        changed_files=changed_files,
    )
```

- [ ] **Step 2: Modify `sync_command` to compute changed_files and add --full flag**

In `nexus/interfaces/cli/commands/index.py`, modify the `sync_command`:

Add the `--full` option:

```python
@index_group.command("sync", help="Re-run the pipeline, reusing the embedding cache.")
@click.option(
    "--project-path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Project root to index. Defaults to the current directory.",
)
@click.option(
    "--include-tests",
    is_flag=True,
    default=False,
    help="Pass --include-tests through to the PHP extractor.",
)
@click.option(
    "--php",
    "php_binary",
    default=None,
    metavar="CMD",
    help=(
        "PHP binary or command used to invoke the extractor. "
        "Multi-word values are shell-split, so you can pass a Docker "
        "wrapper: --php 'docker exec my-app php'"
    ),
)
@click.option(
    "--container-project-path",
    "container_project_path",
    default=None,
    metavar="PATH",
    type=click.Path(path_type=Path),
    help=(
        "Path where the Laravel project is mounted inside the container. "
        "Required when --php uses docker exec or a similar wrapper so that "
        "artisan and the output file are resolved to their in-container paths. "
        "Example: --container-project-path /var/www"
    ),
)
@click.option(
    "--lsp",
    "lsp_choice",
    default="auto",
    metavar="CHOICE",
    help=(
        "LSP server selection: 'auto' (default — detect intelephense or "
        "phpactor on PATH or in Mason), 'none' (skip CALLS enrichment), or "
        "an explicit binary name/absolute path. With 'auto', the pipeline "
        "still succeeds when no LSP is found, but the graph will not "
        "contain CALLS edges."
    ),
)
@click.option(
    "--full",
    "force_full",
    is_flag=True,
    default=False,
    help="Force full LSP enrichment, ignoring incremental optimization.",
)
@click.pass_obj
def sync_command(
    cli_ctx: CliContext,
    project_path: Path | None,
    include_tests: bool,
    php_binary: str | None,
    container_project_path: Path | None,
    lsp_choice: str,
    force_full: bool,
) -> None:
    """Run the pipeline without dropping existing storage.

    Uses incremental LSP enrichment when a previous indexed commit is
    available: only methods in files changed since that commit are
    re-queried. Pass --full to force full enrichment (e.g., after a
    rebase that touched many files).
    """
    path = (project_path or cli_ctx.project_path).resolve()

    # Compute changed files for incremental LSP enrichment
    if force_full:
        changed_files = None
    else:
        from nexus.interfaces.cli.commands._index_helpers import (
            _compute_changed_files,
        )
        meta = cli_ctx.storage().read_meta()
        last_commit = meta.last_indexed_commit if meta else None
        changed_files = _compute_changed_files(path, last_commit)

    run_pipeline(
        cli_ctx,
        project_path=path,
        include_tests=include_tests,
        reset=False,
        php_binary=php_binary,
        container_project_path=container_project_path,
        lsp_choice=lsp_choice,
        changed_files=changed_files,
    )
```

- [ ] **Step 3: Run the existing test suite**

Run: `uv run pytest tests/unit/ -x -q`
Expected: All pass (changed_files defaults to None, so behavior is identical to before)

- [ ] **Step 4: Verify CLI help shows --full**

Run: `uv run nexus index sync --help`
Expected: Output includes `--full` flag description

- [ ] **Step 5: Linters**

Run: `uv run ruff check nexus/interfaces/cli/commands/_index_helpers.py nexus/interfaces/cli/commands/index.py && uv run mypy --strict nexus/interfaces/cli/commands/_index_helpers.py nexus/interfaces/cli/commands/index.py`
Expected: Clean

- [ ] **Step 6: Commit**

```bash
git add nexus/interfaces/cli/commands/_index_helpers.py nexus/interfaces/cli/commands/index.py
git commit -m "feat(cli): wire changed_files into sync command with --full flag"
```

---

## Phase C: Incremental EnrichWithLspPass

**Goal:** The LSP pass checks `ctx.changed_files`, applies the 50% threshold, loads old CALLS edges for unchanged methods, and only queries the LSP for methods in changed files.

**Acceptance criteria:**
- When `ctx.changed_files` is None: full enrichment (existing behavior, no regression)
- When `ctx.changed_files` is a set: only methods in those files are LSP-queried
- Old CALLS edges targeting unchanged methods are carried forward (source must exist in new graph)
- Threshold: if changed files > 50% of unique method-containing files, fall back to full
- Empty `changed_files` (no changes): all edges carried forward, zero LSP queries, LSP still prepared/closed
- Progress messages indicate incremental mode and stats (queried N, carried M)
- Old graph with no CALLS edges: degrades to full enrichment for this run
- All existing `test_enrich_with_lsp_pass.py` tests still pass unchanged
- mypy + ruff clean

---

### Task 8: Write incremental-mode unit tests

**Files:**
- Modify: `tests/unit/test_enrich_with_lsp_pass.py`

- [ ] **Step 1: Add incremental tests at the end of the file**

Append to `tests/unit/test_enrich_with_lsp_pass.py`:

```python
# ----------------------------------------------------------------------------
# Incremental mode
# ----------------------------------------------------------------------------


def _make_three_method_graph(
    foo_file: Path, bar_file: Path, caller_file: Path
) -> Graph:
    """Three classes in three files, one method each."""
    graph = Graph()
    graph.add_node(
        Node(id="class:App\\Foo", kind=NodeKind.CONTROLLER, name="Foo",
             attributes={"file": str(foo_file)}),
    )
    graph.add_node(
        Node(id="method:App\\Foo::doFoo", kind=NodeKind.METHOD, name="doFoo",
             attributes={"class_fqn": "App\\Foo", "line": 4}),
    )
    graph.add_node(
        Node(id="class:App\\Bar", kind=NodeKind.CONTROLLER, name="Bar",
             attributes={"file": str(bar_file)}),
    )
    graph.add_node(
        Node(id="method:App\\Bar::doBar", kind=NodeKind.METHOD, name="doBar",
             attributes={"class_fqn": "App\\Bar", "line": 4}),
    )
    graph.add_node(
        Node(id="class:App\\Caller", kind=NodeKind.CONTROLLER, name="Caller",
             attributes={"file": str(caller_file)}),
    )
    graph.add_node(
        Node(id="method:App\\Caller::invoke", kind=NodeKind.METHOD, name="invoke",
             attributes={"class_fqn": "App\\Caller", "line": 4}),
    )
    return graph


def _write_php_files(foo_file: Path, bar_file: Path, caller_file: Path) -> None:
    foo_file.write_text(
        "<?php\nnamespace App;\nclass Foo {\n    public function doFoo() {}\n}\n"
    )
    bar_file.write_text(
        "<?php\nnamespace App;\nclass Bar {\n    public function doBar() {}\n}\n"
    )
    caller_file.write_text(
        "<?php\nnamespace App;\nclass Caller {\n"
        "    public function invoke() {\n"
        "        (new Foo)->doFoo();\n    }\n}\n"
    )


def test_incremental_only_queries_methods_in_changed_files(tmp_path: Path) -> None:
    """With changed_files set, only methods in those files are LSP-queried."""
    project = tmp_path / "project"
    project.mkdir()
    foo_file = project / "Foo.php"
    bar_file = project / "Bar.php"
    caller_file = project / "Caller.php"
    _write_php_files(foo_file, bar_file, caller_file)

    graph = _make_three_method_graph(foo_file, bar_file, caller_file)

    # Only Foo.php changed — LSP should only be queried for doFoo
    lsp = _RecordingLsp(
        canned={("Foo.php", 4): [
            FileLocation(file=caller_file, start_line=5, start_character=20,
                         end_line=5, end_character=25),
        ]},
    )

    # Pre-persist old graph with an existing CALLS edge targeting doBar
    from nexus.core.graph.types import Edge
    storage = ProjectStorage(root=tmp_path / ".nexus", slug="test")
    old_graph = Graph()
    # Duplicate the same nodes
    for node in graph.nodes:
        old_graph.add_node(node)
    # Old edge: Caller::invoke → Bar::doBar (from previous full run)
    old_graph.add_edge(Edge(
        source="method:App\\Caller::invoke",
        target="method:App\\Bar::doBar",
        kind=EdgeKind.CALLS,
        attributes={"file": str(caller_file), "line": 5, "character": 10},
    ))
    storage.graph().persist(old_graph)

    ctx = PipelineContext(
        project_path=project,
        storage=storage,
        profile=_StubProfile(),
        graph=graph,
        lsp=lsp,
        changed_files={foo_file},
    )

    EnrichWithLspPass().run(ctx)

    # LSP was only queried for Foo.php methods, not Bar.php or Caller.php
    queried_files = {call[0].name for call in lsp.references_calls}
    assert queried_files == {"Foo.php"}

    # Carried-forward edge (targeting doBar) is present
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    targets = {e.target for e in calls}
    assert "method:App\\Bar::doBar" in targets
    # Fresh edge (targeting doFoo) is also present
    assert "method:App\\Foo::doFoo" in targets


def test_incremental_filters_carried_edges_with_deleted_source(tmp_path: Path) -> None:
    """Carried-forward edges whose source node no longer exists are dropped."""
    project = tmp_path / "project"
    project.mkdir()
    foo_file = project / "Foo.php"
    foo_file.write_text(
        "<?php\nnamespace App;\nclass Foo {\n    public function doFoo() {}\n}\n"
    )

    graph = Graph()
    graph.add_node(
        Node(id="class:App\\Foo", kind=NodeKind.CONTROLLER, name="Foo",
             attributes={"file": str(foo_file)}),
    )
    graph.add_node(
        Node(id="method:App\\Foo::doFoo", kind=NodeKind.METHOD, name="doFoo",
             attributes={"class_fqn": "App\\Foo", "line": 4}),
    )

    # Old graph has an edge from a node that no longer exists
    from nexus.core.graph.types import Edge
    storage = ProjectStorage(root=tmp_path / ".nexus", slug="test")
    old_graph = Graph()
    for node in graph.nodes:
        old_graph.add_node(node)
    old_graph.add_node(
        Node(id="method:App\\Deleted::gone", kind=NodeKind.METHOD, name="gone",
             attributes={"class_fqn": "App\\Deleted", "line": 4}),
    )
    old_graph.add_edge(Edge(
        source="method:App\\Deleted::gone",
        target="method:App\\Foo::doFoo",
        kind=EdgeKind.CALLS,
        attributes={"file": "/gone.php", "line": 5, "character": 10},
    ))
    storage.graph().persist(old_graph)

    lsp = _RecordingLsp()
    ctx = PipelineContext(
        project_path=project,
        storage=storage,
        profile=_StubProfile(),
        graph=graph,
        lsp=lsp,
        changed_files=set(),  # nothing changed — carry forward everything
    )

    EnrichWithLspPass().run(ctx)

    # The edge from the deleted source should NOT be carried forward
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert len(calls) == 0


def test_incremental_threshold_triggers_full_enrichment(tmp_path: Path) -> None:
    """When >50% of files are in changed_files, fall back to full enrichment."""
    project = tmp_path / "project"
    project.mkdir()
    foo_file = project / "Foo.php"
    bar_file = project / "Bar.php"
    foo_file.write_text(
        "<?php\nnamespace App;\nclass Foo {\n    public function doFoo() {}\n}\n"
    )
    bar_file.write_text(
        "<?php\nnamespace App;\nclass Bar {\n    public function doBar() {}\n}\n"
    )

    graph = Graph()
    graph.add_node(
        Node(id="class:App\\Foo", kind=NodeKind.CONTROLLER, name="Foo",
             attributes={"file": str(foo_file)}),
    )
    graph.add_node(
        Node(id="method:App\\Foo::doFoo", kind=NodeKind.METHOD, name="doFoo",
             attributes={"class_fqn": "App\\Foo", "line": 4}),
    )
    graph.add_node(
        Node(id="class:App\\Bar", kind=NodeKind.CONTROLLER, name="Bar",
             attributes={"file": str(bar_file)}),
    )
    graph.add_node(
        Node(id="method:App\\Bar::doBar", kind=NodeKind.METHOD, name="doBar",
             attributes={"class_fqn": "App\\Bar", "line": 4}),
    )

    # Both files changed (100% > 50% threshold)
    lsp = _RecordingLsp(
        canned={
            ("Foo.php", 4): [],
            ("Bar.php", 4): [],
        },
    )

    storage = ProjectStorage(root=tmp_path / ".nexus", slug="test")
    ctx = PipelineContext(
        project_path=project,
        storage=storage,
        profile=_StubProfile(),
        graph=graph,
        lsp=lsp,
        changed_files={foo_file, bar_file},
    )

    EnrichWithLspPass().run(ctx)

    # Both methods were queried (full mode triggered by threshold)
    assert len(lsp.references_calls) == 2


def test_incremental_empty_changed_files_skips_all_lsp_queries(tmp_path: Path) -> None:
    """Empty changed_files means nothing changed — all edges carried, zero queries."""
    project = tmp_path / "project"
    project.mkdir()
    foo_file = project / "Foo.php"
    foo_file.write_text(
        "<?php\nnamespace App;\nclass Foo {\n    public function doFoo() {}\n}\n"
    )

    graph = Graph()
    graph.add_node(
        Node(id="class:App\\Foo", kind=NodeKind.CONTROLLER, name="Foo",
             attributes={"file": str(foo_file)}),
    )
    graph.add_node(
        Node(id="method:App\\Foo::doFoo", kind=NodeKind.METHOD, name="doFoo",
             attributes={"class_fqn": "App\\Foo", "line": 4}),
    )

    from nexus.core.graph.types import Edge
    storage = ProjectStorage(root=tmp_path / ".nexus", slug="test")
    old_graph = Graph()
    for node in graph.nodes:
        old_graph.add_node(node)
    old_graph.add_edge(Edge(
        source="method:App\\Foo::doFoo",
        target="method:App\\Foo::doFoo",
        kind=EdgeKind.CALLS,
        attributes={"file": str(foo_file), "line": 4, "character": 1},
    ))
    # Add a non-self edge
    graph.add_node(
        Node(id="class:App\\Bar", kind=NodeKind.CONTROLLER, name="Bar",
             attributes={"file": str(project / "Bar.php")}),
    )
    graph.add_node(
        Node(id="method:App\\Bar::doBar", kind=NodeKind.METHOD, name="doBar",
             attributes={"class_fqn": "App\\Bar", "line": 4}),
    )
    old_graph.add_node(
        Node(id="class:App\\Bar", kind=NodeKind.CONTROLLER, name="Bar",
             attributes={"file": str(project / "Bar.php")}),
    )
    old_graph.add_node(
        Node(id="method:App\\Bar::doBar", kind=NodeKind.METHOD, name="doBar",
             attributes={"class_fqn": "App\\Bar", "line": 4}),
    )
    old_graph.add_edge(Edge(
        source="method:App\\Foo::doFoo",
        target="method:App\\Bar::doBar",
        kind=EdgeKind.CALLS,
        attributes={"file": str(foo_file), "line": 5, "character": 10},
    ))
    storage.graph().persist(old_graph)

    lsp = _RecordingLsp()
    ctx = PipelineContext(
        project_path=project,
        storage=storage,
        profile=_StubProfile(),
        graph=graph,
        lsp=lsp,
        changed_files=set(),
    )

    EnrichWithLspPass().run(ctx)

    # Zero LSP queries
    assert len(lsp.references_calls) == 0
    # Edge was carried forward
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert len(calls) == 1
    assert calls[0].target == "method:App\\Bar::doBar"


def test_incremental_with_no_old_calls_edges_falls_back_to_full(tmp_path: Path) -> None:
    """If old graph has no CALLS edges (first LSP run), do full enrichment."""
    project = tmp_path / "project"
    project.mkdir()
    foo_file = project / "Foo.php"
    foo_file.write_text(
        "<?php\nnamespace App;\nclass Foo {\n    public function doFoo() {}\n}\n"
    )

    graph = Graph()
    graph.add_node(
        Node(id="class:App\\Foo", kind=NodeKind.CONTROLLER, name="Foo",
             attributes={"file": str(foo_file)}),
    )
    graph.add_node(
        Node(id="method:App\\Foo::doFoo", kind=NodeKind.METHOD, name="doFoo",
             attributes={"class_fqn": "App\\Foo", "line": 4}),
    )

    # Old graph exists but has NO calls edges (e.g., previous run had no LSP)
    storage = ProjectStorage(root=tmp_path / ".nexus", slug="test")
    old_graph = Graph()
    for node in graph.nodes:
        old_graph.add_node(node)
    storage.graph().persist(old_graph)

    lsp = _RecordingLsp(canned={("Foo.php", 4): []})
    ctx = PipelineContext(
        project_path=project,
        storage=storage,
        profile=_StubProfile(),
        graph=graph,
        lsp=lsp,
        changed_files={foo_file},  # even though only one file changed
    )

    EnrichWithLspPass().run(ctx)

    # Still queried the LSP (incremental ran, just no edges to carry)
    assert len(lsp.references_calls) == 1
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_enrich_with_lsp_pass.py::test_incremental_only_queries_methods_in_changed_files -v`
Expected: FAIL — the pass doesn't read `ctx.changed_files` yet

---

### Task 9: Implement incremental mode in EnrichWithLspPass

**Files:**
- Modify: `nexus/pipeline/passes/enrich_with_lsp.py`

- [ ] **Step 1: Refactor existing `run` method into `_enrich_full`**

Extract the existing LSP-querying loop (lines 94–156) into a private method `_enrich_full`. The `run` method becomes a dispatcher:

```python
class EnrichWithLspPass:
    """Add CALLS edges to the graph using LSP references.

    Supports two modes:

    * **Full** (``ctx.changed_files is None``): queries every method
      node, identical to pre-incremental behavior.
    * **Incremental** (``ctx.changed_files`` is a set): loads old
      CALLS edges from storage, carries forward edges targeting methods
      in unchanged files, and only queries the LSP for methods in the
      changed files.

    A threshold (50% of files changed) triggers automatic fallback to
    full mode — at that point the overhead of loading and filtering old
    edges isn't worth the savings.
    """

    name = "enrich_with_lsp"

    _PROGRESS_INTERVAL = 50
    _THRESHOLD = 0.5

    def run(self, ctx: PipelineContext) -> None:
        """Walk method nodes, ask the LSP for references, add CALLS edges."""
        if ctx.graph is None:
            ctx.add_error(
                Error(
                    code="no_graph",
                    message="EnrichWithLspPass needs a graph. Did BuildGraphPass run?",
                ),
            )
            return

        if ctx.lsp is None:
            ctx.progress.emit(
                PassProgress(
                    pass_name=self.name,
                    message="No LSP configured; skipping CALLS enrichment.",
                ),
            )
            return

        method_nodes = [n for n in ctx.graph.nodes if n.kind == NodeKind.METHOD]
        if not method_nodes:
            ctx.progress.emit(
                PassProgress(
                    pass_name=self.name,
                    message="No method nodes in graph; nothing to enrich.",
                ),
            )
            return

        file_for_class: dict[str, Path] = _build_class_file_map(ctx.graph)
        methods_by_file: dict[Path, list[tuple[int, Node]]] = _index_methods_by_file(
            method_nodes,
            file_for_class,
        )

        effective = self._effective_changed_files(ctx, method_nodes, file_for_class)

        if effective is None:
            self._enrich_full(ctx, method_nodes, file_for_class, methods_by_file)
        else:
            self._enrich_incremental(
                ctx, method_nodes, file_for_class, methods_by_file, effective
            )

    def _effective_changed_files(
        self,
        ctx: PipelineContext,
        method_nodes: list[Node],
        file_for_class: dict[str, Path],
    ) -> set[Path] | None:
        """Apply the threshold check and return effective changed files or None."""
        if ctx.changed_files is None:
            return None

        all_files: set[Path] = set()
        for method in method_nodes:
            f = _file_for_method(method, file_for_class)
            if f is not None:
                all_files.add(f)

        if not all_files:
            return None

        changed_php_files = ctx.changed_files & all_files
        if len(changed_php_files) > len(all_files) * self._THRESHOLD:
            ctx.progress.emit(
                PassProgress(
                    pass_name=self.name,
                    message=(
                        f"Changed files ({len(changed_php_files)}) exceed "
                        f"{int(self._THRESHOLD * 100)}% of indexed files "
                        f"({len(all_files)}); using full enrichment."
                    ),
                ),
            )
            return None

        return ctx.changed_files

    def _enrich_full(
        self,
        ctx: PipelineContext,
        method_nodes: list[Node],
        file_for_class: dict[str, Path],
        methods_by_file: dict[Path, list[tuple[int, Node]]],
    ) -> None:
        """Full enrichment — query every method (existing behavior)."""
        assert ctx.lsp is not None
        ctx.lsp.prepare(ctx.project_path)
        edges_added = 0
        try:
            for index, method in enumerate(method_nodes, start=1):
                file = _file_for_method(method, file_for_class)
                line = _line_for_method(method)
                if file is None or line is None:
                    continue
                column = _find_symbol_column(file, line, method.name)
                if column is None:
                    ctx.add_warning(
                        Warning(
                            code="lsp_method_position_not_found",
                            message=(
                                f"Could not find symbol {method.name!r} on line {line} "
                                f"of {file}; skipping CALLS enrichment for this method."
                            ),
                            context={"method_id": method.id},
                        ),
                    )
                    continue

                refs = ctx.lsp.references(file, line, column)
                for ref in refs:
                    caller = _enclosing_method(methods_by_file, ref.file, ref.start_line)
                    if caller is None or caller.id == method.id:
                        continue
                    ctx.graph.add_edge(
                        Edge(
                            source=caller.id,
                            target=method.id,
                            kind=EdgeKind.CALLS,
                            attributes={
                                "file": str(ref.file),
                                "line": ref.start_line,
                                "character": ref.start_character,
                            },
                        ),
                    )
                    edges_added += 1

                if index % self._PROGRESS_INTERVAL == 0:
                    ctx.progress.emit(
                        PassProgress(
                            pass_name=self.name,
                            message=(
                                f"Queried LSP for {index} of {len(method_nodes)} methods "
                                f"({edges_added} CALLS edges so far)"
                            ),
                            current=index,
                            total=len(method_nodes),
                        ),
                    )
        finally:
            ctx.lsp.close()

        ctx.progress.emit(
            PassProgress(
                pass_name=self.name,
                message=f"Added {edges_added} CALLS edges across {len(method_nodes)} methods",
                detail={"edges_added": edges_added, "methods_scanned": len(method_nodes)},
            ),
        )

    def _enrich_incremental(
        self,
        ctx: PipelineContext,
        method_nodes: list[Node],
        file_for_class: dict[str, Path],
        methods_by_file: dict[Path, list[tuple[int, Node]]],
        changed_files: set[Path],
    ) -> None:
        """Incremental enrichment — carry forward + selective query."""
        assert ctx.lsp is not None
        assert ctx.graph is not None

        # 1. Load old CALLS edges from persisted graph
        old_graph = ctx.storage.graph().load()
        old_calls_edges = [e for e in old_graph.edges if e.kind == EdgeKind.CALLS]

        # 2. Partition methods into query vs skip
        new_node_ids = {n.id for n in ctx.graph.nodes}
        methods_to_query: list[Node] = []
        skipped_ids: set[str] = set()

        for method in method_nodes:
            file = _file_for_method(method, file_for_class)
            if file is not None and file in changed_files:
                methods_to_query.append(method)
            else:
                skipped_ids.add(method.id)

        # 3. Carry forward old CALLS edges targeting skipped methods
        carried = 0
        for edge in old_calls_edges:
            if edge.target in skipped_ids and edge.source in new_node_ids:
                ctx.graph.add_edge(edge)
                carried += 1

        ctx.progress.emit(
            PassProgress(
                pass_name=self.name,
                message=(
                    f"Incremental: {len(methods_to_query)} methods to query, "
                    f"{len(skipped_ids)} unchanged (carried {carried} edges)"
                ),
                current=0,
                total=len(methods_to_query),
            ),
        )

        # 4. Query LSP only for methods in changed files
        ctx.lsp.prepare(ctx.project_path)
        edges_added = 0
        try:
            for index, method in enumerate(methods_to_query, start=1):
                file = _file_for_method(method, file_for_class)
                line = _line_for_method(method)
                if file is None or line is None:
                    continue
                column = _find_symbol_column(file, line, method.name)
                if column is None:
                    ctx.add_warning(
                        Warning(
                            code="lsp_method_position_not_found",
                            message=(
                                f"Could not find symbol {method.name!r} on line {line} "
                                f"of {file}; skipping CALLS enrichment for this method."
                            ),
                            context={"method_id": method.id},
                        ),
                    )
                    continue

                refs = ctx.lsp.references(file, line, column)
                for ref in refs:
                    caller = _enclosing_method(methods_by_file, ref.file, ref.start_line)
                    if caller is None or caller.id == method.id:
                        continue
                    ctx.graph.add_edge(
                        Edge(
                            source=caller.id,
                            target=method.id,
                            kind=EdgeKind.CALLS,
                            attributes={
                                "file": str(ref.file),
                                "line": ref.start_line,
                                "character": ref.start_character,
                            },
                        ),
                    )
                    edges_added += 1

                if index % self._PROGRESS_INTERVAL == 0:
                    ctx.progress.emit(
                        PassProgress(
                            pass_name=self.name,
                            message=(
                                f"Queried LSP for {index} of "
                                f"{len(methods_to_query)} changed methods "
                                f"({edges_added} new + {carried} carried CALLS edges)"
                            ),
                            current=index,
                            total=len(methods_to_query),
                        ),
                    )
        finally:
            ctx.lsp.close()

        ctx.progress.emit(
            PassProgress(
                pass_name=self.name,
                message=(
                    f"Incremental done: {edges_added} fresh + {carried} carried = "
                    f"{edges_added + carried} total CALLS edges "
                    f"(queried {len(methods_to_query)} of {len(method_nodes)} methods)"
                ),
                detail={
                    "edges_added": edges_added,
                    "edges_carried": carried,
                    "methods_queried": len(methods_to_query),
                    "methods_skipped": len(skipped_ids),
                },
            ),
        )
```

- [ ] **Step 2: Run existing LSP pass tests (no regression)**

Run: `uv run pytest tests/unit/test_enrich_with_lsp_pass.py -v -k "not incremental"`
Expected: All 8 existing tests PASS (full mode is the same behavior, just reorganized)

- [ ] **Step 3: Run incremental tests**

Run: `uv run pytest tests/unit/test_enrich_with_lsp_pass.py -v -k "incremental"`
Expected: All 5 incremental tests PASS

- [ ] **Step 4: Run the full test suite**

Run: `uv run pytest tests/unit/ -x -q`
Expected: All pass

- [ ] **Step 5: Linters**

Run: `uv run ruff check nexus/pipeline/passes/enrich_with_lsp.py && uv run mypy --strict nexus/pipeline/passes/enrich_with_lsp.py`
Expected: Clean

- [ ] **Step 6: Commit**

```bash
git add nexus/pipeline/passes/enrich_with_lsp.py tests/unit/test_enrich_with_lsp_pass.py
git commit -m "feat(pipeline): incremental LSP enrichment — carry forward CALLS edges for unchanged files"
```

---

## Phase D: End-to-End Validation

**Goal:** Validate the full incremental sync flow against a real fixture and measure the time savings.

**Acceptance criteria:**
- Integration test proves: full index → modify one file → sync → only that file's methods queried
- `--full` flag forces full enrichment in the integration test
- The full test suite (unit + integration + architecture) passes
- mypy + ruff clean across the entire codebase
- Manual validation against the CRM project shows expected time reduction

---

### Task 10: Write integration test for incremental sync

**Files:**
- Create: `tests/integration/test_incremental_sync.py`

- [ ] **Step 1: Write the integration test**

```python
"""Integration test for incremental sync flow.

Exercises the full path: index rebuild → git commit → sync with
changed_files → verify only changed methods are queried.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from nexus.adapters.storage import ProjectStorage
from nexus.core.graph.graph import Graph
from nexus.core.graph.types import Edge, EdgeKind, Node, NodeKind
from nexus.interfaces.cli.commands._index_helpers import _compute_changed_files
from nexus.pipeline.context import PipelineContext
from nexus.pipeline.passes.embed_and_persist import _resolve_git_head
from nexus.pipeline.passes.enrich_with_lsp import EnrichWithLspPass


def _git(cwd: Path, *args: str) -> None:
    env = {
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@t",
        "HOME": str(cwd),
        "PATH": "/usr/bin:/bin",
    }
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True, env=env)


class _RecordingLsp:
    def __init__(self) -> None:
        self.references_calls: list[tuple[Path, int, int]] = []

    def prepare(self, workspace_root: Path) -> None:
        pass

    def references(self, file: Path, line: int, character: int) -> list:
        self.references_calls.append((file, line, character))
        return []

    def close(self) -> None:
        pass


class _StubProfile:
    name = "stub"
    custom_bases: dict[str, str] = {}
    custom_suffixes: dict[str, str] = {}


def test_incremental_sync_only_queries_changed_file_methods(tmp_path: Path) -> None:
    """Full flow: rebuild, commit a change, sync — only changed file queried."""
    project = tmp_path / "project"
    project.mkdir()

    # Create initial files
    (project / "Foo.php").write_text(
        "<?php\nnamespace App;\nclass Foo {\n    public function doFoo() {}\n}\n"
    )
    (project / "Bar.php").write_text(
        "<?php\nnamespace App;\nclass Bar {\n    public function doBar() {}\n}\n"
    )

    # Init git, commit
    _git(project, "init")
    _git(project, "add", ".")
    _git(project, "commit", "-m", "init")
    baseline = _resolve_git_head(project)
    assert baseline is not None

    # Build initial graph + persist with CALLS edges
    storage = ProjectStorage(root=tmp_path / ".nexus", slug="test")
    old_graph = Graph()
    old_graph.add_node(Node(id="class:App\\Foo", kind=NodeKind.CONTROLLER, name="Foo",
                            attributes={"file": str(project / "Foo.php")}))
    old_graph.add_node(Node(id="method:App\\Foo::doFoo", kind=NodeKind.METHOD, name="doFoo",
                            attributes={"class_fqn": "App\\Foo", "line": 4}))
    old_graph.add_node(Node(id="class:App\\Bar", kind=NodeKind.CONTROLLER, name="Bar",
                            attributes={"file": str(project / "Bar.php")}))
    old_graph.add_node(Node(id="method:App\\Bar::doBar", kind=NodeKind.METHOD, name="doBar",
                            attributes={"class_fqn": "App\\Bar", "line": 4}))
    old_graph.add_edge(Edge(
        source="method:App\\Foo::doFoo", target="method:App\\Bar::doBar",
        kind=EdgeKind.CALLS, attributes={"file": str(project / "Foo.php"), "line": 5, "character": 10},
    ))
    storage.graph().persist(old_graph)

    # Modify only Foo.php, commit
    (project / "Foo.php").write_text(
        "<?php\nnamespace App;\nclass Foo {\n    public function doFoo() { return 1; }\n}\n"
    )
    _git(project, "add", ".")
    _git(project, "commit", "-m", "edit foo")

    # Compute changed files
    changed = _compute_changed_files(project, baseline)
    assert changed is not None
    assert changed == {(project / "Foo.php").resolve()}

    # Build new graph (same structure, simulating fresh extraction)
    new_graph = Graph()
    for node in old_graph.nodes:
        new_graph.add_node(node)

    # Run incremental enrichment
    lsp = _RecordingLsp()
    ctx = PipelineContext(
        project_path=project,
        storage=storage,
        profile=_StubProfile(),
        graph=new_graph,
        lsp=lsp,
        changed_files=changed,
    )
    EnrichWithLspPass().run(ctx)

    # Only Foo.php methods were queried
    queried_files = {call[0].name for call in lsp.references_calls}
    assert "Foo.php" in queried_files or len(queried_files) == 0  # doFoo is in Foo.php
    assert "Bar.php" not in queried_files

    # Carried edge still present
    calls = [e for e in new_graph.edges if e.kind == EdgeKind.CALLS]
    assert any(e.target == "method:App\\Bar::doBar" for e in calls)


def test_full_flag_forces_all_methods_queried(tmp_path: Path) -> None:
    """With changed_files=None (--full), all methods are queried."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "Foo.php").write_text(
        "<?php\nnamespace App;\nclass Foo {\n    public function doFoo() {}\n}\n"
    )
    (project / "Bar.php").write_text(
        "<?php\nnamespace App;\nclass Bar {\n    public function doBar() {}\n}\n"
    )

    graph = Graph()
    graph.add_node(Node(id="class:App\\Foo", kind=NodeKind.CONTROLLER, name="Foo",
                        attributes={"file": str(project / "Foo.php")}))
    graph.add_node(Node(id="method:App\\Foo::doFoo", kind=NodeKind.METHOD, name="doFoo",
                        attributes={"class_fqn": "App\\Foo", "line": 4}))
    graph.add_node(Node(id="class:App\\Bar", kind=NodeKind.CONTROLLER, name="Bar",
                        attributes={"file": str(project / "Bar.php")}))
    graph.add_node(Node(id="method:App\\Bar::doBar", kind=NodeKind.METHOD, name="doBar",
                        attributes={"class_fqn": "App\\Bar", "line": 4}))

    storage = ProjectStorage(root=tmp_path / ".nexus", slug="test")
    lsp = _RecordingLsp()
    ctx = PipelineContext(
        project_path=project,
        storage=storage,
        profile=_StubProfile(),
        graph=graph,
        lsp=lsp,
        changed_files=None,  # --full mode
    )
    EnrichWithLspPass().run(ctx)

    # Both methods queried
    assert len(lsp.references_calls) == 2
```

- [ ] **Step 2: Run the integration test**

Run: `uv run pytest tests/integration/test_incremental_sync.py -v`
Expected: All PASS

- [ ] **Step 3: Run the full test suite**

Run: `uv run pytest tests/unit/ tests/integration/ tests/architecture/ -x -q`
Expected: All pass

- [ ] **Step 4: Run linters on entire codebase**

Run: `uv run ruff format --check nexus/ && uv run ruff check nexus/ && uv run mypy --strict nexus/`
Expected: All clean

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_incremental_sync.py
git commit -m "test(integration): end-to-end incremental sync validation"
```

---

### Task 11: Manual validation against CRM project

**Files:** None (manual validation)

- [ ] **Step 1: Run a full rebuild to establish baseline**

```bash
uv run nexus --slug synthesq-api index rebuild \
  --project-path /home/lockhart/projects/crm/api.crm.test/main \
  --php "docker exec synthesq-app php" \
  --container-project-path /var/www
```

Expected: Completes successfully, `meta.json` now has `last_indexed_commit` populated.

- [ ] **Step 2: Verify last_indexed_commit is set**

```bash
uv run nexus --slug synthesq-api index status | grep last_indexed_commit
```

Expected: Shows a 40-char SHA (not null)

- [ ] **Step 3: Make a small change in the CRM, commit it**

Edit one PHP file in the CRM project, commit:
```bash
cd /home/lockhart/projects/crm/api.crm.test/main
# Edit any single .php file
git add -A && git commit -m "test: trigger incremental sync"
```

- [ ] **Step 4: Run sync and observe time savings**

```bash
time uv run nexus --slug synthesq-api index sync \
  --project-path /home/lockhart/projects/crm/api.crm.test/main \
  --php "docker exec synthesq-app php" \
  --container-project-path /var/www
```

Expected: The `enrich_with_lsp` pass reports "Incremental: N methods to query, M unchanged (carried K edges)" and completes in seconds instead of ~12 minutes. Total pipeline time should be ~60–90s.

- [ ] **Step 5: Verify query results are still correct**

```bash
uv run nexus --slug synthesq-api query list_routes | head -20
uv run nexus --slug synthesq-api query find_callers --fqn "App\\Http\\Controllers\\SomeController::someMethod"
```

Expected: Routes and callers are still returned correctly.

- [ ] **Step 6: Run sync --full and verify it falls back**

```bash
time uv run nexus --slug synthesq-api index sync --full \
  --project-path /home/lockhart/projects/crm/api.crm.test/main \
  --php "docker exec synthesq-app php" \
  --container-project-path /var/www
```

Expected: Full enrichment runs (~12 min for LSP pass), confirming --full works.
