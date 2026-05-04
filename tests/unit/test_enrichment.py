"""Tests for nexus.core.chunking.enrichment."""

from __future__ import annotations

from pathlib import Path

from nexus.core.chunking import Chunk, ChunkKind, EnrichedTextBuilder
from nexus.core.graph.graph import Graph
from nexus.core.graph.types import Edge, EdgeKind, Node, NodeKind


def make_chunk(
    *,
    kind: ChunkKind = ChunkKind.METHOD,
    node_id: str | None = None,
    symbol: str = "m",
    text: str = "public function m() { return 1; }",
    namespace: str = "",
) -> Chunk:
    return Chunk(
        id="abc",
        kind=kind,
        file_path=Path("/app/Models/User.php"),
        start_byte=0,
        end_byte=len(text),
        start_line=10,
        end_line=12,
        text=text,
        node_id=node_id,
        symbol=symbol,
        attributes={"namespace": namespace},
    )


class TestHeader:
    def test_header_uses_node_kind_when_available(self) -> None:
        g = Graph()
        g.add_node(
            Node(
                id="method:App\\Models\\User::posts",
                kind=NodeKind.CONTROLLER_METHOD,
                name="posts",
                attributes={"class_fqn": "App\\Models\\User"},
            ),
        )

        chunk = make_chunk(
            node_id="method:App\\Models\\User::posts",
            symbol="posts",
        )
        text = EnrichedTextBuilder().build(chunk, g)

        first_line = text.splitlines()[0]
        assert first_line == "controller_method: posts"

    def test_header_falls_back_to_chunk_kind_when_node_missing(self) -> None:
        g = Graph()
        chunk = make_chunk(node_id=None, symbol="helper")
        text = EnrichedTextBuilder().build(chunk, g)

        assert text.splitlines()[0] == "method: helper"


class TestLocation:
    def test_file_and_range(self) -> None:
        g = Graph()
        chunk = make_chunk()

        text = EnrichedTextBuilder().build(chunk, g)

        assert "file: /app/Models/User.php:10-12" in text


class TestNamespace:
    def test_namespace_included_when_present(self) -> None:
        g = Graph()
        chunk = make_chunk(namespace="App\\Models")

        text = EnrichedTextBuilder().build(chunk, g)

        assert "namespace: App\\Models" in text

    def test_namespace_omitted_when_empty(self) -> None:
        g = Graph()
        chunk = make_chunk(namespace="")

        text = EnrichedTextBuilder().build(chunk, g)

        assert "namespace:" not in text


class TestMethodContext:
    def test_in_class_line(self) -> None:
        g = Graph()
        g.add_node(
            Node(
                id="method:App\\Models\\User::posts",
                kind=NodeKind.CONTROLLER_METHOD,
                name="posts",
                attributes={"class_fqn": "App\\Models\\User"},
            ),
        )
        chunk = make_chunk(node_id="method:App\\Models\\User::posts", symbol="posts")

        text = EnrichedTextBuilder().build(chunk, g)

        assert "in class: App\\Models\\User" in text


class TestRouteContext:
    def test_route_with_handler_and_middleware(self) -> None:
        g = Graph()
        g.add_node(
            Node(
                id="route:GET:/users",
                kind=NodeKind.ROUTE,
                name="/users",
                attributes={
                    "methods": ["GET", "HEAD"],
                    "uri": "/users",
                    "name": "users.index",
                },
            ),
        )
        g.add_node(
            Node(
                id="middleware:auth",
                kind=NodeKind.MIDDLEWARE,
                name="auth",
            ),
        )
        g.add_node(
            Node(
                id="method:App\\Http\\UserController::index",
                kind=NodeKind.CONTROLLER_METHOD,
                name="index",
                attributes={"class_fqn": "App\\Http\\UserController"},
            ),
        )
        g.add_edge(
            Edge(
                source="route:GET:/users",
                target="middleware:auth",
                kind=EdgeKind.HAS_MIDDLEWARE,
            ),
        )
        g.add_edge(
            Edge(
                source="route:GET:/users",
                target="method:App\\Http\\UserController::index",
                kind=EdgeKind.ROUTES_TO,
            ),
        )

        chunk = Chunk(
            id="x",
            kind=ChunkKind.CLASS_HEADER,  # irrelevant; the node kind drives the template
            file_path=Path("/routes/web.php"),
            start_byte=0,
            end_byte=1,
            start_line=1,
            end_line=1,
            text="",
            node_id="route:GET:/users",
            symbol="/users",
        )

        text = EnrichedTextBuilder().build(chunk, g)

        assert "route: GET|HEAD /users" in text
        assert "route name: users.index" in text
        assert "middleware: auth" in text
        assert "handled by: App\\Http\\UserController::index" in text

    def test_middleware_can_be_suppressed(self) -> None:
        g = Graph()
        g.add_node(
            Node(
                id="r",
                kind=NodeKind.ROUTE,
                name="/x",
                attributes={"methods": ["GET"], "uri": "/x"},
            ),
        )
        g.add_node(Node(id="mw:a", kind=NodeKind.MIDDLEWARE, name="a"))
        g.add_edge(Edge(source="r", target="mw:a", kind=EdgeKind.HAS_MIDDLEWARE))

        chunk = Chunk(
            id="x",
            kind=ChunkKind.CLASS_HEADER,
            file_path=Path("/routes/web.php"),
            start_byte=0,
            end_byte=1,
            start_line=1,
            end_line=1,
            text="",
            node_id="r",
        )

        builder = EnrichedTextBuilder(include_middleware=False)
        text = builder.build(chunk, g)

        assert "middleware:" not in text


class TestListenerContext:
    def test_listener_lists_events(self) -> None:
        g = Graph()
        g.add_node(
            Node(
                id="listener:App\\Listeners\\Send::handle",
                kind=NodeKind.LISTENER,
                name="App\\Listeners\\Send",
            ),
        )
        g.add_node(
            Node(
                id="event:App\\Events\\UserRegistered",
                kind=NodeKind.EVENT,
                name="App\\Events\\UserRegistered",
            ),
        )
        g.add_edge(
            Edge(
                source="listener:App\\Listeners\\Send::handle",
                target="event:App\\Events\\UserRegistered",
                kind=EdgeKind.LISTENS_TO,
            ),
        )

        chunk = Chunk(
            id="x",
            kind=ChunkKind.CLASS_HEADER,
            file_path=Path("/x.php"),
            start_byte=0,
            end_byte=1,
            start_line=1,
            end_line=1,
            text="",
            node_id="listener:App\\Listeners\\Send::handle",
        )

        text = EnrichedTextBuilder().build(chunk, g)

        assert "listens to: App\\Events\\UserRegistered" in text


class TestClassContext:
    def test_class_extends_and_implements(self) -> None:
        g = Graph()
        g.add_node(
            Node(
                id="class:App\\Models\\User",
                kind=NodeKind.MODEL,
                name="User",
                attributes={"fqn": "App\\Models\\User"},
            ),
        )
        g.add_node(
            Node(
                id="class:Illuminate\\Foundation\\Auth\\User",
                kind=NodeKind.CLASS,
                name="User",
                attributes={"fqn": "Illuminate\\Foundation\\Auth\\User"},
            ),
        )
        g.add_node(
            Node(
                id="class:Illuminate\\Contracts\\Auth\\Authenticatable",
                kind=NodeKind.CLASS,
                name="Authenticatable",
                attributes={"fqn": "Illuminate\\Contracts\\Auth\\Authenticatable"},
            ),
        )
        g.add_edge(
            Edge(
                source="class:App\\Models\\User",
                target="class:Illuminate\\Foundation\\Auth\\User",
                kind=EdgeKind.EXTENDS,
            ),
        )
        g.add_edge(
            Edge(
                source="class:App\\Models\\User",
                target="class:Illuminate\\Contracts\\Auth\\Authenticatable",
                kind=EdgeKind.IMPLEMENTS,
            ),
        )

        chunk = Chunk(
            id="x",
            kind=ChunkKind.CLASS_HEADER,
            file_path=Path("/User.php"),
            start_byte=0,
            end_byte=1,
            start_line=1,
            end_line=1,
            text="",
            node_id="class:App\\Models\\User",
        )

        text = EnrichedTextBuilder().build(chunk, g)

        assert "class: App\\Models\\User" in text
        assert "extends: Illuminate\\Foundation\\Auth\\User" in text
        assert "implements: Illuminate\\Contracts\\Auth\\Authenticatable" in text


class TestSourceIsIncluded:
    def test_source_appears_after_context(self) -> None:
        g = Graph()
        chunk = make_chunk(
            text="public function foo() { return 1; }",
            symbol="foo",
        )

        text = EnrichedTextBuilder().build(chunk, g)

        assert "source:" in text
        assert "public function foo() { return 1; }" in text
        # Source block comes last.
        assert text.rstrip().endswith("public function foo() { return 1; }")


class TestDeterminism:
    def test_same_inputs_produce_same_text(self) -> None:
        g = Graph()
        g.add_node(
            Node(
                id="r",
                kind=NodeKind.ROUTE,
                name="/x",
                attributes={"methods": ["GET"], "uri": "/x", "name": "x"},
            ),
        )

        chunk = Chunk(
            id="x",
            kind=ChunkKind.CLASS_HEADER,
            file_path=Path("/r.php"),
            start_byte=0,
            end_byte=1,
            start_line=1,
            end_line=1,
            text="",
            node_id="r",
        )

        builder = EnrichedTextBuilder()
        assert builder.build(chunk, g) == builder.build(chunk, g)
