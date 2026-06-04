"""End-to-end smoke check for the nexus CLI.

Phase 5 Test Strategy calls for an end-to-end smoke test in CI. The
"full" version uses ``tests/fixtures/sample-laravel-app/`` to drive
``nexus index rebuild`` (PHP extractor → ingest → query). That fixture
doesn't exist yet, so this is the **lite** version: it exercises the
CLI surface and the Python ingestion path against a pre-built
``reflection.json`` from ``tests/fixtures/reflection-samples/``,
skipping the PHP extractor entirely.

What this script verifies:

1. ``nexus --version`` reports the package version.
2. ``nexus --help`` lists the expected top-level commands.
3. ``nexus doctor --json-summary`` runs to completion and emits a
   valid JSON report (individual checks may report warnings in CI;
   we only care that the command itself doesn't crash).
4. ``nexus init --non-interactive`` creates a ``nexus.yml`` honouring
   the supplied ``--slug``.
5. ``nexus profile list`` lists the bundled profiles.
6. ``nexus query --help`` exposes the auto-generated tool subcommands
   from ``ToolRegistry``.
7. A pre-built ``reflection.json`` from ``reflection-samples/`` loads
   cleanly through ``nexus.core.reflection.loader.load_reflection``.

Invoked by the ``smoke`` job in ``.github/workflows/ci.yml`` and by
``make ci``. Exits 0 on success, non-zero with a descriptive message
on failure.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REFLECTION_SAMPLES = REPO_ROOT / "tests" / "fixtures" / "reflection-samples"

# Use ``uv run nexus`` because the project memory mandates ``uv run``
# over plain ``python -m`` invocations.
NEXUS = ["uv", "run", "nexus"]


def _run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a command; mirror stderr on failure when ``check`` is true."""
    print(f"$ {' '.join(cmd)}", flush=True)
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        print(f"  exit {result.returncode}")
        print(f"  stdout: {result.stdout}")
        print(f"  stderr: {result.stderr}", file=sys.stderr)
        raise SystemExit(result.returncode)
    return result


def check_version() -> None:
    """``nexus --version`` reports the package version from ``nexus.version``."""
    from nexus.version import __version__

    result = _run([*NEXUS, "--version"])
    if __version__ not in result.stdout:
        raise SystemExit(
            f"Expected {__version__} in nexus --version output, got: {result.stdout!r}"
        )


def check_top_level_help() -> None:
    """``nexus --help`` advertises every documented top-level command."""
    result = _run([*NEXUS, "--help"])
    expected = ("init", "doctor", "index", "query", "ask", "mcp", "profile", "cache")
    missing = [cmd for cmd in expected if cmd not in result.stdout]
    if missing:
        raise SystemExit(f"Missing top-level commands in --help: {missing}")


def check_doctor_runs() -> None:
    """``nexus doctor --json-summary`` runs cleanly and emits valid JSON."""
    # ``--json-summary`` emits a pretty-printed JSON document on stdout
    # (the full check report). Individual checks may report warnings in
    # a CI environment (no PHP, no LSP, no nexus.yml), but the command
    # itself must run to completion and emit valid JSON with the
    # expected top-level shape.
    result = _run([*NEXUS, "doctor", "--json-summary"], check=False)
    if result.returncode not in (0, 1):
        raise SystemExit(
            f"nexus doctor --json-summary crashed (exit {result.returncode}):\n{result.stderr}"
        )

    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise SystemExit(
            f"nexus doctor --json-summary did not emit valid JSON: {e}\n"
            f"Full stdout:\n{result.stdout}"
        ) from e

    for required in ("overall", "checks", "summary"):
        if required not in report:
            raise SystemExit(
                f"nexus doctor --json-summary missing required key "
                f"{required!r}: {list(report.keys())}"
            )


def check_init_creates_yaml() -> None:
    """``nexus init --non-interactive --slug=smoke`` writes a nexus.yml."""
    with tempfile.TemporaryDirectory() as work:
        work_dir = Path(work)
        _run([*NEXUS, "init", "--non-interactive", "--slug", "smoke"], cwd=work_dir)
        nexus_yml = work_dir / "nexus.yml"
        if not nexus_yml.is_file():
            raise SystemExit(f"nexus init did not create {nexus_yml}")
        content = nexus_yml.read_text(encoding="utf-8")
        if "smoke" not in content:
            raise SystemExit(f"nexus init did not honour --slug=smoke; got:\n{content}")


def check_profile_list() -> None:
    """``nexus profile list`` lists at least the laravel-default profile."""
    result = _run([*NEXUS, "profile", "list"])
    if "laravel-default" not in result.stdout:
        raise SystemExit(f"laravel-default not in nexus profile list output: {result.stdout!r}")


def check_query_help_lists_tools() -> None:
    """``nexus query --help`` exposes every tool from the registry."""
    result = _run([*NEXUS, "query", "--help"])
    # Auto-generated subcommands per ToolRegistry. Tool names use
    # snake_case (matching the registry's tool names verbatim, not
    # Click's auto-hyphenation). Spot-check a few stable names.
    expected = ("list_routes", "describe_class", "find_listeners")
    missing = [t for t in expected if t not in result.stdout]
    if missing:
        raise SystemExit(f"Auto-generated tool subcommands missing from query --help: {missing}")


def check_reflection_sample_loads() -> None:
    """A known reflection.json loads cleanly through the Pydantic loader."""
    sample = REFLECTION_SAMPLES / "momskitchen.json"
    if not sample.is_file():
        raise SystemExit(f"Missing fixture: {sample}")

    # Run the load via ``uv run python -c`` so the smoke check itself
    # doesn't import nexus - keeps the script self-contained and
    # mirrors how a downstream user would invoke the package.
    code = (
        "from pathlib import Path; "
        "from nexus.core.reflection.loader import load_reflection; "
        f"doc = load_reflection(Path(r'{sample}')); "
        "print('schema=' + doc.schema_version); "
        "print('routes=' + str(doc.sections.routes.count "
        "if doc.sections.routes else 0))"
    )
    result = _run(["uv", "run", "python", "-c", code])
    if "schema=" not in result.stdout or "routes=" not in result.stdout:
        raise SystemExit(f"Reflection sample did not load: {result.stdout!r}")


def main() -> int:
    """Run every smoke check in order; report and exit 0 on success."""
    print(f"Repo root: {REPO_ROOT}")

    checks = (
        ("nexus --version", check_version),
        ("nexus --help", check_top_level_help),
        ("nexus doctor --json-summary", check_doctor_runs),
        ("nexus init --non-interactive", check_init_creates_yaml),
        ("nexus profile list", check_profile_list),
        ("nexus query --help (auto-generated)", check_query_help_lists_tools),
        ("reflection-samples/momskitchen.json loads", check_reflection_sample_loads),
    )

    for label, fn in checks:
        print(f"\n=== {label} ===")
        fn()
        print(f"  ✓ {label}")

    print("\nAll smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
