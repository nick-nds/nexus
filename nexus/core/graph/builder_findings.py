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

    Findings without a resolvable source class or target are silently
    skipped - they represent dynamic dispatch the AST visitor
    couldn't statically resolve, which is expected and not an error.
    """
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
