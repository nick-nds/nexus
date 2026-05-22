"""Unit tests for the ``find_jobs_dispatching`` query tool.

Exercises all branches of ``FindJobsDispatchingTool.execute`` and the
``_resolve_job_id`` helper using a hand-built in-memory graph and a
lightweight storage stub.
"""

from __future__ import annotations

from nexus.core.graph.graph import Graph
from nexus.core.graph.types import Edge, EdgeKind, Node, NodeKind
from nexus.core.query import ResponseBudget
from nexus.core.query.context import QueryContext
from nexus.core.query.tools.find_jobs_dispatching import (
    FindJobsDispatchingInput,
    FindJobsDispatchingTool,
    _resolve_job_id,
)

# ---------------------------------------------------------------------------
# Stub storage
# ---------------------------------------------------------------------------


class _StubGraphStore:
    def __init__(self, graph: Graph) -> None:
        self._graph = graph

    def load(self) -> Graph:
        return self._graph


class _StubStorage:
    def __init__(self, graph: Graph) -> None:
        self._graph_store = _StubGraphStore(graph)

    def graph(self) -> _StubGraphStore:
        return self._graph_store


def _make_ctx(graph: Graph) -> QueryContext:
    return QueryContext(
        storage=_StubStorage(graph),  # type: ignore[arg-type]
        budget=ResponseBudget(),
    )


# ---------------------------------------------------------------------------
# Graph helpers
# ---------------------------------------------------------------------------


def _build_graph() -> Graph:
    """Build a minimal graph with one job and two dispatch callers."""
    g = Graph()

    # Job node
    g.add_node(Node(id="job:App\\Jobs\\ProcessPayment", kind=NodeKind.JOB, name="ProcessPayment"))

    # Caller 1: PayOrderAction::handle
    g.add_node(
        Node(
            id="method:App\\Actions\\PayOrderAction::handle",
            kind=NodeKind.METHOD,
            name="handle",
            attributes={
                "class_fqn": "App\\Actions\\PayOrderAction",
                "file": "app/Actions/PayOrderAction.php",
                "line": 42,
            },
        )
    )

    # Caller 2: CheckoutController::store
    g.add_node(
        Node(
            id="method:App\\Http\\Controllers\\CheckoutController::store",
            kind=NodeKind.METHOD,
            name="store",
            attributes={
                "class_fqn": "App\\Http\\Controllers\\CheckoutController",
                "file": "app/Http/Controllers/CheckoutController.php",
                "line": 88,
            },
        )
    )

    # DISPATCHES edges (caller → job)
    g.add_edge(
        Edge(
            source="method:App\\Actions\\PayOrderAction::handle",
            target="job:App\\Jobs\\ProcessPayment",
            kind=EdgeKind.DISPATCHES,
        )
    )
    g.add_edge(
        Edge(
            source="method:App\\Http\\Controllers\\CheckoutController::store",
            target="job:App\\Jobs\\ProcessPayment",
            kind=EdgeKind.DISPATCHES,
        )
    )

    return g


# ---------------------------------------------------------------------------
# _resolve_job_id
# ---------------------------------------------------------------------------


class TestResolveJobId:
    def test_prefixed_id_that_exists(self) -> None:
        g = _build_graph()
        result = _resolve_job_id(g, "job:App\\Jobs\\ProcessPayment")
        assert result == "job:App\\Jobs\\ProcessPayment"

    def test_prefixed_id_that_does_not_exist(self) -> None:
        g = _build_graph()
        result = _resolve_job_id(g, "job:Nope\\Missing")
        assert result is None

    def test_fqn_without_prefix_finds_candidate(self) -> None:
        g = _build_graph()
        result = _resolve_job_id(g, "App\\Jobs\\ProcessPayment")
        assert result == "job:App\\Jobs\\ProcessPayment"

    def test_short_name_fallback_walks_nodes(self) -> None:
        g = _build_graph()
        result = _resolve_job_id(g, "ProcessPayment")
        assert result == "job:App\\Jobs\\ProcessPayment"

    def test_unknown_short_name_returns_none(self) -> None:
        g = _build_graph()
        result = _resolve_job_id(g, "UnknownJob")
        assert result is None


# ---------------------------------------------------------------------------
# FindJobsDispatchingTool.execute
# ---------------------------------------------------------------------------


class TestFindJobsDispatchingExecute:
    def setup_method(self) -> None:
        self.tool = FindJobsDispatchingTool()
        self.graph = _build_graph()
        self.ctx = _make_ctx(self.graph)

    def test_returns_sites_for_known_job(self) -> None:
        payload = FindJobsDispatchingInput(job="App\\Jobs\\ProcessPayment")
        result = self.tool.execute(payload, self.ctx)

        assert result.error is None
        assert result.total == 2
        assert result.returned == 2
        assert len(result.sites) == 2

    def test_sites_sorted_by_class_fqn_then_method(self) -> None:
        payload = FindJobsDispatchingInput(job="App\\Jobs\\ProcessPayment")
        result = self.tool.execute(payload, self.ctx)

        fqns = [s.class_fqn or "" for s in result.sites]
        assert fqns == sorted(fqns)

    def test_site_attributes_populated(self) -> None:
        payload = FindJobsDispatchingInput(job="App\\Jobs\\ProcessPayment")
        result = self.tool.execute(payload, self.ctx)

        by_class = {s.class_fqn: s for s in result.sites}
        action_site = by_class["App\\Actions\\PayOrderAction"]
        assert action_site.method == "handle"
        assert action_site.file == "app/Actions/PayOrderAction.php"
        assert action_site.line == 42

    def test_job_not_found_returns_error(self) -> None:
        payload = FindJobsDispatchingInput(job="App\\Jobs\\Ghost")
        result = self.tool.execute(payload, self.ctx)

        assert result.error is not None
        assert result.error_code == "job_not_found"
        assert result.total == 0

    def test_prefixed_id_resolves_correctly(self) -> None:
        payload = FindJobsDispatchingInput(job="job:App\\Jobs\\ProcessPayment")
        result = self.tool.execute(payload, self.ctx)

        assert result.error is None
        assert result.total == 2

    def test_no_dispatchers_returns_empty_sites(self) -> None:
        g = Graph()
        g.add_node(Node(id="job:App\\Jobs\\Orphan", kind=NodeKind.JOB, name="Orphan"))
        ctx = _make_ctx(g)
        payload = FindJobsDispatchingInput(job="App\\Jobs\\Orphan")
        result = self.tool.execute(payload, ctx)

        assert result.error is None
        assert result.total == 0
        assert result.sites == []

    def test_dangling_source_node_is_skipped(self) -> None:
        """Edge pointing to a non-existent source node is silently skipped."""
        g = Graph()
        g.add_node(Node(id="job:App\\Jobs\\X", kind=NodeKind.JOB, name="X"))
        g.add_edge(
            Edge(
                source="method:Missing::action",
                target="job:App\\Jobs\\X",
                kind=EdgeKind.DISPATCHES,
            )
        )
        ctx = _make_ctx(g)
        payload = FindJobsDispatchingInput(job="App\\Jobs\\X")
        result = self.tool.execute(payload, ctx)

        # The dangling edge is skipped — no crash, no sites
        assert result.error is None
        assert result.total == 0
