"""Tests for `nexus cache` subcommands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from nexus.interfaces.cli.commands.cache import _human_bytes, _measure_cache
from nexus.interfaces.cli.main import main


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ---------------------------------------------------------------------------
# _human_bytes helper
# ---------------------------------------------------------------------------


class TestHumanBytes:
    def test_bytes(self) -> None:
        assert _human_bytes(512) == "512 B"

    def test_kibibytes(self) -> None:
        assert "KiB" in _human_bytes(2048)

    def test_mebibytes(self) -> None:
        assert "MiB" in _human_bytes(2 * 1024 * 1024)

    def test_gibibytes(self) -> None:
        assert "GiB" in _human_bytes(2 * 1024 * 1024 * 1024)

    def test_zero(self) -> None:
        assert _human_bytes(0) == "0 B"


# ---------------------------------------------------------------------------
# _measure_cache helper
# ---------------------------------------------------------------------------


class TestMeasureCache:
    def test_empty_dir_returns_zeros(self, tmp_path: Path) -> None:
        total, count = _measure_cache(tmp_path)
        assert total == 0
        assert count == 0

    def test_counts_json_files(self, tmp_path: Path) -> None:
        (tmp_path / "a.json").write_text('{"v": [1.0]}')
        (tmp_path / "b.json").write_text('{"v": [2.0]}')
        (tmp_path / "not_counted.txt").write_text("ignored")
        _, count = _measure_cache(tmp_path)
        assert count == 2

    def test_sums_file_sizes(self, tmp_path: Path) -> None:
        content = '{"v": [1.0]}'
        (tmp_path / "a.json").write_text(content)
        total, _ = _measure_cache(tmp_path)
        assert total == len(content.encode())

    def test_recurses_into_subdirs(self, tmp_path: Path) -> None:
        sub = tmp_path / "model-abc"
        sub.mkdir()
        (sub / "entry.json").write_text("{}")
        _, count = _measure_cache(tmp_path)
        assert count == 1


# ---------------------------------------------------------------------------
# nexus cache size
# ---------------------------------------------------------------------------


class TestCacheSize:
    def test_size_when_cache_does_not_exist(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(
            main,
            ["--storage-root", str(tmp_path), "--format", "json", "cache", "size"],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["exists"] is False
        assert data["total_bytes"] == 0
        assert data["entry_count"] == 0

    def test_size_with_entries(self, runner: CliRunner, tmp_path: Path) -> None:
        cache_dir = tmp_path / "cache" / "embeddings" / "model-x"
        cache_dir.mkdir(parents=True)
        (cache_dir / "entry.json").write_text('{"v": [1.0]}')

        result = runner.invoke(
            main,
            ["--storage-root", str(tmp_path), "--format", "json", "cache", "size"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["exists"] is True
        assert data["entry_count"] == 1
        assert data["total_bytes"] > 0
        assert "total_human" in data


# ---------------------------------------------------------------------------
# nexus cache clear
# ---------------------------------------------------------------------------


class TestCacheClear:
    def test_clear_nothing_to_clear(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(
            main,
            [
                "--storage-root",
                str(tmp_path),
                "--format",
                "json",
                "cache",
                "clear",
                "--force",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "nothing to clear"

    def test_clear_removes_cache_dir(self, runner: CliRunner, tmp_path: Path) -> None:
        cache_dir = tmp_path / "cache" / "embeddings"
        cache_dir.mkdir(parents=True)
        (cache_dir / "entry.json").write_text('{"v": [1.0]}')

        result = runner.invoke(
            main,
            [
                "--storage-root",
                str(tmp_path),
                "--format",
                "json",
                "cache",
                "clear",
                "--force",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "cleared"
        assert not cache_dir.exists()

    def test_clear_reports_entries_and_bytes(self, runner: CliRunner, tmp_path: Path) -> None:
        cache_dir = tmp_path / "cache" / "embeddings"
        cache_dir.mkdir(parents=True)
        (cache_dir / "a.json").write_text('{"v": [1.0]}')
        (cache_dir / "b.json").write_text('{"v": [2.0]}')

        result = runner.invoke(
            main,
            [
                "--storage-root",
                str(tmp_path),
                "--format",
                "json",
                "cache",
                "clear",
                "--force",
            ],
        )
        data = json.loads(result.output)
        assert data["entries_removed"] == 2
        assert data["bytes_freed"] > 0
        assert "bytes_freed_human" in data

    def test_clear_requires_confirm_without_force(self, runner: CliRunner, tmp_path: Path) -> None:
        cache_dir = tmp_path / "cache" / "embeddings"
        cache_dir.mkdir(parents=True)
        (cache_dir / "entry.json").write_text("{}")

        result = runner.invoke(
            main,
            ["--storage-root", str(tmp_path), "--format", "json", "cache", "clear"],
            input="n\n",
        )
        assert result.exit_code != 0
        assert cache_dir.exists()

    def test_clear_yes_flag_skips_confirm(self, runner: CliRunner, tmp_path: Path) -> None:
        cache_dir = tmp_path / "cache" / "embeddings"
        cache_dir.mkdir(parents=True)
        (cache_dir / "entry.json").write_text("{}")

        result = runner.invoke(
            main,
            [
                "--storage-root",
                str(tmp_path),
                "--yes",
                "--format",
                "json",
                "cache",
                "clear",
            ],
        )
        assert result.exit_code == 0
        assert not cache_dir.exists()


# ---------------------------------------------------------------------------
# Group help
# ---------------------------------------------------------------------------


class TestCacheGroupHelp:
    def test_cache_in_root_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["--help"])
        assert "cache" in result.output

    def test_cache_subcommands_in_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["cache", "--help"])
        assert "size" in result.output
        assert "clear" in result.output
