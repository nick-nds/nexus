"""Workbench/Testbench/Orchestra noise must be filtered from the index."""

from __future__ import annotations

import json
from pathlib import Path

from nexus.adapters.package.composer_metadata import read_composer_metadata
from nexus.pipeline.package_indexer import PackageIndexer
from tests.integration.package.conftest import EXTRACTOR_ROOT, skip_unless_integration


@skip_unless_integration
def test_workbench_routes_and_classes_excluded(fixture_clone: Path, tmp_path: Path) -> None:
    indexer = PackageIndexer(
        cache_root=tmp_path / "cache",
        nexus_root=tmp_path / "nexus",
        extractor_root=EXTRACTOR_ROOT,
    )
    meta = read_composer_metadata(fixture_clone)
    result = indexer.index(meta)

    doc = json.loads(result.reflection_path.read_text())

    routes_section = doc["sections"].get("routes") or {}
    routes = routes_section.get("items", [])
    uris = [r["uri"] for r in routes]
    # The fixture's own /sample route is present.
    assert any("sample" in u for u in uris), f"Sample route missing; got {uris}"
    # The Workbench fixture route MUST be filtered.
    assert not any("workbench-fixture" in u for u in uris), (
        f"Workbench fixture route leaked into the index: {uris}"
    )

    classes_section = doc["sections"].get("classes") or {}
    classes = classes_section.get("items", [])
    names = [c["reflection"]["name"] for c in classes]
    # Package's own service provider is present.
    assert "NexusFixtures\\Sample\\SamplePackageServiceProvider" in names
    # No Workbench / Orchestra class survives the filter.
    for name in names:
        assert not name.startswith("Workbench\\"), f"Workbench class leaked: {name}"
        assert not name.startswith("Orchestra\\Testbench\\"), f"Testbench class leaked: {name}"
        assert not name.startswith("Orchestra\\Workbench\\"), (
            f"Orchestra Workbench class leaked: {name}"
        )
