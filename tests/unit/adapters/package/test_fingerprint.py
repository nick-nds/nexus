"""Cache fingerprint includes target source state per spec decision #6."""

from __future__ import annotations

import subprocess
from pathlib import Path

from nexus.adapters.package.composer_metadata import ComposerMetadata
from nexus.adapters.package.fingerprint import compute_fingerprint


def _meta(tmp: Path) -> ComposerMetadata:
    return ComposerMetadata(
        package_root=tmp,
        vendor="foo",
        name="bar",
        version="1.0",
        psr4_namespaces={"Foo\\Bar\\": "src/"},
        testbench_yaml=tmp / "testbench.yaml",
    )


def test_fingerprint_is_deterministic_for_same_inputs(tmp_path: Path) -> None:
    (tmp_path / "composer.json").write_text('{"name":"foo/bar"}', encoding="utf-8")
    (tmp_path / "testbench.yaml").write_text("providers: []", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "A.php").write_text("<?php class A {}", encoding="utf-8")

    extractor_composer = tmp_path / "extractor.json"
    extractor_composer.write_text('{"name":"nick-nds/nexus-extractor"}', encoding="utf-8")

    fp1 = compute_fingerprint(_meta(tmp_path), extractor_composer)
    fp2 = compute_fingerprint(_meta(tmp_path), extractor_composer)
    assert fp1 == fp2


def test_fingerprint_changes_when_composer_json_changes(tmp_path: Path) -> None:
    (tmp_path / "composer.json").write_text('{"name":"foo/bar"}', encoding="utf-8")
    (tmp_path / "testbench.yaml").write_text("providers: []", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "A.php").write_text("<?php class A {}", encoding="utf-8")

    ec = tmp_path / "extractor.json"
    ec.write_text("{}", encoding="utf-8")

    fp_before = compute_fingerprint(_meta(tmp_path), ec)

    (tmp_path / "composer.json").write_text('{"name":"foo/bar","version":"2.0"}', encoding="utf-8")
    fp_after = compute_fingerprint(_meta(tmp_path), ec)

    assert fp_before != fp_after


def test_fingerprint_changes_when_source_file_edited_nongit(tmp_path: Path) -> None:
    (tmp_path / "composer.json").write_text('{"name":"foo/bar"}', encoding="utf-8")
    (tmp_path / "testbench.yaml").write_text("providers: []", encoding="utf-8")
    (tmp_path / "src").mkdir()
    a = tmp_path / "src" / "A.php"
    a.write_text("<?php class A {}", encoding="utf-8")

    ec = tmp_path / "extractor.json"
    ec.write_text("{}", encoding="utf-8")

    fp_before = compute_fingerprint(_meta(tmp_path), ec)
    a.write_text("<?php class A { public function x() {} }", encoding="utf-8")
    fp_after = compute_fingerprint(_meta(tmp_path), ec)

    assert fp_before != fp_after


def test_fingerprint_includes_git_head_when_available(tmp_path: Path) -> None:
    (tmp_path / "composer.json").write_text('{"name":"foo/bar"}', encoding="utf-8")
    (tmp_path / "testbench.yaml").write_text("", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "A.php").write_text("<?php class A {}", encoding="utf-8")

    ec = tmp_path / "extractor.json"
    ec.write_text("{}", encoding="utf-8")

    subprocess.run(["git", "init", "--quiet", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "initial"], cwd=tmp_path, check=True)

    fp_before = compute_fingerprint(_meta(tmp_path), ec)

    (tmp_path / "src" / "A.php").write_text("<?php class A { public $x; }", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "edit"], cwd=tmp_path, check=True)

    fp_after = compute_fingerprint(_meta(tmp_path), ec)
    assert fp_before != fp_after


def test_dirty_working_tree_busts_fingerprint(tmp_path: Path) -> None:
    (tmp_path / "composer.json").write_text('{"name":"foo/bar"}', encoding="utf-8")
    (tmp_path / "testbench.yaml").write_text("", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "A.php").write_text("<?php class A {}", encoding="utf-8")
    ec = tmp_path / "extractor.json"
    ec.write_text("{}", encoding="utf-8")

    subprocess.run(["git", "init", "--quiet", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "initial"], cwd=tmp_path, check=True)

    fp_clean = compute_fingerprint(_meta(tmp_path), ec)

    (tmp_path / "src" / "A.php").write_text("<?php class A { /* edited */ }", encoding="utf-8")
    fp_dirty = compute_fingerprint(_meta(tmp_path), ec)
    assert fp_clean != fp_dirty
