"""scratch_builder owns ~/.nexus/cache/package-builds/<slug>/<version>/."""

from __future__ import annotations

import json
from pathlib import Path

from nexus.adapters.package.composer_metadata import ComposerMetadata
from nexus.adapters.package.scratch_builder import (
    ScratchBuilder,
    ScratchManifest,
    scratch_dir_for,
)


def _meta(tmp: Path) -> ComposerMetadata:
    (tmp / "composer.json").write_text('{"name":"foo/bar"}', encoding="utf-8")
    (tmp / "testbench.yaml").write_text("providers: []", encoding="utf-8")
    return ComposerMetadata(
        package_root=tmp,
        vendor="foo",
        name="bar",
        version="1.0",
        psr4_namespaces={"Foo\\Bar\\": "src/"},
        testbench_yaml=tmp / "testbench.yaml",
    )


def test_scratch_dir_for_returns_correct_path(tmp_path: Path) -> None:
    meta = _meta(tmp_path)
    base = tmp_path / "cache"
    sd = scratch_dir_for(meta, base=base)
    assert sd == base / "package-builds" / "foo--bar" / "1.0"


def test_manifest_round_trip(tmp_path: Path) -> None:
    sd = tmp_path / "scratch"
    sd.mkdir()

    manifest = ScratchManifest(
        fingerprint="abc123",
        composer_install_at="2026-05-08T10:00:00Z",
        extracted_at="2026-05-08T10:01:00Z",
    )
    builder = ScratchBuilder(sd)
    builder.write_manifest(manifest)

    read_back = builder.read_manifest()
    assert read_back == manifest


def test_manifest_absent_returns_none(tmp_path: Path) -> None:
    sd = tmp_path / "scratch"
    sd.mkdir()
    builder = ScratchBuilder(sd)
    assert builder.read_manifest() is None


def test_generate_composer_json_shape(tmp_path: Path) -> None:
    meta = _meta(tmp_path)
    sd = tmp_path / "scratch"
    sd.mkdir()
    extractor_path = tmp_path / "extractor"
    extractor_path.mkdir()
    (extractor_path / "composer.json").write_text(
        '{"name":"nick-nds/nexus-extractor"}', encoding="utf-8"
    )

    builder = ScratchBuilder(sd)
    builder.generate_composer_json(meta, extractor_path)

    written = json.loads((sd / "composer.json").read_text(encoding="utf-8"))
    assert "repositories" in written
    assert any("type" in r and r["type"] == "path" for r in written["repositories"])
    assert "foo/bar" in written["require"]
    assert "nick-nds/nexus-extractor" in written["require"]
    assert "orchestra/testbench" in written["require"]


def test_copy_testbench_yaml_to_scratch(tmp_path: Path) -> None:
    meta = _meta(tmp_path)
    sd = tmp_path / "scratch"
    sd.mkdir()

    builder = ScratchBuilder(sd)
    builder.copy_testbench_yaml(meta)

    assert (sd / "testbench.yaml").is_file()
    assert (sd / "testbench.yaml").read_text() == "providers: []"


def test_symlink_workbench_when_present(tmp_path: Path) -> None:
    meta = _meta(tmp_path)
    workbench = tmp_path / "workbench"
    workbench.mkdir()
    sd = tmp_path / "scratch"
    sd.mkdir()

    builder = ScratchBuilder(sd)
    builder.symlink_workbench_if_present(meta)

    assert (sd / "workbench").is_symlink()


def test_no_symlink_when_workbench_absent(tmp_path: Path) -> None:
    meta = _meta(tmp_path)
    sd = tmp_path / "scratch"
    sd.mkdir()

    builder = ScratchBuilder(sd)
    builder.symlink_workbench_if_present(meta)

    assert not (sd / "workbench").exists()
