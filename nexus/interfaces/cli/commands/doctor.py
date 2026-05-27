"""``nexus doctor`` - environment diagnostic.

Checks the installation and reports per-check status in both the rich
pretty format and the JSON-lines format for CI/scripted use. The design
goal is that the first thing any bug report should say is "run
``nexus doctor``" - the output is the canonical snapshot of the
environment.

Checks performed (in order):
1.  Python version (≥ 3.11 required).
2.  Nexus version (the installed package).
3.  Data directory (``~/.nexus/`` or ``--storage-root``) - writable.
4.  PHP binary on PATH and its version.
5.  Composer binary on PATH.
6.  ``nexus.yml`` presence in the current / specified project directory.
7.  Nexus extractor Artisan command presence
    (``artisan`` + ``vendor/bin/nexus-extract`` or the installed list).
8.  Embedder reachability (best-effort; some backends cannot be tested
    without a heavy import so we probe lazily).

Each check produces a :class:`CheckResult` with a status (``ok``,
``warning``, ``error``) and a human-readable message.

Design reference: ``internal_docs/PHASE-5-interface-layer.md`` §D5.7.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import click

from nexus.interfaces.cli.output import render
from nexus.version import __version__

if TYPE_CHECKING:
    from nexus.interfaces.cli.context import CliContext


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

_OK = "ok"
_WARN = "warning"
_ERROR = "error"


@dataclass
class CheckResult:
    """The outcome of a single diagnostic check."""

    name: str
    status: str  # "ok" | "warning" | "error"
    message: str
    hint: str = ""

    def to_dict(self) -> dict[str, object]:
        """Serialise to a plain dict for JSON output."""
        d: dict[str, object] = {
            "check": self.name,
            "status": self.status,
            "message": self.message,
        }
        if self.hint:
            d["hint"] = self.hint
        return d


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


@click.command(
    "doctor",
    help="Run environment diagnostics and report status.",
)
@click.option(
    "--project-path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Project root to inspect. Defaults to the current directory.",
)
@click.option(
    "--json-summary",
    is_flag=True,
    default=False,
    help="Emit a machine-readable JSON summary line at the end.",
)
@click.pass_obj
def doctor_command(
    cli_ctx: CliContext,
    project_path: Path | None,
    json_summary: bool,
) -> None:
    """Run all diagnostic checks and print a status report."""
    path = (project_path or cli_ctx.project_path).resolve()

    checks: list[CheckResult] = [
        _check_python_version(),
        _check_nexus_version(),
        _check_data_dir(cli_ctx),
        _check_php(),
        _check_composer(),
        _check_nexus_yml(path),
        _check_extractor(path),
        _check_lsp(),
    ]

    n_errors = sum(1 for c in checks if c.status == _ERROR)
    n_warnings = sum(1 for c in checks if c.status == _WARN)

    if n_errors > 0:
        overall = _ERROR
    elif n_warnings > 0:
        overall = _WARN
    else:
        overall = _OK

    payload = {
        "overall": overall,
        "checks": [c.to_dict() for c in checks],
        "summary": {
            "ok": sum(1 for c in checks if c.status == _OK),
            "warnings": n_warnings,
            "errors": n_errors,
        },
    }
    render(cli_ctx, payload)

    if overall == _ERROR:
        raise click.exceptions.Exit(1)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_python_version() -> CheckResult:
    """Python ≥ 3.11 is required."""
    major, minor, micro = sys.version_info[:3]
    version_str = f"{major}.{minor}.{micro}"
    if (major, minor) < (3, 11):
        return CheckResult(
            name="python_version",
            status=_ERROR,
            message=f"Python {version_str} - requires ≥ 3.11",
            hint="Upgrade Python: https://python.org/downloads/",
        )
    return CheckResult(
        name="python_version",
        status=_OK,
        message=f"Python {version_str}",
    )


def _check_nexus_version() -> CheckResult:
    """Report the installed Nexus version."""
    return CheckResult(
        name="nexus_version",
        status=_OK,
        message=f"nexus {__version__}",
    )


def _check_data_dir(cli_ctx: CliContext) -> CheckResult:
    """Verify that the Nexus data directory is writable."""
    data_dir = cli_ctx.storage_root
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        test_file = data_dir / ".nexus_write_test"
        test_file.write_text("ok")
        test_file.unlink()
        return CheckResult(
            name="data_directory",
            status=_OK,
            message=f"{data_dir} (writable)",
        )
    except OSError as exc:
        return CheckResult(
            name="data_directory",
            status=_ERROR,
            message=f"{data_dir} - not writable: {exc}",
            hint="Check filesystem permissions on the storage root.",
        )


def _check_php() -> CheckResult:
    """PHP binary on PATH and its version."""
    php = shutil.which("php")
    if php is None:
        return CheckResult(
            name="php",
            status=_ERROR,
            message="php not found on PATH",
            hint="Install PHP 8.2+ and ensure it's on your PATH.",
        )
    try:
        out = subprocess.check_output(
            [php, "--version"],
            stderr=subprocess.STDOUT,
            timeout=5,
            text=True,
        )
        first_line = out.splitlines()[0] if out else "unknown version"
        return CheckResult(name="php", status=_OK, message=first_line)
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return CheckResult(
            name="php",
            status=_WARN,
            message=f"php found at {php} but version check failed",
        )


def _check_composer() -> CheckResult:
    """Composer binary on PATH."""
    composer = shutil.which("composer")
    if composer is None:
        return CheckResult(
            name="composer",
            status=_WARN,
            message="composer not found on PATH",
            hint=(
                "Install Composer: https://getcomposer.org/download/ "
                "then run `composer require --dev nick-nds/nexus-extractor`."
            ),
        )
    try:
        out = subprocess.check_output(
            [composer, "--version", "--no-ansi"],
            stderr=subprocess.STDOUT,
            timeout=10,
            text=True,
        )
        first_line = out.splitlines()[0] if out else "unknown version"
        return CheckResult(name="composer", status=_OK, message=first_line)
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return CheckResult(
            name="composer",
            status=_WARN,
            message=f"composer found at {composer} but version check failed",
        )


def _check_nexus_yml(project_path: Path) -> CheckResult:
    """``nexus.yml`` present in the project directory."""
    nexus_yml = project_path / "nexus.yml"
    if not nexus_yml.exists():
        return CheckResult(
            name="nexus_yml",
            status=_WARN,
            message=f"nexus.yml not found in {project_path}",
            hint="Run `nexus init` to create one.",
        )
    try:
        from nexus.config import load_project_profile  # noqa: PLC0415

        load_project_profile(nexus_yml)
        return CheckResult(
            name="nexus_yml",
            status=_OK,
            message=str(nexus_yml),
        )
    except Exception as exc:  # broad catch: propagation would crash the doctor
        return CheckResult(
            name="nexus_yml",
            status=_WARN,
            message=f"nexus.yml found but invalid: {exc}",
            hint="Run `nexus init --overwrite` to recreate it.",
        )


def _check_lsp() -> CheckResult:
    """Resolve an LSP server, then verify it actually responds to ``initialize``.

    Three distinct outcomes:

    * **ok** - a binary was resolved AND it answered an ``initialize``
      request within the timeout. CALLS enrichment will work.
    * **warning / not_found** - no binary available. The pipeline can
      still build a structural-only graph.
    * **error / found_but_unresponsive** - a binary was found but it
      didn't respond. This is worth flagging loudly because indexing
      will hang on it; the user should pick a different ``--lsp`` value
      or fix the install.
    """
    import tempfile  # noqa: PLC0415

    from nexus.adapters.lsp import LspClient, LspProtocolError, resolve_lsp_binary  # noqa: PLC0415

    resolved = resolve_lsp_binary()
    if resolved is None:
        return CheckResult(
            name="lsp",
            status=_WARN,
            message="no LSP server found (intelephense or phpactor)",
            hint=(
                "CALLS-edge enrichment is optional but improves `find_callers` results.\n"
                "  Install intelephense:  npm install -g intelephense\n"
                "  Install phpactor:      "
                "https://phpactor.readthedocs.io/en/master/usage/standalone.html"
            ),
        )

    binary, args = resolved
    client = LspClient(binary, args, request_timeout_seconds=5.0)
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                client.prepare(Path(tmpdir))
            except LspProtocolError as exc:
                return CheckResult(
                    name="lsp",
                    status=_ERROR,
                    message=(
                        f"{binary} found but did not respond to ``initialize`` within 5s ({exc})"
                    ),
                    hint=(
                        "Try a different LSP via `--lsp <name>` or reinstall the server. "
                        "Indexing will hang on an unresponsive LSP."
                    ),
                )
    finally:
        client.close()

    return CheckResult(
        name="lsp",
        status=_OK,
        message=f"{binary} responded to ``initialize``",
    )


def _check_extractor(project_path: Path) -> CheckResult:
    """Composer extractor package installed in the project."""
    artisan = project_path / "artisan"
    if not artisan.exists():
        return CheckResult(
            name="extractor",
            status=_WARN,
            message=f"no artisan file found in {project_path}",
            hint="Point --project-path at a Laravel project root.",
        )

    # Check vendor/autoload.php so we know Composer has been run
    autoload = project_path / "vendor" / "autoload.php"
    if not autoload.exists():
        return CheckResult(
            name="extractor",
            status=_WARN,
            message="vendor/autoload.php missing - run `composer install` in the project",
            hint="Run `composer install` then `composer require --dev nick-nds/nexus-extractor`.",
        )

    # Look for the extractor binary that the Composer package installs
    extractor_bin = project_path / "vendor" / "bin" / "nexus-extract"
    if extractor_bin.exists():
        return CheckResult(
            name="extractor",
            status=_OK,
            message=f"nexus-extract found at {extractor_bin}",
        )

    # Fall back: check if the Artisan command is registered (lightweight)
    php = shutil.which("php")
    if php:
        try:
            result = subprocess.run(
                [php, "artisan", "list", "--format=json"],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=project_path,
                check=False,
            )
            if "nexus:extract" in result.stdout:
                return CheckResult(
                    name="extractor",
                    status=_OK,
                    message="nexus:extract Artisan command is registered",
                )
        except (OSError, subprocess.TimeoutExpired):
            pass

    return CheckResult(
        name="extractor",
        status=_WARN,
        message="nick-nds/nexus-extractor not found in project vendor",
        hint="Install with: composer require --dev nick-nds/nexus-extractor",
    )
