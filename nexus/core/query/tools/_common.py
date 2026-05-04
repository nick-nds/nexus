"""Shared helpers for typed access to the loose ``attributes`` dicts.

Graph nodes store metadata as ``dict[str, object]`` so the store
layer doesn't need a per-node-kind schema. Query tools need
specific typed fields from those dicts (a method's line number,
a route's URI). These helpers do the isinstance-narrowing in one
place so every tool gets the same behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from nexus.core.graph.ids import class_id
from nexus.core.graph.types import EdgeKind
from nexus.core.query.traversal import outgoing

if TYPE_CHECKING:
    from nexus.core.graph.graph import Graph
    from nexus.core.graph.types import Node


# ---------------------------------------------------------------------------
# Typed readers for loose attribute dicts
# ---------------------------------------------------------------------------


def str_attr(attrs: dict[str, Any], key: str) -> str | None:
    """Return ``attrs[key]`` if it's a string, else ``None``."""
    value = attrs.get(key)
    return value if isinstance(value, str) else None


def bool_attr(attrs: dict[str, Any], key: str, *, default: bool = False) -> bool:
    """Return ``attrs[key]`` coerced to ``bool`` (default on missing)."""
    return bool(attrs.get(key, default))


def int_attr(attrs: dict[str, Any], key: str) -> int | None:
    """Return ``attrs[key]`` if it's an int-ish value, else ``None``.

    Excludes booleans even though they're a subclass of ``int`` —
    we don't want ``True`` / ``False`` showing up as line numbers.
    """
    value = attrs.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def str_list_attr(attrs: dict[str, Any], key: str) -> list[str]:
    """Return ``attrs[key]`` as ``list[str]``, coercing or empty."""
    value = attrs.get(key)
    if not isinstance(value, list):
        return []
    return [str(v) for v in value]


def dict_list_attr(attrs: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """Return ``attrs[key]`` as ``list[dict]``, or empty."""
    value = attrs.get(key)
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, dict)]


# ---------------------------------------------------------------------------
# Typed method view
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MethodAttributes:
    """Typed snapshot of a method node's attribute fields.

    Used by :func:`read_method_attributes` so tool code can access
    strongly-typed fields instead of tuning out mypy warnings.
    """

    name: str
    visibility: str | None
    static: bool
    abstract: bool
    final: bool
    return_type: str | None
    line: int | None
    parameters: list[dict[str, Any]]


def read_method_attributes(node: Node) -> MethodAttributes:
    """Build a :class:`MethodAttributes` snapshot from a method graph node."""
    attrs = node.attributes
    return MethodAttributes(
        name=node.name,
        visibility=str_attr(attrs, "visibility"),
        static=bool_attr(attrs, "static"),
        abstract=bool_attr(attrs, "abstract"),
        final=bool_attr(attrs, "final"),
        return_type=str_attr(attrs, "return_type"),
        line=int_attr(attrs, "line"),
        parameters=dict_list_attr(attrs, "parameters"),
    )


# ---------------------------------------------------------------------------
# Route summary (lazy import to break cycle with list_routes)
# ---------------------------------------------------------------------------


def route_summary(node: Node, graph: Graph) -> Any:
    """Build a :class:`RouteSummary` from a route graph node.

    Imported lazily to break a cyclic import with ``list_routes``.
    Returns :class:`Any` so the import can stay inside the
    function body; the concrete type is
    :class:`nexus.core.query.tools.list_routes.RouteSummary`.
    """
    from nexus.core.query.tools.list_routes import RouteSummary  # noqa: PLC0415

    attrs = node.attributes
    middleware: list[str] = []
    controller: str | None = None
    method_name: str | None = None

    for edge in outgoing(graph, node.id):
        if edge.kind == EdgeKind.HAS_MIDDLEWARE:
            target = graph.node_by_id(edge.target)
            if target is not None:
                middleware.append(target.name)
        elif edge.kind == EdgeKind.ROUTES_TO:
            target = graph.node_by_id(edge.target)
            if target is not None:
                class_fqn = str_attr(target.attributes, "class_fqn")
                if class_fqn is not None:
                    controller = class_fqn
                method_name = target.name

    return RouteSummary(
        id=node.id,
        uri=str_attr(attrs, "uri") or node.name,
        methods=str_list_attr(attrs, "methods"),
        name=str_attr(attrs, "name"),
        controller=controller,
        method=method_name,
        action_kind=str_attr(attrs, "action_kind") or "unknown",
        middleware=sorted(middleware),
    )


def class_fqn_to_id(fqn: str) -> str:
    """Convenience: ``class:<fqn>``."""
    return class_id(fqn)


def fqn_from_class_id(graph: Graph, class_node_id: str) -> str:
    """Resolve a ``class:<fqn>`` edge target to its bare FQN.

    Class node ids embed the fully-qualified name after the
    ``class:`` prefix; node ``.name`` carries the *short* name (e.g.
    ``"OutboxWorkerJob"``), which is wrong for an agent that needs to
    reference the class globally. We always strip the prefix from the
    id rather than using ``node.name`` so project classes and vendor
    classes alike surface as full FQNs.

    Falls back to ``node.name`` for non-``class:`` ids (defensive —
    no edge consumer should pass one in today, but keeps the call
    resilient to id-scheme changes).
    """
    if class_node_id.startswith("class:"):
        return class_node_id[len("class:") :]
    target = graph.node_by_id(class_node_id)
    if target is not None:
        return target.name
    return class_node_id


def file_for_method_node(graph: Graph, method_node: Node) -> str | None:
    """Return the source file path of the class a method node belongs to.

    Method nodes carry only ``class_fqn`` and ``line``; the parent
    class node is the one with ``file``. Tools that surface a "go to
    this method's source" link (``trace_route`` handler, ``find_handlers``
    rows) should call this so they don't return ``file: None`` and
    force the agent into an extra ``describe_class`` round trip.
    """
    class_fqn = str_attr(method_node.attributes, "class_fqn")
    if class_fqn is None:
        return None
    class_node = graph.node_by_id(class_id(class_fqn))
    if class_node is None:
        return None
    return str_attr(class_node.attributes, "file")
