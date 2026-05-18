"""Source edits in a git repo bust the fingerprint."""

from __future__ import annotations

import subprocess
from pathlib import Path

from nexus.adapters.package.composer_metadata import read_composer_metadata
from nexus.adapters.package.fingerprint import compute_fingerprint
from tests.integration.package.conftest import EXTRACTOR_ROOT, skip_unless_integration


@skip_unless_integration
def test_committed_source_change_busts_cache(fixture_clone: Path) -> None:
    # Make the fixture a git repo with a clean working tree.
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
    fp_before = compute_fingerprint(meta, extractor_composer)

    # Edit + commit one source file.
    sp = fixture_clone / "src" / "Models" / "SampleModel.php"
    sp.write_text(sp.read_text() + "\n// edit", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=fixture_clone, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "edit"], cwd=fixture_clone, check=True)

    fp_after = compute_fingerprint(meta, extractor_composer)
    assert fp_before != fp_after, "Fingerprint should change after committing a source edit"
