"""Unit tests for :class:`FindCacheUsersTool`."""

from __future__ import annotations

from unittest.mock import MagicMock

from nexus.core.graph.graph import Graph
from nexus.core.graph.types import Edge, EdgeKind, Node, NodeKind
from nexus.core.query.budget import ResponseBudget
from nexus.core.query.context import QueryContext
from nexus.core.query.tools.find_cache_users import (
    FindCacheUsersInput,
    FindCacheUsersTool,
)


def _make_ctx(graph: Graph) -> QueryContext:
    handle = MagicMock()
    handle.load.return_value = graph
    storage = MagicMock()
    storage.graph.return_value = handle
    return QueryContext(storage=storage, budget=ResponseBudget())


def _add_method(g: Graph, class_fqn: str, method: str) -> str:
    method_id = f"method:{class_fqn}::{method}"
    if g.node_by_id(method_id) is None:
        g.add_node(
            Node(
                id=method_id,
                kind=NodeKind.CONTROLLER_METHOD,
                name=method,
                attributes={"class_fqn": class_fqn, "file": "/app.php", "line": 5},
            ),
        )
    return method_id


def _add_cache_key(g: Graph, key: str) -> str:
    node_id = f"cache_key:{key}"
    if g.node_by_id(node_id) is None:
        g.add_node(
            Node(
                id=node_id,
                kind=NodeKind.CACHE_KEY,
                name=key,
                attributes={},
            ),
        )
    return node_id


def _add_cache_edge(
    g: Graph,
    *,
    method_id: str,
    cache_id: str,
    mode: str,
    file_: str = "/caller.php",
    line: int = 42,
    form: str | None = None,
) -> None:
    attrs: dict[str, object] = {"file": file_, "line": line}
    if form is not None:
        attrs["form"] = form
    g.add_edge(
        Edge(
            source=method_id,
            target=cache_id,
            kind=EdgeKind.CACHE_READ if mode == "read" else EdgeKind.CACHE_WRITE,
            attributes=attrs,
        ),
    )


def _make_graph() -> Graph:
    g = Graph()
    _add_cache_key(g, "settings.timezone")
    _add_cache_key(g, "user.42.session")
    _add_cache_key(g, "user.99.session")
    _add_cache_key(g, "feature.flags")

    settings_get = _add_method(g, "App\\Services\\Settings", "get")
    settings_set = _add_method(g, "App\\Services\\Settings", "set")
    user_loader = _add_method(g, "App\\Services\\UserCache", "load")
    feature_flag = _add_method(g, "App\\Services\\FeatureFlags", "isEnabled")

    _add_cache_edge(
        g,
        method_id=settings_get,
        cache_id="cache_key:settings.timezone",
        mode="read",
        line=10,
    )
    _add_cache_edge(
        g,
        method_id=settings_set,
        cache_id="cache_key:settings.timezone",
        mode="write",
        line=20,
    )
    _add_cache_edge(
        g,
        method_id=user_loader,
        cache_id="cache_key:user.42.session",
        mode="read",
        line=30,
        form="prefix",
    )
    _add_cache_edge(
        g,
        method_id=user_loader,
        cache_id="cache_key:user.99.session",
        mode="read",
        line=30,
        form="prefix",
    )
    _add_cache_edge(
        g,
        method_id=feature_flag,
        cache_id="cache_key:feature.flags",
        mode="read",
        line=15,
    )
    return g


# ---------------------------------------------------------------------------
# Exact key match
# ---------------------------------------------------------------------------


def test_exact_key_returns_readers_and_writers() -> None:
    ctx = _make_ctx(_make_graph())
    output = FindCacheUsersTool().execute(
        FindCacheUsersInput(key="settings.timezone"),
        ctx,
    )

    assert output.error_code is None
    assert output.matched_keys == ["settings.timezone"]
    modes = sorted(r.mode for r in output.rows)
    assert modes == ["read", "write"]
    methods = sorted(r.method for r in output.rows)
    assert methods == ["get", "set"]


def test_mode_filter_read_only() -> None:
    ctx = _make_ctx(_make_graph())
    output = FindCacheUsersTool().execute(
        FindCacheUsersInput(key="settings.timezone", mode="read"),
        ctx,
    )

    assert {r.mode for r in output.rows} == {"read"}
    assert {r.method for r in output.rows} == {"get"}


def test_mode_filter_write_only() -> None:
    ctx = _make_ctx(_make_graph())
    output = FindCacheUsersTool().execute(
        FindCacheUsersInput(key="settings.timezone", mode="write"),
        ctx,
    )

    assert {r.mode for r in output.rows} == {"write"}
    assert {r.method for r in output.rows} == {"set"}


def test_invalid_mode_returns_structured_error() -> None:
    ctx = _make_ctx(_make_graph())
    output = FindCacheUsersTool().execute(
        FindCacheUsersInput(key="settings.timezone", mode="bogus"),
        ctx,
    )

    assert output.error_code == "invalid_mode"


# ---------------------------------------------------------------------------
# Pattern resolution
# ---------------------------------------------------------------------------


def test_glob_match_resolves_multiple_keys() -> None:
    """``user.*.session`` should match both 42 and 99."""
    ctx = _make_ctx(_make_graph())
    output = FindCacheUsersTool().execute(
        FindCacheUsersInput(key="user.*.session"),
        ctx,
    )

    assert output.error_code is None
    assert set(output.matched_keys) == {"user.42.session", "user.99.session"}
    # Both rows came from the same method (one call site per key).
    assert all(r.method == "load" for r in output.rows)


def test_substring_match_when_no_wildcard() -> None:
    """``feature`` (no glob) should still match ``feature.flags``."""
    ctx = _make_ctx(_make_graph())
    output = FindCacheUsersTool().execute(
        FindCacheUsersInput(key="feature"),
        ctx,
    )

    assert output.matched_keys == ["feature.flags"]
    assert {r.method for r in output.rows} == {"isEnabled"}


def test_unknown_key_returns_structured_error() -> None:
    ctx = _make_ctx(_make_graph())
    output = FindCacheUsersTool().execute(
        FindCacheUsersInput(key="nonsense.zzz"),
        ctx,
    )

    assert output.error_code == "key_not_found"
    assert output.rows == []


# ---------------------------------------------------------------------------
# Edge attribute propagation
# ---------------------------------------------------------------------------


def test_form_propagated_from_edge_attribute() -> None:
    """``form="prefix"`` on the edge should land on the row."""
    ctx = _make_ctx(_make_graph())
    output = FindCacheUsersTool().execute(
        FindCacheUsersInput(key="user.42.session"),
        ctx,
    )

    [row] = output.rows
    assert row.form == "prefix"


def test_call_site_file_and_line_propagated() -> None:
    ctx = _make_ctx(_make_graph())
    output = FindCacheUsersTool().execute(
        FindCacheUsersInput(key="settings.timezone", mode="read"),
        ctx,
    )

    [row] = output.rows
    assert row.file == "/caller.php"
    assert row.line == 10
