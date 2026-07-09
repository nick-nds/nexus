"""Mode detection: in-repo vs Nexus-driven, and scope path resolution."""

from __future__ import annotations

from pathlib import Path

from nexus.adapters.package.composer_metadata import ComposerMetadata
from nexus.adapters.package.scratch_builder import scratch_dir_for
from nexus.pipeline.package_indexer import IndexMode, _vendor_path_for, detect_mode


def _meta(root: Path) -> ComposerMetadata:
    return ComposerMetadata(package_root=root, vendor="acme", name="foo", version="1.0.0")


def test_vendor_path_in_repo_is_the_package_root(tmp_path: Path) -> None:
    """In-repo: a self-developed checkout is never installed under its own
    vendor/, so its source root - the prefix the normalizer strips - is the
    package root itself, not ``<root>/vendor/<vendor>/<name>``."""
    meta = _meta(tmp_path / "pkg")

    assert _vendor_path_for(IndexMode.IN_REPO, meta, tmp_path / "cache") == tmp_path / "pkg"


def test_vendor_path_nexus_driven_is_under_scratch_vendor(tmp_path: Path) -> None:
    """Nexus-driven: the package is installed into the scratch Testbench
    vendor tree, so that is the prefix to strip."""
    meta = _meta(tmp_path / "pkg")
    cache = tmp_path / "cache"
    expected = scratch_dir_for(meta, base=cache) / "vendor" / "acme" / "foo"

    assert _vendor_path_for(IndexMode.NEXUS_DRIVEN, meta, cache) == expected


def test_in_repo_when_both_testbench_and_extractor_present(tmp_path: Path) -> None:
    (tmp_path / "vendor" / "bin").mkdir(parents=True)
    (tmp_path / "vendor" / "bin" / "testbench").touch()
    (tmp_path / "vendor" / "nick-nds" / "nexus-extractor").mkdir(parents=True)

    assert detect_mode(tmp_path) == IndexMode.IN_REPO


def test_nexus_driven_when_testbench_missing(tmp_path: Path) -> None:
    (tmp_path / "vendor" / "nick-nds" / "nexus-extractor").mkdir(parents=True)

    assert detect_mode(tmp_path) == IndexMode.NEXUS_DRIVEN


def test_nexus_driven_when_extractor_missing(tmp_path: Path) -> None:
    (tmp_path / "vendor" / "bin").mkdir(parents=True)
    (tmp_path / "vendor" / "bin" / "testbench").touch()

    assert detect_mode(tmp_path) == IndexMode.NEXUS_DRIVEN


def test_nexus_driven_when_neither(tmp_path: Path) -> None:
    assert detect_mode(tmp_path) == IndexMode.NEXUS_DRIVEN
