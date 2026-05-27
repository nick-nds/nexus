"""Integration tests: every registered MCP tool returns a valid response.

Builds a real engine backed by the momskitchen reflection fixture,
wraps it in a :class:`FastMCP` server, and calls every tool via
``mcp.call_tool``. The goal is to prove the MCP serialisation layer
handles all 15 tool output types without crashing.

A "valid response" means:
- ``call_tool`` returns without raising a ``ToolError`` or Python exception.
- The return value is not ``None``.

Structured "not found" errors (e.g. ``error_code="route_not_found"``)
are valid responses - they are part of the tool contract and the MCP
client can inspect them.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from nexus.adapters.storage import ProjectStorage
from nexus.core.graph.builder import GraphBuilder
from nexus.core.query import QueryEngine, ResponseBudget, ToolRegistry
from nexus.core.query.context import QueryContext
from nexus.core.query.tools import register_builtin_tools
from nexus.core.reflection import load_reflection
from nexus.interfaces.mcp import build_mcp_server

pytestmark = pytest.mark.integration


FIXTURE = Path(__file__).parent.parent / "fixtures" / "reflection-samples" / "momskitchen.json"

# ---------------------------------------------------------------------------
# Known-good IDs from the momskitchen fixture (borrowed from existing tests)
# ---------------------------------------------------------------------------

CUSTOMERS_CONTROLLER = "App\\Http\\Controllers\\CustomersController"
BASE_CONTROLLER = "App\\Http\\Controllers\\Controller"
REFRESH_TOKEN_MODEL = "App\\Models\\RefreshToken"
JWT_SERVICE = "App\\Services\\JwtService"
CUSTOMER_ACTIVATED = "Synthesq\\Relay\\Modules\\CRM\\Customers\\Events\\CustomerActivated"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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


class _FakeEmbedder:
    """Deterministic 4-D embedder for semantic_search tests."""

    model_id = "fake:mcp"
    dimensions = 4

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [
            [
                float(len(t) % 7),
                float(sum(c for c in t.encode()[:20]) % 11) / 10.0,
                float(t.count(" ")) / max(len(t), 1),
                1.0,
            ]
            for t in texts
        ]

    def estimate_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)


@pytest.fixture
def engine(tmp_path: Path) -> QueryEngine:
    """Engine backed by the momskitchen fixture; function-scoped so each test
    owns its own SQLite connection (FastMCP dispatches handlers in a thread
    pool, and SQLite connections are not thread-safe across threads)."""
    storage = ProjectStorage(root=tmp_path / ".nexus", slug="mcp-test")

    document = load_reflection(FIXTURE)
    graph = GraphBuilder().build(document, _StubProfile()).value
    persist_result = storage.graph().persist(graph)
    assert persist_result.ok, f"fixture load failed: {persist_result}"

    registry = ToolRegistry()
    register_builtin_tools(registry)

    embedder = _FakeEmbedder()
    ctx = QueryContext(
        storage=storage,
        budget=ResponseBudget(),
        embedder=embedder,
        vector_dimensions=embedder.dimensions,
    )
    yield QueryEngine(registry, ctx)
    storage.close()


def _call(mcp: Any, tool: str, args: dict[str, Any]) -> Any:
    """Synchronously invoke ``mcp.call_tool`` and return the result."""

    async def _run() -> Any:
        return await mcp.call_tool(tool, args)

    return asyncio.run(_run())


# ---------------------------------------------------------------------------
# Tests - one per tool
# ---------------------------------------------------------------------------


class TestMcpToolsCall:
    """Each method calls one MCP tool and asserts the response is valid."""

    def test_list_routes_returns_valid_response(self, engine: QueryEngine) -> None:
        mcp = build_mcp_server(engine)
        result = _call(mcp, "list_routes", {})
        assert result is not None

    def test_list_routes_with_method_filter(self, engine: QueryEngine) -> None:
        mcp = build_mcp_server(engine)
        result = _call(mcp, "list_routes", {"method": "GET"})
        assert result is not None

    def test_describe_class_known_controller(self, engine: QueryEngine) -> None:
        mcp = build_mcp_server(engine)
        result = _call(mcp, "describe_class", {"fqn": CUSTOMERS_CONTROLLER})
        assert result is not None

    def test_describe_class_unknown_returns_structured_error(self, engine: QueryEngine) -> None:
        mcp = build_mcp_server(engine)
        # A structured "not found" result is still a valid MCP response.
        result = _call(mcp, "describe_class", {"fqn": "App\\Not\\A\\Real\\Class"})
        assert result is not None

    def test_get_model_context_known_model(self, engine: QueryEngine) -> None:
        mcp = build_mcp_server(engine)
        result = _call(mcp, "get_model_context", {"fqn": REFRESH_TOKEN_MODEL})
        assert result is not None

    def test_trace_route_controller_route(self, engine: QueryEngine) -> None:
        mcp = build_mcp_server(engine)
        result = _call(mcp, "trace_route", {"method": "GET", "uri": "/api/v1/customers"})
        assert result is not None

    def test_trace_route_unknown_returns_structured_error(self, engine: QueryEngine) -> None:
        mcp = build_mcp_server(engine)
        result = _call(mcp, "trace_route", {"method": "GET", "uri": "/this/does/not/exist"})
        assert result is not None

    def test_get_request_flow_controller_route(self, engine: QueryEngine) -> None:
        mcp = build_mcp_server(engine)
        result = _call(mcp, "get_request_flow", {"method": "GET", "uri": "/api/v1/customers"})
        assert result is not None

    def test_find_handlers_by_uri_glob(self, engine: QueryEngine) -> None:
        mcp = build_mcp_server(engine)
        result = _call(mcp, "find_handlers", {"uri_glob": "/api/v1/*"})
        assert result is not None

    def test_find_listeners_known_event(self, engine: QueryEngine) -> None:
        mcp = build_mcp_server(engine)
        result = _call(mcp, "find_listeners", {"event": CUSTOMER_ACTIVATED})
        assert result is not None

    def test_find_dispatchers_known_event(self, engine: QueryEngine) -> None:
        mcp = build_mcp_server(engine)
        result = _call(mcp, "find_dispatchers", {"event": CUSTOMER_ACTIVATED})
        assert result is not None

    def test_find_event_chains_known_event(self, engine: QueryEngine) -> None:
        mcp = build_mcp_server(engine)
        result = _call(mcp, "find_event_chains", {"event": CUSTOMER_ACTIVATED, "max_depth": 2})
        assert result is not None

    def test_find_jobs_dispatching_unknown_job(self, engine: QueryEngine) -> None:
        # No job dispatch edges in this fixture - structured "not found" is valid.
        mcp = build_mcp_server(engine)
        result = _call(mcp, "find_jobs_dispatching", {"job": "App\\Jobs\\SomeJob"})
        assert result is not None

    def test_get_policy_for_model_without_policy(self, engine: QueryEngine) -> None:
        mcp = build_mcp_server(engine)
        result = _call(mcp, "get_policy_for", {"model_fqn": REFRESH_TOKEN_MODEL})
        assert result is not None

    def test_resolve_binding_known_service(self, engine: QueryEngine) -> None:
        mcp = build_mcp_server(engine)
        result = _call(mcp, "resolve_binding", {"abstract": JWT_SERVICE})
        assert result is not None

    def test_find_implementations_base_controller(self, engine: QueryEngine) -> None:
        mcp = build_mcp_server(engine)
        result = _call(
            mcp,
            "find_implementations",
            {"interface_fqn": BASE_CONTROLLER, "include_subclasses": True},
        )
        assert result is not None

    def test_find_callers_unknown_method(self, engine: QueryEngine) -> None:
        # No CALLS edges in this fixture - structured "not found" is valid.
        mcp = build_mcp_server(engine)
        result = _call(
            mcp,
            "find_callers",
            {"method_fqn": f"{CUSTOMERS_CONTROLLER}::index"},
        )
        assert result is not None

    def test_semantic_search_returns_valid_response(self, engine: QueryEngine) -> None:
        mcp = build_mcp_server(engine)
        # The vector store is empty (no embeddings persisted) so the result
        # will have zero items - but the MCP response itself is valid.
        result = _call(mcp, "semantic_search", {"query": "customer login flow"})
        assert result is not None

    def test_explore_entity_short_name(self, engine: QueryEngine) -> None:
        mcp = build_mcp_server(engine)
        # Whether or not the fixture has ``Customer`` matters less than
        # the MCP layer round-tripping an ExploreEntityOutput cleanly.
        result = _call(mcp, "explore_entity", {"name": "Customer"})
        assert result is not None

    def test_list_by_kind_models(self, engine: QueryEngine) -> None:
        mcp = build_mcp_server(engine)
        result = _call(mcp, "list_by_kind", {"kind": "model"})
        assert result is not None

    def test_list_by_kind_invalid_kind_returns_structured_error(self, engine: QueryEngine) -> None:
        mcp = build_mcp_server(engine)
        # Structured ``invalid_kind`` is a valid response, not an exception.
        result = _call(mcp, "list_by_kind", {"kind": "nonsense"})
        assert result is not None

    def test_list_scheduled_tasks_returns_valid_response(self, engine: QueryEngine) -> None:
        mcp = build_mcp_server(engine)
        result = _call(mcp, "list_scheduled_tasks", {})
        assert result is not None

    def test_describe_flow_fuzzy_query(self, engine: QueryEngine) -> None:
        mcp = build_mcp_server(engine)
        # ``customers`` should hit at least one route in the fixture.
        result = _call(mcp, "describe_flow", {"query": "customers"})
        assert result is not None

    def test_describe_flow_no_matches_returns_structured_error(self, engine: QueryEngine) -> None:
        mcp = build_mcp_server(engine)
        result = _call(mcp, "describe_flow", {"query": "zznonsense"})
        assert result is not None

    def test_list_modules_returns_valid_response(self, engine: QueryEngine) -> None:
        mcp = build_mcp_server(engine)
        result = _call(mcp, "list_modules", {"min_classes": 1})
        assert result is not None

    def test_describe_module_known_prefix(self, engine: QueryEngine) -> None:
        mcp = build_mcp_server(engine)
        # Whether or not "App\\Http" exists in the fixture, MCP must
        # serialise the response cleanly - empty_module is a valid shape.
        result = _call(mcp, "describe_module", {"prefix": "App\\Http"})
        assert result is not None

    def test_describe_module_empty_returns_structured_error(self, engine: QueryEngine) -> None:
        mcp = build_mcp_server(engine)
        result = _call(mcp, "describe_module", {"prefix": "App\\NoneOfThis"})
        assert result is not None

    def test_find_cache_users_unknown_key(self, engine: QueryEngine) -> None:
        # No cache_key nodes in this fixture; structured "not found" is valid.
        mcp = build_mcp_server(engine)
        result = _call(mcp, "find_cache_users", {"key": "settings.timezone"})
        assert result is not None

    def test_expand_call_tree_unknown_method(self, engine: QueryEngine) -> None:
        # No CALLS edges in this fixture either; structured "not found" is valid.
        mcp = build_mcp_server(engine)
        result = _call(
            mcp,
            "expand_call_tree",
            {"method_fqn": f"{CUSTOMERS_CONTROLLER}::index", "direction": "downstream"},
        )
        assert result is not None

    def test_get_full_block_missing_file_returns_structured_error(
        self,
        engine: QueryEngine,
    ) -> None:
        # No project_path stamped on this engine's coverage, so the
        # containment gate is bypassed. The file simply doesn't exist
        # → file_not_found.
        mcp = build_mcp_server(engine)
        result = _call(
            mcp,
            "get_full_block",
            {
                "file_path": "/nonexistent/path/Foo.php",
                "start_line": 1,
                "end_line": 10,
            },
        )
        assert result is not None

    def test_get_node_body_unknown_node_returns_structured_error(
        self,
        engine: QueryEngine,
    ) -> None:
        mcp = build_mcp_server(engine)
        result = _call(
            mcp,
            "get_node_body",
            {"node_id": "method:Does\\Not::Exist"},
        )
        assert result is not None

    def test_all_tools_covered_by_this_suite(self, engine: QueryEngine) -> None:
        """Canary: fail loudly if a new tool is added to the registry
        but not covered by a dedicated test above."""
        expected_tools = set(engine.registry.names())
        tested_tools = {
            "list_routes",
            "list_scheduled_tasks",
            "list_by_kind",
            "list_modules",
            "describe_class",
            "describe_flow",
            "describe_module",
            "explore_entity",
            "get_model_context",
            "trace_route",
            "get_request_flow",
            "find_handlers",
            "find_listeners",
            "find_dispatchers",
            "find_event_chains",
            "find_jobs_dispatching",
            "get_policy_for",
            "resolve_binding",
            "find_implementations",
            "find_callers",
            "find_cache_users",
            "expand_call_tree",
            "semantic_search",
            "get_full_block",
            "get_node_body",
        }
        missing = expected_tools - tested_tools
        assert not missing, (
            f"The following tools are in the registry but not covered by a "
            f"tools/call MCP test: {sorted(missing)}. "
            f"Add a test to {__file__}."
        )
