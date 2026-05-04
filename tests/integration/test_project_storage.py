"""Integration tests for ProjectStorage."""

from __future__ import annotations

from pathlib import Path

import pytest
from nexus.adapters.storage import (
    ProjectMeta,
    ProjectStorage,
    ProjectStorageError,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def storage(tmp_path: Path):
    root = tmp_path / ".nexus"
    s = ProjectStorage(root=root, slug="test-project")
    yield s
    s.close()


class TestDirectoryLayout:
    def test_initialise_creates_project_dir(self, storage: ProjectStorage) -> None:
        storage.initialise()

        assert storage.project_dir.is_dir()
        # Parent `projects/` dir created too.
        assert storage.project_dir.parent.is_dir()

    def test_initialise_is_idempotent(self, storage: ProjectStorage) -> None:
        storage.initialise()
        storage.initialise()
        assert storage.project_dir.is_dir()

    def test_paths_are_stable(self, storage: ProjectStorage) -> None:
        assert storage.graph_path.name == "graph.sqlite"
        assert storage.vectors_path.name == "vectors"
        assert storage.reflection_path.name == "reflection.json"
        assert storage.meta_path.name == "meta.json"
        # Each path is under the project_dir.
        for p in (
            storage.graph_path,
            storage.vectors_path,
            storage.reflection_path,
            storage.meta_path,
        ):
            assert p.parent == storage.project_dir


class TestStoreAccessors:
    def test_graph_store_is_lazy(self, storage: ProjectStorage) -> None:
        # Before calling .graph(), the file doesn't exist.
        assert not storage.graph_path.is_file()

        store = storage.graph()
        # Accessing the store creates the file.
        assert storage.graph_path.is_file()
        # Same call returns the same instance.
        assert storage.graph() is store

    def test_vector_store_is_lazy(self, storage: ProjectStorage) -> None:
        assert not storage.vectors_path.exists()

        store = storage.vectors(dimensions=4)
        # Constructing the store does not touch disk; the directory is
        # created on the first upsert/count/search/delete call.
        assert not storage.vectors_path.exists()
        assert storage.vectors(dimensions=4) is store

        # After an actual operation, the directory exists.
        assert store.count() == 0
        assert storage.vectors_path.is_dir()

    def test_close_releases_stores(self, storage: ProjectStorage) -> None:
        storage.graph()
        storage.vectors(dimensions=4)

        storage.close()

        # Internal handles are cleared — next accessor call builds fresh.
        assert storage._graph is None
        assert storage._vectors is None


class TestMetaJsonIO:
    def test_read_meta_returns_none_for_missing(self, storage: ProjectStorage) -> None:
        assert storage.read_meta() is None

    def test_round_trip(self, storage: ProjectStorage) -> None:
        meta = ProjectMeta(
            project_slug="test-project",
            project_path="/home/user/my-app",
            detected_profile="laravel-default",
            profile_source="auto",
            profile_match_score=87.5,
            all_match_scores={"laravel-default": 87.5, "laravel-api": 40.0},
            laravel_version="12.55.1",
            node_count=120,
            edge_count=384,
            embedder_id="sentence_transformers:all-MiniLM-L6-v2",
        )

        storage.write_meta(meta)
        reloaded = storage.read_meta()

        assert reloaded is not None
        assert reloaded.project_slug == "test-project"
        assert reloaded.detected_profile == "laravel-default"
        assert reloaded.all_match_scores["laravel-default"] == 87.5

    def test_atomic_write_leaves_no_tmp_file(self, storage: ProjectStorage) -> None:
        storage.write_meta(
            ProjectMeta(project_slug="test-project", project_path="/tmp/x"),
        )
        tmp = storage.meta_path.with_suffix(".json.tmp")
        assert not tmp.exists()

    def test_invalid_json_raises(self, storage: ProjectStorage) -> None:
        storage.initialise()
        storage.meta_path.write_text("{ not valid json")

        with pytest.raises(ProjectStorageError):
            storage.read_meta()

    def test_schema_mismatch_raises(self, storage: ProjectStorage) -> None:
        storage.initialise()
        # Missing required field (project_slug).
        storage.meta_path.write_text('{"schema_version": "1.0"}')

        with pytest.raises(ProjectStorageError):
            storage.read_meta()
