"""Architecture test: CLI start-up must not import the vector-store stack.

Every ``nexus query`` call is a fresh process, and scripted callers fan
out many of them. ``lancedb`` alone costs ~2 s to import while
structural tools never touch vectors, so it must stay a lazy import.

Runs in a subprocess so modules already imported by the test session
don't mask a regression.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

_HEAVY_MODULES = ("lancedb", "pyarrow")


@pytest.mark.parametrize("module", _HEAVY_MODULES)
def test_cli_import_does_not_load_heavy_module(module: str) -> None:
    probe = f"import sys, nexus.interfaces.cli.main; sys.exit({module!r} in sys.modules)"

    completed = subprocess.run([sys.executable, "-c", probe], check=False)

    assert completed.returncode == 0, f"importing the CLI loads {module}"
