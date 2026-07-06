"""Static-analysis-finding helpers for :class:`GraphBuilder`.

Extracted from ``builder.py`` so the main builder stays under the
project's 500-LOC ceiling. Every function here is a pure
``(graph, finding) -> None`` mutation that consumes one
:class:`~nexus.core.reflection.document.StaticAnalysisFinding`
emitted by the PHP extractor's PhaseC visitors and adds the
corresponding edge (and any required target node) to the graph.

The dispatcher :func:`apply_static_findings` is the only public
entry point - :class:`GraphBuilder.build` calls it once per
document.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nexus.core.graph.ids import binding_id, class_id, gate_id, method_id
from nexus.core.graph.types import Edge, EdgeKind, Node, NodeKind
from nexus.core.outcome import Warning

if TYPE_CHECKING:
    from nexus.core.graph.graph import Graph
    from nexus.core.reflection.document import StaticAnalysisFinding


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def apply_static_findings(graph: Graph, findings: list[StaticAnalysisFinding]) -> None:
    """Translate PhaseC AST findings into typed graph edges.

    The PHP extractor emits several finding kinds; each maps to a
    specific edge shape:

    * ``event_dispatch`` → :attr:`EdgeKind.FIRES`
    * ``job_dispatch`` → :attr:`EdgeKind.DISPATCHES`
    * ``notification_dispatch`` → :attr:`EdgeKind.NOTIFIES`
    * ``authorize`` / ``gate_check`` → :attr:`EdgeKind.AUTHORISED_BY`
    * ``view_return`` → :attr:`EdgeKind.RETURNS_VIEW` (creates view node)
    * ``observer_registration`` → :attr:`EdgeKind.OBSERVES`
    * ``cache_read`` / ``cache_write`` → :attr:`EdgeKind.CACHE_READ` /
      :attr:`EdgeKind.CACHE_WRITE` (creates cache_key node)
    * ``broadcast_channel`` → :attr:`EdgeKind.BROADCASTS_TO` (creates
      broadcast_channel node)
    * ``closure_binding`` → :attr:`EdgeKind.BOUND_TO` (creates binding
      node if Phase A's runtime extractor didn't see it)
    * ``bus_dispatch`` → synthetic :attr:`EdgeKind.CALLS` (resolves the
      dispatched message to its CQRS handler by naming convention)

    Findings without a resolvable source class or target are silently
    skipped - they represent dynamic dispatch the AST visitor
    couldn't statically resolve, which is expected and not an error.
    """
    # A CQRS bus resolves handlers by convention at runtime, so the
    # short-name index is only needed when bus_dispatch findings exist.
    short_name_index: dict[str, list[str]] | None = None
    if any(f.kind == "bus_dispatch" for f in findings):
        short_name_index = _build_class_short_name_index(graph)

    for finding in findings:
        if finding.kind == "event_dispatch":
            _add_behavioural_edge(graph, finding, EdgeKind.FIRES)
        elif finding.kind == "job_dispatch":
            _add_behavioural_edge(graph, finding, EdgeKind.DISPATCHES)
        elif finding.kind == "notification_dispatch":
            _add_behavioural_edge(graph, finding, EdgeKind.NOTIFIES)
        elif finding.kind in ("authorize", "gate_check"):
            _add_authorisation_edge(graph, finding)
        elif finding.kind == "view_return":
            _add_view_return_edge(graph, finding)
        elif finding.kind == "observer_registration":
            _add_observer_registration_edge(graph, finding)
        elif finding.kind in ("cache_read", "cache_write"):
            _add_cache_edge(graph, finding)
        elif finding.kind == "broadcast_channel":
            _add_broadcast_channel_edge(graph, finding)
        elif finding.kind == "closure_binding":
            _add_closure_binding_edge(graph, finding)
        elif finding.kind == "bus_dispatch" and short_name_index is not None:
            _add_bus_dispatch_edge(graph, finding, short_name_index)
        # form_request_rules and inline_validation are informational
        # findings without a cross-node edge in v1.


# ---------------------------------------------------------------------------
# Per-kind edge helpers
# ---------------------------------------------------------------------------


def _add_behavioural_edge(
    graph: Graph,
    finding: StaticAnalysisFinding,
    kind: EdgeKind,
) -> None:
    """Add a FIRES/DISPATCHES/NOTIFIES edge from the call-site to the target class.

    Uses the method node when ``in_method`` is present, falling back to
    the class node for code paths the AST visitor found outside a named
    method (rare, but possible for top-level closures in service providers).
    """
    if not finding.in_class or not finding.target:
        return

    source = (
        method_id(finding.in_class, finding.in_method)
        if finding.in_method
        else class_id(finding.in_class)
    )
    attrs: dict[str, object] = {}
    if finding.file is not None:
        attrs["file"] = finding.file
    if finding.line is not None:
        attrs["line"] = finding.line
    if isinstance(finding.meta, dict):
        attrs.update(finding.meta)

    graph.add_edge(
        Edge(
            source=source,
            target=class_id(finding.target),
            kind=kind,
            attributes=attrs,
        ),
    )


def _add_view_return_edge(
    graph: Graph,
    finding: StaticAnalysisFinding,
) -> None:
    """Create a ``view`` node + ``RETURNS_VIEW`` edge from the source method.

    View node ids are namespaced as ``view:<dot.notation>`` since
    Laravel views are addressed by name (``auth.login``,
    ``emails.welcome``), not by file path. The same view returned
    from two controllers gets one node and two edges, so an
    agent asking "which controllers render auth.login?" gets a
    clean reverse-traversal answer.
    """
    if not finding.in_class or not finding.target:
        return

    source = (
        method_id(finding.in_class, finding.in_method)
        if finding.in_method
        else class_id(finding.in_class)
    )

    view_node_id = f"view:{finding.target}"
    graph.add_node(
        Node(
            id=view_node_id,
            kind=NodeKind.VIEW,
            name=finding.target,
            attributes={},
        ),
    )

    attrs: dict[str, object] = {}
    if finding.file is not None:
        attrs["file"] = finding.file
    if finding.line is not None:
        attrs["line"] = finding.line

    graph.add_edge(
        Edge(
            source=source,
            target=view_node_id,
            kind=EdgeKind.RETURNS_VIEW,
            attributes=attrs,
        ),
    )


def _add_observer_registration_edge(
    graph: Graph,
    finding: StaticAnalysisFinding,
) -> None:
    """Wire ``Model::observe(Observer::class)`` into an OBSERVES edge.

    ``finding.target`` carries the observer FQN; ``finding.meta['model']``
    carries the model FQN. We add an OBSERVES edge from the observer
    class to the model class. Both ends are in ``class:<fqn>`` form;
    the SQLite store's dangling-source guard drops the edge if the
    observer node doesn't exist in the graph (e.g. observer class
    is in a vendor package that wasn't indexed).
    """
    if finding.target is None:
        return
    model_fqn = finding.meta.get("model") if isinstance(finding.meta, dict) else None
    if not isinstance(model_fqn, str) or not model_fqn:
        return

    attrs: dict[str, object] = {}
    if finding.file is not None:
        attrs["file"] = finding.file
    if finding.line is not None:
        attrs["line"] = finding.line

    graph.add_edge(
        Edge(
            source=class_id(finding.target),
            target=class_id(model_fqn),
            kind=EdgeKind.OBSERVES,
            attributes=attrs,
        ),
    )


def _add_cache_edge(
    graph: Graph,
    finding: StaticAnalysisFinding,
) -> None:
    """Wire ``Cache::get('key')`` / ``Cache::put('key', …)`` into edges.

    Creates a ``cache_key:<key>`` node (deduplicated across call
    sites) and a ``CACHE_READ`` or ``CACHE_WRITE`` edge from the
    caller method to it. The key is the literal prefix the AST
    could recover; ``meta.form == "prefix"`` indicates the agent
    should treat the key as a glob ``<key>*`` rather than an
    exact match.
    """
    if not finding.in_class or not finding.target:
        return

    source = (
        method_id(finding.in_class, finding.in_method)
        if finding.in_method
        else class_id(finding.in_class)
    )

    cache_node_id = f"cache_key:{finding.target}"
    graph.add_node(
        Node(
            id=cache_node_id,
            kind=NodeKind.CACHE_KEY,
            name=finding.target,
            attributes={},
        ),
    )

    attrs: dict[str, object] = {}
    if finding.file is not None:
        attrs["file"] = finding.file
    if finding.line is not None:
        attrs["line"] = finding.line
    # ``form`` (literal vs prefix) and the underlying Cache method
    # ride along on the edge so an agent can distinguish exact
    # keys from prefix-globs without a separate lookup.
    if isinstance(finding.meta, dict):
        for key in ("form", "method"):
            value = finding.meta.get(key)
            if isinstance(value, str):
                attrs[key] = value

    edge_kind = EdgeKind.CACHE_READ if finding.kind == "cache_read" else EdgeKind.CACHE_WRITE
    graph.add_edge(
        Edge(source=source, target=cache_node_id, kind=edge_kind, attributes=attrs),
    )


def _add_broadcast_channel_edge(
    graph: Graph,
    finding: StaticAnalysisFinding,
) -> None:
    """Wire ``new Channel('orders')`` inside ``broadcastOn`` into edges.

    Edge direction is event class → channel node. Channel node ids
    are namespaced ``broadcast_channel:<kind>:<name>`` so a public
    ``Channel('orders')`` and a ``PrivateChannel('orders')`` stay
    distinct (they target different broadcast scopes despite
    sharing a name).
    """
    if not finding.in_class or not finding.target:
        return

    channel_kind: str = "Channel"
    form: str | None = None
    if isinstance(finding.meta, dict):
        kind_value = finding.meta.get("channel_kind")
        if isinstance(kind_value, str):
            channel_kind = kind_value
        form_value = finding.meta.get("form")
        if isinstance(form_value, str):
            form = form_value

    channel_node_id = f"broadcast_channel:{channel_kind}:{finding.target}"
    graph.add_node(
        Node(
            id=channel_node_id,
            kind=NodeKind.BROADCAST_CHANNEL,
            name=finding.target,
            attributes={"channel_kind": channel_kind},
        ),
    )

    attrs: dict[str, object] = {"channel_kind": channel_kind}
    if form is not None:
        attrs["form"] = form
    if finding.file is not None:
        attrs["file"] = finding.file
    if finding.line is not None:
        attrs["line"] = finding.line

    graph.add_edge(
        Edge(
            source=class_id(finding.in_class),
            target=channel_node_id,
            kind=EdgeKind.BROADCASTS_TO,
            attributes=attrs,
        ),
    )


def _add_authorisation_edge(
    graph: Graph,
    finding: StaticAnalysisFinding,
) -> None:
    """Add an AUTHORISED_BY edge from the call-site to a gate-ability node.

    Only emitted when the ability name is a literal string that the AST
    visitor could resolve statically. Dynamic ability names (variables,
    concatenations) produce no finding target, so we skip them cleanly.
    """
    if not finding.in_class or not finding.target:
        return

    source = (
        method_id(finding.in_class, finding.in_method)
        if finding.in_method
        else class_id(finding.in_class)
    )
    attrs: dict[str, object] = {}
    if finding.file is not None:
        attrs["file"] = finding.file
    if finding.line is not None:
        attrs["line"] = finding.line

    graph.add_edge(
        Edge(
            source=source,
            target=gate_id(finding.target),
            kind=EdgeKind.AUTHORISED_BY,
            attributes=attrs,
        ),
    )


def _add_closure_binding_edge(
    graph: Graph,
    finding: StaticAnalysisFinding,
) -> None:
    """Wire ``$this->app->bind(X::class, fn () => new Y)`` into a BOUND_TO edge.

    Audit P1-18. Phase A's runtime extractor sees the binding exists
    but reports ``concrete_kind: "closure"`` with no resolved class,
    so :class:`ResolveBindingTool` had nothing to point at. This
    static-analysis pass walks ServiceProvider bodies and surfaces
    the concrete; we either upgrade the existing closure binding to
    a class binding with a BOUND_TO edge, or synthesise the binding
    node entirely (for closure bindings deferred-registered too late
    for Phase A's snapshot).

    The finding shape:
        target = concrete FQN
        meta.abstract = abstract FQN
        meta.binding_kind = bind|singleton|scoped|instance
    """
    if not finding.target:
        return
    meta = finding.meta if isinstance(finding.meta, dict) else {}
    abstract = meta.get("abstract")
    if not isinstance(abstract, str) or not abstract:
        return

    bid = binding_id(abstract)
    concrete_class_id = class_id(finding.target)

    existing = graph.node_by_id(bid)
    if existing is None:
        # No Phase A binding for this abstract - synthesise one so
        # ``resolve_binding`` can return a useful answer. ``shared``
        # comes from the binding flavour (singleton/scoped are
        # shared; bind is transient).
        binding_kind = (
            meta.get("binding_kind") if isinstance(meta.get("binding_kind"), str) else None
        )
        shared = binding_kind in ("singleton", "scoped", "instance")
        graph.add_node(
            Node(
                id=bid,
                kind=NodeKind.BINDING,
                name=abstract,
                attributes={
                    "shared": shared,
                    "concrete_kind": "class",
                    "concrete_class": finding.target,
                    "concrete_file": finding.file,
                    "concrete_line": finding.line,
                    "binding_kind": binding_kind or "bind",
                    "source": "static_analysis",
                },
            ),
        )
    # Phase A saw the binding but couldn't resolve the concrete.
    # Patch the attributes so ``resolve_binding`` returns the
    # statically-detected class instead of an opaque "closure".
    elif existing.attributes.get("concrete_kind") != "class":
        existing.attributes["concrete_kind"] = "class"
        existing.attributes["concrete_class"] = finding.target
        if existing.attributes.get("concrete_file") is None and finding.file:
            existing.attributes["concrete_file"] = finding.file
        if existing.attributes.get("concrete_line") is None and finding.line:
            existing.attributes["concrete_line"] = finding.line

    # Add the BOUND_TO edge if it doesn't already exist. The edge
    # carries the source-file/line so an agent following the edge
    # can jump straight to the binding callsite.
    attrs: dict[str, object] = {"source": "static_analysis"}
    if finding.file is not None:
        attrs["file"] = finding.file
    if finding.line is not None:
        attrs["line"] = finding.line

    already_linked = any(
        edge.target == concrete_class_id and edge.kind == EdgeKind.BOUND_TO
        for edge in graph.outgoing_index().get(bid, ())
    )
    if not already_linked:
        graph.add_edge(
            Edge(
                source=bid,
                target=concrete_class_id,
                kind=EdgeKind.BOUND_TO,
                attributes=attrs,
            ),
        )


# ---------------------------------------------------------------------------
# CQRS bus-dispatch synthesis
# ---------------------------------------------------------------------------

#: Method names on a handler that receive the dispatched message, in
#: priority order. ``handle`` is the Tactician/Laravel convention;
#: ``__invoke`` covers single-action invokable handlers.
_HANDLER_METHODS = ("handle", "__invoke")

#: Message-class suffixes that a handler name may drop before appending
#: ``Handler`` (e.g. ``CreateUserCommand`` → ``CreateUserHandler``).
_MESSAGE_SUFFIXES = ("Command", "Query")


def _build_class_short_name_index(graph: Graph) -> dict[str, list[str]]:
    r"""Map each class short name to the FQNs that carry it.

    CQRS handlers are conventionally named after their message's *short*
    name (``FooQuery`` → ``FooQueryHandler``) but live in a different
    sub-namespace (``...\Queries`` vs ``...\QueryHandlers``), so
    handler resolution keys on the short name rather than the FQN.

    Keyed off the ``class:<fqn>`` node id rather than ``NodeKind.CLASS``
    or ``node.name``: a handler may carry a more specific NodeKind (e.g.
    a profile classified it), and class nodes store their *short* name in
    ``node.name`` - only the id reliably carries the full FQN.
    """
    prefix = "class:"
    index: dict[str, list[str]] = {}
    for node in graph.nodes:
        if not node.id.startswith(prefix):
            continue
        fqn = node.id[len(prefix) :]
        short = fqn.rsplit("\\", 1)[-1]
        index.setdefault(short, []).append(fqn)
    return index


def _handler_short_name_candidates(message_short: str) -> list[str]:
    """Candidate handler short names for a message short name.

    The primary convention appends ``Handler`` to the full message name;
    the fallback drops a ``Command``/``Query`` suffix first. Order is
    significant: the first candidate that resolves wins.
    """
    candidates = [f"{message_short}Handler"]
    for suffix in _MESSAGE_SUFFIXES:
        if message_short.endswith(suffix) and len(message_short) > len(suffix):
            stripped = f"{message_short[: -len(suffix)]}Handler"
            if stripped not in candidates:
                candidates.append(stripped)
    return candidates


def _add_bus_dispatch_edge(
    graph: Graph,
    finding: StaticAnalysisFinding,
    short_name_index: dict[str, list[str]],
) -> None:
    """Synthesise a ``CALLS`` edge from a bus dispatch site to its handler.

    A CQRS bus (``$queryBus->ask(new FooQuery())``) resolves the handler
    by naming-convention reflection at runtime, so no static reference -
    and therefore no LSP-derived ``CALLS`` edge - exists between the
    dispatch site and ``FooQueryHandler::handle``. Without this,
    ``find_callers`` on the handler returns only the bus's own
    ``dispatch`` method, never the real dispatch sites.

    We resolve the message to its handler *by short name* (handlers live
    in a different sub-namespace than their message) and, only when the
    handler class and its ``handle``/``__invoke`` method actually exist
    as nodes, add a ``CALLS`` edge tagged ``via: bus_convention`` and
    ``synthetic: True``. The provenance tags keep these convention-
    inferred edges distinguishable from LSP-verified ones. Ambiguous
    resolution (two handlers sharing a short name) is skipped rather than
    guessed.
    """
    if not finding.target or not finding.in_class or not finding.in_method:
        return

    message_short = finding.target.rsplit("\\", 1)[-1]

    handler_fqn: str | None = None
    for candidate in _handler_short_name_candidates(message_short):
        matches = short_name_index.get(candidate)
        if not matches:
            continue
        if len(matches) == 1:
            handler_fqn = matches[0]
        else:
            # More than one class shares the short name. Linking to all
            # of them would be wrong and linking to one would be a guess,
            # so we skip - but surface it rather than dropping silently.
            graph.add_warning(
                Warning(
                    code="bus_handler_ambiguous",
                    message=(
                        f"Dispatch of {finding.target} was not linked to a handler: "
                        f"{len(matches)} classes share the name {candidate!r}."
                    ),
                    context={
                        "message": finding.target,
                        "handler_short_name": candidate,
                        "candidates": sorted(matches),
                    },
                ),
            )
        # Stop at the first candidate that resolves either way; a later
        # candidate matching would be a weaker convention we don't prefer.
        break

    if handler_fqn is None:
        return

    target_id: str | None = None
    for method_name in _HANDLER_METHODS:
        candidate_id = method_id(handler_fqn, method_name)
        if graph.node_by_id(candidate_id) is not None:
            target_id = candidate_id
            break

    if target_id is None:
        return

    attrs: dict[str, object] = {
        "via": "bus_convention",
        "synthetic": True,
        "message": finding.target,
    }
    if finding.file is not None:
        attrs["file"] = finding.file
    if finding.line is not None:
        attrs["line"] = finding.line

    graph.add_edge(
        Edge(
            source=method_id(finding.in_class, finding.in_method),
            target=target_id,
            kind=EdgeKind.CALLS,
            attributes=attrs,
        ),
    )
