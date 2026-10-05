"""``nexus.yml`` ``indexing:`` settings -> PHP extractor flags.

Regression: ``indexing.include_tests`` (and the ``--include-tests`` CLI
flag) used to be accepted and then dropped, so test classes were never
extracted. The settings must reach the extractor as Artisan flags, and
``nexus.yml`` must be honoured on flag-less runs such as the
post-commit ``index sync`` hook.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from nexus.config.project_profile import IndexingSettings
from nexus.interfaces.cli.commands._index_helpers import _extractor_args
from nexus.interfaces.cli.main import main
from nexus.pipeline import PipelineResult


class TestExtractorArgs:
    def test_defaults_pass_no_flags(self) -> None:
        assert _extractor_args(IndexingSettings(), include_tests=False) == ()

    def test_config_include_tests_passes_flag(self) -> None:
        settings = IndexingSettings(include_tests=True)

        assert _extractor_args(settings, include_tests=False) == ("--include-tests",)

    def test_cli_include_tests_overrides_config_default(self) -> None:
        assert _extractor_args(IndexingSettings(), include_tests=True) == ("--include-tests",)

    def test_include_vendor_passes_flag(self) -> None:
        settings = IndexingSettings(include_vendor=True)

        assert _extractor_args(settings, include_tests=False) == ("--include-vendor",)

    def test_each_vendor_package_becomes_an_allowlist_flag(self) -> None:
        settings = IndexingSettings(include_vendor_packages=["spatie/permission", "laravel/scout"])

        assert _extractor_args(settings, include_tests=False) == (
            "--vendor-allowlist=spatie/permission",
            "--vendor-allowlist=laravel/scout",
        )

    def test_each_exclude_path_becomes_an_exclude_flag(self) -> None:
        settings = IndexingSettings(exclude_paths=["storage/", "app/Legacy/**"])

        assert _extractor_args(settings, include_tests=False) == (
            "--exclude-path=storage/",
            "--exclude-path=app/Legacy/**",
        )


def _invoke_index(tmp_path: Path, *args: str) -> tuple[object, MagicMock]:
    mock_result = MagicMock(spec=PipelineResult)
    mock_result.ok = True
    with (
        patch("nexus.interfaces.cli.commands._index_helpers._build_pipeline") as mock_build,
        patch("nexus.interfaces.cli.commands._index_helpers._detect_profile") as mock_profile,
    ):
        mock_build.return_value.run.return_value = mock_result
        mock_profile.return_value = MagicMock()
        result = CliRunner().invoke(
            main,
            [
                "--storage-root",
                str(tmp_path / "storage"),
                "--slug",
                "proj",
                "--format",
                "json",
                "index",
                *args,
                "--project-path",
                str(tmp_path),
            ],
        )
    return result, mock_build


class TestCliWiring:
    def test_sync_honours_nexus_yml_include_tests_without_flag(self, tmp_path: Path) -> None:
        (tmp_path / "nexus.yml").write_text(
            "schema_version: '1.0'\nproject:\n  slug: demo\nindexing:\n  include_tests: true\n"
        )

        result, mock_build = _invoke_index(tmp_path, "sync")

        assert result.exit_code == 0, result.output  # type: ignore[attr-defined]
        assert mock_build.call_args.kwargs["extractor_args"] == ("--include-tests",)

    @pytest.mark.parametrize("command", ["rebuild", "sync"])
    def test_include_tests_flag_reaches_extractor(self, tmp_path: Path, command: str) -> None:
        result, mock_build = _invoke_index(tmp_path, command, "--include-tests")

        assert result.exit_code == 0, result.output  # type: ignore[attr-defined]
        assert mock_build.call_args.kwargs["extractor_args"] == ("--include-tests",)
