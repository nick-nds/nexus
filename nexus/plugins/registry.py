"""In-memory registry plugins contribute to.

A :class:`PluginRegistry` is a typed collection of "things plugins can
add": embedders, vector stores, graph stores, query tools, profiles.
Plugin packages call ``register_*`` methods on the registry to
contribute their implementations. The pipeline and interface layers
read the registry to discover what's available.

The registry is the only piece of machinery the OSS side exposes for
the pro tier to plug into. Everything else (protocols, concrete OSS
adapters, core domain code) is imported directly by callers.

Factories, not instances
========================

The registry holds **factories**, not pre-constructed instances. A
plugin registers "give me a function and I'll call it when someone
asks for an embedder of kind ``voyage``"; the registry lazily calls
that factory with configuration at resolution time. This matters
because:

* Pro-tier embedders want to take runtime configuration (API key,
  model name). The factory takes a dict and returns an instance.
* Tests can register fakes without incurring network/setup costs.
* Swapping backends at runtime is cheap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable

    from nexus.core.protocols import Embedder, VectorStore


# Factories take a free-form config dict (loaded from YAML or passed
# programmatically) and return a constructed instance. We deliberately
# don't type the config more strictly - each plugin owns its own config
# shape and validates internally.
EmbedderFactory = "Callable[[dict[str, object]], Embedder]"
VectorStoreFactory = "Callable[[dict[str, object]], VectorStore]"


class _RegistryItem(Protocol):
    """Common shape of every registered plugin contribution."""

    name: str
    source: str  # human-readable "which plugin registered this"


@dataclass(frozen=True, slots=True)
class RegisteredEmbedder:
    """One embedder contribution in the registry."""

    name: str
    source: str
    factory: Callable[[dict[str, object]], Embedder]
    description: str = ""


@dataclass(frozen=True, slots=True)
class RegisteredVectorStore:
    """One vector-store contribution in the registry."""

    name: str
    source: str
    factory: Callable[[dict[str, object]], VectorStore]
    description: str = ""


@dataclass(slots=True)
class PluginRegistry:
    """Mutable container every plugin writes to at registration time.

    A single instance lives in the application for the lifetime of the
    process. The CLI/MCP entry points construct one, call
    :func:`load_plugins` to populate it, and then pass it to the
    pipeline factory.

    Every ``register_*`` method rejects duplicate names within a kind,
    so a typo in one plugin can't silently shadow another plugin's
    contribution.
    """

    _embedders: dict[str, RegisteredEmbedder] = field(default_factory=dict)
    _vector_stores: dict[str, RegisteredVectorStore] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Embedders
    # ------------------------------------------------------------------

    def register_embedder(
        self,
        *,
        name: str,
        factory: Callable[[dict[str, object]], Embedder],
        source: str,
        description: str = "",
    ) -> None:
        """Register a new embedder factory by name.

        Args:
            name: Short identifier (``voyage``, ``ollama``, ``openai``).
                Used by config files and CLI flags.
            factory: Callable that takes a config dict and returns an
                :class:`~nexus.core.protocols.Embedder` implementation.
            source: Human-readable string identifying the plugin that
                owns this contribution (typically the distribution name
                and version).
            description: Optional one-line description shown in help
                output and ``nexus doctor``.

        Raises:
            ValueError: an embedder with the same name is already
                registered.
        """
        if name in self._embedders:
            existing = self._embedders[name]
            raise ValueError(
                f"Embedder {name!r} is already registered by {existing.source}; "
                f"duplicate registration from {source} refused.",
            )
        self._embedders[name] = RegisteredEmbedder(
            name=name,
            source=source,
            factory=factory,
            description=description,
        )

    def embedder_names(self) -> list[str]:
        """Return all registered embedder names in deterministic order."""
        return sorted(self._embedders)

    def resolve_embedder(self, name: str, config: dict[str, object]) -> Embedder:
        """Construct an :class:`Embedder` instance by registered name."""
        if name not in self._embedders:
            available = ", ".join(sorted(self._embedders)) or "<none>"
            raise KeyError(
                f"No embedder registered under {name!r}. Available: {available}",
            )
        return self._embedders[name].factory(config)

    # ------------------------------------------------------------------
    # Vector stores
    # ------------------------------------------------------------------

    def register_vector_store(
        self,
        *,
        name: str,
        factory: Callable[[dict[str, object]], VectorStore],
        source: str,
        description: str = "",
    ) -> None:
        """Register a new vector-store factory by name."""
        if name in self._vector_stores:
            existing = self._vector_stores[name]
            raise ValueError(
                f"Vector store {name!r} is already registered by {existing.source}; "
                f"duplicate registration from {source} refused.",
            )
        self._vector_stores[name] = RegisteredVectorStore(
            name=name,
            source=source,
            factory=factory,
            description=description,
        )

    def vector_store_names(self) -> list[str]:
        """Return all registered vector-store names in deterministic order."""
        return sorted(self._vector_stores)

    def resolve_vector_store(self, name: str, config: dict[str, object]) -> VectorStore:
        """Construct a :class:`VectorStore` instance by registered name."""
        if name not in self._vector_stores:
            available = ", ".join(sorted(self._vector_stores)) or "<none>"
            raise KeyError(
                f"No vector store registered under {name!r}. Available: {available}",
            )
        return self._vector_stores[name].factory(config)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def describe(self) -> dict[str, list[dict[str, str]]]:
        """Return a JSON-serialisable snapshot of everything registered.

        Used by ``nexus doctor`` (Phase 5) to show the user which
        plugins are active and where each contribution came from.
        """
        return {
            "embedders": [
                {"name": e.name, "source": e.source, "description": e.description}
                for e in sorted(self._embedders.values(), key=lambda x: x.name)
            ],
            "vector_stores": [
                {"name": v.name, "source": v.source, "description": v.description}
                for v in sorted(self._vector_stores.values(), key=lambda x: x.name)
            ],
        }
