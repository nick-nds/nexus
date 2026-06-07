"""Pure builder that turns a reflection document into a typed graph.

The builder is the heart of Phase 2's domain logic. It takes a
:class:`~nexus.core.reflection.ReflectionDocument` (the validated output
of the PHP extractor) plus a :class:`~nexus.core.protocols.Profile`
(loaded from a built-in YAML or from the user's ``nexus.yml``), and
walks every section once to populate a :class:`~nexus.core.graph.Graph`.

Design rules this module follows:

1. **Pure.** No filesystem access, no network, no logging side effects.
   Take data in, return data out. Tests run in single-digit milliseconds.
2. **Idempotent.** Calling :meth:`GraphBuilder.build` twice on the same
   inputs returns equal graphs. Determinism is achieved via the id
   helpers in :mod:`nexus.core.graph.ids` and via stable iteration
   order over the input document (which is itself sorted by Phase 1).
3. **Lossy on purpose.** Not every reflection field becomes a node or
   an edge - chunks (Phase 3), call edges (Phase 3 LSP enrichment),
   embeddings (Phase 3) all live downstream. Phase 2's job is to
   produce the *structural skeleton* the rest of the pipeline hangs
   off.
4. **Errors are warnings.** A class with a missing parent, a route
   with an unknown action kind, or any other unmappable condition
   becomes a :class:`~nexus.core.outcome.Warning` in the resulting
   :class:`Outcome` - never an exception.

The builder is intentionally not configurable beyond the profile. Each
section's mapping logic lives in a private method named after the
section so the call site reads as a checklist of which sections we
handle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from nexus.core.graph.builder_findings import apply_static_findings
from nexus.core.graph.graph import Graph
from nexus.core.graph.ids import (
    binding_id,
    class_id,
    event_id,
    gate_id,
    listener_id,
    method_id,
    middleware_id,
    policy_id,
    route_id,
    schedule_id,
)
from nexus.core.graph.types import Edge, EdgeKind, Node, NodeKind
from nexus.core.outcome import Outcome, Warning

if TYPE_CHECKING:
    from nexus.core.protocols import Profile
    from nexus.core.reflection.document import (
        BindingItem,
        ClassEntry,
        EventListenerEntry,
        GateEntry,
        PolicyEntry,
        ReflectionDocument,
        RouteItem,
        ScheduleEvent,
    )


# Mapping from a Phase 1 class-kind label to the corresponding NodeKind.
# Classes can carry multiple kinds; the builder picks the most specific
# one as the node's primary kind. Order matters: earlier entries win.
# Audit P0-6: when ``profile.custom_bases`` labels a class via its
# parent chain, this map translates the label back to a concrete
# NodeKind. Keep narrow to labels that have semantic meaning beyond
# "this class belongs to a category" - only kinds the agent-facing
# query tools care about. Labels not in this map preserve the
# original ``NodeKind.CLASS`` behaviour, with the custom label still
# captured in the node's ``kinds`` attribute.
_LABEL_TO_NODEKIND: dict[str, NodeKind] = {
    "event": NodeKind.EVENT,
    "listener": NodeKind.LISTENER,
    "job": NodeKind.JOB,
    "model": NodeKind.MODEL,
    "controller": NodeKind.CONTROLLER,
    "policy": NodeKind.POLICY,
    "notification": NodeKind.NOTIFICATION,
    "mailable": NodeKind.MAILABLE,
    "command": NodeKind.COMMAND,
    "observer": NodeKind.OBSERVER,
    "form_request": NodeKind.FORM_REQUEST,
    "middleware": NodeKind.MIDDLEWARE,
}


def _walk_for_custom_base(
    parent_chain: dict[str, str | None],
    fqn: str,
    custom_bases: dict[str, str],
) -> NodeKind | None:
    """Walk the parent chain of ``fqn`` looking for a configured base.

    Returns the NodeKind that label maps to, or ``None`` when no
    ancestor matches. The walk is bounded by ``parent_chain`` - i.e.
    only classes we have reflection data for are followed; an
    unindexed parent terminates the walk. A simple cycle guard
    prevents infinite loops on malformed inputs.
    """
    seen: set[str] = set()
    current = parent_chain.get(fqn)
    while current is not None and current not in seen:
        seen.add(current)
        if current in custom_bases:
            label = custom_bases[current]
            kind = _LABEL_TO_NODEKIND.get(label)
            if kind is not None:
                return kind
            # Label matched a base but isn't a known kind - fall back
            # to CLASS, preserving the convention from the legacy
            # custom_bases handling.
            return NodeKind.CLASS
        current = parent_chain.get(current)
    return None


_KIND_PRIORITY: list[tuple[str, NodeKind]] = [
    # PHP language constructs win over Laravel role-based kinds when
    # both apply (which is rare but happens - e.g. a Notification
    # implementation that happens to be abstract). Audit P0-1, P0-2.
    ("interface", NodeKind.INTERFACE),
    ("enum", NodeKind.ENUM),
    ("trait", NodeKind.TRAIT),
    # ``bootstrap`` ranks above ``service_provider`` because the
    # package's primary entry point - e.g. ``Relay::class`` - is more
    # semantically specific than the generic "I extend ServiceProvider"
    # signal. Audit P2-20.
    ("bootstrap", NodeKind.BOOTSTRAP),
    ("controller", NodeKind.CONTROLLER),
    ("form_request", NodeKind.FORM_REQUEST),
    ("policy", NodeKind.POLICY),
    ("middleware", NodeKind.MIDDLEWARE),
    ("observer", NodeKind.OBSERVER),
    ("listener", NodeKind.LISTENER),
    ("job", NodeKind.JOB),
    ("notification", NodeKind.NOTIFICATION),
    ("mailable", NodeKind.MAILABLE),
    ("event", NodeKind.EVENT),
    ("model", NodeKind.MODEL),
    ("resource", NodeKind.RESOURCE),
    ("resource_collection", NodeKind.RESOURCE),
    ("command", NodeKind.COMMAND),
    ("service_provider", NodeKind.SERVICE_PROVIDER),
    ("cast", NodeKind.CAST),
]


@dataclass(slots=True)
class GraphBuilder:
    """Pure transformation: ReflectionDocument + Profile → Graph.

    The builder is stateless across calls; ``build`` reads its inputs
    and writes a fresh :class:`Graph`. The dataclass shape exists only
    so callers can pass it around as a dependency in tests and adapter
    code without having to wrap a free function.
    """

    def build(self, document: ReflectionDocument, profile: Profile) -> Outcome[Graph]:
        """Turn a reflection document into a typed graph.

        Args:
            document: A validated reflection document loaded via
                :func:`nexus.core.reflection.load_reflection`.
            profile: The active profile for the project. The builder
                consults ``profile.custom_bases`` and
                ``profile.custom_suffixes`` when classifying classes
                that didn't fit any built-in NodeKind.

        Returns:
            An :class:`Outcome` carrying the built graph plus any
            warnings the builder accumulated. Errors are reserved for
            programmer-error conditions; the builder itself only
            emits warnings.
        """
        graph = Graph()

        # Pre-collect FormRequest FQNs so _add_method_nodes can build
        # VALIDATES_WITH edges when it encounters typed parameters.
        form_request_fqns: set[str] = set()
        if document.sections.classes is not None:
            for entry in document.sections.classes.items:
                if "form_request" in entry.kinds:
                    form_request_fqns.add(entry.reflection.name)

        # Order matters only for warning attribution and human-readable
        # progress; the resulting graph is the same set of nodes/edges
        # regardless of order.
        if document.sections.classes is not None:
            self._build_classes(graph, document.sections.classes.items, profile, form_request_fqns)
        if document.sections.routes is not None:
            self._build_routes(graph, document.sections.routes.items)
        if document.sections.events is not None:
            self._build_events(graph, document.sections.events.listeners)
            self._build_events(graph, document.sections.events.wildcards, wildcard=True)
        if document.sections.gates_policies is not None:
            self._build_gates(graph, document.sections.gates_policies.gates)
            self._build_policies(graph, document.sections.gates_policies.policies)
        if document.sections.middleware is not None:
            self._build_middleware_aliases(graph, document.sections.middleware.aliases)
        if document.sections.bindings is not None:
            self._build_bindings(graph, document.sections.bindings.bindings)
        if document.sections.schedule is not None:
            self._build_schedule(graph, document.sections.schedule.events)
        if document.sections.static_analysis is not None:
            apply_static_findings(graph, document.sections.static_analysis.findings)

        return Outcome.success(graph, warnings=list(graph.warnings))

    # ------------------------------------------------------------------
    # Classes
    # ------------------------------------------------------------------

    def _build_classes(
        self,
        graph: Graph,
        classes: list[ClassEntry],
        profile: Profile,
        form_request_fqns: set[str],
    ) -> None:
        # Audit P0-6: pre-compute a parent map so ``_pick_class_kind``
        # can walk the inheritance chain when checking
        # ``profile.custom_bases``. Without this, only the IMMEDIATE
        # parent counts - but events like ``CustomerCreated extends
        # SynthesQEvent`` were sometimes ``CustomerCreated extends
        # CustomerEvent extends SynthesQEvent`` and the configured
        # base sat two hops away.
        parent_chain: dict[str, str | None] = {
            e.reflection.name: e.reflection.parent for e in classes
        }

        for entry in classes:
            kind = self._pick_class_kind(
                entry.kinds,
                entry.reflection.parent,
                profile,
                parent_chain=parent_chain,
                fqn=entry.reflection.name,
            )
            fqn = entry.reflection.name

            class_attrs: dict[str, Any] = {
                "fqn": fqn,
                "namespace": entry.reflection.namespace,
                "file": entry.reflection.file,
                "abstract": entry.reflection.abstract,
                "final": entry.reflection.final,
                "readonly": entry.reflection.readonly,
                "source": entry.source,
                "kinds": list(entry.kinds),
            }
            # Audit P0-2: enum cases land in the class node's attributes
            # so describe_class can surface them without a side lookup.
            # Only set when non-empty to keep attribute dicts lean for
            # the 99% of classes that aren't enums.
            if entry.reflection.cases:
                class_attrs["cases"] = [
                    {"name": case.name, "value": case.value} for case in entry.reflection.cases
                ]
            # Audit P0-4: transitively-inherited interfaces stored as an
            # attribute (not edges) - they don't represent contracts
            # the class itself declared, so making them first-class
            # IMPLEMENTS edges would inflate find_implementations
            # results with noise. Only set when non-empty.
            if entry.reflection.interfaces_inherited:
                class_attrs["interfaces_inherited"] = list(entry.reflection.interfaces_inherited)

            node = Node(
                id=class_id(fqn),
                kind=kind,
                name=entry.reflection.short_name,
                attributes=class_attrs,
            )
            graph.add_node(node)

            self._add_inheritance_edges(graph, fqn, entry)
            self._add_method_nodes(graph, fqn, entry, form_request_fqns)

    def _pick_class_kind(
        self,
        kinds: list[str],
        parent: str | None,
        profile: Profile,
        *,
        parent_chain: dict[str, str | None] | None = None,
        fqn: str | None = None,
    ) -> NodeKind:
        r"""Resolve the most specific NodeKind for a class.

        Resolution order, highest priority first:

        1. Built-in :data:`_KIND_PRIORITY` mapping over the kinds the
           Phase 1 classifier emitted.
        2. Profile ``custom_bases``: walk the parent chain looking for
           a configured base class (audit P0-6). When the label maps
           to a known ``NodeKind`` (e.g. ``"event"`` → ``NodeKind.EVENT``)
           the class gets that kind; otherwise it's recorded as
           ``CLASS`` with the label preserved in ``kinds``.
        3. Profile ``custom_suffixes``: a class-name suffix match wins.
        4. Fallback to :attr:`NodeKind.CLASS` for unclassified classes.
        """
        # Built-in priority match - most common case, check first for
        # speed on the large enterprise fixtures.
        for label, node_kind in _KIND_PRIORITY:
            if label in kinds:
                return node_kind

        # Audit P0-6: walk the parent chain looking for a configured
        # ``custom_bases`` entry. Without the walk, a class whose
        # immediate parent is a project-local subclass of the
        # configured base wouldn't get the inherited kind.
        if parent_chain is not None and fqn is not None:
            inherited = _walk_for_custom_base(parent_chain, fqn, profile.custom_bases)
            if inherited is not None:
                return inherited
        # Single-hop fallback for callers that don't pass the chain
        # (legacy tests, library callers).
        elif parent is not None and parent in profile.custom_bases:
            kind_from_label = _LABEL_TO_NODEKIND.get(profile.custom_bases[parent])
            if kind_from_label is not None:
                return kind_from_label
            return NodeKind.CLASS

        if profile.custom_suffixes:
            for suffix in profile.custom_suffixes:
                if suffix and any(k.endswith(suffix) for k in kinds):
                    return NodeKind.CLASS

        return NodeKind.CLASS

    def _add_inheritance_edges(self, graph: Graph, fqn: str, entry: ClassEntry) -> None:
        if entry.reflection.parent is not None:
            graph.add_edge(
                Edge(
                    source=class_id(fqn),
                    target=class_id(entry.reflection.parent),
                    kind=EdgeKind.EXTENDS,
                ),
            )

        for interface in entry.reflection.interfaces:
            graph.add_edge(
                Edge(
                    source=class_id(fqn),
                    target=class_id(interface),
                    kind=EdgeKind.IMPLEMENTS,
                ),
            )

        for trait in entry.reflection.traits:
            graph.add_edge(
                Edge(
                    source=class_id(fqn),
                    target=class_id(trait),
                    kind=EdgeKind.USES_TRAIT,
                ),
            )

    def _add_method_nodes(
        self,
        graph: Graph,
        fqn: str,
        entry: ClassEntry,
        form_request_fqns: set[str],
    ) -> None:
        for method in entry.reflection.methods:
            mid = method_id(fqn, method.name)
            graph.add_node(
                Node(
                    id=mid,
                    kind=NodeKind.METHOD,
                    name=method.name,
                    attributes={
                        "class_fqn": fqn,
                        "visibility": method.visibility,
                        "static": method.static,
                        "abstract": method.abstract,
                        "final": method.final,
                        "return_type": method.return_type,
                        "line": method.line,
                        "parameters": [
                            {
                                "name": p.name,
                                "type": p.type,
                                "optional": p.optional,
                                "variadic": p.variadic,
                            }
                            for p in method.parameters
                        ],
                    },
                ),
            )
            graph.add_edge(Edge(source=mid, target=class_id(fqn), kind=EdgeKind.PART_OF))

            # VALIDATES_WITH: any parameter whose type is a known FormRequest
            # class links this method to that FormRequest node.
            for param in method.parameters:
                if param.type and param.type in form_request_fqns:
                    graph.add_edge(
                        Edge(
                            source=mid,
                            target=class_id(param.type),
                            kind=EdgeKind.VALIDATES_WITH,
                        ),
                    )

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    def _build_routes(self, graph: Graph, routes: list[RouteItem]) -> None:
        for route in routes:
            primary_method = route.methods[0] if route.methods else "ANY"
            rid = route_id(primary_method, route.uri)

            graph.add_node(
                Node(
                    id=rid,
                    kind=NodeKind.ROUTE,
                    name=route.uri,
                    attributes={
                        "uri": route.uri,
                        "methods": list(route.methods),
                        "name": route.name,
                        "domain": route.domain,
                        "wheres": dict(route.wheres),
                        "parameters": list(route.parameters),
                        "action_kind": route.action.kind,
                    },
                ),
            )

            self._add_route_action_edge(graph, rid, route)
            self._add_route_middleware_edges(graph, rid, route)

    def _add_route_action_edge(self, graph: Graph, rid: str, route: RouteItem) -> None:
        if route.action.kind == "controller":
            if route.action.controller is None or route.action.method is None:
                graph.add_warning(
                    Warning(
                        code="route_action_incomplete",
                        message=f"Controller route {rid} is missing controller or method.",
                        context={"route": rid},
                    ),
                )
                return
            target = method_id(route.action.controller, route.action.method)
            graph.add_edge(Edge(source=rid, target=target, kind=EdgeKind.ROUTES_TO))

        # Closure routes have no callable target node; we record the
        # file/line in the route's attributes and skip the edge.

    def _add_route_middleware_edges(self, graph: Graph, rid: str, route: RouteItem) -> None:
        for mw in route.middleware:
            graph.add_edge(
                Edge(
                    source=rid,
                    target=middleware_id(mw),
                    kind=EdgeKind.HAS_MIDDLEWARE,
                ),
            )

    # ------------------------------------------------------------------
    # Events and listeners
    # ------------------------------------------------------------------

    def _build_events(
        self,
        graph: Graph,
        entries: list[EventListenerEntry],
        *,
        wildcard: bool = False,
    ) -> None:
        for entry in entries:
            event_node_id = event_id(entry.event)
            graph.add_node(
                Node(
                    id=event_node_id,
                    kind=NodeKind.EVENT,
                    name=entry.event,
                    attributes={"wildcard": wildcard},
                ),
            )

            for order, callback in enumerate(entry.listeners):
                if callback.kind != "class" or callback.class_name is None:
                    # Closure listeners are recorded as warnings; the
                    # source location is captured in their attributes
                    # but they have no stable node id worth creating.
                    graph.add_warning(
                        Warning(
                            code="closure_listener",
                            message=(
                                f"Skipping closure listener for event {entry.event} at "
                                f"{callback.file}:{callback.line}"
                            ),
                            context={"event": entry.event, "kind": callback.kind},
                        ),
                    )
                    continue

                lid = listener_id(callback.class_name, callback.method or "handle")
                # queued/file are properties of the listener class, so they
                # live on the node. source/order describe *this* wiring (a
                # class can be wired to several events differently), so they
                # live on the edge.
                graph.add_node(
                    Node(
                        id=lid,
                        kind=NodeKind.LISTENER,
                        name=callback.class_name,
                        attributes={
                            "class_fqn": callback.class_name,
                            "method": callback.method or "handle",
                            "queued": callback.queued,
                            "file": callback.file,
                        },
                    ),
                )
                graph.add_edge(
                    Edge(
                        source=lid,
                        target=event_node_id,
                        kind=EdgeKind.LISTENS_TO,
                        attributes={"order": order, "source": callback.source},
                    ),
                )

    # ------------------------------------------------------------------
    # Gates and policies
    # ------------------------------------------------------------------

    def _build_gates(self, graph: Graph, gates: list[GateEntry]) -> None:
        for gate in gates:
            graph.add_node(
                Node(
                    id=gate_id(gate.ability),
                    kind=NodeKind.GATE,
                    name=gate.ability,
                    attributes={
                        "callback_kind": gate.callback.kind,
                        "callback_class": gate.callback.class_name,
                        "callback_method": gate.callback.method,
                    },
                ),
            )

    def _build_policies(self, graph: Graph, policies: list[PolicyEntry]) -> None:
        for entry in policies:
            pid = policy_id(entry.policy)
            graph.add_node(
                Node(
                    id=pid,
                    kind=NodeKind.POLICY,
                    name=entry.policy,
                    attributes={"model": entry.model, "policy": entry.policy},
                ),
            )
            graph.add_edge(
                Edge(source=pid, target=class_id(entry.model), kind=EdgeKind.APPLIES_TO),
            )

    # ------------------------------------------------------------------
    # Middleware aliases
    # ------------------------------------------------------------------

    def _build_middleware_aliases(self, graph: Graph, aliases: dict[str, str]) -> None:
        for alias, target_class in aliases.items():
            graph.add_node(
                Node(
                    id=middleware_id(alias),
                    kind=NodeKind.MIDDLEWARE,
                    name=alias,
                    attributes={"alias": alias, "class_fqn": target_class},
                ),
            )

    # ------------------------------------------------------------------
    # Bindings
    # ------------------------------------------------------------------

    def _build_bindings(self, graph: Graph, bindings: list[BindingItem]) -> None:
        for binding in bindings:
            bid = binding_id(binding.abstract)
            graph.add_node(
                Node(
                    id=bid,
                    kind=NodeKind.BINDING,
                    name=binding.abstract,
                    attributes={
                        "shared": binding.shared,
                        "concrete_kind": binding.concrete.kind,
                        "concrete_class": binding.concrete.class_name,
                        "concrete_file": binding.concrete.file,
                        "concrete_line": binding.concrete.line,
                    },
                ),
            )

            if binding.concrete.kind == "class" and binding.concrete.class_name is not None:
                graph.add_edge(
                    Edge(
                        source=bid,
                        target=class_id(binding.concrete.class_name),
                        kind=EdgeKind.BOUND_TO,
                    ),
                )

    # ------------------------------------------------------------------
    # Schedule
    # ------------------------------------------------------------------

    def _build_schedule(self, graph: Graph, events: list[ScheduleEvent]) -> None:
        for event in events:
            target = event.command or event.target or "<closure>"
            sid = schedule_id(event.expression, target)
            graph.add_node(
                Node(
                    id=sid,
                    kind=NodeKind.SCHEDULED_TASK,
                    name=event.description or target,
                    attributes={
                        "expression": event.expression,
                        "timezone": event.timezone,
                        "command": event.command,
                        "callback_target": event.target,
                        "without_overlapping": event.without_overlapping,
                        "on_one_server": event.on_one_server,
                        "kind": event.kind,
                    },
                ),
            )

            # Wire the scheduled_task to its target so an agent asking
            # "what schedule runs this job/command?" gets a reverse
            # traversal. We only emit an edge when ``target`` looks
            # like a class FQN (callback kind, or a command-class
            # reference). Command-signature scheduling
            # (``->command('cache:clear')``) doesn't carry the FQN
            # here, so it stays attribute-only on the scheduled_task
            # node - a future iteration could resolve the signature
            # to its command FQN via the classes section.
            if event.target:
                graph.add_edge(
                    Edge(
                        source=sid,
                        target=class_id(event.target),
                        kind=EdgeKind.RUNS_COMMAND,
                        attributes={"expression": event.expression},
                    ),
                )
