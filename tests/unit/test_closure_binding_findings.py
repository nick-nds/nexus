"""Tests for the ``closure_binding`` finding handler (audit P1-18).

The Phase C ``ContainerBindingVisitor`` emits ``closure_binding``
findings whenever it spots ``$this->app->bind/singleton/scoped(X::class,
fn () => new Y(...))`` patterns. The Python graph builder turns those
into ``BOUND_TO`` edges so ``resolve_binding`` can answer the question
the audit raised.

Two scenarios matter:

1. Phase A's runtime extractor missed the binding entirely (the
   binding registered too late, or it's in a deferred provider).
   We synthesise the ``BINDING`` node from the finding.
2. Phase A saw the binding but reported ``concrete_kind: "closure"``.
   We upgrade the existing node to ``concrete_kind: "class"`` and
   add the ``BOUND_TO`` edge.
"""

from __future__ import annotations

from nexus.core.graph.builder_findings import apply_static_findings
from nexus.core.graph.graph import Graph
from nexus.core.graph.ids import binding_id
from nexus.core.graph.types import Edge, EdgeKind, Node, NodeKind
from nexus.core.reflection.document import StaticAnalysisFinding


def _finding(
    *,
    abstract: str,
    concrete: str,
    binding_kind: str = "singleton",
    file: str | None = "/app/Providers/RelayServiceProvider.php",
    line: int | None = 42,
) -> StaticAnalysisFinding:
    return StaticAnalysisFinding(
        kind="closure_binding",
        target=concrete,
        in_class=None,
        in_method=None,
        file=file,
        line=line,
        meta={"abstract": abstract, "binding_kind": binding_kind},
    )


def test_synthesises_binding_node_when_phase_a_missed_it() -> None:
    """The synthesq-relay case: Phase A's container snapshot didn't
    include this binding (closure registered too late). We create
    the node from the static-analysis finding."""
    graph = Graph()

    apply_static_findings(
        graph,
        [
            _finding(
                abstract="Synthesq\\Relay\\Adapters\\SynthesQClient",
                concrete="Synthesq\\Relay\\Adapters\\SynthesQClient",
                binding_kind="singleton",
            ),
        ],
    )

    bid = binding_id("Synthesq\\Relay\\Adapters\\SynthesQClient")
    node = graph.node_by_id(bid)
    assert node is not None
    assert node.kind == NodeKind.BINDING
    assert node.attributes["concrete_kind"] == "class"
    assert node.attributes["concrete_class"] == "Synthesq\\Relay\\Adapters\\SynthesQClient"
    assert node.attributes["shared"] is True  # singleton → shared
    assert node.attributes["source"] == "static_analysis"


def test_upgrades_phase_a_closure_binding_with_bound_to_edge() -> None:
    """Phase A saw the binding but reported ``concrete_kind: "closure"``.
    The static-analysis finding should patch the attributes AND add
    the BOUND_TO edge so resolve_binding follows it."""
    graph = Graph()
    bid = binding_id("App\\Contracts\\Mailer")
    graph.add_node(
        Node(
            id=bid,
            kind=NodeKind.BINDING,
            name="App\\Contracts\\Mailer",
            attributes={
                "shared": True,
                "concrete_kind": "closure",
                "concrete_class": None,
                "concrete_file": None,
                "concrete_line": None,
            },
        ),
    )

    apply_static_findings(
        graph,
        [
            _finding(
                abstract="App\\Contracts\\Mailer",
                concrete="App\\Mail\\SmtpMailer",
                binding_kind="singleton",
            ),
        ],
    )

    upgraded = graph.node_by_id(bid)
    assert upgraded is not None
    assert upgraded.attributes["concrete_kind"] == "class"
    assert upgraded.attributes["concrete_class"] == "App\\Mail\\SmtpMailer"

    # And the BOUND_TO edge now points at the concrete class node.
    out_edges = graph.outgoing_index().get(bid, [])
    bound_to = [e for e in out_edges if e.kind == EdgeKind.BOUND_TO]
    assert len(bound_to) == 1
    assert bound_to[0].target == "class:App\\Mail\\SmtpMailer"
    assert bound_to[0].attributes.get("source") == "static_analysis"


def test_does_not_duplicate_bound_to_edge() -> None:
    """If Phase A already emitted the BOUND_TO edge (rare - usually
    means the closure resolved during runtime), don't add a second."""
    graph = Graph()
    bid = binding_id("App\\Contracts\\Mailer")
    graph.add_node(
        Node(
            id=bid,
            kind=NodeKind.BINDING,
            name="App\\Contracts\\Mailer",
            attributes={"concrete_kind": "class", "concrete_class": "App\\Mail\\SmtpMailer"},
        ),
    )
    graph.add_edge(
        Edge(
            source=bid,
            target="class:App\\Mail\\SmtpMailer",
            kind=EdgeKind.BOUND_TO,
            attributes={"source": "runtime"},
        ),
    )

    apply_static_findings(
        graph,
        [
            _finding(
                abstract="App\\Contracts\\Mailer",
                concrete="App\\Mail\\SmtpMailer",
            ),
        ],
    )

    out_edges = graph.outgoing_index().get(bid, [])
    bound_to = [e for e in out_edges if e.kind == EdgeKind.BOUND_TO]
    assert len(bound_to) == 1  # not duplicated


def test_bind_flavour_is_not_shared() -> None:
    """``bind`` (transient) → ``shared: False``; ``singleton`` / ``scoped``
    / ``instance`` → ``shared: True``. Matters for ``resolve_binding``."""
    graph = Graph()

    apply_static_findings(
        graph,
        [
            _finding(
                abstract="App\\Contracts\\Transient",
                concrete="App\\Impl\\Transient",
                binding_kind="bind",
            ),
        ],
    )

    node = graph.node_by_id(binding_id("App\\Contracts\\Transient"))
    assert node is not None
    assert node.attributes["shared"] is False


def test_finding_without_abstract_is_skipped() -> None:
    """A defensive check - if the meta is missing the abstract, the
    handler returns silently rather than crashing."""
    graph = Graph()
    finding = StaticAnalysisFinding(
        kind="closure_binding",
        target="App\\Some\\Concrete",
        in_class=None,
        in_method=None,
        meta={},  # no abstract key
    )

    apply_static_findings(graph, [finding])

    # No binding node was created.
    assert not any(n.kind == NodeKind.BINDING for n in graph.nodes)
