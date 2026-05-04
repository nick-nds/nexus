"""Regression corpus for :class:`nexus.core.query.QueryClassifier`.

The classifier is the public-facing API of ``nexus ask``. When it
mis-routes a question, an agent gets noisy semantic-search results
instead of the structural answer that already lives in the graph —
the textbook hallucination shape we're trying to prevent.

Strategy: lock in the routing decisions for ~60 representative
questions, organised by the intent we expect them to hit. Each case
asserts the *tool* the classifier picked plus an optional subset of
the args. The whole corpus runs as one parametrised test; failures
show up grouped per intent so it's clear which patterns are missing.

This file is the *spec* the classifier improvements track against.
The bar set in ``test_classifier_corpus_minimum_pass_rate`` ratchets
upward as new rules are added; the per-case parametrised test makes
individual regressions readable in CI output.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from nexus.core.query.classifier import QueryClassifier


@dataclass(frozen=True)
class CorpusCase:
    """One expected classification.

    ``expected_args_subset`` is partial — only the keys listed must
    match. The classifier may set additional args without failing
    the case.
    """

    query: str
    expected_tool: str
    expected_args_subset: dict[str, object] = field(default_factory=dict)
    min_confidence: float = 0.6
    notes: str = ""


# ---------------------------------------------------------------------------
# Intent groups
# ---------------------------------------------------------------------------

#: Questions that should route to ``find_handlers``. These are the
#: cases that motivated this whole subtask: "what handles the login
#: route" must NOT fall through to ``semantic_search``.
ROUTE_HANDLER_CASES: tuple[CorpusCase, ...] = (
    CorpusCase("what handles the login route", "find_handlers"),
    CorpusCase("what handles login", "find_handlers"),
    CorpusCase("show handler for /login", "find_handlers"),
    CorpusCase("which controller handles /api/users", "find_handlers"),
    CorpusCase("handler for /api/auth/login", "find_handlers"),
    CorpusCase("what handles POST /api/orders", "find_handlers"),
    CorpusCase("who handles GET /api/users", "find_handlers"),
)

#: Route-tracing questions — verb + path or "trace" verb.
ROUTE_TRACE_CASES: tuple[CorpusCase, ...] = (
    CorpusCase("GET /api/users", "trace_route", {"method": "GET", "uri": "/api/users"}),
    CorpusCase("POST /login", "trace_route", {"method": "POST", "uri": "/login"}),
    CorpusCase("trace POST /api/auth/login", "trace_route"),
    CorpusCase("show flow for /api/orders", "get_request_flow"),
    CorpusCase("full request flow for POST /login", "get_request_flow"),
)

#: Listing routes (no specific URI in the question).
LIST_ROUTES_CASES: tuple[CorpusCase, ...] = (
    CorpusCase("list routes", "list_routes"),
    CorpusCase("show all routes", "list_routes"),
    CorpusCase("what routes exist", "list_routes"),
    CorpusCase("show me the routes", "list_routes"),
)

#: Calls of a specific method.
CALLER_CASES: tuple[CorpusCase, ...] = (
    CorpusCase(
        "who calls App\\Models\\User::save",
        "find_callers",
        {"method_fqn": "App\\Models\\User::save"},
    ),
    CorpusCase(
        "callers of App\\Services\\PaymentGateway::charge",
        "find_callers",
        {"method_fqn": "App\\Services\\PaymentGateway::charge"},
    ),
    CorpusCase("what calls App\\Auth\\LandlordAuthenticator::attempt", "find_callers"),
)

#: Event listeners.
LISTENER_CASES: tuple[CorpusCase, ...] = (
    CorpusCase("who listens to UserRegistered", "find_listeners"),
    CorpusCase("listeners for OrderPlaced", "find_listeners"),
    CorpusCase("listeners of PaymentReceived", "find_listeners"),
    CorpusCase("who handles the OrderPlaced event", "find_listeners"),
)

#: Event dispatchers.
DISPATCHER_CASES: tuple[CorpusCase, ...] = (
    CorpusCase("who fires OrderPlaced", "find_dispatchers"),
    CorpusCase("who dispatches UserRegistered", "find_dispatchers"),
    CorpusCase("who triggers PaymentReceived", "find_dispatchers"),
    CorpusCase("where is OrderPlaced fired", "find_dispatchers"),
)

#: Job dispatchers (separate tool — the "Job" suffix is the trigger).
JOB_DISPATCHER_CASES: tuple[CorpusCase, ...] = (
    CorpusCase("who dispatches SendEmailJob", "find_jobs_dispatching"),
    CorpusCase("who queues ProcessOrderJob", "find_jobs_dispatching"),
)

#: Interface implementations.
IMPLEMENTATION_CASES: tuple[CorpusCase, ...] = (
    CorpusCase("who implements PaymentGateway", "find_implementations"),
    CorpusCase("implementations of UserRepository", "find_implementations"),
    CorpusCase("implementers of App\\Contracts\\Mailer", "find_implementations"),
)

#: Class description (non-model FQN).
DESCRIBE_CLASS_CASES: tuple[CorpusCase, ...] = (
    CorpusCase(
        "describe App\\Http\\Controllers\\UserController",
        "describe_class",
        {"fqn": "App\\Http\\Controllers\\UserController"},
    ),
    CorpusCase("App\\Services\\PaymentGateway", "describe_class"),
    CorpusCase("show class App\\Auth\\LandlordAuthenticator", "describe_class"),
)

#: Model context — model FQN should preferred over generic describe.
MODEL_CONTEXT_CASES: tuple[CorpusCase, ...] = (
    CorpusCase(
        "App\\Models\\User",
        "get_model_context",
        {"fqn": "App\\Models\\User"},
    ),
    CorpusCase("tell me about App\\Models\\Order", "get_model_context"),
    CorpusCase("describe App\\Models\\Customer", "get_model_context"),
)

#: Policies.
POLICY_CASES: tuple[CorpusCase, ...] = (
    CorpusCase("policy for App\\Models\\User", "get_policy_for"),
    CorpusCase("authorization for Order", "get_policy_for"),
    CorpusCase("policies for App\\Models\\Account", "get_policy_for"),
)

#: Container bindings.
BINDING_CASES: tuple[CorpusCase, ...] = (
    CorpusCase("binding for App\\Contracts\\Mailer", "resolve_binding"),
    CorpusCase("what does the container resolve PaymentGateway to", "resolve_binding"),
    CorpusCase("bound to App\\Contracts\\Repository", "resolve_binding"),
)

#: Off-topic / nonsense — must not be confidently routed to a
#: structural tool.  Either ``semantic_search`` (acceptable today)
#: or, after subtask 2.3 lands, an explicit ``no_confident_match``.
OFF_TOPIC_CASES: tuple[CorpusCase, ...] = (
    CorpusCase("make me a sandwich", "semantic_search", min_confidence=0.0),
    CorpusCase("explain quantum physics", "semantic_search", min_confidence=0.0),
    CorpusCase("what is the meaning of life", "semantic_search", min_confidence=0.0),
)

#: Discovery questions — single short name with a verb. Routes to
#: ``explore_entity`` so the agent can find a candidate FQN before
#: drilling in with ``describe_class`` / ``get_model_context``.
EXPLORE_ENTITY_CASES: tuple[CorpusCase, ...] = (
    CorpusCase("explain Product", "explore_entity", {"name": "Product"}),
    CorpusCase("explain the Product entity", "explore_entity", {"name": "Product"}),
    CorpusCase("describe Lead", "explore_entity", {"name": "Lead"}),
    CorpusCase("tell me about Order", "explore_entity", {"name": "Order"}),
    CorpusCase("what is Customer", "explore_entity", {"name": "Customer"}),
    CorpusCase("show me the Invoice model", "explore_entity", {"name": "Invoice"}),
)

#: Fuzzy flow discovery — natural-language phrasings without a URI.
#: Routes to ``describe_flow`` so the tool itself does the URI/name/
#: handler resolution.
DESCRIBE_FLOW_CASES: tuple[CorpusCase, ...] = (
    CorpusCase(
        "how does order placement work",
        "describe_flow",
        {"query": "order placement"},
    ),
    CorpusCase(
        "walk me through the lead creation flow",
        "describe_flow",
        {"query": "lead creation"},
    ),
    CorpusCase(
        "describe the flow for placing an order",
        "describe_flow",
        {"query": "placing an order"},
    ),
    CorpusCase(
        "what happens when a user signs up",
        "describe_flow",
        {"query": "a user signs up"},
    ),
)


#: Generic kind enumeration. Routes "list events / show all jobs / what
#: notifications exist" to ``list_by_kind`` with the canonical kind.
LIST_BY_KIND_CASES: tuple[CorpusCase, ...] = (
    CorpusCase("list events", "list_by_kind", {"kind": "event"}),
    CorpusCase("show all jobs", "list_by_kind", {"kind": "job"}),
    CorpusCase("what notifications exist", "list_by_kind", {"kind": "notification"}),
    CorpusCase("list models", "list_by_kind", {"kind": "model"}),
    CorpusCase("show all controllers", "list_by_kind", {"kind": "controller"}),
    CorpusCase("list policies", "list_by_kind", {"kind": "policy"}),
    CorpusCase("show all observers", "list_by_kind", {"kind": "observer"}),
    CorpusCase("list form requests", "list_by_kind", {"kind": "form_request"}),
    CorpusCase("show service providers", "list_by_kind", {"kind": "service_provider"}),
)


CORPUS: tuple[CorpusCase, ...] = (
    *ROUTE_HANDLER_CASES,
    *ROUTE_TRACE_CASES,
    *LIST_ROUTES_CASES,
    *CALLER_CASES,
    *LISTENER_CASES,
    *DISPATCHER_CASES,
    *JOB_DISPATCHER_CASES,
    *IMPLEMENTATION_CASES,
    *DESCRIBE_CLASS_CASES,
    *MODEL_CONTEXT_CASES,
    *POLICY_CASES,
    *BINDING_CASES,
    *OFF_TOPIC_CASES,
    *EXPLORE_ENTITY_CASES,
    *DESCRIBE_FLOW_CASES,
    *LIST_BY_KIND_CASES,
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def classifier() -> QueryClassifier:
    return QueryClassifier()


@pytest.mark.parametrize(
    "case",
    CORPUS,
    ids=[f"{c.expected_tool}::{c.query}" for c in CORPUS],
)
def test_classifier_routes_to_expected_tool(
    classifier: QueryClassifier,
    case: CorpusCase,
) -> None:
    """The classifier picks the expected tool for this question.

    Per-case test so failures show up grouped in the test report — a
    single parametrised id like ``find_handlers::what handles login``
    points directly at the gap.
    """
    plan = classifier.classify(case.query)

    assert plan.tool == case.expected_tool, (
        f"query {case.query!r} routed to {plan.tool!r}, "
        f"expected {case.expected_tool!r} (reason: {plan.reason})"
    )

    assert plan.confidence >= case.min_confidence, (
        f"confidence {plan.confidence} < {case.min_confidence} for {case.query!r}"
    )

    for key, expected in case.expected_args_subset.items():
        assert plan.args.get(key) == expected, (
            f"args[{key!r}] = {plan.args.get(key)!r}, expected {expected!r} for {case.query!r}"
        )


def test_corpus_loads() -> None:
    """The corpus is well-formed and big enough to cover the major intents."""
    assert len(CORPUS) >= 40, f"corpus too small: {len(CORPUS)} cases"
    intents = {c.expected_tool for c in CORPUS}
    expected_intents = {
        "find_handlers",
        "trace_route",
        "get_request_flow",
        "list_routes",
        "find_callers",
        "find_listeners",
        "find_dispatchers",
        "find_jobs_dispatching",
        "find_implementations",
        "describe_class",
        "get_model_context",
        "get_policy_for",
        "resolve_binding",
        "semantic_search",
        "explore_entity",
        "describe_flow",
        "list_by_kind",
    }
    assert expected_intents.issubset(intents), (
        f"corpus missing intent coverage for: {expected_intents - intents}"
    )


def test_classifier_corpus_minimum_pass_rate(classifier: QueryClassifier) -> None:
    """The corpus must reach the documented coverage threshold.

    This is the ratchet that subtask 2.2 raises. The threshold lives
    here so a regression that drops coverage below it is a CI-visible
    failure, not a silent rot. We start at 60% (today's behaviour
    plus a few new rules), then raise to ≥80% as part of subtask 2.2.
    """
    passed = 0
    misses: list[str] = []
    for case in CORPUS:
        plan = classifier.classify(case.query)
        if plan.tool == case.expected_tool:
            passed += 1
        else:
            misses.append(
                f"  {case.query!r} → {plan.tool} (expected {case.expected_tool})",
            )
    rate = passed / len(CORPUS)
    # Threshold ratchet: 60% before the 2.2 rules, 95%+ after.  Drops
    # below the threshold are CI-visible so a regression is loud.
    threshold = 0.95
    assert rate >= threshold, (
        f"classifier corpus pass rate {rate:.0%} < {threshold:.0%}\n"
        + "Misses:\n"
        + "\n".join(misses)
    )
