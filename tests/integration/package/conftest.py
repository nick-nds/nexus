"""Integration-test fixtures for Phase 5.5 package mode.

Gated behind ``RUN_PACKAGE_INTEGRATION=1`` because the cold path runs
``composer install`` (~30-90 s) and requires Composer + PHP on PATH.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

skip_unless_integration = pytest.mark.skipif(
    os.getenv("RUN_PACKAGE_INTEGRATION") != "1",
    reason="Set RUN_PACKAGE_INTEGRATION=1 to run package integration tests.",
)


REPO_ROOT = Path(__file__).resolve().parents[3]
EXTRACTOR_ROOT = REPO_ROOT / "packages" / "nexus-extractor-php"
SAMPLE_PACKAGE = EXTRACTOR_ROOT / "tests" / "fixtures" / "sample-package"


@pytest.fixture
def fixture_clone(tmp_path: Path) -> Path:
    """Copy sample-package into a tmp dir per test (isolation)."""
    dest = tmp_path / "sample-package"
    shutil.copytree(SAMPLE_PACKAGE, dest)
    return dest
