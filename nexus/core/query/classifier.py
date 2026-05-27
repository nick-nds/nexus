"""Rule-based query classifier.

The classifier is the *fallback* router for free-text questions.
Most agents will call structural tools by name via MCP; the
classifier exists for cases where the caller has a natural-
language prompt and no prior knowledge of which tool to pick.

It's deliberately rule-based per design decision D4.3:

* Classification must be **free** (no LLM calls) and
  **deterministic** (same input → same output, forever).
* The rules encode high-signal surface features of the English
  the agent is most likely to produce (HTTP verbs + paths,
  fully-qualified class names, phrases like "who listens to X").
* Missed classifications are not a disaster - the semantic
  search fallback catches them.

The classifier returns a :class:`QueryPlan` naming the tool to
run, the pre-built argument dict, a confidence score in
``[0, 1]``, a human-readable reason for the pick, and an
ordered list of *fallback* plans the caller can try if the
primary plan fails. The semantic search fallback is always last.

Implementation note
===================

The compiled regex constants and the noun lookup table live in
:mod:`nexus.core.query.classifier_patterns` so this module stays
focused on the dispatch flow.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from nexus.core.query import classifier_patterns as patterns

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class QueryPlan:
    """One classification decision.

    Attributes:
        tool: Name of the tool to run (matches a registered
            :class:`~nexus.core.query.Tool`).
        args: Pre-built argument dict to pass to
            :meth:`QueryEngine.query`.
        confidence: ``[0, 1]`` estimate of how certain the rule
            was. Exact matches (HTTP verb + path) score near 1.0;
            keyword matches (single noun phrase) score lower.
        reason: Short English explanation of which rule fired -
            useful for debugging and for the CLI's ``--explain``
            mode in Phase 5.
        fallbacks: Ordered list of alternate plans to try if the
            primary plan returns an empty/error result.
    """

    tool: str
    args: dict[str, object]
    confidence: float
    reason: str
    fallbacks: tuple[QueryPlan, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


class QueryClassifier:
    """Deterministic classifier from free-text to a :class:`QueryPlan`.

    The classifier holds no state between calls, but it is a class
    (rather than a module-level function) so callers can inject a
    custom pattern set in future phases without monkey-patching.
    """

    def __init__(self) -> None:
        """Initialise the classifier. No configuration today."""

    def classify(self, query: str) -> QueryPlan:
        """Route ``query`` to the most likely tool.

        Never raises. If no rule fires the classifier falls back
        to :meth:`_semantic_fallback` with a low confidence score.
        """
        text = query.strip()
        if not text:
            return self._semantic_fallback(query, reason="empty query")

        # Ordering matters: more specific rules run first.
        # The handler/listener/request-flow rules run before
        # ``_match_http_route`` so that "what handles POST /x" (a
        # handler question) doesn't get hijacked by the bare verb+path
        # pattern that would otherwise route it to ``trace_route``.
        # ``_match_explore_entity`` is the discovery fallback for
        # short-name questions and runs AFTER the FQN rule so that an
        # explicit ``App\Models\User`` still goes to describe_class.
        for rule in (
            self._match_listeners,
            self._match_request_flow,
            self._match_handler,
            self._match_http_route,
            self._match_job_dispatchers,
            self._match_dispatchers,
            self._match_implementers,
            self._match_callers,
            self._match_policy,
            self._match_binding,
            self._match_fqn,
            self._match_list_routes,
            self._match_list_by_kind,
            self._match_describe_flow,
            self._match_explore_entity,
        ):
            plan = rule(text)
            if plan is not None:
                return plan

        return self._semantic_fallback(query, reason="no rule matched")

    # ------------------------------------------------------------------
    # Rule implementations
    # ------------------------------------------------------------------

    def _match_http_route(self, text: str) -> QueryPlan | None:
        match = patterns.HTTP_VERB_PATH.search(text)
        if match is None:
            return None
        method, path = match.group(1).upper(), match.group(2)
        args: dict[str, object] = {"method": method, "uri": path}
        return QueryPlan(
            tool="trace_route",
            args=args,
            confidence=0.95,
            reason=f"detected HTTP verb + path ({method} {path})",
            fallbacks=(
                QueryPlan(
                    tool="get_request_flow",
                    args=args,
                    confidence=0.80,
                    reason="same route, deeper flow",
                ),
                self._semantic_fallback(text, reason="semantic fallback"),
            ),
        )

    def _match_handler(self, text: str) -> QueryPlan | None:
        """Route 'what/who/which handles X' and 'handler for X' to ``find_handlers``.

        Builds a ``uri_glob`` from the trailing target:

        * ``/api/users`` → ``"/api/users"`` (absolute path passes through)
        * ``login``, ``the login route`` → ``"*login*"`` (wildcard match)
        * ``POST /api/orders`` → ``"*/api/orders*"`` (verb dropped - the
          tool ignores it; the URI fragment is what we match on)
        """
        match = patterns.HANDLER_OF.search(text)
        if match is None:
            return None
        rest = match.group("rest").strip()
        if not rest:
            return None

        # Don't intercept the listener / dispatcher rules' territory.
        lowered = rest.lower()
        if lowered.endswith(" event") or " event " in f" {lowered} ":
            return None

        # Strip a leading HTTP verb if present.  ``find_handlers`` does
        # not take a method param; we keep the URI fragment.
        method_match = re.match(
            r"(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(.+)",
            rest,
            re.IGNORECASE,
        )
        if method_match:
            rest = method_match.group(2)

        # Trim trailing fillers like "route" / "endpoint" and punctuation.
        rest = re.sub(r"\s+(?:route|endpoint)s?\s*$", "", rest, flags=re.IGNORECASE)
        rest = _strip_punctuation(rest).strip()
        if not rest:
            return None

        uri_glob = rest if rest.startswith("/") else f"*{rest}*"

        args: dict[str, object] = {"uri_glob": uri_glob}
        return QueryPlan(
            tool="find_handlers",
            args=args,
            confidence=0.85,
            reason=f"detected 'handles X' phrasing (uri_glob={uri_glob!r})",
            fallbacks=(self._semantic_fallback(text, reason="semantic fallback"),),
        )

    def _match_request_flow(self, text: str) -> QueryPlan | None:
        """Route '(show )?(request )?flow for [verb] /path' to ``get_request_flow``."""
        match = patterns.REQUEST_FLOW.search(text)
        if match is None:
            return None
        uri = match.group("uri")
        method = match.group("method")
        args: dict[str, object] = {"uri": _strip_punctuation(uri)}
        if method is not None:
            args["method"] = method.upper()
        return QueryPlan(
            tool="get_request_flow",
            args=args,
            confidence=0.85,
            reason=f"detected 'flow for {uri}' phrasing",
            fallbacks=(self._semantic_fallback(text, reason="semantic fallback"),),
        )

    def _match_listeners(self, text: str) -> QueryPlan | None:
        match = patterns.LISTENERS_OF.search(text)
        if match is None:
            return None
        event = _first_group(match)
        if event is None:
            return None
        return QueryPlan(
            tool="find_listeners",
            args={"event": _strip_punctuation(event)},
            confidence=0.85,
            reason="detected 'listeners of X' phrasing",
            fallbacks=(self._semantic_fallback(text, reason="semantic fallback"),),
        )

    def _match_job_dispatchers(self, text: str) -> QueryPlan | None:
        match = patterns.JOB_DISPATCHERS.search(text)
        if match is None:
            return None
        job = _first_group(match)
        if job is None:
            return None
        return QueryPlan(
            tool="find_jobs_dispatching",
            args={"job": _strip_punctuation(job)},
            confidence=0.85,
            reason="detected 'dispatches XJob' phrasing",
            fallbacks=(self._semantic_fallback(text, reason="semantic fallback"),),
        )

    def _match_dispatchers(self, text: str) -> QueryPlan | None:
        match = patterns.DISPATCHERS_OF.search(text)
        if match is None:
            return None
        event = _first_group(match)
        if event is None:
            return None
        return QueryPlan(
            tool="find_dispatchers",
            args={"event": _strip_punctuation(event)},
            confidence=0.80,
            reason="detected 'who dispatches/fires X' phrasing",
            fallbacks=(self._semantic_fallback(text, reason="semantic fallback"),),
        )

    def _match_implementers(self, text: str) -> QueryPlan | None:
        match = patterns.IMPLEMENTERS_OF.search(text)
        if match is None:
            return None
        interface = _first_group(match)
        if interface is None:
            return None
        return QueryPlan(
            tool="find_implementations",
            args={"interface_fqn": _strip_punctuation(interface)},
            confidence=0.85,
            reason="detected 'implementations of X' phrasing",
            fallbacks=(self._semantic_fallback(text, reason="semantic fallback"),),
        )

    def _match_callers(self, text: str) -> QueryPlan | None:
        match = patterns.CALLERS_OF.search(text)
        if match is None:
            return None
        method = _first_group(match)
        if method is None or "::" not in method:
            return None
        return QueryPlan(
            tool="find_callers",
            args={"method_fqn": _strip_punctuation(method)},
            confidence=0.85,
            reason="detected 'callers of X::y' phrasing",
            fallbacks=(self._semantic_fallback(text, reason="semantic fallback"),),
        )

    def _match_policy(self, text: str) -> QueryPlan | None:
        match = patterns.POLICY_FOR.search(text)
        if match is None:
            return None
        model = _first_group(match)
        if model is None:
            return None
        return QueryPlan(
            tool="get_policy_for",
            args={"model_fqn": _strip_punctuation(model)},
            confidence=0.80,
            reason="detected 'policy for X' phrasing",
            fallbacks=(self._semantic_fallback(text, reason="semantic fallback"),),
        )

    def _match_binding(self, text: str) -> QueryPlan | None:
        match = patterns.BINDING_OF.search(text)
        if match is None:
            return None
        abstract = _first_group(match)
        if abstract is None:
            return None
        return QueryPlan(
            tool="resolve_binding",
            args={"abstract": _strip_punctuation(abstract)},
            confidence=0.75,
            reason="detected 'bound/resolve X' phrasing",
            fallbacks=(self._semantic_fallback(text, reason="semantic fallback"),),
        )

    def _match_fqn(self, text: str) -> QueryPlan | None:
        match = patterns.FQN.search(text)
        if match is None:
            return None
        fqn = match.group(1)
        if patterns.MODEL_NAMESPACE_HINT.search(fqn) is not None:
            return QueryPlan(
                tool="get_model_context",
                args={"fqn": fqn},
                confidence=0.85,
                reason=f"detected model FQN {fqn!r}",
                fallbacks=(
                    QueryPlan(
                        tool="describe_class",
                        args={"fqn": fqn},
                        confidence=0.70,
                        reason="fall back to generic describe_class",
                    ),
                    self._semantic_fallback(text, reason="semantic fallback"),
                ),
            )
        return QueryPlan(
            tool="describe_class",
            args={"fqn": fqn},
            confidence=0.80,
            reason=f"detected class FQN {fqn!r}",
            fallbacks=(self._semantic_fallback(text, reason="semantic fallback"),),
        )

    def _match_list_routes(self, text: str) -> QueryPlan | None:
        if patterns.LIST_ROUTES.search(text) is None:
            return None
        return QueryPlan(
            tool="list_routes",
            args={},
            confidence=0.90,
            reason="detected 'list routes' phrasing",
            fallbacks=(),
        )

    def _match_list_by_kind(self, text: str) -> QueryPlan | None:
        """Route "list/show events|jobs|notifications|..." to ``list_by_kind``.

        Maps the captured noun to a canonical ``NodeKind`` value via
        :data:`patterns.LIST_BY_KIND_NOUNS`, normalising plurals and
        minor spelling variants (``form requests`` vs ``form_request``).
        Returns ``None`` (and lets the classifier fall through) when
        the captured noun is something we don't have a kind for -
        that case shouldn't fire because the regex itself enumerates
        the supported nouns, but the explicit None keeps the helper
        safe under future regex churn.
        """
        match = patterns.LIST_BY_KIND.search(text)
        if match is None:
            return None
        noun = match.group("noun").lower()
        # Collapse whitespace runs so ``"form  requests"`` still maps.
        noun_key = re.sub(r"\s+", " ", noun)
        kind = patterns.LIST_BY_KIND_NOUNS.get(noun_key)
        if kind is None:
            return None
        return QueryPlan(
            tool="list_by_kind",
            args={"kind": kind},
            confidence=0.85,
            reason=f"detected 'list {noun}' phrasing → kind={kind!r}",
            fallbacks=(),
        )

    def _match_describe_flow(self, text: str) -> QueryPlan | None:
        """Route fuzzy flow questions to :class:`DescribeFlowTool`.

        Handles "how does X work", "walk me through X", "describe the
        flow for X", and "what happens when X". The URI form
        (``flow for /api/foo``) was already taken by
        :meth:`_match_request_flow` earlier in the chain.

        The captured target has trailing fillers like ``"flow"`` or
        ``"process"`` stripped before being passed to the tool, since
        ``"order placement"`` is a better fuzzy match than
        ``"order placement flow"``.
        """
        match = patterns.DESCRIBE_FLOW.search(text)
        if match is None:
            return None
        rest = _first_group(match)
        if rest is None:
            return None
        rest = rest.strip()
        rest = patterns.FLOW_NOISE_SUFFIX.sub("", rest).strip()
        rest = _strip_punctuation(rest).strip()
        if not rest:
            return None
        return QueryPlan(
            tool="describe_flow",
            args={"query": rest},
            confidence=0.75,
            reason=f"detected fuzzy-flow phrasing for {rest!r}",
            fallbacks=(self._semantic_fallback(text, reason="semantic fallback"),),
        )

    def _match_explore_entity(self, text: str) -> QueryPlan | None:
        """Route discovery questions to :class:`ExploreEntityTool`.

        Handles "explain Product", "what is Lead", "tell me about
        Order", etc. Runs after :meth:`_match_fqn`, so explicit FQNs
        already went to ``describe_class`` / ``get_model_context`` by
        the time we get here. The captured target is stripped of
        "noise" suffix words like ``"entity"`` and ``"and its related
        entities"`` so the graph lookup gets a clean short name.

        We refuse very short fragments (< ``patterns.MIN_ENTITY_LENGTH``
        chars) - they tend to produce huge match sets that are more
        noise than signal.
        """
        match = patterns.EXPLORE_ENTITY.search(text)
        if match is None:
            return None
        rest = match.group("rest").strip()
        if not rest:
            return None
        rest = patterns.ENTITY_NOISE_SUFFIX.sub("", rest).strip()
        rest = _strip_punctuation(rest).strip()

        # Reject anything with whitespace inside - multi-word phrases
        # like "the meaning of life" aren't candidate class names; let
        # the semantic fallback handle them.
        if not rest or " " in rest:
            return None
        if len(rest) < patterns.MIN_ENTITY_LENGTH:
            return None

        return QueryPlan(
            tool="explore_entity",
            args={"name": rest},
            confidence=0.80,
            reason=f"detected entity-discovery phrasing for {rest!r}",
            fallbacks=(self._semantic_fallback(text, reason="semantic fallback"),),
        )

    # ------------------------------------------------------------------
    # Fallback
    # ------------------------------------------------------------------

    def _semantic_fallback(self, query: str, *, reason: str) -> QueryPlan:
        return QueryPlan(
            tool="semantic_search",
            args={"query": query},
            confidence=0.40,
            reason=reason,
            fallbacks=(),
        )


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _first_group(match: re.Match[str]) -> str | None:
    """Return the first non-empty capturing group."""
    for group in match.groups():
        if group:
            return group
    return None


def _strip_punctuation(symbol: str) -> str:
    """Strip trailing punctuation an English question often carries."""
    return symbol.rstrip(".,;:?!'\"`)]}")
