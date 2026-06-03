"""``Coverage`` - what was indexed, attached to every tool result.

Every tool response carries a :class:`Coverage` block so an agent can
distinguish three indistinguishable-looking outcomes:

1. **No matches because nothing matches** (``find_callers`` for an
   uncalled method) - the answer is genuinely empty.
2. **No matches because the feature isn't indexed** (``find_callers``
   when ``calls_indexed`` is ``False``) - the question can't be
   answered with the current index, the agent should know.
3. **Stale matches** - the index is old and the user's edit hasn't
   been re-indexed yet (``indexed_at`` is days ago).

The coverage block is the explicit signal that turns case 2 from a
silent hallucination risk into a structured "this index does not
support that query" response.

The data is sourced from :class:`~nexus.adapters.storage.ProjectMeta`
written by the indexing pipeline. New fields are added defensively -
absent fields default so old ``meta.json`` files keep deserialising.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from nexus.adapters.storage import ProjectMeta
    from nexus.core.protocols import Embedder

_log = logging.getLogger(__name__)


class Coverage(BaseModel):
    """What was indexed for the project this tool just ran against.

    Frozen so callers can rely on it being a stable snapshot of the
    indexing run, not something a tool mutated mid-flight.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    calls_indexed: bool = Field(
        default=False,
        description=(
            "True when an LSP server ran during indexing and CALLS "
            "edges were populated. ``find_callers`` and the call-graph "
            "side of ``get_request_flow`` only return meaningful "
            "results when this is True."
        ),
    )
    lsp_server: str | None = Field(
        default=None,
        description="Path or name of the LSP binary used, if any.",
    )
    embedder_id: str | None = Field(
        default=None,
        description=(
            "Identifier of the embedder used. Different embedders "
            "produce different vector_score distributions, so an "
            "agent comparing scores across projects should weight by "
            "this id."
        ),
    )
    indexed_at: str | None = Field(
        default=None,
        description=(
            "ISO-8601 UTC timestamp of when the index was last built. "
            "An agent may decide a multi-day-old index warrants a "
            "warning to the user."
        ),
    )
    project_path: str | None = Field(
        default=None,
        description=(
            "The host-side project root the index was built from. "
            "Useful when a single agent has multiple projects mounted."
        ),
    )
    semantic_search_available: bool | None = Field(
        default=None,
        description=(
            "Liveness signal for the embedder that ``semantic_search`` "
            "depends on. ``True`` means the embedder responded to a "
            "probe; ``False`` means the embedder is configured but "
            "unreachable (typically Ollama daemon not running) - "
            "``semantic_search`` calls will fail until it's restored. "
            "``None`` means no probe ran (no embedder configured, or "
            "the engine was built without one). Agents can use this "
            "to pick capabilities upfront rather than discovering the "
            "outage mid-tool-call."
        ),
    )
    semantic_search_unavailable_reason: str | None = Field(
        default=None,
        description=(
            "Human-readable reason ``semantic_search_available`` is "
            "``False``. Names the specific cause - e.g. the embed pass "
            "never ran (no vectors indexed) versus the query-time "
            "embedder being unreachable, with the underlying error. "
            "``None`` when search is available or no probe ran. Lets an "
            "agent relay *why* search is off instead of silently falling "
            "back to grep."
        ),
    )

    @classmethod
    def from_meta(
        cls,
        meta: ProjectMeta | None,
        *,
        embedder: Embedder | None = None,
    ) -> Coverage:
        """Build a coverage snapshot from the persisted ``ProjectMeta``.

        Returns an all-defaults instance when ``meta`` is ``None``
        (the project hasn't been indexed yet or the meta.json is
        missing). Callers can still surface this to the agent so the
        ``calls_indexed: False`` signal is emitted.

        When ``embedder`` is supplied, also computes
        ``semantic_search_available`` from two signals: whether the
        index has vectors (``meta.embedder_id`` is set, written by the
        EmbedAndPersistPass) AND whether the query-time embedder
        responds to a single 1-character probe. Both halves must be
        true for the field to be ``True``; either failure produces
        ``False``. The probe runs at most once per Coverage construction.
        """
        probe, reason = _probe_semantic_search(embedder=embedder, meta=meta)
        if meta is None:
            return cls(
                semantic_search_available=probe,
                semantic_search_unavailable_reason=reason,
            )
        return cls(
            calls_indexed=meta.lsp_server is not None,
            lsp_server=meta.lsp_server,
            embedder_id=meta.embedder_id,
            indexed_at=meta.indexed_at,
            project_path=meta.project_path,
            semantic_search_available=probe,
            semantic_search_unavailable_reason=reason,
        )


def _probe_semantic_search(
    *,
    embedder: Embedder | None,
    meta: ProjectMeta | None,
) -> tuple[bool | None, str | None]:
    """Compute whether ``semantic_search`` will work, plus the reason if not.

    Truth table (second column is the returned ``reason``):

    +------------------+-------------------+------------+--------+--------------------+
    | query embedder   | meta.embedder_id  | live probe | result | reason             |
    +==================+===================+============+========+====================+
    | ``None``         | any               | n/a        | None   | None               |
    | live             | ``None``          | n/a        | False  | "no embeddings ..."|
    | live             | set               | OK         | True   | None               |
    | live             | set               | raises     | False  | "embedder ..."     |
    +------------------+-------------------+------------+--------+--------------------+

    The probe call is skipped when ``meta.embedder_id is None`` -
    we already know the answer is ``False`` (no vectors to search),
    so paying the round-trip would be wasteful.

    Any exception from the embedder is caught and treated as
    unreachable; this function never propagates errors.
    """
    if embedder is None:
        return None, None
    if meta is None or meta.embedder_id is None:
        return False, (
            "no embeddings in this index: the embed pass did not run "
            "(no vectors to search). Re-index with an embedder configured."
        )
    try:
        embedder.embed(["x"])
    except Exception as e:
        _log.debug("embedder_probe_failed", extra={"error": str(e)})
        return False, (
            f"query-time embedder {meta.embedder_id!r} is unreachable: {e}. "
            "Semantic search will fail until it is restored "
            "(for Ollama, check that `ollama serve` is running)."
        )
    return True, None
