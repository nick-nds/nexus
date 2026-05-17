"""Compose target package metadata from composer.json + git/disk state."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from nexus.adapters.package.composer_metadata import (
    ComposerMetadataError,
    read_composer_metadata,
)


def write_composer(tmp: Path, payload: dict) -> Path:
    (tmp / "composer.json").write_text(json.dumps(payload), encoding="utf-8")
    return tmp


def test_reads_basic_composer_json(tmp_path: Path) -> None:
    write_composer(tmp_path, {"name": "spatie/laravel-permission", "version": "v6.18.0"})
    (tmp_path / "testbench.yaml").write_text("providers: []", encoding="utf-8")

    meta = read_composer_metadata(tmp_path)

    assert meta.vendor == "spatie"
    assert meta.name == "laravel-permission"
    assert meta.version == "v6.18.0"
    assert meta.testbench_yaml == tmp_path / "testbench.yaml"


def test_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(ComposerMetadataError, match="path_missing"):
        read_composer_metadata(tmp_path / "does-not-exist")


def test_missing_composer_json_raises(tmp_path: Path) -> None:
    (tmp_path / "testbench.yaml").write_text("", encoding="utf-8")
    with pytest.raises(ComposerMetadataError, match="composer_missing"):
        read_composer_metadata(tmp_path)


def test_missing_testbench_yaml_raises(tmp_path: Path) -> None:
    write_composer(tmp_path, {"name": "spatie/laravel-permission", "version": "v6.18.0"})
    with pytest.raises(ComposerMetadataError, match="testbench_yaml_missing"):
        read_composer_metadata(tmp_path)


def test_composer_invalid_json_raises(tmp_path: Path) -> None:
    (tmp_path / "composer.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "testbench.yaml").write_text("", encoding="utf-8")
    with pytest.raises(ComposerMetadataError, match="composer_invalid"):
        read_composer_metadata(tmp_path)


def test_composer_missing_name_raises(tmp_path: Path) -> None:
    write_composer(tmp_path, {"version": "1.0"})
    (tmp_path / "testbench.yaml").write_text("", encoding="utf-8")
    with pytest.raises(ComposerMetadataError, match="composer_invalid"):
        read_composer_metadata(tmp_path)


def test_version_falls_back_to_git_tag(tmp_path: Path) -> None:
    write_composer(tmp_path, {"name": "foo/bar"})
    (tmp_path / "testbench.yaml").write_text("", encoding="utf-8")
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "initial"], cwd=tmp_path, check=True)
    subprocess.run(["git", "tag", "v1.0.0"], cwd=tmp_path, check=True)

    meta = read_composer_metadata(tmp_path)
    assert meta.version == "v1.0.0"


def test_version_falls_back_to_dev_branch(tmp_path: Path) -> None:
    write_composer(tmp_path, {"name": "foo/bar"})
    (tmp_path / "testbench.yaml").write_text("", encoding="utf-8")
    subprocess.run(["git", "init", "--quiet", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "initial"], cwd=tmp_path, check=True)

    meta = read_composer_metadata(tmp_path)
    assert meta.version == "dev-main"


def test_explicit_version_override(tmp_path: Path) -> None:
    write_composer(tmp_path, {"name": "foo/bar"})
    (tmp_path / "testbench.yaml").write_text("", encoding="utf-8")

    meta = read_composer_metadata(tmp_path, version_override="v9.9.9")
    assert meta.version == "v9.9.9"
