"""Mode detection: in-repo vs Nexus-driven."""

from __future__ import annotations

from pathlib import Path

from nexus.pipeline.package_indexer import IndexMode, detect_mode


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
