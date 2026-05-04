"""Query-time context passed to every tool.

The context is the query-side counterpart to
:class:`nexus.pipeline.PipelineContext`. It carries the open
storage handles, optional embedder for semantic tools, response
budget, and a tiny set of per-query scratchpad fields.

The context is immutable once constructed; tools may read but must
not mutate. That's the simplest way to guarantee the "tools are
read-only" rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nexus.core.protocols import Embedder, ProjectStorageProtocol
    from nexus.core.query.budget import ResponseBudget
    from nexus.core.query.coverage import Coverage


@dataclass(frozen=True, slots=True)
class QueryContext:
    """Per-query handle carrying storage + optional embedder.

    Attributes:
        storage: Open project storage (any object conforming to
            :class:`ProjectStorageProtocol`). Tools call
            ``storage.graph()`` and
            ``storage.vectors(dimensions=...)`` as needed.
        embedder: Optional embedder used by semantic tools. Purely
            structural tools (``list_routes``, ``describe_class``)
            don't need it.
        budget: The response-budget instance used to trim oversized
            outputs.
        vector_dimensions: The vector width the LanceDB store was
            created with. Required so tools can open the store
            without re-discovering the dimensionality on every call.
        coverage: Index-level metadata about what was indexed. The
            engine attaches this to every tool output so agents can
            distinguish "no matches" from "feature not indexed".
            ``None`` means the engine has no metadata (very early
            test paths); a populated :class:`Coverage` is normal.
    """

    storage: ProjectStorageProtocol
    budget: ResponseBudget
    embedder: Embedder | None = None
    vector_dimensions: int | None = None
    coverage: Coverage | None = None
