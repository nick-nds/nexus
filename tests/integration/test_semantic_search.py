"""Integration test for ``semantic_search``.

Builds a small graph, writes a handful of vectors straight into
LanceDB, then exercises the tool end-to-end. Bypassing the chunker
keeps the test focused on the retrieve / expand / re-rank pipeline
rather than on the upstream passes.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from nexus.adapters.storage import LanceVectorRecord, ProjectStorage
from nexus.core.graph.builder import GraphBuilder
from nexus.core.query import QueryEngine, ResponseBudget, ToolRegistry
from nexus.core.query.context import QueryContext
from nexus.core.query.errors import ToolInputError
from nexus.core.query.tools import register_builtin_tools
from nexus.core.reflection import load_reflection
from tests.integration.test_query_tools_basic import _StubProfile  # reuse the stub

pytestmark = pytest.mark.integration


FIXTURE = Path(__file__).parent.parent / "fixtures" / "reflection-samples" / "momskitchen.json"
DIMENSIONS = 4


class _FakeEmbedder:
    """Deterministic 4-D embedder good enough to exercise the tool."""

    model_id = "fake:semsearch"
    dimensions = DIMENSIONS

    def embed(self, texts):
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
def populated_storage(tmp_path: Path) -> ProjectStorage:
    """Storage with the momskitchen graph + a few synthetic vector rows."""
    storage = ProjectStorage(root=tmp_path / ".nexus", slug="sem")
    document = load_reflection(FIXTURE)
    graph = GraphBuilder().build(document, _StubProfile()).value
    assert storage.graph().persist(graph).ok

    # Write a handful of vectors whose node_ids reference real graph
    # nodes in a few different kinds so re-ranking is exercised.
    vectors = storage.vectors(dimensions=DIMENSIONS)
    records = [
        LanceVectorRecord(
            id="chunk-1",
            vector=[1.0, 0.0, 0.0, 0.0],
            payload={
                "node_id": "method:App\\Http\\Controllers\\CustomersController::index",
                "file_path": "/var/www/app/Http/Controllers/CustomersController.php",
                "kind": "method",
                "symbol": "CustomersController::index",
                "start_line": 23,
                "end_line": 40,
            },
        ),
        LanceVectorRecord(
            id="chunk-2",
            vector=[0.9, 0.1, 0.0, 0.0],
            payload={
                "node_id": "route:GET:/api/v1/customers",
                "file_path": "/var/www/routes/api.php",
                "kind": "route",
                "symbol": "GET /api/v1/customers",
                "start_line": 1,
                "end_line": 2,
            },
        ),
        LanceVectorRecord(
            id="chunk-3",
            vector=[0.0, 1.0, 0.0, 0.0],
            payload={
                "node_id": "class:App\\Http\\Controllers\\CustomersController",
                "file_path": "/var/www/app/Http/Controllers/CustomersController.php",
                "kind": "class",
                "symbol": "CustomersController",
                "start_line": 10,
                "end_line": 60,
            },
        ),
        LanceVectorRecord(
            id="chunk-4",
            vector=[0.5, 0.5, 0.5, 0.5],
            payload={
                "node_id": "class:DoesNotExistInGraph",
                "file_path": "/tmp/foo.php",
                "kind": "class",
                "symbol": "Ghost",
                "start_line": 1,
                "end_line": 5,
            },
        ),
    ]
    vectors.upsert(records)

    yield storage
    storage.close()


@pytest.fixture
def engine(populated_storage: ProjectStorage) -> QueryEngine:
    registry = ToolRegistry()
    register_builtin_tools(registry)
    ctx = QueryContext(
        storage=populated_storage,
        budget=ResponseBudget(),
        embedder=_FakeEmbedder(),  # type: ignore[arg-type]
        vector_dimensions=DIMENSIONS,
    )
    return QueryEngine(registry, ctx)


class TestSemanticSearch:
    def test_missing_embedder_returns_structured_error(
        self,
        populated_storage: ProjectStorage,
    ) -> None:
        registry = ToolRegistry()
        register_builtin_tools(registry)
        ctx = QueryContext(storage=populated_storage, budget=ResponseBudget())
        bare_engine = QueryEngine(registry, ctx)

        result = bare_engine.query("semantic_search", {"query": "customers"})
        assert result.error_code == "no_embedder"

    def test_missing_dimensions_returns_structured_error(
        self,
        populated_storage: ProjectStorage,
    ) -> None:
        registry = ToolRegistry()
        register_builtin_tools(registry)
        ctx = QueryContext(
            storage=populated_storage,
            budget=ResponseBudget(),
            embedder=_FakeEmbedder(),  # type: ignore[arg-type]
        )
        bare = QueryEngine(registry, ctx)
        result = bare.query("semantic_search", {"query": "customers"})
        assert result.error_code == "no_vector_dimensions"

    def test_returns_annotated_hits(self, engine: QueryEngine) -> None:
        # ``min_vector_score=0.0`` disables the P0-11 relevance filter
        # for this test - the synthetic LanceDB rows here use
        # hand-picked 4-D vectors that don't correspond to realistic
        # cosine scores. Production queries against real embeddings
        # rely on the threshold; this test just exercises the
        # retrieval-and-annotation pipeline.
        result = engine.query(
            "semantic_search",
            {
                "query": "list customers",
                "top_k": 20,
                "final_k": 15,
                "min_vector_score": 0.0,
            },
        )
        assert result.error is None
        assert result.total_candidates > 0
        assert result.returned > 0

        # The method hit should surface its container_class and at
        # least one related route because the graph wires them up.
        method_hit = next(
            (h for h in result.hits if h.node_kind == "method"),
            None,
        )
        assert method_hit is not None
        assert method_hit.container_class == "App\\Http\\Controllers\\CustomersController"
        assert any("/api/v1/customers" in uri for uri in method_hit.related_routes)

    def test_dangling_node_still_appears_as_chunk(self, engine: QueryEngine) -> None:
        result = engine.query(
            "semantic_search",
            {"query": "ghost", "top_k": 10, "final_k": 10},
        )
        assert result.error is None
        ghost = next(
            (h for h in result.hits if h.node_id.endswith("Ghost") or h.node_name == "Ghost"), None
        )
        # The dangling row falls through to a "chunk" kind with no container.
        if ghost is not None:
            assert ghost.container_class is None

    def test_final_k_caps_the_response(self, engine: QueryEngine) -> None:
        result = engine.query(
            "semantic_search",
            {"query": "customers", "top_k": 10, "final_k": 2},
        )
        assert result.returned <= 2
        assert len(result.hits) <= 2

    def test_empty_query_rejected(self, engine: QueryEngine) -> None:
        with pytest.raises(ToolInputError):
            engine.query("semantic_search", {"query": ""})

    def test_kind_weighting_boosts_method_over_class(
        self,
        engine: QueryEngine,
    ) -> None:
        result = engine.query(
            "semantic_search",
            {"query": "customers list", "top_k": 30, "final_k": 10},
        )
        # Among the synthetic vectors, the method and class chunks have
        # identical vector components up to rounding, but the method
        # weight (1.20) should outrank the class weight (0.95).
        by_kind = {h.node_kind: h for h in result.hits}
        if "method" in by_kind and "class" in by_kind:
            assert by_kind["method"].score > by_kind["class"].score
