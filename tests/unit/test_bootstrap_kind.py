"""Tests for the ``bootstrap`` NodeKind (audit P2-20).

The PHP-side classifier tags package entry-point classes (those with
a public static ``boot()`` declared on themselves, but not Models or
ServiceProviders) with the ``bootstrap`` label. The Python graph
builder must:

1. Recognise ``bootstrap`` as a valid NodeKind.
2. Pick it as the primary kind when the classifier emits it.
3. Position it above ``service_provider`` in the priority cascade so
   a class tagged with BOTH labels (rare but legal) resolves to
   ``bootstrap``.
"""

from __future__ import annotations

from nexus.core.graph.builder import _KIND_PRIORITY
from nexus.core.graph.types import NodeKind


def test_bootstrap_is_a_valid_nodekind() -> None:
    assert NodeKind.BOOTSTRAP.value == "bootstrap"


def test_bootstrap_appears_in_kind_priority_list() -> None:
    labels = [label for label, _ in _KIND_PRIORITY]
    assert "bootstrap" in labels


def test_bootstrap_outranks_service_provider() -> None:
    """A class tagged ``bootstrap`` + ``service_provider`` resolves to BOOTSTRAP.

    Some package facades extend ServiceProvider AND expose a static
    ``boot()`` of their own. The audit (P2-20) wants the more-specific
    "bootstrap" label to win.
    """
    labels = [label for label, _ in _KIND_PRIORITY]
    assert labels.index("bootstrap") < labels.index("service_provider")


def test_bootstrap_priority_pair_maps_to_bootstrap_nodekind() -> None:
    """The ``bootstrap`` label resolves to ``NodeKind.BOOTSTRAP``."""
    bootstrap_pair = next(p for p in _KIND_PRIORITY if p[0] == "bootstrap")
    assert bootstrap_pair[1] is NodeKind.BOOTSTRAP
