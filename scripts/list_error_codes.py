#!/usr/bin/env python3
"""Audit script for the ``error_code`` taxonomy.

Greps the codebase for every literal ``error_code="…"`` emission and
checks each one is mentioned in ``docs/error-codes.md``. Run with
``--strict`` to exit non-zero when an undocumented code is found —
suitable for CI.

Usage::

    uv run python scripts/list_error_codes.py            # informational
    uv run python scripts/list_error_codes.py --strict   # CI mode

The script is intentionally string-based rather than AST-based: the
emission pattern is a stable convention (Pydantic kwargs in tool
output constructors), and a regex keeps the script portable across
Python versions and test fixtures.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
NEXUS_ROOT = REPO_ROOT / "nexus"
DOC_PATH = REPO_ROOT / "docs" / "error-codes.md"

# Match ``error_code="..."`` and ``error_code='...'``. We deliberately
# skip ``error_code: str | None`` annotations and assignments to None.
EMIT_PATTERN = re.compile(r"""error_code\s*=\s*["']([a-z][a-z0-9_]*)["']""")

# Match a backticked code in the doc, e.g. ``invalid_kind``.
DOC_PATTERN = re.compile(r"`([a-z][a-z0-9_]*)`")


def find_emitted_codes() -> dict[str, list[Path]]:
    """Return ``{code: [files...]}`` for every literal emission in ``nexus/``."""
    emissions: dict[str, list[Path]] = {}
    for path in NEXUS_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for match in EMIT_PATTERN.finditer(text):
            code = match.group(1)
            emissions.setdefault(code, []).append(path.relative_to(REPO_ROOT))
    return emissions


def find_documented_codes() -> set[str]:
    """Return the set of codes mentioned in ``docs/error-codes.md``."""
    if not DOC_PATH.exists():
        return set()
    text = DOC_PATH.read_text(encoding="utf-8")
    candidates = set(DOC_PATTERN.findall(text))
    # Filter to only things that look like error codes (lower_snake_case
    # with an underscore or known one-word codes).
    return {c for c in candidates if "_" in c or c in {"any", "read", "write"}}


def main() -> int:
    """Audit emitted vs documented codes and print a report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when an undocumented code is found.",
    )
    args = parser.parse_args()

    emitted = find_emitted_codes()
    documented = find_documented_codes()

    print(f"Emitted error codes: {len(emitted)}")
    print(f"Documented codes:    {len(documented)}")

    undocumented = sorted(set(emitted) - documented)
    if undocumented:
        print("\nUndocumented codes (add to docs/error-codes.md):")
        for code in undocumented:
            files = emitted[code]
            print(f"  - {code} ({len(files)} site(s)): {files[0]}")
    else:
        print("\nAll emitted codes are documented.")

    print("\nEmitted codes (sites):")
    for code in sorted(emitted):
        files = sorted({str(f) for f in emitted[code]})
        if len(files) == 1:
            print(f"  - {code}: {files[0]}")
        else:
            print(f"  - {code}: {len(files)} sites")
            for f in files:
                print(f"      - {f}")

    if args.strict and undocumented:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
