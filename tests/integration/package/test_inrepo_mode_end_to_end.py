"""In-repo mode: composer install runs inside the fixture before extraction."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from nexus.adapters.package.composer_metadata import read_composer_metadata
from nexus.pipeline.package_indexer import IndexMode, PackageIndexer
from tests.integration.package.conftest import EXTRACTOR_ROOT, skip_unless_integration

pytestmark = pytest.mark.integration


def _install_extractor_into_fixture(fixture_dir: Path) -> None:
    """Add a composer path-repo for the extractor and install dev deps in-place."""
    # Add a 'repositories' entry to the fixture's composer.json so the
    # extractor can be required as a dev dep without hitting Packagist.
    composer_json = fixture_dir / "composer.json"
    payload = json.loads(composer_json.read_text())
    payload.setdefault("repositories", []).append(
        {
            "type": "path",
            "url": str(EXTRACTOR_ROOT.resolve()),
            "options": {"symlink": True},
        }
    )
    payload.setdefault("require-dev", {})["nexus/extractor-php"] = "*"
    payload["require-dev"]["orchestra/testbench"] = "^8.0|^9.0|^10.0|^11.0"
    # Force dev stability so path-repo dev-branch packages are resolvable.
    payload["minimum-stability"] = "dev"
    payload["prefer-stable"] = True
    composer_json.write_text(json.dumps(payload, indent=2))

    subprocess.run(
        ["composer", "install", "--no-interaction"],
        cwd=fixture_dir,
        check=True,
        capture_output=True,
    )


@skip_unless_integration
def test_inrepo_mode_extracts_and_ingests(fixture_clone: Path, tmp_path: Path) -> None:
    """When the user has already composer-installed extractor + testbench."""
    _install_extractor_into_fixture(fixture_clone)

    meta = read_composer_metadata(fixture_clone)
    indexer = PackageIndexer(
        cache_root=tmp_path / "cache",
        nexus_root=tmp_path / "nexus",
        extractor_root=EXTRACTOR_ROOT,
    )
    result = indexer.index(meta)

    assert result.mode == IndexMode.IN_REPO
    assert result.slug == "nexus-fixtures--sample"

    project_meta = json.loads((result.project_dir / "meta.json").read_text())
    assert project_meta["kind"] == "package"
    assert project_meta["build_mode"] == "in-repo"


@skip_unless_integration
def test_inrepo_mode_writes_meta_attribution(fixture_clone: Path, tmp_path: Path) -> None:
    """SPEC: in-repo meta.json carries full attribution from the target package.

    KNOWN BUG at the time this test was written: the PHP extractor reads
    package metadata from the Testbench Laravel skeleton's composer.json
    rather than the target package's composer.json, so description shows
    "The Laravel Framework." and authors is empty. This test pins the bug
    — it should PASS after the fix lands.
    """
    _install_extractor_into_fixture(fixture_clone)

    meta = read_composer_metadata(fixture_clone)
    indexer = PackageIndexer(
        cache_root=tmp_path / "cache",
        nexus_root=tmp_path / "nexus",
        extractor_root=EXTRACTOR_ROOT,
    )
    result = indexer.index(meta)

    project_meta = json.loads((result.project_dir / "meta.json").read_text())
    assert project_meta["package"]["vendor"] == "nexus-fixtures"
    assert project_meta["package"]["name"] == "sample"
    # Version comes from the PHP extractor: "1.2.0" when a tag is present,
    # or "dev-<branch>" when running inside a cloned git tree with no tag.
    assert project_meta["package"]["version"]
    assert project_meta["package"]["license"] == "MIT"
    # Authors should come from the target package's composer.json (2 authors).
    # Currently returns [] because the extractor reads the skeleton composer.json.
    assert len(project_meta["package"]["authors"]) == 2, (
        f"Expected 2 authors from target package composer.json, "
        f"got {project_meta['package']['authors']}. "
        f"PHP extractor likely reading the wrong composer.json."
    )
