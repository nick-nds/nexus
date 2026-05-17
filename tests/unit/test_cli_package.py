"""Tests for `nexus package index <path>` CLI subcommand."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from nexus.interfaces.cli.main import main


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class TestPackageIndexHelp:
    def test_index_help_includes_path_argument(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["package", "index", "--help"])
        assert result.exit_code == 0
        assert "PATH" in result.output or "<path>" in result.output

    def test_package_appears_in_root_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "package" in result.output.lower()

    def test_package_group_help_lists_index(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["package", "--help"])
        assert result.exit_code == 0
        assert "index" in result.output


class TestPackageIndexMissingPath:
    def test_nonexistent_path_returns_exit_2(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(main, ["package", "index", str(tmp_path / "does-not-exist")])
        assert result.exit_code == 2
        assert (
            "package_path_missing" in result.output.lower()
            or "no such directory" in result.output.lower()
            or "does not exist" in result.output.lower()
        )


class TestPackageIndexSuccess:
    def test_success_prints_slug_and_mode(self, runner: CliRunner, tmp_path: Path) -> None:
        """A successful index run prints the slug and mode."""
        from nexus.pipeline.package_indexer import IndexMode, IndexResult

        mock_result = IndexResult(
            slug="acme--my-pkg",
            mode=IndexMode.NEXUS_DRIVEN,
            project_dir=tmp_path / "projects" / "acme--my-pkg",
            reflection_path=tmp_path / "reflection.json",
        )

        with (
            patch(
                "nexus.interfaces.cli.commands.package.index.read_composer_metadata"
            ) as mock_meta,
            patch("nexus.interfaces.cli.commands.package.index.PackageIndexer") as mock_indexer_cls,
        ):
            fake_meta = MagicMock()
            fake_meta.full_name = "acme/my-pkg"
            fake_meta.version = "1.0.0"
            mock_meta.return_value = fake_meta

            mock_indexer = MagicMock()
            mock_indexer.index.return_value = mock_result
            mock_indexer_cls.return_value = mock_indexer

            result = runner.invoke(main, ["package", "index", str(tmp_path)])

        assert result.exit_code == 0
        assert "acme--my-pkg" in result.output
        assert "nexus-driven" in result.output

    def test_name_override_is_passed_to_read_composer_metadata(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        from nexus.pipeline.package_indexer import IndexMode, IndexResult

        mock_result = IndexResult(
            slug="vendor--name",
            mode=IndexMode.IN_REPO,
            project_dir=tmp_path / "projects" / "vendor--name",
            reflection_path=tmp_path / "reflection.json",
        )

        with (
            patch(
                "nexus.interfaces.cli.commands.package.index.read_composer_metadata"
            ) as mock_meta,
            patch("nexus.interfaces.cli.commands.package.index.PackageIndexer") as mock_indexer_cls,
        ):
            fake_meta = MagicMock()
            fake_meta.full_name = "vendor/name"
            fake_meta.version = "dev-main"
            mock_meta.return_value = fake_meta

            mock_indexer = MagicMock()
            mock_indexer.index.return_value = mock_result
            mock_indexer_cls.return_value = mock_indexer

            result = runner.invoke(
                main, ["package", "index", str(tmp_path), "--name", "vendor/name"]
            )

        assert result.exit_code == 0
        mock_meta.assert_called_once_with(
            tmp_path.resolve(), name_override="vendor/name", version_override=None
        )

    def test_version_override_is_passed_to_read_composer_metadata(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        from nexus.pipeline.package_indexer import IndexMode, IndexResult

        mock_result = IndexResult(
            slug="acme--pkg",
            mode=IndexMode.NEXUS_DRIVEN,
            project_dir=tmp_path / "projects" / "acme--pkg",
            reflection_path=tmp_path / "reflection.json",
        )

        with (
            patch(
                "nexus.interfaces.cli.commands.package.index.read_composer_metadata"
            ) as mock_meta,
            patch("nexus.interfaces.cli.commands.package.index.PackageIndexer") as mock_indexer_cls,
        ):
            fake_meta = MagicMock()
            fake_meta.full_name = "acme/pkg"
            fake_meta.version = "2.0.0"
            mock_meta.return_value = fake_meta

            mock_indexer = MagicMock()
            mock_indexer.index.return_value = mock_result
            mock_indexer_cls.return_value = mock_indexer

            result = runner.invoke(main, ["package", "index", str(tmp_path), "--version", "2.0.0"])

        assert result.exit_code == 0
        mock_meta.assert_called_once_with(
            tmp_path.resolve(), name_override=None, version_override="2.0.0"
        )


class TestPackageIndexErrors:
    def test_composer_metadata_error_exits_2(self, runner: CliRunner, tmp_path: Path) -> None:
        from nexus.adapters.package.composer_metadata import ComposerMetadataError

        with patch(
            "nexus.interfaces.cli.commands.package.index.read_composer_metadata"
        ) as mock_meta:
            mock_meta.side_effect = ComposerMetadataError(
                "package_composer_missing", "no composer.json"
            )
            result = runner.invoke(main, ["package", "index", str(tmp_path)])

        assert result.exit_code == 2
        assert "package_composer_missing" in result.output

    def test_package_index_error_exits_1(self, runner: CliRunner, tmp_path: Path) -> None:
        from nexus.pipeline.package_indexer import PackageIndexError

        with (
            patch(
                "nexus.interfaces.cli.commands.package.index.read_composer_metadata"
            ) as mock_meta,
            patch("nexus.interfaces.cli.commands.package.index.PackageIndexer") as mock_indexer_cls,
        ):
            fake_meta = MagicMock()
            mock_meta.return_value = fake_meta

            mock_indexer = MagicMock()
            mock_indexer.index.side_effect = PackageIndexError(
                "package_extraction_failed", "testbench exited 1"
            )
            mock_indexer_cls.return_value = mock_indexer

            result = runner.invoke(main, ["package", "index", str(tmp_path)])

        assert result.exit_code == 1
        assert "package_extraction_failed" in result.output

    def test_timeout_option_is_passed_to_indexer(self, runner: CliRunner, tmp_path: Path) -> None:
        from nexus.pipeline.package_indexer import IndexMode, IndexResult

        mock_result = IndexResult(
            slug="acme--pkg",
            mode=IndexMode.NEXUS_DRIVEN,
            project_dir=tmp_path / "projects" / "acme--pkg",
            reflection_path=tmp_path / "reflection.json",
        )

        with (
            patch(
                "nexus.interfaces.cli.commands.package.index.read_composer_metadata"
            ) as mock_meta,
            patch("nexus.interfaces.cli.commands.package.index.PackageIndexer") as mock_indexer_cls,
        ):
            fake_meta = MagicMock()
            fake_meta.full_name = "acme/pkg"
            fake_meta.version = "1.0.0"
            mock_meta.return_value = fake_meta

            mock_indexer = MagicMock()
            mock_indexer.index.return_value = mock_result
            mock_indexer_cls.return_value = mock_indexer

            result = runner.invoke(main, ["package", "index", str(tmp_path), "--timeout", "600"])

        assert result.exit_code == 0
        _, kwargs = mock_indexer_cls.call_args
        assert kwargs["timeout_s"] == 600
