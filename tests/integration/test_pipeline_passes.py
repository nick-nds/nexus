"""Integration tests for the pipeline passes.

Wires a full pipeline against a temporary Laravel-ish project and a
fake embedder so every pass runs against real adapters (real SQLite,
real LanceDB, real chunker) without needing an actual PHP install.
The reflection file is the committed demoapp fixture.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest
from nexus.adapters.embedders.cache import EmbeddingCache
from nexus.adapters.storage import ProjectStorage
from nexus.core.outcome import Warning
from nexus.pipeline import Pipeline, PipelineContext
from nexus.pipeline.passes import (
    BuildGraphPass,
    ChunkPass,
    EmbedAndPersistPass,
    RunExtractorPass,
)

pytestmark = pytest.mark.integration


FIXTURE_REFLECTION = (
    Path(__file__).parent.parent / "fixtures" / "reflection-samples" / "demoapp.json"
)


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StubProfile:
    name: str = "stub"
    custom_bases: dict[str, str] = None  # type: ignore[assignment]
    custom_suffixes: dict[str, str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.custom_bases is None:
            object.__setattr__(self, "custom_bases", {})
        if self.custom_suffixes is None:
            object.__setattr__(self, "custom_suffixes", {})


class StubExtractor:
    """Copies the committed reflection fixture into the output path.

    Tests don't need to run real PHP; the reflection document is the
    only contract between the Python side and the extractor, so
    substituting this stub exercises the pipeline end-to-end without
    a PHP install.
    """

    def extract(self, project_path: Path, *, output_path: Path):
        from nexus.adapters.extractor.php_subprocess import ExtractorResult

        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURE_REFLECTION, output_path)
        return ExtractorResult(
            output_path=output_path,
            exit_code=0,
            stdout="",
            stderr="",
        )


class FakeEmbedder:
    """Deterministic embedder that returns fixed-length dummy vectors."""

    model_id = "fake:test"
    dimensions = 4

    def __init__(self) -> None:
        self.call_count = 0
        self.last_batch: list[str] | None = None

    def embed(self, texts):
        self.call_count += 1
        texts_list = list(texts)
        self.last_batch = texts_list
        # A trivial deterministic hash → vector mapping so runs are
        # reproducible without any ML dependency.
        return [
            [
                float(len(t) % 7),
                float(sum(c for c in t.encode()[:20]) % 11) / 10.0,
                float(t.count(" ")) / max(len(t), 1),
                1.0,
            ]
            for t in texts_list
        ]

    def estimate_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)


@pytest.fixture
def project_path(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / "artisan").write_text("#!/usr/bin/env php\n")
    # Create a couple of real PHP files the chunker can read.
    src_dir = project / "app" / "Services"
    src_dir.mkdir(parents=True)
    (src_dir / "UserService.php").write_text(
        "<?php\nnamespace App\\Services;\n"
        "final class UserService {\n"
        "    public function create(array $data) { return 42; }\n"
        "}\n",
    )
    return project


@pytest.fixture
def ctx(tmp_path: Path, project_path: Path) -> PipelineContext:
    storage = ProjectStorage(root=tmp_path / ".nexus", slug="test")
    return PipelineContext(
        project_path=project_path,
        storage=storage,
        profile=StubProfile(),
    )


# ---------------------------------------------------------------------------
# RunExtractorPass
# ---------------------------------------------------------------------------


class TestRunExtractorPass:
    def test_loads_reflection_into_context(self, ctx: PipelineContext) -> None:
        pass_ = RunExtractorPass(extractor=StubExtractor())  # type: ignore[arg-type]

        pass_.run(ctx)

        assert ctx.ok()
        assert ctx.reflection is not None
        assert ctx.reflection.schema_version.startswith("2.")


# ---------------------------------------------------------------------------
# BuildGraphPass
# ---------------------------------------------------------------------------


class TestBuildGraphPass:
    def test_builds_graph_from_reflection(self, ctx: PipelineContext) -> None:
        RunExtractorPass(extractor=StubExtractor()).run(ctx)  # type: ignore[arg-type]

        BuildGraphPass().run(ctx)

        assert ctx.ok()
        assert ctx.graph is not None
        assert len(ctx.graph.nodes) > 0
        assert len(ctx.graph.edges) > 0

    def test_fails_without_reflection(self, ctx: PipelineContext) -> None:
        BuildGraphPass().run(ctx)

        assert not ctx.ok()
        assert any(e.code == "no_reflection" for e in ctx.errors)


# ---------------------------------------------------------------------------
# ChunkPass
# ---------------------------------------------------------------------------


class TestChunkPass:
    def test_empty_without_reflection(self, ctx: PipelineContext) -> None:
        ChunkPass().run(ctx)

        assert ctx.chunks == []
        assert any(w.code == "no_classes_section" for w in ctx.warnings)


# ---------------------------------------------------------------------------
# EmbedAndPersistPass
# ---------------------------------------------------------------------------


class TestEmbedAndPersistPass:
    def test_persists_graph_without_embedder(self, ctx: PipelineContext) -> None:
        RunExtractorPass(extractor=StubExtractor()).run(ctx)  # type: ignore[arg-type]
        BuildGraphPass().run(ctx)

        EmbedAndPersistPass().run(ctx)

        assert any(w.code == "no_embedder" for w in ctx.warnings)
        store = ctx.storage.graph()
        assert store.node_count() > 0

    def test_end_to_end_with_fake_embedder(self, tmp_path: Path, project_path: Path) -> None:
        storage = ProjectStorage(root=tmp_path / ".nexus", slug="e2e")
        cache = EmbeddingCache(root=tmp_path / "cache" / "embeddings")
        embedder = FakeEmbedder()

        ctx = PipelineContext(
            project_path=project_path,
            storage=storage,
            profile=StubProfile(),
            embedder=embedder,  # type: ignore[arg-type]
        )

        pipeline = Pipeline(
            [
                RunExtractorPass(extractor=StubExtractor()),  # type: ignore[arg-type]
                BuildGraphPass(),
                ChunkPass(),
                EmbedAndPersistPass(cache=cache),
            ],
        )

        result = pipeline.run(ctx)

        assert result.ok, [e.message for e in ctx.errors]
        assert ctx.graph is not None

        # Graph is persisted.
        graph_store = ctx.storage.graph()
        assert graph_store.node_count() == len(ctx.graph.nodes)
        assert graph_store.edge_count() == len(ctx.graph.edges)

        # Vectors are upserted with the right dimensionality.
        vec_store = ctx.storage.vectors(dimensions=embedder.dimensions)
        assert vec_store.count() == len(ctx.chunks)

        # Meta is written with the embedder id.
        meta = ctx.storage.read_meta()
        assert meta is not None
        assert meta.embedder_id == "fake:test"
        assert meta.node_count == len(ctx.graph.nodes)

        # Cache is populated - a second run reuses every vector.
        before_calls = embedder.call_count
        # Reset graph/chunks so we re-run end-to-end.
        ctx2 = PipelineContext(
            project_path=project_path,
            storage=storage,
            profile=StubProfile(),
            embedder=embedder,  # type: ignore[arg-type]
        )
        pipeline.run(ctx2)

        # The embedder's second call count should be unchanged - all
        # texts hit the cache.
        assert embedder.call_count == before_calls, (
            f"Expected full cache hit but embedder was called "
            f"{embedder.call_count - before_calls} extra time(s)"
        )

    def test_fails_without_graph(self, ctx: PipelineContext) -> None:
        EmbedAndPersistPass().run(ctx)
        assert not ctx.ok()

    def test_embedder_error_is_captured(self, tmp_path: Path, project_path: Path) -> None:
        class BoomEmbedder:
            model_id = "boom:test"
            dimensions = 4

            def embed(self, texts):
                raise RuntimeError("oops")

            def estimate_tokens(self, text: str) -> int:
                return 1

        # Go straight to EmbedAndPersistPass with a pre-built graph
        # and a synthetic chunk so there's guaranteed work for the
        # embedder to blow up on. The earlier passes would produce
        # zero chunks because the reflection's file paths don't
        # exist in the test tmp_path.
        from nexus.core.chunking import Chunk, ChunkKind
        from nexus.core.graph.graph import Graph
        from nexus.core.graph.types import Node, NodeKind

        storage = ProjectStorage(root=tmp_path / ".nexus", slug="err")
        graph = Graph()
        graph.add_node(Node(id="class:X", kind=NodeKind.CLASS, name="X"))

        chunk = Chunk(
            id="c1",
            kind=ChunkKind.METHOD,
            file_path=Path("/tmp/x.php"),
            start_byte=0,
            end_byte=10,
            start_line=1,
            end_line=1,
            text="<?php x;",
            node_id="class:X",
            symbol="x",
        )

        ctx = PipelineContext(
            project_path=project_path,
            storage=storage,
            profile=StubProfile(),
            embedder=BoomEmbedder(),  # type: ignore[arg-type]
        )
        ctx.graph = graph
        ctx.chunks = [chunk]

        EmbedAndPersistPass(cache=EmbeddingCache(root=tmp_path / "cache")).run(ctx)

        assert not ctx.ok()
        assert any(e.code == "embedder_failed" for e in ctx.errors)


# ---------------------------------------------------------------------------
# Warnings on partial reflection
# ---------------------------------------------------------------------------


class TestPartialReflection:
    def test_build_graph_collects_builder_warnings(self, ctx: PipelineContext) -> None:
        RunExtractorPass(extractor=StubExtractor()).run(ctx)  # type: ignore[arg-type]
        BuildGraphPass().run(ctx)

        # DemoApp's reflection has closure listeners that the
        # graph builder records as warnings. They should be surfaced.
        closure_warnings = [
            w for w in ctx.warnings if isinstance(w, Warning) and w.code == "closure_listener"
        ]
        assert len(closure_warnings) > 0
