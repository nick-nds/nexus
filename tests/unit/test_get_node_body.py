"""Unit tests for ``get_node_body``.

Tests cover the node-id → source-block resolution path:

* method nodes (file lives on the parent class, line range from chunk)
* class-like nodes (file + chunk on the class node itself)
* nodes without a source location (routes, bindings) yield an error
* missing-from-graph yields ``node_not_found``
* missing-from-vector-store yields ``chunk_not_found``

The chunk lookup is exercised against a synthetic in-memory vector
store; the integration test in
``tests/integration/test_query_tools_body_retrieval.py`` covers the
end-to-end LanceDB path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from nexus.core.graph.graph import Graph
from nexus.core.graph.types import Edge, EdgeKind, Node, NodeKind
from nexus.core.query.budget import ResponseBudget
from nexus.core.query.context import QueryContext
from nexus.core.query.coverage import Coverage
from nexus.core.query.tools.get_node_body import (
    GetNodeBodyInput,
    GetNodeBodyTool,
)
from pydantic import ValidationError

if TYPE_CHECKING:
    from collections.abc import Iterator


# ---------------------------------------------------------------------------
# Fake vector store
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _FakeRecord:
    id: str
    vector: list[float] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)


class _FakeVectorStore:
    """In-memory vector store, just enough to satisfy iter_records."""

    def __init__(self, records: list[_FakeRecord]) -> None:
        self._records = records

    def upsert(self, items) -> None:  # pragma: no cover — unused
        raise NotImplementedError

    def delete(self, ids) -> None:  # pragma: no cover — unused
        raise NotImplementedError

    def search(self, query, *, top_k):  # pragma: no cover — unused
        raise NotImplementedError

    def count(self) -> int:
        return len(self._records)

    def iter_records(self) -> Iterator[_FakeRecord]:
        return iter(self._records)

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Fake graph store + project storage
# ---------------------------------------------------------------------------


class _FakeGraphStore:
    def __init__(self, graph: Graph) -> None:
        self._graph = graph

    def load(self) -> Graph:
        return self._graph

    # Not used by get_node_body but required by structural typing in
    # some call sites — kept as no-ops so the duck typing holds.
    def initialise(self) -> None:  # pragma: no cover
        pass

    def persist(self, graph: Graph):  # pragma: no cover
        raise NotImplementedError

    def clear(self) -> None:  # pragma: no cover
        pass

    def close(self) -> None:
        pass


class _FakeStorage:
    slug = "fake"

    def __init__(self, graph: Graph, vector_store: _FakeVectorStore) -> None:
        self._graph = _FakeGraphStore(graph)
        self._vectors = vector_store

    def graph(self) -> _FakeGraphStore:
        return self._graph

    def vectors(self, *, dimensions: int) -> _FakeVectorStore:
        return self._vectors

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


SAMPLE_PHP = """<?php

namespace App\\Operations\\Presentation\\Requests;

use Illuminate\\Foundation\\Http\\FormRequest;

class CreateProductRequest extends FormRequest
{
    public function rules(): array
    {
        return [
            'name' => ['required', 'string', 'max:255'],
            'sku' => ['required', 'string', 'max:100'],
        ];
    }

    public function messages(): array
    {
        return [
            'name.required' => 'The name field is required.',
        ];
    }
}
"""


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
def graph(php_file: Path) -> Graph:
    g = Graph()

    class_id = "class:App\\Operations\\Presentation\\Requests\\CreateProductRequest"
    rules_id = "method:App\\Operations\\Presentation\\Requests\\CreateProductRequest::rules"
    messages_id = "method:App\\Operations\\Presentation\\Requests\\CreateProductRequest::messages"

    g.add_node(
        Node(
            id=class_id,
            kind=NodeKind.FORM_REQUEST,
            name="CreateProductRequest",
            attributes={
                "fqn": "App\\Operations\\Presentation\\Requests\\CreateProductRequest",
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
                "class_fqn": "App\\Operations\\Presentation\\Requests\\CreateProductRequest",
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
                "class_fqn": "App\\Operations\\Presentation\\Requests\\CreateProductRequest",
                "line": 17,
                "visibility": "public",
            },
        ),
    )
    g.add_edge(Edge(source=rules_id, target=class_id, kind=EdgeKind.PART_OF))
    g.add_edge(Edge(source=messages_id, target=class_id, kind=EdgeKind.PART_OF))

    # A route node — has no source location at all, used to verify
    # the "node has no source" error path.
    g.add_node(
        Node(
            id="route:GET:/api/products",
            kind=NodeKind.ROUTE,
            name="/api/products",
            attributes={"uri": "/api/products", "methods": ["GET"]},
        ),
    )

    return g


@pytest.fixture
def vector_store(php_file: Path) -> _FakeVectorStore:
    """Build chunks that mirror what the real chunker would emit."""
    return _FakeVectorStore(
        records=[
            _FakeRecord(
                id="chunk-class-header",
                payload={
                    "node_id": (
                        "class:App\\Operations\\Presentation\\Requests\\CreateProductRequest"
                    ),
                    "kind": "class_header",
                    "file_path": str(php_file),
                    "start_line": 7,
                    "end_line": 8,
                    "symbol": "CreateProductRequest",
                },
            ),
            _FakeRecord(
                id="chunk-rules",
                payload={
                    "node_id": (
                        "method:App\\Operations\\Presentation\\Requests"
                        "\\CreateProductRequest::rules"
                    ),
                    "kind": "method",
                    "file_path": str(php_file),
                    "start_line": 9,
                    "end_line": 15,
                    "symbol": "rules",
                },
            ),
            _FakeRecord(
                id="chunk-messages",
                payload={
                    "node_id": (
                        "method:App\\Operations\\Presentation\\Requests"
                        "\\CreateProductRequest::messages"
                    ),
                    "kind": "method",
                    "file_path": str(php_file),
                    "start_line": 17,
                    "end_line": 22,
                    "symbol": "messages",
                },
            ),
        ],
    )


def _ctx(
    graph: Graph,
    vector_store: _FakeVectorStore,
    project_root: Path,
    *,
    indexed_at: str | None = None,
) -> QueryContext:
    return QueryContext(
        storage=_FakeStorage(graph, vector_store),  # type: ignore[arg-type]
        budget=ResponseBudget(),
        vector_dimensions=4,
        coverage=Coverage(project_path=str(project_root), indexed_at=indexed_at),
    )


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def test_input_rejects_empty_node_id() -> None:
    with pytest.raises(ValidationError):
        GetNodeBodyInput(node_id="")


def test_input_rejects_negative_context_lines() -> None:
    with pytest.raises(ValidationError):
        GetNodeBodyInput(node_id="method:X::y", context_lines=-1)


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_returns_method_body(
    graph: Graph,
    vector_store: _FakeVectorStore,
    project_root: Path,
    php_file: Path,
) -> None:
    tool = GetNodeBodyTool()
    payload = GetNodeBodyInput(
        node_id=("method:App\\Operations\\Presentation\\Requests\\CreateProductRequest::rules"),
    )

    out = tool.execute(payload, _ctx(graph, vector_store, project_root))

    assert out.error is None
    assert out.content is not None
    assert "public function rules" in out.content
    assert "'name' =>" in out.content
    assert out.node_id == payload.node_id
    assert out.node_kind == "method"
    assert out.symbol == "rules"
    assert out.container_class == "App\\Operations\\Presentation\\Requests\\CreateProductRequest"
    assert out.file == str(php_file)
    assert out.start_line == 9
    assert out.end_line == 15


def test_returns_class_header(
    graph: Graph,
    vector_store: _FakeVectorStore,
    project_root: Path,
) -> None:
    tool = GetNodeBodyTool()
    payload = GetNodeBodyInput(
        node_id=("class:App\\Operations\\Presentation\\Requests\\CreateProductRequest"),
    )

    out = tool.execute(payload, _ctx(graph, vector_store, project_root))

    assert out.error is None
    assert out.content is not None
    assert "class CreateProductRequest" in out.content
    assert out.node_kind == "form_request"
    assert out.symbol == "CreateProductRequest"
    assert out.container_class is None  # not a method


def test_context_lines_expands_window(
    graph: Graph,
    vector_store: _FakeVectorStore,
    project_root: Path,
) -> None:
    tool = GetNodeBodyTool()
    payload = GetNodeBodyInput(
        node_id=("method:App\\Operations\\Presentation\\Requests\\CreateProductRequest::rules"),
        context_lines=3,
    )

    out = tool.execute(payload, _ctx(graph, vector_store, project_root))

    assert out.error is None
    assert out.content is not None
    # Widened window now includes the class declaration above (line 7).
    assert "class CreateProductRequest" in out.content


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_unknown_node_returns_node_not_found(
    graph: Graph,
    vector_store: _FakeVectorStore,
    project_root: Path,
) -> None:
    tool = GetNodeBodyTool()
    payload = GetNodeBodyInput(node_id="method:Does\\Not::Exist")

    out = tool.execute(payload, _ctx(graph, vector_store, project_root))

    assert out.error_code == "node_not_found"
    assert out.content is None


def test_node_without_source_location_returns_error(
    graph: Graph,
    vector_store: _FakeVectorStore,
    project_root: Path,
) -> None:
    """A route node has no chunk and no file/line attributes."""
    tool = GetNodeBodyTool()
    payload = GetNodeBodyInput(node_id="route:GET:/api/products")

    out = tool.execute(payload, _ctx(graph, vector_store, project_root))

    assert out.error_code == "node_has_no_source"
    assert out.content is None


def test_method_node_without_chunk_returns_chunk_not_found(
    graph: Graph,
    project_root: Path,
) -> None:
    """A method node in the graph but missing from the vector store."""
    empty_store = _FakeVectorStore(records=[])
    tool = GetNodeBodyTool()
    payload = GetNodeBodyInput(
        node_id=("method:App\\Operations\\Presentation\\Requests\\CreateProductRequest::rules"),
    )

    out = tool.execute(payload, _ctx(graph, empty_store, project_root))

    assert out.error_code == "chunk_not_found"
    assert out.content is None


# ---------------------------------------------------------------------------
# Caching: repeated calls reuse the same chunk index
# ---------------------------------------------------------------------------


def test_repeated_calls_reuse_chunk_index_cache(
    graph: Graph,
    vector_store: _FakeVectorStore,
    project_root: Path,
) -> None:
    """The chunk locator's cache is shared across calls in the same context."""

    class _CountingStore(_FakeVectorStore):
        def __init__(self, records: list[_FakeRecord]) -> None:
            super().__init__(records)
            self.iter_calls = 0

        def iter_records(self):
            self.iter_calls += 1
            return super().iter_records()

    counting = _CountingStore(vector_store._records)
    ctx = QueryContext(
        storage=_FakeStorage(graph, counting),  # type: ignore[arg-type]
        budget=ResponseBudget(),
        vector_dimensions=4,
        coverage=Coverage(project_path=str(project_root)),
    )
    tool = GetNodeBodyTool()
    payload = GetNodeBodyInput(
        node_id=("method:App\\Operations\\Presentation\\Requests\\CreateProductRequest::rules"),
    )

    tool.execute(payload, ctx)
    tool.execute(payload, ctx)
    tool.execute(payload, ctx)

    # The cache lives on the context (or via a context-keyed module-level
    # weak map). Either way, repeated calls must not iterate the store
    # more than once.
    assert counting.iter_calls == 1


# ---------------------------------------------------------------------------
# file_mtime_utc + chunk_may_be_stale (reporter feedback)
# ---------------------------------------------------------------------------


def test_returns_file_mtime_for_method_body(
    graph: Graph,
    vector_store: _FakeVectorStore,
    project_root: Path,
) -> None:
    """Successful resolution carries the source file's mtime."""
    tool = GetNodeBodyTool()
    payload = GetNodeBodyInput(
        node_id=("method:App\\Operations\\Presentation\\Requests\\CreateProductRequest::rules"),
    )

    out = tool.execute(payload, _ctx(graph, vector_store, project_root))

    assert out.error is None
    assert out.file_mtime_utc is not None
    assert "T" in out.file_mtime_utc


def test_chunk_may_be_stale_when_file_newer_than_index(
    graph: Graph,
    vector_store: _FakeVectorStore,
    project_root: Path,
) -> None:
    """File touched after the index was built → staleness signal fires."""
    tool = GetNodeBodyTool()
    payload = GetNodeBodyInput(
        node_id=("method:App\\Operations\\Presentation\\Requests\\CreateProductRequest::rules"),
    )

    out = tool.execute(
        payload,
        _ctx(graph, vector_store, project_root, indexed_at="2020-01-01T00:00:00+00:00"),
    )

    assert out.error is None
    assert out.chunk_may_be_stale is True


def test_chunk_not_stale_when_index_is_newer(
    graph: Graph,
    vector_store: _FakeVectorStore,
    project_root: Path,
) -> None:
    """Far-future indexed_at → no staleness signal."""
    tool = GetNodeBodyTool()
    payload = GetNodeBodyInput(
        node_id=("method:App\\Operations\\Presentation\\Requests\\CreateProductRequest::rules"),
    )

    out = tool.execute(
        payload,
        _ctx(graph, vector_store, project_root, indexed_at="2099-12-31T23:59:59+00:00"),
    )

    assert out.error is None
    assert out.chunk_may_be_stale is False


def test_chunk_may_be_stale_default_when_indexed_at_absent(
    graph: Graph,
    vector_store: _FakeVectorStore,
    project_root: Path,
) -> None:
    """No indexed_at on coverage → can't prove staleness, default to False."""
    tool = GetNodeBodyTool()
    payload = GetNodeBodyInput(
        node_id=("method:App\\Operations\\Presentation\\Requests\\CreateProductRequest::rules"),
    )

    out = tool.execute(payload, _ctx(graph, vector_store, project_root))

    assert out.error is None
    assert out.file_mtime_utc is not None
    assert out.chunk_may_be_stale is False
