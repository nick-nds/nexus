"""Unit tests for :func:`resolve_class_id` - case-insensitive FQN fallback.

Pins audit finding P1-17. The helper is shared by ``describe_class``,
``get_model_context``, ``get_policy_for``, and ``find_implementations``;
each of those tools delegates to it on class-FQN lookups.
"""

from __future__ import annotations

from nexus.core.graph.graph import Graph
from nexus.core.graph.types import Node, NodeKind
from nexus.core.query.tools._common import resolve_class_id


def _add_class(graph: Graph, fqn: str) -> str:
    node_id = f"class:{fqn}"
    graph.add_node(
        Node(
            id=node_id,
            kind=NodeKind.CLASS,
            name=fqn.rsplit("\\", 1)[-1],
            attributes={"fqn": fqn},
        ),
    )
    return node_id


def test_exact_case_returns_id_with_no_warning() -> None:
    g = Graph()
    canonical = _add_class(g, "Synthesq\\Relay\\Events\\SynthesQEvent")

    resolved, warning = resolve_class_id(g, "Synthesq\\Relay\\Events\\SynthesQEvent")

    assert resolved == canonical
    assert warning is None


def test_lowercase_input_resolves_via_case_insensitive_fallback() -> None:
    """The synthesq-relay audit's exact reproduction case."""
    g = Graph()
    canonical = _add_class(g, "Synthesq\\Relay\\Events\\SynthesQEvent")

    resolved, warning = resolve_class_id(g, "synthesq\\relay\\events\\synthesqevent")

    assert resolved == canonical
    assert warning is not None
    assert "synthesqevent" in warning  # original is mentioned
    assert "SynthesQEvent" in warning  # canonical is mentioned
    assert "case-corrected" in warning


def test_mixed_case_input_also_resolves() -> None:
    g = Graph()
    canonical = _add_class(g, "App\\Models\\User")

    # Random case shuffle.
    resolved, warning = resolve_class_id(g, "app\\MODELS\\user")

    assert resolved == canonical
    assert warning is not None


def test_unknown_class_returns_none_with_no_warning() -> None:
    g = Graph()
    _add_class(g, "App\\Models\\User")

    resolved, warning = resolve_class_id(g, "App\\Models\\NonExistent")

    assert resolved is None
    assert warning is None


def test_warning_uses_canonical_form_from_graph() -> None:
    """The warning's canonical FQN comes from the graph, not from input.

    Important so the agent's next call uses the right casing - without
    this contract the warning would be useless.
    """
    g = Graph()
    _add_class(g, "Vendor\\Package\\MyClass")

    _, warning = resolve_class_id(g, "VENDOR\\PACKAGE\\MYCLASS")

    assert warning is not None
    # Canonical short name (mixed case from graph) appears in the message.
    # We check the short name to avoid backslash escaping noise from repr().
    assert "MyClass" in warning
    assert "Vendor" in warning
    # And the wrong-case input is reported too.
    assert "MYCLASS" in warning
