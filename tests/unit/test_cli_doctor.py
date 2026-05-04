"""Tests for `nexus doctor` command."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from nexus.interfaces.cli.commands.doctor import (
    _ERROR,
    _OK,
    _WARN,
    CheckResult,
    _check_lsp,
    _check_nexus_yml,
    _check_php,
    _check_python_version,
)
from nexus.interfaces.cli.main import main


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ---------------------------------------------------------------------------
# CheckResult helper
# ---------------------------------------------------------------------------


class TestCheckResult:
    def test_to_dict_without_hint(self) -> None:
        r = CheckResult(name="test", status=_OK, message="all good")
        d = r.to_dict()
        assert d == {"check": "test", "status": "ok", "message": "all good"}
        assert "hint" not in d

    def test_to_dict_with_hint(self) -> None:
        r = CheckResult(name="test", status=_WARN, message="needs attention", hint="do x")
        d = r.to_dict()
        assert d["hint"] == "do x"


# ---------------------------------------------------------------------------
# Individual check functions
# ---------------------------------------------------------------------------


class TestCheckPythonVersion:
    def test_current_python_is_ok(self) -> None:
        result = _check_python_version()
        # We're running tests, so Python version must be ≥ 3.11
        assert result.status == _OK
        assert "3." in result.message

    def test_old_python_returns_error(self) -> None:
        with patch.object(sys, "version_info", (3, 10, 0)):
            result = _check_python_version()
        assert result.status == _ERROR
        assert "3.10" in result.message


class TestCheckNexusYml:
    def test_missing_file_is_warning(self, tmp_path: Path) -> None:
        result = _check_nexus_yml(tmp_path)
        assert result.status == _WARN
        assert "not found" in result.message

    def test_valid_file_is_ok(self, tmp_path: Path) -> None:
        (tmp_path / "nexus.yml").write_text("schema_version: '1.0'\nproject:\n  slug: test\n")
        result = _check_nexus_yml(tmp_path)
        assert result.status == _OK

    def test_invalid_yaml_is_warning(self, tmp_path: Path) -> None:
        (tmp_path / "nexus.yml").write_text("this: {is: invalid yaml: :")
        result = _check_nexus_yml(tmp_path)
        assert result.status == _WARN
        assert "invalid" in result.message


class TestCheckPhp:
    def test_php_not_on_path_is_error(self) -> None:
        with patch("shutil.which", return_value=None):
            result = _check_php()
        assert result.status == _ERROR
        assert "not found" in result.message

    def test_php_on_path_and_version_ok(self) -> None:
        with (
            patch("shutil.which", return_value="/usr/bin/php"),
            patch(
                "subprocess.check_output",
                return_value="PHP 8.2.0 (cli)\n",
            ),
        ):
            result = _check_php()
        assert result.status == _OK
        assert "PHP 8.2" in result.message


# ---------------------------------------------------------------------------
# nexus doctor CLI
# ---------------------------------------------------------------------------


class TestDoctorCommand:
    def test_returns_json_with_overall_and_checks(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(
            main,
            [
                "--storage-root",
                str(tmp_path / "storage"),
                "--format",
                "json",
                "doctor",
                "--project-path",
                str(tmp_path),
            ],
        )
        assert result.exit_code in (0, 1), result.output
        data = json.loads(result.output)
        assert "overall" in data
        assert "checks" in data
        assert "summary" in data
        assert isinstance(data["checks"], list)

    def test_checks_list_has_expected_check_names(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(
            main,
            [
                "--storage-root",
                str(tmp_path / "storage"),
                "--format",
                "json",
                "doctor",
                "--project-path",
                str(tmp_path),
            ],
        )
        data = json.loads(result.output)
        names = {c["check"] for c in data["checks"]}
        assert "python_version" in names
        assert "nexus_version" in names
        assert "data_directory" in names
        assert "php" in names

    def test_each_check_has_status_and_message(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(
            main,
            [
                "--storage-root",
                str(tmp_path / "storage"),
                "--format",
                "json",
                "doctor",
                "--project-path",
                str(tmp_path),
            ],
        )
        data = json.loads(result.output)
        for check in data["checks"]:
            assert "status" in check
            assert check["status"] in (_OK, _WARN, _ERROR)
            assert "message" in check

    def test_summary_counts_match_checks(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(
            main,
            [
                "--storage-root",
                str(tmp_path / "storage"),
                "--format",
                "json",
                "doctor",
                "--project-path",
                str(tmp_path),
            ],
        )
        data = json.loads(result.output)
        checks = data["checks"]
        summary = data["summary"]
        assert summary["ok"] == sum(1 for c in checks if c["status"] == _OK)
        assert summary["warnings"] == sum(1 for c in checks if c["status"] == _WARN)
        assert summary["errors"] == sum(1 for c in checks if c["status"] == _ERROR)

    def test_overall_error_exits_1(self, runner: CliRunner, tmp_path: Path) -> None:
        """When there are errors (e.g. PHP missing), exit code is 1."""
        with patch("shutil.which", return_value=None):
            result = runner.invoke(
                main,
                [
                    "--storage-root",
                    str(tmp_path / "storage"),
                    "--format",
                    "json",
                    "doctor",
                    "--project-path",
                    str(tmp_path),
                ],
            )
        data = json.loads(result.output)
        if data["overall"] == _ERROR:
            assert result.exit_code == 1
        # If "ok" overall (somehow), exit code can be 0

    def test_doctor_in_root_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "doctor" in result.output

    def test_doctor_help_mentions_project_path(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["doctor", "--help"])
        assert result.exit_code == 0
        assert "--project-path" in result.output

    def test_checks_list_includes_lsp(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(
            main,
            [
                "--storage-root",
                str(tmp_path / "storage"),
                "--format",
                "json",
                "doctor",
                "--project-path",
                str(tmp_path),
            ],
        )
        data = json.loads(result.output)
        names = {c["check"] for c in data["checks"]}
        assert "lsp" in names


class TestCheckLsp:
    """Three distinct outcomes: not_found, ok (responds), found_but_unresponsive."""

    def test_no_lsp_resolved_returns_warning(self) -> None:
        with patch(
            "nexus.adapters.lsp.resolve_lsp_binary",
            return_value=None,
        ):
            result = _check_lsp()
        assert result.status == _WARN
        assert "no LSP server found" in result.message
        assert result.hint != ""
        assert "intelephense" in result.hint or "phpactor" in result.hint

    def test_responsive_lsp_returns_ok(self) -> None:
        """A binary that completes ``prepare()`` cleanly is reported as ok."""

        class _FakeClient:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def prepare(self, _workspace_root: Path) -> None:
                return  # success: the "server" responded

            def close(self) -> None:
                return

        with (
            patch(
                "nexus.adapters.lsp.resolve_lsp_binary",
                return_value=("/fake/intelephense", ("--stdio",)),
            ),
            patch("nexus.adapters.lsp.LspClient", _FakeClient),
        ):
            result = _check_lsp()
        assert result.status == _OK
        assert "/fake/intelephense" in result.message
        assert "responded" in result.message

    def test_unresponsive_lsp_returns_error(self) -> None:
        """A binary that times out on initialize is reported as a hard error."""
        from nexus.adapters.lsp import LspProtocolError

        class _UnresponsiveClient:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def prepare(self, _workspace_root: Path) -> None:
                raise LspProtocolError("LSP 'initialize' request timed out.")

            def close(self) -> None:
                return

        with (
            patch(
                "nexus.adapters.lsp.resolve_lsp_binary",
                return_value=("/fake/dead-server", ()),
            ),
            patch("nexus.adapters.lsp.LspClient", _UnresponsiveClient),
        ):
            result = _check_lsp()
        assert result.status == _ERROR
        assert "did not respond" in result.message
        assert "/fake/dead-server" in result.message
        assert "Indexing will hang" in result.hint
