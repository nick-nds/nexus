"""The in-memory ``Graph`` container.

A graph is a tuple of nodes plus edges, plus the warnings the builder
emitted while constructing it. The container itself is plain — there is
no traversal API here, because traversal is the storage layer's job
(SQLite recursive CTEs in Phase 4 are dramatically faster than anything
we could write in Python over a multi-thousand-node graph).

The container does provide:

* O(1) ``node_by_id`` lookup via an internal dict built lazily on first
  access.
* :meth:`add_node` and :meth:`add_edge` helpers used by the builder
  with deduplication semantics: adding a node whose id already exists
  is a no-op (the first wins). Tests rely on this so we can assemble
  graphs incrementally without bookkeeping.
* :meth:`merge` to combine two partial graphs (used by tests and by
  the federation layer in Phase 6).

The container is intentionally mutable during construction. Once the
builder hands a ``Graph`` to a store, callers should treat it as
immutable; nothing in the type system enforces this because freezing
would prevent the natural append-during-build pattern.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from nexus.core.graph.types import Edge, Node
    from nexus.core.outcome import Warning


@dataclass(slots=True)
class Graph:
    """Mutable in-memory typed graph.

    The graph builder appends to ``nodes``, ``edges``, and ``warnings``
    as it walks the reflection document. The id → node index is
    maintained eagerly so deduplication during build is O(1) per add;
    on the helm-v7 fixture (~46k nodes) this is the difference between
    a 30-second build and a 30-millisecond build.
    """

    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    warnings: list[Warning] = field(default_factory=list)
    _node_index: dict[str, Node] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )
    _outgoing_index: dict[str, list[Edge]] | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    _incoming_index: dict[str, list[Edge]] | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def _invalidate_traversal_caches(self) -> None:
        """Drop cached adjacency indices after a mutation."""
        self._outgoing_index = None
        self._incoming_index = None

    def outgoing_index(self) -> dict[str, list[Edge]]:
        """Lazy source-id → outgoing-edges index.

        Built on first access and cached for the life of the graph.
        Query-time tools walk the graph thousands of times per second;
        a per-call linear scan of ``self.edges`` turns a 200 ms budget
        into a 2 s regression on the helm-v7 index. The index is reset
        whenever :meth:`add_edge` (or merge) mutates the edge list so
        correctness is preserved during incremental construction.
        """
        if self._outgoing_index is None:
            index: dict[str, list[Edge]] = {}
            for edge in self.edges:
                index.setdefault(edge.source, []).append(edge)
            self._outgoing_index = index
        return self._outgoing_index

    def incoming_index(self) -> dict[str, list[Edge]]:
        """Lazy target-id → incoming-edges index. See :meth:`outgoing_index`."""
        if self._incoming_index is None:
            index: dict[str, list[Edge]] = {}
            for edge in self.edges:
                index.setdefault(edge.target, []).append(edge)
            self._incoming_index = index
        return self._incoming_index

    def add_node(self, node: Node) -> bool:
        """Append a node, deduplicating by id.

        Returns:
            ``True`` if the node was new, ``False`` if a node with the
            same id was already present.
        """
        if node.id in self._node_index:
            return False
        self._node_index[node.id] = node
        self.nodes.append(node)
        return True

    def add_edge(self, edge: Edge) -> None:
        """Append an edge. Edges are not deduplicated.

        Multi-edges between the same source/target with the same kind
        are legitimate (e.g., a controller method that fires the same
        event from two different sites would produce two ``FIRES``
        edges with different ``file:line`` attributes). The graph
        store may collapse them on persist; the in-memory builder
        keeps them separate.
        """
        self.edges.append(edge)
        self._invalidate_traversal_caches()

    def add_warning(self, warning: Warning) -> None:
        """Record a non-fatal problem encountered during construction."""
        self.warnings.append(warning)

    def node_by_id(self, node_id: str) -> Node | None:
        """O(1) node lookup by id."""
        return self._node_index.get(node_id)

    def has_node(self, node_id: str) -> bool:
        """Whether a node with the given id exists in the graph."""
        return self.node_by_id(node_id) is not None

    def merge(self, other: Graph) -> None:
        """Append everything from ``other`` into ``self`` in place.

        Used by tests assembling fixtures incrementally and (in Phase 6)
        by the federation layer when overlaying cross-project links.
        """
        for node in other.nodes:
            self.add_node(node)
        for edge in other.edges:
            self.add_edge(edge)
        self.warnings.extend(other.warnings)

    def __len__(self) -> int:
        """Number of nodes in the graph."""
        return len(self.nodes)

    def edges_from(self, source_id: str) -> Iterable[Edge]:
        """All edges originating at ``source_id``.

        This is a linear scan; for traversal-heavy workloads use the
        storage layer (Phase 4 query engine) which has SQL indices.
        """
        return (e for e in self.edges if e.source == source_id)

    def edges_to(self, target_id: str) -> Iterable[Edge]:
        """All edges terminating at ``target_id``."""
        return (e for e in self.edges if e.target == target_id)
