"""Integration tests for the basic structural query tools.

Builds a real :class:`ProjectStorage` from the committed
momskitchen reflection fixture, persists the graph to a temp
SQLite file, and runs list_routes / describe_class /
get_model_context against the result. Verifies end-to-end
correctness of the whole Phase 4 infrastructure plus Batch 1
of the tools.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from nexus.adapters.storage import ProjectStorage
from nexus.core.graph.builder import GraphBuilder
from nexus.core.query import (
    QueryEngine,
    ResponseBudget,
    ToolRegistry,
)
from nexus.core.query.context import QueryContext
from nexus.core.query.tools import register_builtin_tools
from nexus.core.reflection import load_reflection

pytestmark = pytest.mark.integration


FIXTURE = Path(__file__).parent.parent / "fixtures" / "reflection-samples" / "momskitchen.json"


@dataclass(frozen=True)
class _StubProfile:
    name: str = "stub"
    custom_bases: dict[str, str] = None  # type: ignore[assignment]
    custom_suffixes: dict[str, str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.custom_bases is None:
            object.__setattr__(self, "custom_bases", {})
        if self.custom_suffixes is None:
            object.__setattr__(self, "custom_suffixes", {})


@pytest.fixture
def storage(tmp_path: Path):
    s = ProjectStorage(root=tmp_path / ".nexus", slug="test")
    document = load_reflection(FIXTURE)
    graph = GraphBuilder().build(document, _StubProfile()).value
    result = s.graph().persist(graph)
    assert result.ok
    yield s
    s.close()


@pytest.fixture
def engine(storage: ProjectStorage) -> QueryEngine:
    registry = ToolRegistry()
    register_builtin_tools(registry)
    ctx = QueryContext(
        storage=storage,
        budget=ResponseBudget(),
    )
    return QueryEngine(registry, ctx)


# ---------------------------------------------------------------------------
# list_routes
# ---------------------------------------------------------------------------


class TestListRoutes:
    def test_lists_all_routes(self, engine: QueryEngine) -> None:
        result = engine.query("list_routes")

        # momskitchen has 23 routes; assert we see them all
        assert result.total == 23
        assert result.returned == 23
        assert len(result.routes) == 23

    def test_filter_by_method(self, engine: QueryEngine) -> None:
        result = engine.query("list_routes", {"method": "POST"})

        assert result.total > 0
        for route in result.routes:
            assert "POST" in route.methods

    def test_filter_by_uri_glob(self, engine: QueryEngine) -> None:
        result = engine.query("list_routes", {"uri_glob": "/api/v1/*"})

        assert result.total > 0
        for route in result.routes:
            assert route.uri.startswith("/api/v1/")

    def test_filter_by_middleware(self, engine: QueryEngine) -> None:
        # momskitchen uses 'web' as an aliased middleware group.
        result = engine.query("list_routes", {"middleware": "web"})

        # Not every route has web middleware, but some do.
        for route in result.routes:
            assert "web" in route.middleware

    def test_no_match_returns_empty(self, engine: QueryEngine) -> None:
        result = engine.query("list_routes", {"method": "OPTIONS"})
        assert result.total == 0
        assert result.routes == []

    def test_returns_controller_for_controller_actions(self, engine: QueryEngine) -> None:
        result = engine.query("list_routes")

        # At least some of momskitchen's routes are controller actions
        controller_routes = [r for r in result.routes if r.action_kind == "controller"]
        assert len(controller_routes) > 0
        for r in controller_routes:
            assert r.controller is not None


# ---------------------------------------------------------------------------
# describe_class
# ---------------------------------------------------------------------------


class TestDescribeClass:
    def test_unknown_class_returns_error(self, engine: QueryEngine) -> None:
        result = engine.query("describe_class", {"fqn": "App\\Not\\A\\Class"})

        assert result.error_code == "class_not_found"
        assert result.error is not None

    def test_describes_known_controller(self, engine: QueryEngine) -> None:
        # momskitchen has many controllers; pick one we saw in Phase 3
        result = engine.query(
            "describe_class",
            {"fqn": "App\\Http\\Controllers\\CustomersController"},
        )

        assert result.error is None
        assert result.fqn == "App\\Http\\Controllers\\CustomersController"
        assert result.kind == "controller"
        assert len(result.methods) > 0
        # This controller is referenced by at least one route
        assert len(result.related_routes) > 0

    def test_class_has_parent_when_extends(self, engine: QueryEngine) -> None:
        result = engine.query(
            "describe_class",
            {"fqn": "App\\Http\\Controllers\\CustomersController"},
        )
        assert result.parent is not None

    def test_methods_are_sorted(self, engine: QueryEngine) -> None:
        result = engine.query(
            "describe_class",
            {"fqn": "App\\Http\\Controllers\\CustomersController"},
        )
        method_names = [m.name for m in result.methods]
        assert method_names == sorted(method_names)


# ---------------------------------------------------------------------------
# get_model_context
# ---------------------------------------------------------------------------


class TestGetModelContext:
    def test_unknown_model_returns_error(self, engine: QueryEngine) -> None:
        result = engine.query("get_model_context", {"fqn": "App\\Missing"})
        assert result.error_code == "class_not_found"

    def test_model_flag_set_for_real_model(self, engine: QueryEngine) -> None:
        # momskitchen has App\Models\* classes
        # Find a real one via list of models
        result = engine.query("get_model_context", {"fqn": "App\\Models\\RefreshToken"})
        assert result.error is None
        assert result.is_model is True


# ---------------------------------------------------------------------------
# Registry + engine integration
# ---------------------------------------------------------------------------


class TestBuiltInRegistry:
    def test_builtin_tools_registered(self) -> None:
        registry = ToolRegistry()
        register_builtin_tools(registry)

        names = registry.names()
        assert "list_routes" in names
        assert "describe_class" in names
        assert "get_model_context" in names
        assert "trace_route" in names
        assert "get_request_flow" in names
        assert "find_handlers" in names
        assert "get_full_block" in names
        assert "get_node_body" in names

    def test_each_tool_declares_latency_budget(self) -> None:
        registry = ToolRegistry()
        register_builtin_tools(registry)

        for entry in registry.tools():
            assert entry.tool_class.latency_budget_ms > 0


# ---------------------------------------------------------------------------
# trace_route
# ---------------------------------------------------------------------------


class TestTraceRoute:
    def test_missing_route_returns_structured_error(self, engine: QueryEngine) -> None:
        result = engine.query(
            "trace_route",
            {"method": "GET", "uri": "/this/route/does/not/exist"},
        )

        assert result.error_code == "route_not_found"
        assert result.error is not None
        assert result.handler is None

    def test_controller_route_returns_handler_and_middleware(
        self,
        engine: QueryEngine,
    ) -> None:
        # momskitchen: GET /api/v1/customers → CustomersController::index,
        # five middleware entries including the JWT + tenancy stack.
        result = engine.query(
            "trace_route",
            {"method": "GET", "uri": "/api/v1/customers"},
        )

        assert result.error is None
        assert result.uri == "/api/v1/customers"
        assert "GET" in result.methods
        assert result.handler is not None
        assert result.handler.class_fqn == "App\\Http\\Controllers\\CustomersController"
        assert result.handler.method_name == "index"
        assert result.handler.action_kind == "controller"
        assert "api" in result.middleware
        # Handler has no FIRES/DISPATCHES edges in this fixture yet —
        # but the lists should exist and be empty, not missing.
        assert result.fires_events == []
        assert result.dispatches_jobs == []

    def test_resolve_by_route_id(self, engine: QueryEngine) -> None:
        result = engine.query(
            "trace_route",
            {"route_id": "route:GET:/api/v1/customers"},
        )
        assert result.error is None
        assert result.uri == "/api/v1/customers"

    def test_closure_route_has_no_handler_class(
        self,
        engine: QueryEngine,
    ) -> None:
        # The /_boost/browser-logs route is a closure action with no
        # class/method target in the graph.
        result = engine.query(
            "trace_route",
            {"method": "POST", "uri": "/_boost/browser-logs"},
        )
        assert result.error is None
        # Either no handler at all or one whose action_kind says closure.
        if result.handler is not None:
            assert result.handler.class_fqn is None


# ---------------------------------------------------------------------------
# get_request_flow
# ---------------------------------------------------------------------------


class TestGetRequestFlow:
    def test_missing_route_returns_structured_error(self, engine: QueryEngine) -> None:
        result = engine.query(
            "get_request_flow",
            {"method": "GET", "uri": "/nope"},
        )
        assert result.error_code == "route_not_found"

    def test_controller_flow_includes_trace_fields(self, engine: QueryEngine) -> None:
        result = engine.query(
            "get_request_flow",
            {"method": "GET", "uri": "/api/v1/customers"},
        )
        assert result.error is None
        assert result.handler is not None
        assert result.handler.class_fqn == "App\\Http\\Controllers\\CustomersController"
        assert len(result.middleware) > 0
        # event_chain is empty on this fixture because the handler has
        # no FIRES edges yet — but the field must exist and be a list.
        assert result.event_chain == []


# ---------------------------------------------------------------------------
# find_handlers
# ---------------------------------------------------------------------------


class TestFindHandlers:
    def test_requires_a_filter(self, engine: QueryEngine) -> None:
        result = engine.query("find_handlers", {})
        assert result.error_code == "missing_filter"

    def test_by_uri_glob(self, engine: QueryEngine) -> None:
        result = engine.query("find_handlers", {"uri_glob": "/api/v1/customers*"})
        assert result.total > 0
        uris = {h.uri for h in result.handlers}
        assert any(uri.startswith("/api/v1/customers") for uri in uris)

    def test_by_handler_fqn(self, engine: QueryEngine) -> None:
        result = engine.query(
            "find_handlers",
            {"handler_fqn": "App\\Http\\Controllers\\CustomersController"},
        )
        assert result.total > 0
        for row in result.handlers:
            assert row.class_fqn == "App\\Http\\Controllers\\CustomersController"

    def test_by_handler_fqn_with_method(self, engine: QueryEngine) -> None:
        result = engine.query(
            "find_handlers",
            {"handler_fqn": "App\\Http\\Controllers\\CustomersController::index"},
        )
        assert result.total >= 1
        for row in result.handlers:
            assert row.method_name == "index"

    def test_method_filter_narrows_results(self, engine: QueryEngine) -> None:
        all_results = engine.query("find_handlers", {"uri_glob": "/api/v1/*"})
        post_only = engine.query(
            "find_handlers",
            {"uri_glob": "/api/v1/*", "method": "POST"},
        )
        assert post_only.total <= all_results.total
        for row in post_only.handlers:
            assert "POST" in row.methods
