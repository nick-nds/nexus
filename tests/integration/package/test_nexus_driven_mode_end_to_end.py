"""Nexus-driven mode: composer install runs in scratch.

These tests exercise the orchestrator's nexus-driven path end-to-end:
generate scratch composer.json, run composer install, boot Testbench,
run nexus:extract-package, normalize paths, ingest into the graph.

Known bugs may cause assertions to fail - that's intentional. Each
failure documents a regression the fix dispatch must close.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from nexus.adapters.package.composer_metadata import read_composer_metadata
from nexus.pipeline.package_indexer import IndexMode, PackageIndexer
from tests.integration.package.conftest import EXTRACTOR_ROOT, skip_unless_integration

pytestmark = pytest.mark.integration


@skip_unless_integration
def test_cold_run_indexes_package_and_writes_meta(fixture_clone: Path, tmp_path: Path) -> None:
    """Full cold path: composer install → extract → ingest → meta written."""
    indexer = PackageIndexer(
        cache_root=tmp_path / "cache",
        nexus_root=tmp_path / "nexus",
        extractor_root=EXTRACTOR_ROOT,
    )

    meta = read_composer_metadata(fixture_clone)
    result = indexer.index(meta)

    assert result.mode == IndexMode.NEXUS_DRIVEN
    assert result.slug == "nexus-fixtures--sample"
    assert result.project_dir.is_dir()

    project_meta = json.loads((result.project_dir / "meta.json").read_text())
    assert project_meta["kind"] == "package"
    assert project_meta["schema_version"] == "1.1"
    assert project_meta["build_mode"] == "nexus-driven"
    assert project_meta["source_path"] == str(fixture_clone.resolve())
    assert project_meta["package"]["vendor"] == "nexus-fixtures"
    assert project_meta["package"]["name"] == "sample"
    assert project_meta["package"]["version"] == "1.2.0"
    assert project_meta["package"]["license"] == "MIT"
    assert len(project_meta["package"]["authors"]) == 2


@skip_unless_integration
def test_cold_run_writes_scratch_manifest(fixture_clone: Path, tmp_path: Path) -> None:
    """Successful cold run writes manifest.json (cache-hit signal)."""
    indexer = PackageIndexer(
        cache_root=tmp_path / "cache",
        nexus_root=tmp_path / "nexus",
        extractor_root=EXTRACTOR_ROOT,
    )
    meta = read_composer_metadata(fixture_clone)
    indexer.index(meta)

    scratch = tmp_path / "cache" / "package-builds" / "nexus-fixtures--sample" / "1.2.0"
    manifest = json.loads((scratch / "manifest.json").read_text())
    assert "fingerprint" in manifest
    assert "composer_install_at" in manifest
    assert "extracted_at" in manifest


@skip_unless_integration
def test_warm_run_skips_composer_install(fixture_clone: Path, tmp_path: Path) -> None:
    """Cache hit on warm rerun - should be at least 5x faster."""
    indexer = PackageIndexer(
        cache_root=tmp_path / "cache",
        nexus_root=tmp_path / "nexus",
        extractor_root=EXTRACTOR_ROOT,
    )
    meta = read_composer_metadata(fixture_clone)

    t0 = time.monotonic()
    indexer.index(meta)
    cold = time.monotonic() - t0

    t1 = time.monotonic()
    indexer.index(meta)
    warm = time.monotonic() - t1

    assert warm < cold / 5, f"warm {warm:.1f}s vs cold {cold:.1f}s - cache hit broken"


@skip_unless_integration
def test_packages_own_classes_reach_the_graph(fixture_clone: Path, tmp_path: Path) -> None:
    """SPEC: scope-aware extraction emits the target package's classes.

    KNOWN BUG at the time this test was written: Phase B + Phase C
    both emit zero class entries because the scope-path comparison
    is symlink-mismatched. This test pins the bug down - it should
    PASS after the fix lands.
    """
    indexer = PackageIndexer(
        cache_root=tmp_path / "cache",
        nexus_root=tmp_path / "nexus",
        extractor_root=EXTRACTOR_ROOT,
    )
    meta = read_composer_metadata(fixture_clone)
    result = indexer.index(meta)

    reflection = json.loads(result.reflection_path.read_text())
    class_names = [
        c["reflection"]["name"] for c in reflection["sections"].get("classes", {}).get("items", [])
    ]

    expected = {
        "NexusFixtures\\Sample\\Models\\SampleModel",
        "NexusFixtures\\Sample\\Events\\SampleEvent",
        "NexusFixtures\\Sample\\Listeners\\SampleListener",
        "NexusFixtures\\Sample\\Jobs\\SampleJob",
        "NexusFixtures\\Sample\\Http\\Controllers\\SampleController",
    }
    missing = expected - set(class_names)
    assert not missing, (
        f"Package's own classes missing from the index: {sorted(missing)}. "
        f"Got {len(class_names)} classes total."
    )


@skip_unless_integration
def test_workbench_routes_filtered_from_index(fixture_clone: Path, tmp_path: Path) -> None:
    """SPEC: NamespaceExclusionFilter drops Workbench/Orchestra routes."""
    indexer = PackageIndexer(
        cache_root=tmp_path / "cache",
        nexus_root=tmp_path / "nexus",
        extractor_root=EXTRACTOR_ROOT,
    )
    meta = read_composer_metadata(fixture_clone)
    result = indexer.index(meta)

    reflection = json.loads(result.reflection_path.read_text())
    uris = [r["uri"] for r in reflection["sections"].get("routes", {}).get("items", [])]

    assert "/sample" in uris or "sample" in uris, "Package's own route missing"
    assert "workbench-fixture" not in uris, "Workbench fixture route leaked into the index"


@skip_unless_integration
def test_file_paths_are_package_relative(fixture_clone: Path, tmp_path: Path) -> None:
    """SPEC: PathNormalizer rewrites scratch paths to package-relative."""
    indexer = PackageIndexer(
        cache_root=tmp_path / "cache",
        nexus_root=tmp_path / "nexus",
        extractor_root=EXTRACTOR_ROOT,
    )
    meta = read_composer_metadata(fixture_clone)
    result = indexer.index(meta)

    reflection = json.loads(result.reflection_path.read_text())
    classes = reflection["sections"].get("classes", {}).get("items", [])
    for c in classes:
        file = c["reflection"].get("file") or ""
        # Package-relative paths shouldn't start with /scratch or /tmp
        assert not file.startswith("/tmp/"), f"Unnormalized path: {file}"
        assert "scratch" not in file, f"Path leaks scratch dir: {file}"


@skip_unless_integration
def test_bindings_are_scope_filtered(fixture_clone: Path, tmp_path: Path) -> None:
    """SPEC: Only package-own bindings appear; Laravel core must not leak.

    KNOWN BUG at the time this test was written: Phase A registries are
    not scope-filtered, so ~218 Laravel core bindings leak into the
    package index. This test pins the bug - it should PASS after the fix.
    """
    indexer = PackageIndexer(
        cache_root=tmp_path / "cache",
        nexus_root=tmp_path / "nexus",
        extractor_root=EXTRACTOR_ROOT,
    )
    meta = read_composer_metadata(fixture_clone)
    result = indexer.index(meta)

    reflection = json.loads(result.reflection_path.read_text())
    bindings_section = reflection["sections"].get("bindings", {})
    bindings = bindings_section.get("bindings", [])

    # All bindings must belong to the package namespace or be empty.
    # Laravel core abstract names like "auth", "cache", "db" must not appear.
    core_leaked = [
        b["abstract"]
        for b in bindings
        if not b["abstract"].startswith("NexusFixtures\\")
        and not b["abstract"].startswith("nexus-fixtures")
    ]
    assert not core_leaked, (
        f"Laravel core bindings leaked into the package index "
        f"({len(core_leaked)} entries). First 5: {core_leaked[:5]}"
    )


@skip_unless_integration
def test_closure_routes_do_not_leak_from_core(fixture_clone: Path, tmp_path: Path) -> None:
    """SPEC: Laravel core closure routes must not appear in the package index.

    KNOWN BUG at the time this test was written: the namespace filter only
    catches class-based handlers, so ~2 Laravel core /storage/{path}
    closure routes still pass through. This test pins the bug.
    """
    indexer = PackageIndexer(
        cache_root=tmp_path / "cache",
        nexus_root=tmp_path / "nexus",
        extractor_root=EXTRACTOR_ROOT,
    )
    meta = read_composer_metadata(fixture_clone)
    result = indexer.index(meta)

    reflection = json.loads(result.reflection_path.read_text())
    routes = reflection["sections"].get("routes", {}).get("items", [])

    closure_routes = [
        r["uri"]
        for r in routes
        if r.get("action", {}).get("kind") == "closure"
        and not r["uri"].startswith("/sample")
        and r["uri"] != "sample"
    ]
    assert not closure_routes, (
        f"Laravel core closure routes leaked into the package index: {closure_routes}"
    )
