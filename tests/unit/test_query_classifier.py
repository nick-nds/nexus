"""Unit tests for the rule-based query classifier.

The classifier is deterministic and stateless; these tests use
natural-language phrasings harvested from the design doc and from
my own first-pass agent prompts. The assertion shape is
"classified phrasings go to the right tool" - exact confidence
scores are implementation detail and not asserted, only lower
bounds.
"""

from __future__ import annotations

import pytest
from nexus.core.query.classifier import QueryClassifier, QueryPlan


@pytest.fixture
def classifier() -> QueryClassifier:
    return QueryClassifier()


# ---------------------------------------------------------------------------
# HTTP route patterns
# ---------------------------------------------------------------------------


class TestHttpRoutes:
    def test_basic_verb_and_path(self, classifier: QueryClassifier) -> None:
        plan = classifier.classify("What happens on POST /api/orders?")
        assert plan.tool == "trace_route"
        assert plan.args == {"method": "POST", "uri": "/api/orders"}
        assert plan.confidence >= 0.9

    def test_path_with_params(self, classifier: QueryClassifier) -> None:
        plan = classifier.classify("trace GET /api/users/{id}")
        assert plan.tool == "trace_route"
        assert plan.args["uri"] == "/api/users/{id}"

    def test_lowercase_verb(self, classifier: QueryClassifier) -> None:
        plan = classifier.classify("explain get /healthz")
        assert plan.tool == "trace_route"
        assert plan.args == {"method": "GET", "uri": "/healthz"}

    def test_includes_request_flow_fallback(
        self,
        classifier: QueryClassifier,
    ) -> None:
        plan = classifier.classify("POST /orders")
        fallback_tools = [fb.tool for fb in plan.fallbacks]
        assert "get_request_flow" in fallback_tools
        assert "semantic_search" in fallback_tools


# ---------------------------------------------------------------------------
# FQN-based classes and models
# ---------------------------------------------------------------------------


class TestFqnRouting:
    def test_plain_class_fqn_goes_to_describe_class(
        self,
        classifier: QueryClassifier,
    ) -> None:
        plan = classifier.classify(
            "What does App\\Http\\Controllers\\UserController do?",
        )
        assert plan.tool == "describe_class"
        assert plan.args == {"fqn": "App\\Http\\Controllers\\UserController"}

    def test_model_fqn_goes_to_model_context(
        self,
        classifier: QueryClassifier,
    ) -> None:
        plan = classifier.classify("Tell me about App\\Models\\User")
        assert plan.tool == "get_model_context"
        assert plan.args == {"fqn": "App\\Models\\User"}
        # And falls back to describe_class if the model lookup fails.
        assert any(fb.tool == "describe_class" for fb in plan.fallbacks)


# ---------------------------------------------------------------------------
# Event / job / implementers / callers
# ---------------------------------------------------------------------------


class TestDomainQueries:
    def test_listeners_of_event(self, classifier: QueryClassifier) -> None:
        plan = classifier.classify("who listens to OrderCreated?")
        assert plan.tool == "find_listeners"
        assert plan.args == {"event": "OrderCreated"}

    def test_listeners_alt_phrasing(self, classifier: QueryClassifier) -> None:
        plan = classifier.classify("listeners of CustomerActivated")
        assert plan.tool == "find_listeners"
        assert plan.args == {"event": "CustomerActivated"}

    def test_dispatchers_of_event(self, classifier: QueryClassifier) -> None:
        plan = classifier.classify("who fires OrderPlaced")
        assert plan.tool == "find_dispatchers"
        assert plan.args == {"event": "OrderPlaced"}

    def test_job_dispatchers(self, classifier: QueryClassifier) -> None:
        plan = classifier.classify("where is SendWelcomeJob dispatched?")
        assert plan.tool == "find_jobs_dispatching"
        assert plan.args == {"job": "SendWelcomeJob"}

    def test_implementers(self, classifier: QueryClassifier) -> None:
        plan = classifier.classify("who implements RepositoryInterface?")
        assert plan.tool == "find_implementations"
        assert plan.args == {"interface_fqn": "RepositoryInterface"}

    def test_callers(self, classifier: QueryClassifier) -> None:
        plan = classifier.classify("callers of UserService::create")
        assert plan.tool == "find_callers"
        assert plan.args == {"method_fqn": "UserService::create"}

    def test_policy_for_model(self, classifier: QueryClassifier) -> None:
        plan = classifier.classify("policy for App\\Models\\Order")
        assert plan.tool == "get_policy_for"
        assert plan.args == {"model_fqn": "App\\Models\\Order"}

    def test_binding_resolution(self, classifier: QueryClassifier) -> None:
        plan = classifier.classify(
            "what's bound to App\\Services\\PaymentGateway?",
        )
        assert plan.tool == "resolve_binding"
        assert plan.args == {"abstract": "App\\Services\\PaymentGateway"}


# ---------------------------------------------------------------------------
# Routes listing
# ---------------------------------------------------------------------------


class TestListRoutes:
    def test_show_all_routes(self, classifier: QueryClassifier) -> None:
        plan = classifier.classify("show all the routes")
        assert plan.tool == "list_routes"

    def test_what_routes_exist(self, classifier: QueryClassifier) -> None:
        plan = classifier.classify("what are the routes?")
        assert plan.tool == "list_routes"


# ---------------------------------------------------------------------------
# Semantic fallback
# ---------------------------------------------------------------------------


class TestSemanticFallback:
    def test_vague_question_goes_to_semantic_search(
        self,
        classifier: QueryClassifier,
    ) -> None:
        plan = classifier.classify("how do we send welcome emails?")
        assert plan.tool == "semantic_search"
        assert plan.args == {"query": "how do we send welcome emails?"}
        assert plan.confidence < 0.6

    def test_empty_query_is_safe(self, classifier: QueryClassifier) -> None:
        plan = classifier.classify("   ")
        assert plan.tool == "semantic_search"

    def test_punctuation_only_goes_to_semantic(
        self,
        classifier: QueryClassifier,
    ) -> None:
        plan = classifier.classify("?!")
        assert plan.tool == "semantic_search"


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_query_same_plan(self, classifier: QueryClassifier) -> None:
        a = classifier.classify("GET /api/users")
        b = classifier.classify("GET /api/users")
        assert a == b

    def test_plans_are_frozen(self, classifier: QueryClassifier) -> None:
        plan = classifier.classify("GET /api/users")
        assert isinstance(plan, QueryPlan)
        with pytest.raises(AttributeError):
            plan.tool = "something_else"  # type: ignore[misc]
