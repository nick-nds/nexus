"""Phase 2 end-to-end smoke test.

Exercises every Phase 2 component on a real reflection document:

1. Load the committed demoapp reflection.json via the Pydantic
   loader.
2. Load the built-in profiles and run the auto-detector against a
   synthetic project tree.
3. Build a typed graph from the reflection + an empty profile via
   :class:`GraphBuilder`.
4. Open a per-project :class:`ProjectStorage` in a temp directory,
   persist the graph to SQLite via :class:`SqliteGraphStore`, reload
   it, and assert node counts round-trip.
5. Open the :class:`LanceDbVectorStore`, insert a handful of mock
   vectors, run a search, and assert the ranking.
6. Write a :class:`ProjectMeta` and read it back.

Run from the repo root with the venv activated:

    .venv/bin/python examples/phase2_smoketest.py

This is not a replacement for the test suite - it's the "does the
whole package hang together" check that the Phase 2 acceptance
criteria in ``internal_docs/PHASE-2-core-engine.md`` asks for.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from nexus.adapters.storage import (
    LanceVectorRecord,
    ProjectMeta,
    ProjectStorage,
)
from nexus.config import GlobalConfig
from nexus.core.graph.builder import GraphBuilder
from nexus.core.reflection import load_reflection
from nexus.plugins import PluginRegistry, load_plugins
from nexus.profiles import ProfileDetector, load_builtin_profiles


@dataclass(frozen=True)
class EmptyProfile:
    """Structural stub for the graph builder."""

    name: str = "smoketest"
    custom_bases: dict[str, str] = None  # type: ignore[assignment]
    custom_suffixes: dict[str, str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.custom_bases is None:
            object.__setattr__(self, "custom_bases", {})
        if self.custom_suffixes is None:
            object.__setattr__(self, "custom_suffixes", {})


def step(name: str) -> None:
    print(f"\n=== {name} ===")


def main() -> None:
    repo_root = Path(__file__).parent.parent
    fixture = repo_root / "tests" / "fixtures" / "reflection-samples" / "demoapp.json"

    step("1. Load reflection document")
    document = load_reflection(fixture)
    print(f"   project      : {document.project.name}")
    print(f"   laravel      : {document.project.laravel_version}")
    print(f"   schema ver   : {document.schema_version}")
    assert document.sections.routes is not None
    print(f"   routes       : {document.sections.routes.count}")
    assert document.sections.classes is not None
    print(f"   classes      : {document.sections.classes.count}")

    step("2. Load built-in profiles + run detector")
    profiles = load_builtin_profiles()
    print(f"   built-ins    : {profiles.names()}")

    with tempfile.TemporaryDirectory() as tmp:
        fake_project = Path(tmp) / "fake-project"
        (fake_project / "app" / "Http" / "Controllers").mkdir(parents=True)
        (fake_project / "app" / "Models").mkdir(parents=True)
        (fake_project / "composer.json").write_text(
            '{"require": {"laravel/framework": "^12.0"}}',
        )

        detector = ProfileDetector(builtins=profiles)
        matches = detector.detect(fake_project)
        print(f"   top match    : {matches[0].profile.name} ({matches[0].score:.0f}%)")

    step("3. Build graph from reflection")
    builder = GraphBuilder()
    result = builder.build(document, EmptyProfile())
    print(f"   nodes        : {len(result.value.nodes)}")
    print(f"   edges        : {len(result.value.edges)}")
    print(f"   warnings     : {len(result.value.warnings)}")
    print(f"   ok           : {result.ok}")

    step("4. Persist and reload through SQLite graph store")
    with tempfile.TemporaryDirectory() as tmp:
        storage = ProjectStorage(root=Path(tmp), slug="smoketest")
        graph_store = storage.graph()

        persist_result = graph_store.persist(result.value)
        assert persist_result.ok
        print(f"   persist ok   : {persist_result.ok}")
        print(f"   nodes stored : {graph_store.node_count()}")
        print(f"   edges stored : {graph_store.edge_count()}")

        reloaded = graph_store.load()
        assert len(reloaded.nodes) == len(result.value.nodes)
        print(f"   reload match : {len(reloaded.nodes)} nodes")

        step("5. Exercise LanceDB vector store")
        vec_store = storage.vectors(dimensions=4)
        vec_store.upsert(
            [
                LanceVectorRecord(
                    id="chunk-1",
                    vector=[1.0, 0.0, 0.0, 0.0],
                    payload={"src": "App/Models/User.php", "line": 10},
                ),
                LanceVectorRecord(
                    id="chunk-2",
                    vector=[0.0, 1.0, 0.0, 0.0],
                    payload={"src": "App/Models/Post.php", "line": 15},
                ),
            ],
        )
        print(f"   vectors      : {vec_store.count()}")
        hits = vec_store.search([1.0, 0.0, 0.0, 0.0], top_k=2)
        print(f"   top hit      : {hits[0].id} (score={hits[0].score:.3f})")
        assert hits[0].id == "chunk-1"

        step("6. Write and read ProjectMeta")
        meta = ProjectMeta(
            project_slug="smoketest",
            project_path="/tmp/fake",
            detected_profile="laravel-default",
            profile_match_score=100.0,
            all_match_scores={"laravel-default": 100.0},
            laravel_version=document.project.laravel_version,
            node_count=len(result.value.nodes),
            edge_count=len(result.value.edges),
        )
        storage.write_meta(meta)
        loaded_meta = storage.read_meta()
        assert loaded_meta is not None
        print(f"   meta profile : {loaded_meta.detected_profile}")
        print(f"   meta version : {loaded_meta.laravel_version}")

        storage.close()

    step("7. Plugin loader smoke")
    registry = PluginRegistry()
    plugin_result = load_plugins(registry)
    print(f"   loaded       : {list(plugin_result.loaded) or '<none>'}")
    print(f"   failed       : {plugin_result.failed or '<none>'}")

    step("8. Global config defaults")
    cfg = GlobalConfig.defaults()
    print(f"   embedder     : {cfg.embedder.provider}/{cfg.embedder.model}")
    print(f"   cost gate    : ${cfg.cost.confirm_above_usd}")

    print("\nPhase 2 smoke test completed successfully.")


if __name__ == "__main__":
    main()
