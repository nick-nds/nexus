r"""``describe_flow`` — fuzzy-resolve a route then return its full request flow.

A natural-language counterpart to :class:`GetRequestFlowTool`. The
agent supplies a free-text description of *what* it wants the flow
for — a URI fragment (``"orders"``), a route name (``"leads.store"``),
a controller class name (``"OrderController"``), or a verb-noun
phrase (``"create invoice"``) — and the tool resolves it against
every route in the graph.

Two shapes of response:

* **Single confident match** → ``flow`` is populated with the same
  shape as ``get_request_flow``'s output (handler, middleware,
  events, listeners, jobs, notifications, policies). The agent
  gets a single-shot answer and doesn't need a follow-up call.

* **Multiple candidates** → ``candidates`` lists each matching
  route with its URI, method, name, and handler so the agent can
  pick the right one and re-issue with a precise ``route_id`` via
  ``get_request_flow``.

Match strategy
==============

The query is tokenised on whitespace; stop-words (articles,
auxiliaries, prepositions) are dropped. Each route's URI, name,
and handler FQN/short-name are scored by how many query tokens
they contain. An exact URI or name match always wins outright.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from pydantic import Field

from nexus.core.graph.types import EdgeKind, NodeKind
from nexus.core.query.tool_protocol import ToolInput, ToolOutput
from nexus.core.query.tools._common import str_attr, str_list_attr
from nexus.core.query.tools.get_request_flow import (
    GetRequestFlowOutput,
    build_request_flow,
)
from nexus.core.query.traversal import outgoing

if TYPE_CHECKING:
    from nexus.core.graph.graph import Graph
    from nexus.core.graph.types import Node
    from nexus.core.query.context import QueryContext


# Stop-words dropped from queries before token matching. Keeping the
# set tight (articles, auxiliaries, common prepositions) so verbs and
# nouns the user actually typed survive: ``"how does order placement
# work"`` → ``["order", "placement", "work"]``.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "do",
        "does",
        "for",
        "from",
        "in",
        "into",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "their",
        "this",
        "to",
        "was",
        "were",
        "when",
        "where",
        "which",
        "who",
        "with",
    },
)

# Minimum token length to count toward scoring. Filters out single
# letters and noise like "1" while keeping common 2-letter Laravel
# words (e.g. ``id``) — at length 2 we're still permissive.
_MIN_TOKEN_LENGTH = 2


class DescribeFlowInput(ToolInput):
    """Free-text input describing the desired route's flow."""

    query: str = Field(
        min_length=1,
        description=(
            "Description of the route whose flow you want — a URI "
            "fragment (``orders``), a route name (``leads.store``), "
            "a controller class (``OrderController``), or a verb-noun "
            "phrase (``create invoice``). The matcher is fuzzy and "
            "case-insensitive."
        ),
    )
    max_candidates: int = Field(
        default=10,
        ge=1,
        le=50,
        description=(
            "Cap on how many candidate routes to return when the query "
            "is ambiguous. The single-match flow path ignores this."
        ),
    )


class FlowCandidate(ToolOutput):
    """One route that matched the query."""

    route_id: str
    methods: list[str] = Field(default_factory=list)
    uri: str
    name: str | None = None
    handler_fqn: str | None = None
    handler_method: str | None = None
    match_quality: str = Field(
        description=(
            "How the candidate matched: ``exact_uri``, ``exact_name``, "
            "``uri_substring``, ``name_substring``, ``handler_class``, "
            "or ``token_overlap``."
        ),
    )
    score: int = Field(
        description="Number of query tokens that hit URI/name/handler.",
    )


class DescribeFlowOutput(ToolOutput):
    """Container for the fuzzy-flow response."""

    query: str | None = None
    matched: int = 0
    flow: GetRequestFlowOutput | None = Field(
        default=None,
        description=(
            "Populated when there's exactly one candidate, or when the "
            "top candidate is an unambiguous winner (exact URI/name "
            "match). Otherwise null and the caller picks from "
            "``candidates``."
        ),
    )
    candidates: list[FlowCandidate] = Field(default_factory=list)
    error: str | None = None
    error_code: str | None = None
    truncated: bool = False
    truncated_lists: list[str] = Field(default_factory=list)

    _trimmable_lists: ClassVar[tuple[str, ...]] = ("candidates",)


class DescribeFlowTool:
    """Resolve a fuzzy query to one route then return its full request flow."""

    name: ClassVar[str] = "describe_flow"
    description: ClassVar[str] = (
        "Resolve a free-text description of a route to its full "
        "request-handling flow: middleware, controller, form request, "
        "events fired, jobs dispatched, notifications, policies, plus "
        "the listeners that respond to each event. "
        "**Argument:** ``query`` (string) — a route fragment "
        '(``query="orders"``), a route name (``query="leads.store"``), '
        'a controller class (``query="OrderController"``), or a '
        'verb-noun phrase (``query="create invoice"``). '
        "When the query is ambiguous, returns ``candidates`` instead of "
        "``flow`` so the agent can pick. For an exact ``(method, uri)`` "
        "lookup use ``get_request_flow``."
    )
    input_model: ClassVar[type[ToolInput]] = DescribeFlowInput
    output_model: ClassVar[type[ToolOutput]] = DescribeFlowOutput
    latency_budget_ms: ClassVar[int] = 300

    def execute(
        self,
        payload: DescribeFlowInput,
        ctx: QueryContext,
    ) -> DescribeFlowOutput:
        """Score every route against the query and return flow or candidates."""
        graph = ctx.storage.graph().load()
        query = payload.query.strip()
        tokens = _tokenise(query)

        scored = _score_routes(graph, query=query, tokens=tokens)
        if not scored:
            return DescribeFlowOutput(
                query=query,
                matched=0,
                error=(
                    f"No route matched {query!r}. Try a URI fragment "
                    f"like ``/api/...``, a controller class name, or "
                    f"use ``list_routes`` to enumerate every route."
                ),
                error_code="no_matches",
            )

        # Sort by quality rank ascending, then score descending, then URI.
        scored.sort(key=lambda c: (_quality_rank(c.match_quality), -c.score, c.uri))

        top = scored[0]
        single_winner = len(scored) == 1 or _is_unambiguous_winner(top, scored)

        flow: GetRequestFlowOutput | None = None
        if single_winner:
            route_node = graph.node_by_id(top.route_id)
            if route_node is not None:
                flow = build_request_flow(graph, route_node)

        return DescribeFlowOutput(
            query=query,
            matched=len(scored),
            flow=flow,
            candidates=scored[: payload.max_candidates],
        )


# ---------------------------------------------------------------------------
# Tokenisation + scoring
# ---------------------------------------------------------------------------


def _tokenise(query: str) -> list[str]:
    """Split ``query`` into search tokens, dropping stop-words."""
    raw = query.lower().replace("/", " ").replace("\\", " ").split()
    return [t for t in raw if t not in _STOPWORDS and len(t) >= _MIN_TOKEN_LENGTH]


def _score_routes(
    graph: Graph,
    *,
    query: str,
    tokens: list[str],
) -> list[FlowCandidate]:
    """Score every ROUTE node against ``query`` and return candidates."""
    candidates: list[FlowCandidate] = []
    query_lower = query.lower().lstrip("/")

    for node in graph.nodes:
        if node.kind != NodeKind.ROUTE:
            continue
        candidate = _score_single_route(graph, node, query_lower=query_lower, tokens=tokens)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def _score_single_route(
    graph: Graph,
    node: Node,
    *,
    query_lower: str,
    tokens: list[str],
) -> FlowCandidate | None:
    """Score one route. Returns ``None`` if it doesn't match at all."""
    attrs = node.attributes
    uri = str_attr(attrs, "uri") or node.name
    name = str_attr(attrs, "name")
    methods = str_list_attr(attrs, "methods")
    handler_fqn, handler_method = _resolve_handler_info(graph, node.id)

    # Quality cascade — first match wins.
    uri_lower = uri.lower().lstrip("/") if uri else ""
    name_lower = name.lower() if name else ""
    handler_short = handler_fqn.rsplit("\\", 1)[-1] if handler_fqn else ""
    handler_fqn_lower = handler_fqn.lower() if handler_fqn else ""

    quality: str | None = None
    if uri_lower and uri_lower == query_lower:
        quality = "exact_uri"
    elif name_lower and name_lower == query_lower:
        quality = "exact_name"
    elif uri_lower and query_lower and query_lower in uri_lower:
        quality = "uri_substring"
    elif name_lower and query_lower and query_lower in name_lower:
        quality = "name_substring"
    elif handler_short and query_lower == handler_short.lower():
        quality = "handler_class"

    # Token-overlap fallback: count how many of the query's tokens
    # appear anywhere in the route's searchable text.
    haystack = " ".join(filter(None, [uri_lower, name_lower, handler_fqn_lower]))
    score = sum(1 for t in tokens if t in haystack) if haystack else 0

    if quality is None:
        if score == 0:
            return None
        quality = "token_overlap"

    return FlowCandidate(
        route_id=node.id,
        methods=methods,
        uri=uri or "",
        name=name,
        handler_fqn=handler_fqn,
        handler_method=handler_method,
        match_quality=quality,
        score=max(score, 1),
    )


def _resolve_handler_info(graph: Graph, route_id: str) -> tuple[str | None, str | None]:
    """Return ``(handler_fqn, handler_method)`` for the route, if any."""
    for edge in outgoing(graph, route_id, EdgeKind.ROUTES_TO):
        method_node = graph.node_by_id(edge.target)
        if method_node is None:
            continue
        attrs = method_node.attributes
        return str_attr(attrs, "class_fqn"), method_node.name
    return None, None


def _quality_rank(quality: str) -> int:
    """Lower number = stronger match for sort precedence."""
    return {
        "exact_uri": 0,
        "exact_name": 1,
        "uri_substring": 2,
        "name_substring": 3,
        "handler_class": 4,
        "token_overlap": 5,
    }.get(quality, 6)


def _is_unambiguous_winner(top: FlowCandidate, all_candidates: list[FlowCandidate]) -> bool:
    """Decide whether ``top`` is decisive enough to short-circuit to flow.

    A top match wins outright when:
    * it's an exact URI or exact name match AND no other candidate
      has the same quality (one peer would mean ``GET /orders`` and
      ``POST /orders`` both match, which we don't auto-pick between).
    """
    if top.match_quality not in {"exact_uri", "exact_name"}:
        return False
    peers = [c for c in all_candidates if c.match_quality == top.match_quality]
    return len(peers) == 1
