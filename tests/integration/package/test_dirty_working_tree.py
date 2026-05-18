"""Uncommitted edits in a git repo bust the fingerprint via porcelain output."""

from __future__ import annotations

import subprocess
from pathlib import Path

from nexus.adapters.package.composer_metadata import read_composer_metadata
from nexus.adapters.package.fingerprint import compute_fingerprint
from tests.integration.package.conftest import EXTRACTOR_ROOT, skip_unless_integration


@skip_unless_integration
def test_dirty_tree_busts_then_revert_restores(fixture_clone: Path) -> None:
    for cmd in [
        ["git", "init", "--quiet", "-b", "main"],
        ["git", "config", "user.email", "t@e.com"],
        ["git", "config", "user.name", "T"],
        ["git", "add", "."],
        ["git", "commit", "--quiet", "-m", "init"],
    ]:
        subprocess.run(cmd, cwd=fixture_clone, check=True)

    extractor_composer = EXTRACTOR_ROOT / "composer.json"
    meta = read_composer_metadata(fixture_clone)
    fp_clean = compute_fingerprint(meta, extractor_composer)

    sp = fixture_clone / "src" / "Models" / "SampleModel.php"
    original = sp.read_text()
    sp.write_text(original + "\n// dirty", encoding="utf-8")

    fp_dirty = compute_fingerprint(meta, extractor_composer)
    assert fp_clean != fp_dirty, (
        "Fingerprint should reflect uncommitted edits via git status --porcelain"
    )

    # Revert and verify the fingerprint returns to its clean value.
    subprocess.run(["git", "checkout", "--", "."], cwd=fixture_clone, check=True)
    fp_reverted = compute_fingerprint(meta, extractor_composer)
    assert fp_clean == fp_reverted, (
        "Reverting the dirty edit should restore the original fingerprint"
    )
