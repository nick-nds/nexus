"""End-to-end integration tests for ``get_full_block`` and ``get_node_body``.

Builds a tiny Laravel-ish PHP file on disk, runs the real chunker
against it, persists chunks to a real LanceDB store with a stub
embedder, builds a real graph, and verifies both tools resolve the
node body via the registered query engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from nexus.adapters.storage import (
    LanceVectorRecord,
    ProjectStorage,
)
from nexus.core.chunking.php_chunker import PhpChunker
from nexus.core.graph.graph import Graph
from nexus.core.graph.types import Edge, EdgeKind, Node, NodeKind
from nexus.core.query import (
    QueryEngine,
    ResponseBudget,
    ToolRegistry,
)
from nexus.core.query.context import QueryContext
from nexus.core.query.coverage import Coverage
from nexus.core.query.tools import register_builtin_tools

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.integration


SAMPLE_PHP = """<?php

namespace App\\Requests;

use Illuminate\\Foundation\\Http\\FormRequest;

class CreateProductRequest extends FormRequest
{
    public function rules(): array
    {
        return [
            'name' => ['required', 'string', 'max:255'],
            'sku' => ['required', 'string', 'max:100', 'unique:products'],
            'price' => ['required', 'numeric', 'min:0'],
            'description' => ['nullable', 'string'],
        ];
    }

    public function messages(): array
    {
        return ['name.required' => 'The name field is required.'];
    }
}
"""


@dataclass(frozen=True)
class _StubProfile:
    name: str = "stub"
    custom_bases: dict[str, str] = None  # type: ignore[assignment]
    custom_suffixes: dict[str, str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.custom_bases is None:
            object.__setattr__(self, "custom_bases", {})
        if self.custom_suffixes is None:
            object.__setattr__(self, "custom_suffixes", {})


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    return root


@pytest.fixture
def php_file(project_root: Path) -> Path:
    file = project_root / "app" / "Requests" / "CreateProductRequest.php"
    file.parent.mkdir(parents=True)
    file.write_text(SAMPLE_PHP, encoding="utf-8")
    return file


@pytest.fixture
def storage(tmp_path: Path, project_root: Path, php_file: Path) -> Iterator[ProjectStorage]:
    """A real ProjectStorage with a hand-built graph and chunked vectors."""
    s = ProjectStorage(root=tmp_path / ".nexus", slug="bodytest")

    # Build the graph
    class_id = "class:App\\Requests\\CreateProductRequest"
    rules_id = "method:App\\Requests\\CreateProductRequest::rules"
    messages_id = "method:App\\Requests\\CreateProductRequest::messages"
    g = Graph()
    g.add_node(
        Node(
            id=class_id,
            kind=NodeKind.FORM_REQUEST,
            name="CreateProductRequest",
            attributes={
                "fqn": "App\\Requests\\CreateProductRequest",
                "file": str(php_file),
                "line": 7,
            },
        ),
    )
    g.add_node(
        Node(
            id=rules_id,
            kind=NodeKind.METHOD,
            name="rules",
            attributes={
                "class_fqn": "App\\Requests\\CreateProductRequest",
                "line": 9,
                "visibility": "public",
            },
        ),
    )
    g.add_node(
        Node(
            id=messages_id,
            kind=NodeKind.METHOD,
            name="messages",
            attributes={
                "class_fqn": "App\\Requests\\CreateProductRequest",
                "line": 19,
                "visibility": "public",
            },
        ),
    )
    g.add_edge(Edge(source=rules_id, target=class_id, kind=EdgeKind.PART_OF))
    g.add_edge(Edge(source=messages_id, target=class_id, kind=EdgeKind.PART_OF))
    assert s.graph().persist(g).ok

    # Chunk the file and write chunks into LanceDB with stub vectors.
    chunker = PhpChunker()
    chunks = chunker.chunk_file(php_file)
    assert len(chunks) >= 3  # class header + 2 methods

    vectors = s.vectors(dimensions=4)
    records: list[LanceVectorRecord] = []
    for chunk in chunks:
        node_id_value = chunk.node_id or ""
        records.append(
            LanceVectorRecord(
                id=chunk.id,
                vector=[0.0, 0.0, 0.0, 0.0],
                payload={
                    "node_id": node_id_value,
                    "file_path": str(chunk.file_path),
                    "kind": chunk.kind.value,
                    "symbol": chunk.symbol,
                    "start_line": chunk.start_line,
                    "end_line": chunk.end_line,
                },
            ),
        )
    vectors.upsert(records)

    # Stamp coverage so the project-root containment check passes.
    from nexus.adapters.storage import ProjectMeta

    s.write_meta(
        ProjectMeta(
            schema_version="1.0",
            project_path=str(project_root),
            project_slug="bodytest",
            embedder_id="stub:test",
            indexed_at="2026-05-14T00:00:00+00:00",
        ),
    )

    yield s
    s.close()


@pytest.fixture
def engine(storage: ProjectStorage, project_root: Path) -> QueryEngine:
    registry = ToolRegistry()
    register_builtin_tools(registry)
    ctx = QueryContext(
        storage=storage,
        budget=ResponseBudget(),
        vector_dimensions=4,
        coverage=Coverage(project_path=str(project_root)),
    )
    return QueryEngine(registry, ctx)


# ---------------------------------------------------------------------------
# get_full_block
# ---------------------------------------------------------------------------


class TestGetFullBlock:
    def test_reads_line_range_from_indexed_file(
        self,
        engine: QueryEngine,
        php_file: Path,
    ) -> None:
        result = engine.query(
            "get_full_block",
            {"file_path": str(php_file), "start_line": 9, "end_line": 17},
        )

        assert result.error is None
        assert result.content is not None
        assert "public function rules" in result.content
        assert "'name' =>" in result.content
        assert result.start_line == 9
        assert result.end_line == 17

    def test_rejects_file_outside_project(
        self,
        engine: QueryEngine,
        tmp_path: Path,
    ) -> None:
        outside = tmp_path / "outside.php"
        outside.write_text("<?php\n", encoding="utf-8")

        result = engine.query(
            "get_full_block",
            {"file_path": str(outside), "start_line": 1, "end_line": 1},
        )

        assert result.error_code == "file_outside_project"


# ---------------------------------------------------------------------------
# get_node_body
# ---------------------------------------------------------------------------


class TestGetNodeBody:
    def test_resolves_method_body_via_chunk_index(
        self,
        engine: QueryEngine,
    ) -> None:
        result = engine.query(
            "get_node_body",
            {"node_id": "method:App\\Requests\\CreateProductRequest::rules"},
        )

        assert result.error is None
        assert result.content is not None
        assert "public function rules" in result.content
        assert "'sku' =>" in result.content
        assert result.node_kind == "method"
        assert result.symbol == "rules"
        assert result.container_class == "App\\Requests\\CreateProductRequest"

    def test_resolves_class_header(self, engine: QueryEngine) -> None:
        result = engine.query(
            "get_node_body",
            {"node_id": "class:App\\Requests\\CreateProductRequest"},
        )

        assert result.error is None
        assert result.content is not None
        assert "class CreateProductRequest" in result.content
        assert result.node_kind == "form_request"
        assert result.symbol == "CreateProductRequest"

    def test_unknown_node_returns_structured_error(self, engine: QueryEngine) -> None:
        result = engine.query(
            "get_node_body",
            {"node_id": "method:Does\\Not::Exist"},
        )

        assert result.error_code == "node_not_found"

    def test_context_lines_widens_returned_range(self, engine: QueryEngine) -> None:
        result = engine.query(
            "get_node_body",
            {
                "node_id": "method:App\\Requests\\CreateProductRequest::rules",
                "context_lines": 5,
            },
        )

        assert result.error is None
        # Window widened to include the class declaration above.
        assert result.content is not None
        assert "class CreateProductRequest" in result.content
