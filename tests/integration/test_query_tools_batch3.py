"""Integration tests for Phase 4 batch-3 tools.

Covers the event / job / policy / binding / implementation /
caller tools registered in :mod:`nexus.core.query.tools`. Uses
the committed momskitchen reflection fixture so every test
exercises the real graph adapter pipeline, not a mock.

Some edge kinds (``FIRES``, ``DISPATCHES``, ``CALLS``,
``APPLIES_TO``) are populated by Phase 3's static analyser,
which hadn't run against the momskitchen fixture at the time
these tests were written. Tests that would require those edges
assert the structured ``*_not_found`` error shape — still a
useful contract test even on a partially-populated fixture.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from nexus.adapters.storage import ProjectStorage
from nexus.core.graph.builder import GraphBuilder
from nexus.core.query import QueryEngine, ResponseBudget, ToolRegistry
from nexus.core.query.context import QueryContext
from nexus.core.query.errors import ToolInputError
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
def engine(tmp_path: Path) -> QueryEngine:
    storage = ProjectStorage(root=tmp_path / ".nexus", slug="b3")
    document = load_reflection(FIXTURE)
    graph = GraphBuilder().build(document, _StubProfile()).value
    assert storage.graph().persist(graph).ok

    registry = ToolRegistry()
    register_builtin_tools(registry)
    ctx = QueryContext(storage=storage, budget=ResponseBudget())
    yield QueryEngine(registry, ctx)
    storage.close()


# ---------------------------------------------------------------------------
# find_listeners
# ---------------------------------------------------------------------------


CUSTOMER_ACTIVATED = "Synthesq\\Relay\\Modules\\CRM\\Customers\\Events\\CustomerActivated"


class TestFindListeners:
    def test_unknown_event_returns_error(self, engine: QueryEngine) -> None:
        result = engine.query("find_listeners", {"event": "App\\Not\\An\\Event"})
        assert result.error_code == "event_not_found"
        assert result.listeners == []

    def test_returns_listeners_for_real_event(self, engine: QueryEngine) -> None:
        result = engine.query("find_listeners", {"event": CUSTOMER_ACTIVATED})
        assert result.error is None
        assert result.total >= 1
        # InvalidateEligibilityCacheOnCustomerActivated is wired up.
        fqns = {r.listener_fqn for r in result.listeners}
        assert any("InvalidateEligibilityCacheOnCustomerActivated" in f for f in fqns)

    def test_accepts_graph_id(self, engine: QueryEngine) -> None:
        result = engine.query(
            "find_listeners",
            {"event": f"event:{CUSTOMER_ACTIVATED}"},
        )
        assert result.error is None
        assert result.total >= 1


# ---------------------------------------------------------------------------
# find_dispatchers
# ---------------------------------------------------------------------------


class TestFindDispatchers:
    def test_unknown_event_returns_error(self, engine: QueryEngine) -> None:
        result = engine.query("find_dispatchers", {"event": "App\\Nope"})
        assert result.error_code == "event_not_found"

    def test_known_event_without_fires_edges(self, engine: QueryEngine) -> None:
        # momskitchen's fixture predates Phase 3 static analysis, so
        # no FIRES edges exist — the call should succeed with empty
        # results rather than erroring.
        result = engine.query("find_dispatchers", {"event": CUSTOMER_ACTIVATED})
        assert result.error is None
        assert result.dispatchers == []


# ---------------------------------------------------------------------------
# find_event_chains
# ---------------------------------------------------------------------------


class TestFindEventChains:
    def test_rejects_invalid_depth(self, engine: QueryEngine) -> None:
        with pytest.raises(ToolInputError):
            engine.query(
                "find_event_chains",
                {"event": CUSTOMER_ACTIVATED, "max_depth": 0},
            )

    def test_walks_listeners_at_depth_one(self, engine: QueryEngine) -> None:
        result = engine.query(
            "find_event_chains",
            {"event": CUSTOMER_ACTIVATED, "max_depth": 1},
        )
        assert result.error is None
        assert result.depth_reached >= 1
        # At least one listener appears at depth 1.
        assert any(s.depth == 1 for s in result.steps)


# ---------------------------------------------------------------------------
# find_jobs_dispatching
# ---------------------------------------------------------------------------


class TestFindJobsDispatching:
    def test_unknown_job_returns_error(self, engine: QueryEngine) -> None:
        result = engine.query("find_jobs_dispatching", {"job": "App\\Not\\A\\Job"})
        assert result.error_code == "job_not_found"


# ---------------------------------------------------------------------------
# get_policy_for
# ---------------------------------------------------------------------------


class TestGetPolicyFor:
    def test_unknown_model_returns_error(self, engine: QueryEngine) -> None:
        result = engine.query("get_policy_for", {"model_fqn": "App\\Nothing"})
        assert result.error_code == "model_not_found"

    def test_known_model_without_policy(self, engine: QueryEngine) -> None:
        result = engine.query(
            "get_policy_for",
            {"model_fqn": "App\\Models\\RefreshToken"},
        )
        assert result.error_code == "policy_not_found"


# ---------------------------------------------------------------------------
# resolve_binding
# ---------------------------------------------------------------------------


class TestResolveBinding:
    def test_unknown_binding_returns_error(self, engine: QueryEngine) -> None:
        result = engine.query("resolve_binding", {"abstract": "No\\Such\\Thing"})
        assert result.error_code == "binding_not_found"

    def test_closure_binding(self, engine: QueryEngine) -> None:
        # App\Services\JwtService is registered as a shared closure.
        result = engine.query(
            "resolve_binding",
            {"abstract": "App\\Services\\JwtService"},
        )
        assert result.error is None
        assert result.shared is True
        assert result.concrete_kind == "closure"
        assert result.provider_file is not None


# ---------------------------------------------------------------------------
# find_implementations
# ---------------------------------------------------------------------------


class TestFindImplementations:
    def test_unknown_interface_returns_error(self, engine: QueryEngine) -> None:
        result = engine.query(
            "find_implementations",
            {"interface_fqn": "App\\Missing"},
        )
        assert result.error_code == "class_not_found"

    def test_abstract_controller_has_subclasses(
        self,
        engine: QueryEngine,
    ) -> None:
        # momskitchen's concrete controllers all extend the project's
        # base ``App\Http\Controllers\Controller`` abstract class. It's
        # the only interface-like type whose node is actually present
        # in the fixture (vendor interfaces appear only as dangling
        # edge targets).
        result = engine.query(
            "find_implementations",
            {
                "interface_fqn": "App\\Http\\Controllers\\Controller",
                "include_subclasses": True,
            },
        )
        assert result.error is None
        assert result.total >= 1
        for row in result.implementations:
            assert row.via in {"implements", "extends"}


# ---------------------------------------------------------------------------
# find_callers
# ---------------------------------------------------------------------------


class TestFindCallers:
    def test_unknown_method_returns_error(self, engine: QueryEngine) -> None:
        result = engine.query(
            "find_callers",
            {"method_fqn": "App\\Missing::nope"},
        )
        assert result.error_code == "method_not_found"
