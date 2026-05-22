"""Unit tests for :class:`nexus.pipeline.passes.EnrichWithLspPass`.

The pass takes a graph plus an :class:`Lsp` and writes ``CALLS`` edges.
We use a hand-rolled stub LSP that returns canned references, so the
tests are pure — no real subprocess, no real language server.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from nexus.adapters.lsp import NullLsp
from nexus.adapters.storage import ProjectStorage
from nexus.core.graph.graph import Graph
from nexus.core.graph.types import EdgeKind, Node, NodeKind
from nexus.core.lsp import FileLocation
from nexus.pipeline.context import PipelineContext
from nexus.pipeline.passes import EnrichWithLspPass

if TYPE_CHECKING:
    from nexus.core.protocols import Lsp


@dataclass(frozen=True)
class _StubProfile:
    """A minimal Profile-shaped object used by tests."""

    name: str = "stub"
    custom_bases: dict[str, str] = field(default_factory=dict)
    custom_suffixes: dict[str, str] = field(default_factory=dict)


class _RecordingLsp:
    """Stub LSP that records calls and replays canned references.

    Configure with a mapping from a ``(file_name, line)`` key to the
    list of :class:`FileLocation` results for that query.
    """

    def __init__(
        self,
        canned: dict[tuple[str, int], list[FileLocation]] | None = None,
    ) -> None:
        self._canned = canned or {}
        self.prepare_calls: list[Path] = []
        self.references_calls: list[tuple[Path, int, int]] = []
        self.close_calls: int = 0

    def prepare(self, workspace_root: Path) -> None:
        self.prepare_calls.append(workspace_root)

    def references(
        self,
        file: Path,
        line: int,
        character: int,
    ) -> list[FileLocation]:
        self.references_calls.append((file, line, character))
        return self._canned.get((file.name, line), [])

    def close(self) -> None:
        self.close_calls += 1


def _make_ctx(
    *,
    tmp_path: Path,
    graph: Graph,
    lsp: Lsp | None,
) -> PipelineContext:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    storage = ProjectStorage(root=tmp_path / ".nexus", slug="test")
    return PipelineContext(
        project_path=project,
        storage=storage,
        profile=_StubProfile(),
        graph=graph,
        lsp=lsp,
    )


def _make_two_method_graph(foo_file: Path, caller_file: Path) -> Graph:
    """Two classes, one method each, in two files. No edges yet."""
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
            id="method:App\\Foo::bar",
            kind=NodeKind.METHOD,
            name="bar",
            attributes={"class_fqn": "App\\Foo", "line": 4},
        ),
    )
    graph.add_node(
        Node(
            id="class:App\\Caller",
            kind=NodeKind.CONTROLLER,
            name="Caller",
            attributes={"file": str(caller_file)},
        ),
    )
    graph.add_node(
        Node(
            id="method:App\\Caller::callBar",
            kind=NodeKind.METHOD,
            name="callBar",
            attributes={"class_fqn": "App\\Caller", "line": 4},
        ),
    )
    return graph


# ----------------------------------------------------------------------------
# Happy path
# ----------------------------------------------------------------------------


def test_pass_adds_calls_edge_for_returned_reference(tmp_path: Path) -> None:
    """A reference to ``Foo::bar`` from inside ``Caller::callBar`` becomes a CALLS edge."""
    foo_file = tmp_path / "Foo.php"
    foo_file.write_text(
        "<?php\nnamespace App;\nclass Foo {\n    public function bar() {}\n}\n",
    )
    caller_file = tmp_path / "Caller.php"
    caller_file.write_text(
        "<?php\nnamespace App;\nclass Caller {\n    public function callBar() {\n"
        "        (new Foo)->bar();\n    }\n}\n",
    )

    graph = _make_two_method_graph(foo_file, caller_file)

    lsp = _RecordingLsp(
        canned={
            # Reference to `bar` (line 4 of Foo.php) → call site on line 5 of Caller.php
            ("Foo.php", 4): [
                FileLocation(
                    file=caller_file,
                    start_line=5,
                    start_character=20,
                    end_line=5,
                    end_character=23,
                ),
            ],
        },
    )

    ctx = _make_ctx(tmp_path=tmp_path, graph=graph, lsp=lsp)

    EnrichWithLspPass().run(ctx)

    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert len(calls) == 1
    assert calls[0].source == "method:App\\Caller::callBar"
    assert calls[0].target == "method:App\\Foo::bar"
    assert calls[0].attributes["file"] == str(caller_file)
    assert calls[0].attributes["line"] == 5

    # Lifecycle: prepare and close were called exactly once.
    assert lsp.prepare_calls == [ctx.project_path]
    assert lsp.close_calls == 1


def test_self_references_are_not_added_as_calls_edges(tmp_path: Path) -> None:
    """A reference to ``Foo::bar`` inside ``Foo::bar`` itself must not produce a CALLS edge."""
    foo_file = tmp_path / "Foo.php"
    foo_file.write_text(
        "<?php\nnamespace App;\nclass Foo {\n    public function bar() {\n"
        "        $this->bar();\n    }\n}\n",
    )

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
            id="method:App\\Foo::bar",
            kind=NodeKind.METHOD,
            name="bar",
            attributes={"class_fqn": "App\\Foo", "line": 4},
        ),
    )

    lsp = _RecordingLsp(
        canned={
            ("Foo.php", 4): [
                FileLocation(
                    file=foo_file,
                    start_line=5,
                    start_character=16,
                    end_line=5,
                    end_character=19,
                ),
            ],
        },
    )

    ctx = _make_ctx(tmp_path=tmp_path, graph=graph, lsp=lsp)

    EnrichWithLspPass().run(ctx)

    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert calls == []


# ----------------------------------------------------------------------------
# Skip / no-op paths
# ----------------------------------------------------------------------------


def test_pass_is_no_op_when_lsp_is_none(tmp_path: Path) -> None:
    """No LSP configured = no edges added, no errors raised."""
    foo_file = tmp_path / "Foo.php"
    foo_file.write_text("<?php\n")

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
            id="method:App\\Foo::bar",
            kind=NodeKind.METHOD,
            name="bar",
            attributes={"class_fqn": "App\\Foo", "line": 1},
        ),
    )

    ctx = _make_ctx(tmp_path=tmp_path, graph=graph, lsp=None)

    EnrichWithLspPass().run(ctx)

    assert ctx.ok()
    assert [e for e in graph.edges if e.kind == EdgeKind.CALLS] == []


def test_pass_is_no_op_with_null_lsp_substituted(tmp_path: Path) -> None:
    """A NullLsp returns no references, so no CALLS edges are added."""
    foo_file = tmp_path / "Foo.php"
    foo_file.write_text("<?php\nclass Foo {\n    function bar() {}\n}\n")

    graph = Graph()
    graph.add_node(
        Node(
            id="class:Foo",
            kind=NodeKind.CONTROLLER,
            name="Foo",
            attributes={"file": str(foo_file)},
        ),
    )
    graph.add_node(
        Node(
            id="method:Foo::bar",
            kind=NodeKind.METHOD,
            name="bar",
            attributes={"class_fqn": "Foo", "line": 3},
        ),
    )

    ctx = _make_ctx(tmp_path=tmp_path, graph=graph, lsp=NullLsp())

    EnrichWithLspPass().run(ctx)

    assert [e for e in graph.edges if e.kind == EdgeKind.CALLS] == []


def test_pass_emits_error_when_graph_is_missing(tmp_path: Path) -> None:
    """Without a graph the pass cannot run; it records a fatal error."""
    ctx = _make_ctx(tmp_path=tmp_path, graph=Graph(), lsp=_RecordingLsp())
    ctx.graph = None  # simulate BuildGraphPass failure

    EnrichWithLspPass().run(ctx)

    assert not ctx.ok()
    assert any(err.code == "no_graph" for err in ctx.errors)


def test_pass_handles_graph_with_no_method_nodes(tmp_path: Path) -> None:
    """Empty method set is treated as 'nothing to enrich' — not an error."""
    graph = Graph()
    graph.add_node(
        Node(id="class:Foo", kind=NodeKind.CONTROLLER, name="Foo", attributes={}),
    )

    ctx = _make_ctx(tmp_path=tmp_path, graph=graph, lsp=_RecordingLsp())

    EnrichWithLspPass().run(ctx)

    assert ctx.ok()
    assert [e for e in graph.edges if e.kind == EdgeKind.CALLS] == []


# ----------------------------------------------------------------------------
# Robustness
# ----------------------------------------------------------------------------


def test_pass_warns_when_method_symbol_not_on_declared_line(tmp_path: Path) -> None:
    """If we can't locate the method name on its declared line, surface a warning."""
    # The file claims method 'bar' is on line 1, but the file content
    # doesn't include 'bar' at all. The pass must skip cleanly with a warning.
    foo_file = tmp_path / "Foo.php"
    foo_file.write_text("<?php // empty stub\n")

    graph = Graph()
    graph.add_node(
        Node(
            id="class:Foo",
            kind=NodeKind.CONTROLLER,
            name="Foo",
            attributes={"file": str(foo_file)},
        ),
    )
    graph.add_node(
        Node(
            id="method:Foo::bar",
            kind=NodeKind.METHOD,
            name="bar",
            attributes={"class_fqn": "Foo", "line": 1},
        ),
    )

    ctx = _make_ctx(tmp_path=tmp_path, graph=graph, lsp=_RecordingLsp())

    EnrichWithLspPass().run(ctx)

    assert ctx.ok()  # warnings, not errors
    assert any(w.code == "lsp_method_position_not_found" for w in ctx.warnings)


def test_close_is_called_even_when_iteration_raises(tmp_path: Path) -> None:
    """Exception inside the loop must not leak the LSP subprocess."""
    foo_file = tmp_path / "Foo.php"
    foo_file.write_text("<?php\n    public function bar() {}\n")

    graph = Graph()
    graph.add_node(
        Node(
            id="class:Foo",
            kind=NodeKind.CONTROLLER,
            name="Foo",
            attributes={"file": str(foo_file)},
        ),
    )
    graph.add_node(
        Node(
            id="method:Foo::bar",
            kind=NodeKind.METHOD,
            name="bar",
            attributes={"class_fqn": "Foo", "line": 2},
        ),
    )

    class _ExplodingLsp(_RecordingLsp):
        def references(self, file: Path, line: int, character: int) -> list[FileLocation]:
            self.references_calls.append((file, line, character))
            raise RuntimeError("boom")

    lsp = _ExplodingLsp()
    ctx = _make_ctx(tmp_path=tmp_path, graph=graph, lsp=lsp)

    with pytest.raises(RuntimeError, match="boom"):
        EnrichWithLspPass().run(ctx)

    assert lsp.close_calls == 1, "close() must run via the try/finally even on error"
