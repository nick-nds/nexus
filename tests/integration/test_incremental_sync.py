"""Integration test for incremental sync flow.

Exercises the full path: git repo -> persist old graph -> commit change ->
compute changed_files -> incremental EnrichWithLspPass -> verify selective querying.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from nexus.adapters.storage import ProjectStorage
from nexus.core.graph.graph import Graph
from nexus.core.graph.types import Edge, EdgeKind, Node, NodeKind
from nexus.interfaces.cli.commands._index_helpers import _compute_changed_files
from nexus.pipeline.context import PipelineContext
from nexus.pipeline.passes.embed_and_persist import _resolve_git_head
from nexus.pipeline.passes.enrich_with_lsp import EnrichWithLspPass

pytestmark = pytest.mark.integration


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


@dataclass(frozen=True)
class _StubProfile:
    name: str = "stub"
    custom_bases: dict[str, str] = field(default_factory=dict)
    custom_suffixes: dict[str, str] = field(default_factory=dict)


def _build_two_class_graph(foo_file: Path, bar_file: Path) -> Graph:
    graph = Graph()
    graph.add_node(
        Node(
            id="class:App\\Foo",
            kind=NodeKind.CONTROLLER,
            name="Foo",
            attributes={"file": str(foo_file)},
        ),
    )
    graph.add_node(
        Node(
            id="method:App\\Foo::doFoo",
            kind=NodeKind.METHOD,
            name="doFoo",
            attributes={"class_fqn": "App\\Foo", "line": 4},
        ),
    )
    graph.add_node(
        Node(
            id="class:App\\Bar",
            kind=NodeKind.CONTROLLER,
            name="Bar",
            attributes={"file": str(bar_file)},
        ),
    )
    graph.add_node(
        Node(
            id="method:App\\Bar::doBar",
            kind=NodeKind.METHOD,
            name="doBar",
            attributes={"class_fqn": "App\\Bar", "line": 4},
        ),
    )
    return graph


def test_incremental_sync_only_queries_changed_file_methods(tmp_path: Path) -> None:
    """Full flow: rebuild, commit a change, sync -- only changed file queried."""
    project = tmp_path / "project"
    project.mkdir()

    # Create initial PHP files
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

    # Build and persist old graph with a CALLS edge
    storage = ProjectStorage(root=tmp_path / ".nexus", slug="test")
    foo_file = project / "Foo.php"
    bar_file = project / "Bar.php"
    old_graph = _build_two_class_graph(foo_file, bar_file)
    old_graph.add_edge(
        Edge(
            source="method:App\\Foo::doFoo",
            target="method:App\\Bar::doBar",
            kind=EdgeKind.CALLS,
            attributes={"file": str(foo_file), "line": 5, "character": 10},
        ),
    )
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

    # Build new graph (same structure -- simulates fresh extraction)
    new_graph = _build_two_class_graph(foo_file, bar_file)

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

    # Only Foo.php methods were queried (doFoo is in Foo.php)
    queried_files = {call[0].name for call in lsp.references_calls}
    assert "Bar.php" not in queried_files

    # Carried edge (Foo::doFoo -> Bar::doBar) still present
    calls = [e for e in new_graph.edges if e.kind == EdgeKind.CALLS]
    assert any(
        e.source == "method:App\\Foo::doFoo" and e.target == "method:App\\Bar::doBar" for e in calls
    )


def test_full_flag_forces_all_methods_queried(tmp_path: Path) -> None:
    """With changed_files=None (--full), all methods are queried."""
    project = tmp_path / "project"
    project.mkdir()
    foo_file = project / "Foo.php"
    bar_file = project / "Bar.php"
    foo_file.write_text("<?php\nnamespace App;\nclass Foo {\n    public function doFoo() {}\n}\n")
    bar_file.write_text("<?php\nnamespace App;\nclass Bar {\n    public function doBar() {}\n}\n")

    graph = _build_two_class_graph(foo_file, bar_file)

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
