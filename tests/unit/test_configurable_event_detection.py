"""Pinning audit P0-6: configurable domain-event detection.

Before this change, classes that extended a project-specific event
base class (``Synthesq\\Relay\\Events\\SynthesQEvent``,
``App\\Events\\DomainEvent``, etc.) ended up as ``kind: "class"``.
Every event-graph tool (``find_dispatchers``, ``find_listeners``)
returned ``event_not_found`` even though the symbol existed.

The fix lets a user — via ``nexus.yml`` or a built-in profile —
declare event base classes in ``custom_bases``:

    custom_bases:
      Synthesq\\Relay\\Events\\SynthesQEvent: event

The graph builder then walks the parent chain for every class node;
if any ancestor matches, the kind is promoted to ``NodeKind.EVENT``.
"""

from __future__ import annotations

from dataclasses import dataclass

from nexus.core.graph.builder import GraphBuilder
from nexus.core.graph.types import NodeKind
from nexus.core.reflection.document import (
    ClassEntry,
    ClassesSection,
    ClassReflection,
    ProjectMetadata,
    ReflectionDocument,
    ReflectionSections,
    ReflectionSummary,
)


@dataclass(frozen=True)
class _StubProfile:
    """Minimal profile satisfying the Profile protocol."""

    name: str = "test"
    custom_bases: dict[str, str] | None = None
    custom_suffixes: dict[str, str] | None = None

    def __post_init__(self) -> None:
        if self.custom_bases is None:
            object.__setattr__(self, "custom_bases", {})
        if self.custom_suffixes is None:
            object.__setattr__(self, "custom_suffixes", {})


def _class(fqn: str, *, parent: str | None = None) -> ClassEntry:
    """Build a minimal ClassEntry for the builder."""
    return ClassEntry(
        source="project",
        kinds=[],
        reflection=ClassReflection(
            name=fqn,
            short_name=fqn.rsplit("\\", 1)[-1],
            namespace=fqn.rsplit("\\", 1)[0] if "\\" in fqn else "",
            abstract=False,
            final=False,
            parent=parent,
            methods=[],
        ),
    )


def _doc(*entries: ClassEntry) -> ReflectionDocument:
    return ReflectionDocument(
        schema_version="2.4.0",
        generated_at="2026-05-23T00:00:00+00:00",
        kind="project",
        project=ProjectMetadata(
            name="t",
            environment="t",
            laravel_version="11.0.0",
            php_version="8.2.0",
            base_path="/tmp",
        ),
        sections=ReflectionSections(
            classes=ClassesSection(items=list(entries), count=len(entries)),
        ),
        summary=ReflectionSummary(sections=["classes"], warning_count=0, error_count=0),
    )


def test_immediate_parent_event_base_promotes_kind_to_event() -> None:
    """``CustomerCreated extends SynthesQEvent`` → kind=event when configured."""
    doc = _doc(
        _class("Synthesq\\Relay\\Events\\SynthesQEvent"),
        _class(
            "App\\Customers\\Events\\CustomerCreated",
            parent="Synthesq\\Relay\\Events\\SynthesQEvent",
        ),
    )
    profile = _StubProfile(
        custom_bases={"Synthesq\\Relay\\Events\\SynthesQEvent": "event"},
    )

    result = GraphBuilder().build(doc, profile)  # type: ignore[arg-type]
    graph = result.value

    node = graph.node_by_id("class:App\\Customers\\Events\\CustomerCreated")
    assert node is not None
    assert node.kind == NodeKind.EVENT


def test_transitive_parent_event_base_promotes_kind() -> None:
    """``Created extends BaseDomainEvent extends SynthesQEvent`` resolves
    through the chain (audit P0-6's chain-walking requirement)."""
    doc = _doc(
        _class("Synthesq\\Relay\\Events\\SynthesQEvent"),
        _class(
            "App\\Common\\BaseDomainEvent",
            parent="Synthesq\\Relay\\Events\\SynthesQEvent",
        ),
        _class(
            "App\\Customers\\Events\\Created",
            parent="App\\Common\\BaseDomainEvent",
        ),
    )
    profile = _StubProfile(
        custom_bases={"Synthesq\\Relay\\Events\\SynthesQEvent": "event"},
    )

    result = GraphBuilder().build(doc, profile)  # type: ignore[arg-type]
    graph = result.value

    # Both the intermediate and leaf class get classified as events.
    intermediate = graph.node_by_id("class:App\\Common\\BaseDomainEvent")
    leaf = graph.node_by_id("class:App\\Customers\\Events\\Created")
    assert intermediate is not None
    assert intermediate.kind == NodeKind.EVENT
    assert leaf is not None
    assert leaf.kind == NodeKind.EVENT


def test_unrelated_class_is_not_promoted() -> None:
    """A class whose ancestry never touches a configured base stays CLASS."""
    doc = _doc(
        _class("Synthesq\\Relay\\Events\\SynthesQEvent"),
        _class("App\\Services\\PaymentService"),
    )
    profile = _StubProfile(
        custom_bases={"Synthesq\\Relay\\Events\\SynthesQEvent": "event"},
    )

    result = GraphBuilder().build(doc, profile)  # type: ignore[arg-type]
    graph = result.value

    payment = graph.node_by_id("class:App\\Services\\PaymentService")
    assert payment is not None
    assert payment.kind == NodeKind.CLASS


def test_cycle_in_parent_chain_does_not_loop() -> None:
    """Malformed input (A extends B extends A) terminates safely."""
    doc = _doc(
        _class("App\\A", parent="App\\B"),
        _class("App\\B", parent="App\\A"),
    )
    profile = _StubProfile()

    # Just exercising the cycle guard — the result is whatever it is,
    # as long as the call returns.
    result = GraphBuilder().build(doc, profile)  # type: ignore[arg-type]
    assert result.value is not None
