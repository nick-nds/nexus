"""Unit tests for the ``cache_keys`` field on :class:`DescribeClassOutput`.

Covers the new behaviour added when ``find_cache_users`` was wired
in: every cache key any method on the class touches surfaces as a
:class:`CacheKeyUsage`, with ``mode`` collapsing read+write into
``both`` and the edge's ``form`` attribute riding along.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from nexus.core.graph.graph import Graph
from nexus.core.graph.types import Edge, EdgeKind, Node, NodeKind
from nexus.core.query.budget import ResponseBudget
from nexus.core.query.context import QueryContext
from nexus.core.query.tools.describe_class import (
    DescribeClassInput,
    DescribeClassTool,
)


def _make_ctx(graph: Graph) -> QueryContext:
    handle = MagicMock()
    handle.load.return_value = graph
    storage = MagicMock()
    storage.graph.return_value = handle
    return QueryContext(storage=storage, budget=ResponseBudget())


def _build_class_graph() -> Graph:
    g = Graph()
    fqn = "App\\Services\\SettingsService"
    class_id = f"class:{fqn}"
    g.add_node(
        Node(
            id=class_id,
            kind=NodeKind.CLASS,
            name="SettingsService",
            attributes={"fqn": fqn, "namespace": "App\\Services"},
        ),
    )
    method_id = f"method:{fqn}::sync"
    g.add_node(
        Node(
            id=method_id,
            kind=NodeKind.METHOD,
            name="sync",
            attributes={"class_fqn": fqn},
        ),
    )
    g.add_edge(Edge(source=method_id, target=class_id, kind=EdgeKind.PART_OF, attributes={}))

    # Two cache keys: one read-only, one read+write.
    for key in ("settings.timezone", "feature.flags"):
        g.add_node(
            Node(
                id=f"cache_key:{key}",
                kind=NodeKind.CACHE_KEY,
                name=key,
                attributes={},
            ),
        )

    g.add_edge(
        Edge(
            source=method_id,
            target="cache_key:feature.flags",
            kind=EdgeKind.CACHE_READ,
            attributes={"file": "/svc.php", "line": 10, "form": "literal"},
        ),
    )
    g.add_edge(
        Edge(
            source=method_id,
            target="cache_key:settings.timezone",
            kind=EdgeKind.CACHE_READ,
            attributes={"file": "/svc.php", "line": 12, "form": "literal"},
        ),
    )
    g.add_edge(
        Edge(
            source=method_id,
            target="cache_key:settings.timezone",
            kind=EdgeKind.CACHE_WRITE,
            attributes={"file": "/svc.php", "line": 20, "form": "literal"},
        ),
    )
    return g


def test_describe_class_includes_cache_keys() -> None:
    ctx = _make_ctx(_build_class_graph())
    output = DescribeClassTool().execute(
        DescribeClassInput(fqn="App\\Services\\SettingsService"),
        ctx,
    )

    assert output.error_code is None
    keys = {row.key: row for row in output.cache_keys}
    assert set(keys) == {"settings.timezone", "feature.flags"}


def test_cache_keys_collapse_read_and_write_into_both() -> None:
    ctx = _make_ctx(_build_class_graph())
    output = DescribeClassTool().execute(
        DescribeClassInput(fqn="App\\Services\\SettingsService"),
        ctx,
    )

    keys = {row.key: row for row in output.cache_keys}
    assert keys["settings.timezone"].mode == "both"
    assert keys["feature.flags"].mode == "read"


def test_cache_key_form_propagated_from_edge() -> None:
    ctx = _make_ctx(_build_class_graph())
    output = DescribeClassTool().execute(
        DescribeClassInput(fqn="App\\Services\\SettingsService"),
        ctx,
    )

    keys = {row.key: row for row in output.cache_keys}
    assert keys["settings.timezone"].form == "literal"
    assert keys["feature.flags"].form == "literal"


def test_cache_keys_empty_when_class_does_no_caching() -> None:
    """A class with no CACHE_* edges should have an empty cache_keys list."""
    g = Graph()
    fqn = "App\\Models\\Plain"
    g.add_node(
        Node(
            id=f"class:{fqn}",
            kind=NodeKind.MODEL,
            name="Plain",
            attributes={"fqn": fqn},
        ),
    )
    ctx = _make_ctx(g)

    output = DescribeClassTool().execute(DescribeClassInput(fqn=fqn), ctx)

    assert output.error_code is None
    assert output.cache_keys == []
