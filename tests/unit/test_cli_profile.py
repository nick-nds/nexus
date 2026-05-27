"""Tests for `nexus profile` subcommands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from nexus.interfaces.cli.main import main


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ---------------------------------------------------------------------------
# nexus profile list
# ---------------------------------------------------------------------------


class TestProfileList:
    def test_returns_all_builtin_profiles(self, runner: CliRunner) -> None:
        from nexus.profiles import load_builtin_profiles

        expected_names = {p.name for p in load_builtin_profiles()}
        result = runner.invoke(main, ["--format", "json", "profile", "list"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert isinstance(data, list)
        returned_names = {item["name"] for item in data}
        assert returned_names == expected_names

    def test_each_entry_has_name_display_name_description(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["--format", "json", "profile", "list"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        for item in data:
            assert "name" in item
            assert "display_name" in item
            assert "description" in item

    def test_descriptions_are_single_line(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["--format", "json", "profile", "list"])
        data = json.loads(result.output)
        for item in data:
            assert "\n" not in item["description"]


# ---------------------------------------------------------------------------
# nexus profile detect
# ---------------------------------------------------------------------------


class TestProfileDetect:
    def test_detect_returns_matches(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(
            main,
            ["--format", "json", "profile", "detect", "--project-path", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert "matches" in data
        assert "project_path" in data

    def test_detect_best_match_key_present(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(
            main,
            ["--format", "json", "profile", "detect", "--project-path", str(tmp_path)],
        )
        data = json.loads(result.output)
        assert "best_match" in data

    def test_detect_top_n_limits_results(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(
            main,
            [
                "--format",
                "json",
                "profile",
                "detect",
                "--project-path",
                str(tmp_path),
                "--top",
                "1",
            ],
        )
        data = json.loads(result.output)
        assert len(data["matches"]) <= 1

    def test_detect_matches_have_rank_name_score(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(
            main,
            ["--format", "json", "profile", "detect", "--project-path", str(tmp_path)],
        )
        data = json.loads(result.output)
        for match in data["matches"]:
            assert "rank" in match
            assert "name" in match
            assert "score" in match

    def test_detect_ranks_are_sequential(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(
            main,
            ["--format", "json", "profile", "detect", "--project-path", str(tmp_path)],
        )
        data = json.loads(result.output)
        ranks = [m["rank"] for m in data["matches"]]
        assert ranks == list(range(1, len(ranks) + 1))


# ---------------------------------------------------------------------------
# nexus profile show
# ---------------------------------------------------------------------------


class TestProfileShow:
    def test_show_known_profile(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["--format", "json", "profile", "show", "laravel-default"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["name"] == "laravel-default"
        assert "display_name" in data
        assert "description" in data
        assert "signals" in data

    def test_show_all_builtin_profiles(self, runner: CliRunner) -> None:
        from nexus.profiles import load_builtin_profiles

        for profile in load_builtin_profiles():
            result = runner.invoke(main, ["--format", "json", "profile", "show", profile.name])
            assert result.exit_code == 0, f"profile show {profile.name} failed"

    def test_show_unknown_profile_exits_1(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["--format", "json", "profile", "show", "nonexistent-profile"])
        assert result.exit_code == 1

    def test_show_signals_list_nonempty(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["--format", "json", "profile", "show", "laravel-api"])
        data = json.loads(result.output)
        assert len(data["signals"]) > 0

    def test_show_signals_have_kind_and_weight(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["--format", "json", "profile", "show", "laravel-api"])
        data = json.loads(result.output)
        for sig in data["signals"]:
            assert "kind" in sig
            assert "weight" in sig


# ---------------------------------------------------------------------------
# nexus profile - group help
# ---------------------------------------------------------------------------


class TestProfileGroupHelp:
    def test_profile_in_root_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "profile" in result.output

    def test_profile_help_lists_subcommands(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["profile", "--help"])
        assert result.exit_code == 0
        for sub in ("list", "detect", "show"):
            assert sub in result.output
