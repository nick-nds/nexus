"""Source edits in a non-git target bust the fingerprint via content hash."""

from __future__ import annotations

from pathlib import Path

from nexus.adapters.package.composer_metadata import read_composer_metadata
from nexus.adapters.package.fingerprint import compute_fingerprint
from tests.integration.package.conftest import EXTRACTOR_ROOT, skip_unless_integration


@skip_unless_integration
def test_nongit_source_change_busts_cache(fixture_clone: Path) -> None:
    extractor_composer = EXTRACTOR_ROOT / "composer.json"
    meta = read_composer_metadata(fixture_clone)
    fp_before = compute_fingerprint(meta, extractor_composer)

    sp = fixture_clone / "src" / "Models" / "SampleModel.php"
    sp.write_text(sp.read_text() + "\n// edit", encoding="utf-8")

    fp_after = compute_fingerprint(meta, extractor_composer)
    assert fp_before != fp_after, "Fingerprint should change when a non-git source file is edited"
