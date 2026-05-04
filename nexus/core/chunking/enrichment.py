"""Graph-aware enriched text for embedding.

v1's embeddings used raw source code as input. That worked badly:
vectors clustered by syntax ("classes with many private properties")
instead of by meaning ("things that handle orders"). v2 builds a
structured natural-language description of each chunk that weaves in
the graph context around it — the containing class, the module, who
calls it, what events it fires — and embeds *that*.

The specific template lives in
``internal_docs/08-embedding-and-chunking.md`` §"Graph-enriched
embedding text". This module implements that template as a pure
function of (chunk, graph) plus optional knobs.

Why pure
========

Every argument is data. No filesystem reads, no embedder calls, no
logging. That lets the Phase 3 tests assert on exact output strings
and keeps the enrichment step cacheable — swapping in a new template
flavour is an input change, not a code-path change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from nexus.core.graph.types import EdgeKind, NodeKind

if TYPE_CHECKING:
    from nexus.core.chunking.chunk import Chunk
    from nexus.core.graph.graph import Graph
    from nexus.core.graph.types import Node


@dataclass(slots=True)
class EnrichedTextBuilder:
    """Turns a :class:`Chunk` and its graph context into embedding input.

    The builder is stateless; construct one per pipeline run and call
    :meth:`build` for every chunk. Configuration knobs (how many
    callers to include, whether to list middleware on route chunks,
    the neighbourhood depth) live on the instance so the pipeline can
    tune them without threading arguments through the caller chain.

    Defaults are chosen so a freshly-installed Nexus produces
    reasonable quality without any user configuration.
    """

    max_callers: int = 5
    max_related_events: int = 5
    include_middleware: bool = True

    def build(self, chunk: Chunk, graph: Graph) -> str:
        """Return the enriched text string for ``chunk``.

        Args:
            chunk: The source chunk produced by the chunker.
            graph: The project graph the chunk links into. May be
                ``None``-free but partially populated — missing
                neighbours are silently omitted, not an error.

        Returns:
            A multiline natural-language description of the chunk
            suitable as embedder input.
        """
        lines: list[str] = []

        node: Node | None = graph.node_by_id(chunk.node_id) if chunk.node_id is not None else None
        header = self._header_line(chunk, node)
        lines.append(header)

        location = self._location_line(chunk)
        if location:
            lines.append(location)

        namespace = chunk.attributes.get("namespace")
        if isinstance(namespace, str) and namespace:
            lines.append(f"namespace: {namespace}")

        if node is not None:
            lines.extend(self._context_lines(node, graph))

        lines.append("")  # blank line before the source
        lines.append("source:")
        lines.append(chunk.text.rstrip())

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Header + location
    # ------------------------------------------------------------------

    @staticmethod
    def _header_line(chunk: Chunk, node: Node | None) -> str:
        """First line: ``<kind>: <symbol>`` with node kind if known."""
        symbol = chunk.symbol or "<anonymous>"
        if node is not None:
            return f"{node.kind.value}: {symbol}"
        return f"{chunk.kind.value}: {symbol}"

    @staticmethod
    def _location_line(chunk: Chunk) -> str:
        """Second line: ``file: <path>:<start>-<end>``."""
        return f"file: {chunk.file_path}:{chunk.start_line}-{chunk.end_line}"

    # ------------------------------------------------------------------
    # Graph context
    # ------------------------------------------------------------------

    def _context_lines(self, node: Node, graph: Graph) -> list[str]:
        """Produce the middle block that describes graph relationships.

        The exact lines depend on the node kind. For a method node:
        containing class, callers, events fired, jobs dispatched. For
        a route node: method, URI, middleware, controller method. For
        a listener: event it handles.

        Missing relationships are silently omitted — a method that
        fires no events simply has no ``fires:`` line.
        """
        lines: list[str] = []

        if node.kind == NodeKind.CONTROLLER_METHOD:
            lines.extend(self._method_context(node, graph))
        elif node.kind == NodeKind.ROUTE:
            lines.extend(self._route_context(node, graph))
        elif node.kind == NodeKind.LISTENER:
            lines.extend(self._listener_context(node, graph))
        elif node.kind in {
            NodeKind.CONTROLLER,
            NodeKind.MODEL,
            NodeKind.JOB,
            NodeKind.EVENT,
            NodeKind.NOTIFICATION,
            NodeKind.POLICY,
            NodeKind.FORM_REQUEST,
            NodeKind.CLASS,
        }:
            lines.extend(self._class_context(node, graph))

        return lines

    def _method_context(self, node: Node, graph: Graph) -> list[str]:
        lines: list[str] = []
        class_fqn = node.attributes.get("class_fqn")
        if isinstance(class_fqn, str):
            lines.append(f"in class: {class_fqn}")

        fire_names = self._neighbour_names(graph, self._targets_of(node, graph, EdgeKind.FIRES))
        if fire_names:
            lines.append(f"fires: {', '.join(sorted(set(fire_names))[: self.max_related_events])}")

        dispatch_names = self._neighbour_names(
            graph, self._targets_of(node, graph, EdgeKind.DISPATCHES)
        )
        if dispatch_names:
            lines.append(
                f"dispatches: {', '.join(sorted(set(dispatch_names))[: self.max_related_events])}"
            )

        caller_ids = self._sources_of(node, graph, EdgeKind.CALLS)[: self.max_callers]
        caller_names = self._neighbour_names(graph, caller_ids)
        if caller_names:
            lines.append(f"callers: {', '.join(caller_names)}")

        return lines

    def _route_context(self, node: Node, graph: Graph) -> list[str]:
        lines: list[str] = []
        methods = node.attributes.get("methods")
        uri = node.attributes.get("uri")
        if isinstance(methods, list) and uri:
            lines.append(f"route: {'|'.join(str(m) for m in methods)} {uri}")

        route_name = node.attributes.get("name")
        if isinstance(route_name, str):
            lines.append(f"route name: {route_name}")

        if self.include_middleware:
            mw_names = self._neighbour_names(
                graph, self._targets_of(node, graph, EdgeKind.HAS_MIDDLEWARE)
            )
            if mw_names:
                lines.append(f"middleware: {', '.join(mw_names)}")

        handler = self._targets_of(node, graph, EdgeKind.ROUTES_TO)
        if handler:
            handler_node = graph.node_by_id(handler[0])
            if handler_node is not None:
                class_fqn = handler_node.attributes.get("class_fqn")
                if isinstance(class_fqn, str):
                    lines.append(f"handled by: {class_fqn}::{handler_node.name}")

        return lines

    def _listener_context(self, node: Node, graph: Graph) -> list[str]:
        lines: list[str] = []
        event_names = self._neighbour_names(
            graph, self._targets_of(node, graph, EdgeKind.LISTENS_TO)
        )
        if event_names:
            lines.append(f"listens to: {', '.join(event_names)}")
        return lines

    def _class_context(self, node: Node, graph: Graph) -> list[str]:
        lines: list[str] = []
        fqn = node.attributes.get("fqn")
        if isinstance(fqn, str):
            lines.append(f"class: {fqn}")

        parents = self._targets_of(node, graph, EdgeKind.EXTENDS)
        if parents:
            parent_node = graph.node_by_id(parents[0])
            if parent_node is not None:
                parent_fqn = parent_node.attributes.get("fqn")
                lines.append(
                    f"extends: {parent_fqn if isinstance(parent_fqn, str) else parent_node.name}"
                )

        interface_fqns = self._neighbour_fqns(
            graph, self._targets_of(node, graph, EdgeKind.IMPLEMENTS)
        )
        if interface_fqns:
            lines.append(f"implements: {', '.join(interface_fqns)}")

        return lines

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _targets_of(node: Node, graph: Graph, kind: EdgeKind) -> list[str]:
        return [e.target for e in graph.edges_from(node.id) if e.kind == kind]

    @staticmethod
    def _sources_of(node: Node, graph: Graph, kind: EdgeKind) -> list[str]:
        return [e.source for e in graph.edges_to(node.id) if e.kind == kind]

    @staticmethod
    def _neighbour_names(graph: Graph, ids: list[str]) -> list[str]:
        """Resolve ids to their node ``name``s, skipping any that don't exist."""
        names: list[str] = []
        for node_id in ids:
            n = graph.node_by_id(node_id)
            if n is not None:
                names.append(n.name)
        return names

    @staticmethod
    def _neighbour_fqns(graph: Graph, ids: list[str]) -> list[str]:
        """Resolve ids to their ``fqn`` attribute when set, else the node name."""
        out: list[str] = []
        for node_id in ids:
            n = graph.node_by_id(node_id)
            if n is None:
                continue
            fqn = n.attributes.get("fqn")
            out.append(fqn if isinstance(fqn, str) else n.name)
        return out
