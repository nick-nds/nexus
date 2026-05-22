r"""``explore_entity`` — fuzzy-search across the graph by name.

The first question an agent asks on an unfamiliar codebase is some
form of *"explain the X entity"*: a short name with no namespace
context. Every other tool in this engine needs an FQN, so without a
discovery primitive that question dead-ends in semantic_search.

This tool resolves a short name (or fragment) to every matching
class node in the graph and groups the results by kind. The agent
gets a structured "here are the candidates" answer instead of a
guess: from there it can pick a specific FQN and call ``describe_class``
or ``get_model_context``.

Match precedence
================

1. Exact FQN match (``"App\\Models\\User"`` or just ``"User"`` if
   exactly one class has that short name).
2. Exact short-name match (case-insensitive).
3. Prefix match on short name.
4. Substring match anywhere in the FQN.

Within a kind, matches are sorted by precedence first, then by FQN
ascending. Each kind is capped at ``max_per_kind`` rows; the ``total``
field tells the caller how many additional matches were trimmed so
the agent knows whether to narrow its query.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from pydantic import Field

from nexus.core.graph.types import NodeKind
from nexus.core.query.tool_protocol import ToolInput, ToolOutput
from nexus.core.query.tools._common import str_attr

if TYPE_CHECKING:
    from nexus.core.graph.types import Node
    from nexus.core.query.context import QueryContext


# Class-shaped kinds ``explore_entity`` searches across. ``CHUNK``,
# ``ROUTE``, ``MIDDLEWARE``, etc. are excluded — those have dedicated
# list tools and aren't what an agent means when it asks about an
# "entity" or "class".
_SEARCHABLE_KINDS: frozenset[NodeKind] = frozenset(
    {
        NodeKind.CONTROLLER,
        NodeKind.MODEL,
        NodeKind.EVENT,
        NodeKind.LISTENER,
        NodeKind.JOB,
        NodeKind.NOTIFICATION,
        NodeKind.MAILABLE,
        NodeKind.POLICY,
        NodeKind.FORM_REQUEST,
        NodeKind.OBSERVER,
        NodeKind.RESOURCE,
        NodeKind.COMMAND,
        NodeKind.SERVICE_PROVIDER,
        NodeKind.CAST,
        NodeKind.CLASS,
    },
)


class ExploreEntityInput(ToolInput):
    """Free-text discovery input.

    ``name`` is the short name, fragment, or FQN the agent is asking
    about. The matcher is case-insensitive on the short-name path so
    ``"Product"`` and ``"product"`` resolve identically.
    """

    name: str = Field(
        min_length=1,
        description=(
            "Short name (``Product``), fragment (``Forecast``), or FQN "
            "(``App\\Models\\User``) to search the graph for. The match "
            "is case-insensitive on short names; FQNs match exactly."
        ),
    )
    max_per_kind: int = Field(
        default=10,
        ge=1,
        le=50,
        description=(
            "Cap on rows returned per kind. Each kind's ``total`` "
            "field still reports the full match count so the agent "
            "can narrow its search if many matches were trimmed."
        ),
    )


class EntityMatch(ToolOutput):
    """One matching class in the response."""

    fqn: str
    short_name: str
    kind: str
    file: str | None = None
    namespace: str | None = None
    parent: str | None = None
    match_quality: str = Field(
        description=(
            "How the match was made: ``exact_fqn``, ``exact_name``, "
            "``prefix``, or ``substring``. Tells the agent which results "
            "are most likely the intended target."
        ),
    )


class EntityKindGroup(ToolOutput):
    """Matches grouped by NodeKind so the agent can pick the right flavour."""

    kind: str
    total: int = Field(description="Total matches in this kind, before capping.")
    returned: int
    matches: list[EntityMatch] = Field(default_factory=list)


class ExploreEntityOutput(ToolOutput):
    """Container for the discovery response."""

    query: str | None = None
    total: int = Field(default=0, description="Total matches across all kinds.")
    groups: list[EntityKindGroup] = Field(default_factory=list)
    error: str | None = None
    error_code: str | None = None
    truncated: bool = False
    truncated_lists: list[str] = Field(default_factory=list)

    _trimmable_lists: ClassVar[tuple[str, ...]] = ("groups",)


class ExploreEntityTool:
    """Fuzzy-search the graph by class name."""

    name: ClassVar[str] = "explore_entity"
    description: ClassVar[str] = (
        "Discover candidate classes by short name or fragment. "
        "**Argument:** ``name`` (string) — short name, fragment, or FQN "
        'to search for (e.g. ``name="Product"`` or '
        '``name="App\\\\Models\\\\User"``). '
        "Returns every class node whose name matches, grouped by kind "
        "(model, command, event, listener, controller, …) and sorted "
        "by match quality. Use this as the first step on an unfamiliar "
        "codebase when you don't have a fully-qualified name yet — "
        "pair the result with ``describe_class`` or "
        "``get_model_context`` once you've picked the right FQN. "
        'Returns ``error_code: "no_matches"`` when nothing matches so '
        "the agent can broaden its query."
    )
    input_model: ClassVar[type[ToolInput]] = ExploreEntityInput
    output_model: ClassVar[type[ToolOutput]] = ExploreEntityOutput
    latency_budget_ms: ClassVar[int] = 200

    def execute(
        self,
        payload: ExploreEntityInput,
        ctx: QueryContext,
    ) -> ExploreEntityOutput:
        """Resolve ``payload.name`` to every matching class, grouped by kind."""
        graph = ctx.storage.graph().load()
        query = payload.name.strip()

        # Pass 1: collect every class-kinded node with a match quality.
        ranked: list[tuple[int, EntityMatch]] = []
        for node in graph.nodes:
            if node.kind not in _SEARCHABLE_KINDS:
                continue
            if not node.id.startswith("class:"):
                continue
            fqn = node.id[len("class:") :]
            quality = _classify_match(fqn=fqn, short_name=node.name, query=query)
            if quality is None:
                continue
            ranked.append(
                (
                    _quality_rank(quality),
                    _build_match(node=node, fqn=fqn, quality=quality),
                ),
            )

        if not ranked:
            return ExploreEntityOutput(
                query=query,
                total=0,
                error=(
                    f"No class found matching {query!r}. Try a shorter "
                    f"fragment, a different casing, or use ``list_routes`` / "
                    f"``list_by_kind`` to enumerate the available entities."
                ),
                error_code="no_matches",
            )

        # Pass 2: group by kind, sort within group, cap at max_per_kind.
        ranked.sort(key=lambda pair: (pair[0], pair[1].fqn))
        groups: dict[str, list[EntityMatch]] = {}
        for _, match in ranked:
            groups.setdefault(match.kind, []).append(match)

        kind_groups: list[EntityKindGroup] = []
        for kind, matches in groups.items():
            kind_groups.append(
                EntityKindGroup(
                    kind=kind,
                    total=len(matches),
                    returned=min(len(matches), payload.max_per_kind),
                    matches=matches[: payload.max_per_kind],
                ),
            )

        # Order groups by exact-match presence (so the most relevant
        # category surfaces first) and then by total count.
        kind_groups.sort(
            key=lambda g: (
                0 if any(m.match_quality.startswith("exact") for m in g.matches) else 1,
                -g.total,
            ),
        )

        return ExploreEntityOutput(
            query=query,
            total=sum(g.total for g in kind_groups),
            groups=kind_groups,
        )


# ---------------------------------------------------------------------------
# Match scoring
# ---------------------------------------------------------------------------


def _classify_match(*, fqn: str, short_name: str, query: str) -> str | None:
    """Decide how (or if) ``fqn``/``short_name`` matches the query.

    The cascade is documented at the module level. Returns one of
    ``"exact_fqn"``, ``"exact_name"``, ``"prefix"``, ``"substring"``,
    or ``None`` if there's no match.
    """
    if fqn == query:
        return "exact_fqn"
    if short_name.lower() == query.lower():
        return "exact_name"
    if short_name.lower().startswith(query.lower()):
        return "prefix"
    if query.lower() in fqn.lower():
        return "substring"
    return None


def _quality_rank(quality: str) -> int:
    """Lower number = higher precedence for sorting."""
    return {"exact_fqn": 0, "exact_name": 1, "prefix": 2, "substring": 3}.get(
        quality,
        4,
    )


def _build_match(*, node: Node, fqn: str, quality: str) -> EntityMatch:
    """Pluck the agent-facing fields off a graph node."""
    attrs = node.attributes
    return EntityMatch(
        fqn=fqn,
        short_name=node.name,
        kind=node.kind.value,
        file=str_attr(attrs, "file"),
        namespace=str_attr(attrs, "namespace"),
        parent=str_attr(attrs, "parent_fqn"),
        match_quality=quality,
    )
