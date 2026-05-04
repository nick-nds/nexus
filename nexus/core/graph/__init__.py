"""In-memory graph types and the builder that produces them.

The graph is the heart of Nexus's domain model. It is a typed,
directed multigraph where:

* **Nodes** represent Laravel primitives (a route, a model, a controller
  method, a job, an event, a listener, a policy, a chunk of source ...).
* **Edges** represent typed relationships between them (a route's
  `routes_to` a controller method, a listener `listens_to` an event,
  a job is `dispatched_by` a controller method, a class `extends`
  another, etc.).

The in-memory representation is intentionally simple — three immutable
dataclasses (:class:`~nexus.core.graph.types.Node`,
:class:`~nexus.core.graph.types.Edge`,
:class:`~nexus.core.graph.graph.Graph`) plus enums for the kinds. The
:class:`~nexus.core.graph.builder.GraphBuilder` is a pure function that
takes a :class:`~nexus.core.reflection.ReflectionDocument` plus a
:class:`~nexus.core.protocols.Profile` and emits a graph. Persistence
is a separate concern handled by the storage adapters.
"""

from nexus.core.graph.graph import Graph
from nexus.core.graph.types import Edge, EdgeKind, Node, NodeKind

__all__ = ["Edge", "EdgeKind", "Graph", "Node", "NodeKind"]
