"""Node and edge value types for the in-memory graph.

Two enums and two frozen dataclasses. Kept deliberately small so the
graph builder, the storage adapter, and the query engine all see the
same shape with no duck-typing.

Why immutable dataclasses with slots:

* The graph is built once per indexing run and then handed off to the
  store. Mutation after construction would only obscure the data flow.
* ``slots=True`` is a meaningful memory win on the larger projects (the
  helm-v7 fixture builds ~5000 nodes plus ~30k edges; the slot layout
  saves ~30% versus a vanilla dataclass).
* Frozen instances are hashable, which lets the builder use them as
  dict keys for de-duplication during construction.

Why string-valued enums and not :class:`enum.IntEnum`:

* Stable serialisation: the SQLite store keeps the kind as TEXT so it
  reads naturally and survives schema migrations without remapping.
* Profiles can declare custom kinds in YAML; integer enums would force
  a registry of "magic numbers" we'd have to manage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class NodeKind(StrEnum):
    """The kinds of node Nexus can store in the graph.

    Add a new kind here when introducing a new Laravel primitive. The
    storage layer reads the value as text, so adding members is a
    schema-compatible additive change.
    """

    # Routing
    ROUTE = "route"

    # Controllers and HTTP boundary
    CONTROLLER = "controller"
    FORM_REQUEST = "form_request"
    MIDDLEWARE = "middleware"
    RESOURCE = "resource"

    # Every method on every class (controllers, models, services, jobs,
    # listeners, …). The audit (P0-3) showed that hard-coding
    # ``method`` on every method was misleading for non-HTTP
    # packages — most Laravel-extras libraries have zero controllers.
    METHOD = "method"

    # Models and persistence
    MODEL = "model"
    OBSERVER = "observer"
    CAST = "cast"

    # Domain
    EVENT = "event"
    LISTENER = "listener"
    JOB = "job"
    NOTIFICATION = "notification"
    MAILABLE = "mailable"
    POLICY = "policy"
    GATE = "gate"
    COMMAND = "command"
    SCHEDULED_TASK = "scheduled_task"

    # Container
    BINDING = "binding"
    SERVICE_PROVIDER = "service_provider"

    # Package bootstrap / facade entry point — e.g.
    # ``Synthesq\Relay\Relay::boot()``. Detected by ClassClassifier
    # when a class has a public static ``boot()`` declared on itself
    # and is neither a Model nor a ServiceProvider. Audit P2-20.
    BOOTSTRAP = "bootstrap"

    # Catch-all for any class the classifier didn't tag with a more
    # specific kind. Profile-defined kinds also live here in v1; a
    # later phase may promote them to first-class members.
    CLASS = "class"

    # Blade view referenced by a controller method's return value.
    VIEW = "view"

    # Cache key referenced by ``Cache::get()`` / ``Cache::put()`` calls.
    CACHE_KEY = "cache_key"

    # Broadcast channel referenced by an event's ``broadcastOn()``.
    BROADCAST_CHANNEL = "broadcast_channel"

    # Source code chunks (Phase 3 populates these; in Phase 2 we just
    # define the type so the schema is stable).
    CHUNK = "chunk"


class EdgeKind(StrEnum):
    """The kinds of directed edge between nodes."""

    # Routing flow
    ROUTES_TO = "routes_to"  # route → method
    HAS_MIDDLEWARE = "has_middleware"  # route → middleware
    VALIDATES_WITH = "validates_with"  # method → form_request

    # Class relationships
    EXTENDS = "extends"  # class → class (parent)
    IMPLEMENTS = "implements"  # class → class (interface)
    USES_TRAIT = "uses_trait"  # class → trait class

    # Behaviour
    CALLS = "calls"  # method → method (LSP-derived in Phase 3)
    DISPATCHES = "dispatches"  # caller → job
    NOTIFIES = "notifies"  # caller → notification class
    FIRES = "fires"  # caller → event
    LISTENS_TO = "listens_to"  # listener → event
    HANDLES = "handles"  # observer → model
    OBSERVES = "observes"  # observer → model
    AUTHORISED_BY = "authorised_by"  # caller → gate or policy
    APPLIES_TO = "applies_to"  # policy → model

    # Container
    BOUND_TO = "bound_to"  # binding (abstract) → concrete class

    # Schedule
    RUNS_COMMAND = "runs_command"  # scheduled_task → target (command, job, or any class)

    # Presentation
    RETURNS_VIEW = "returns_view"  # method → view

    # Caching
    CACHE_READ = "cache_read"  # method → cache_key
    CACHE_WRITE = "cache_write"  # method → cache_key

    # Broadcasting
    BROADCASTS_TO = "broadcasts_to"  # event → broadcast_channel

    # Source linkage
    DEFINED_IN = "defined_in"  # node → chunk
    PART_OF = "part_of"  # method → class


@dataclass(frozen=True, slots=True)
class Node:
    r"""One vertex in the graph.

    Attributes:
        id: Globally unique within the graph. The graph builder uses
            stable, deterministic ids derived from the underlying entity
            (e.g. ``class:App\Models\User``, ``route:GET:/api/users``)
            so the same input produces the same ids on every build —
            the foundation of the golden-diff testing strategy.
        kind: One of :class:`NodeKind`.
        name: A short human-readable label (the class short name, the
            route URI, etc.). Not necessarily unique.
        attributes: Free-form metadata. Keys are arbitrary; values must
            be JSON-serialisable so the storage layer can persist them
            without a custom encoder.
    """

    id: str
    kind: NodeKind
    name: str
    attributes: dict[str, object] = field(default_factory=dict, hash=False, compare=False)


@dataclass(frozen=True, slots=True)
class Edge:
    """One directed relationship between two nodes.

    Attributes:
        source: Id of the originating node.
        target: Id of the destination node.
        kind: One of :class:`EdgeKind`.
        attributes: Free-form metadata (e.g. the call site's
            file/line/column for a ``CALLS`` edge).
    """

    source: str
    target: str
    kind: EdgeKind
    attributes: dict[str, object] = field(default_factory=dict, hash=False, compare=False)
