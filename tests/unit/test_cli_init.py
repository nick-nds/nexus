"""Tests for `nexus init` command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from nexus.interfaces.cli.commands.init import _slugify
from nexus.interfaces.cli.main import main


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _isolate_storage_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep ``nexus init``'s global ``config.yml`` write inside ``tmp_path``.

    ``init`` writes the chosen embedder to ``<storage_root>/config.yml``;
    without this, every invocation would clobber the developer's real
    ``~/.nexus/config.yml``.
    """
    import importlib

    main_mod = importlib.import_module("nexus.interfaces.cli.main")
    monkeypatch.setattr(main_mod, "DEFAULT_ROOT", tmp_path / ".nexus")


# ---------------------------------------------------------------------------
# _slugify helper
# ---------------------------------------------------------------------------


class TestSlugify:
    def test_lowercase(self) -> None:
        assert _slugify("MyProject") == "myproject"

    def test_underscores_become_dashes(self) -> None:
        assert _slugify("my_project") == "my-project"

    def test_spaces_become_dashes(self) -> None:
        assert _slugify("my cool project") == "my-cool-project"

    def test_special_chars_stripped(self) -> None:
        assert _slugify("My Project!") == "my-project"

    def test_consecutive_separators_collapsed(self) -> None:
        assert _slugify("my--project") == "my-project"

    def test_empty_fallback(self) -> None:
        assert _slugify("!!!") == "project"

    def test_already_valid(self) -> None:
        assert _slugify("my-project") == "my-project"


# ---------------------------------------------------------------------------
# nexus init - non-interactive
# ---------------------------------------------------------------------------


class TestInitNonInteractive:
    def test_creates_nexus_yml_with_detected_values(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        result = runner.invoke(
            main,
            [
                "--format",
                "json",
                "init",
                "--project-path",
                str(tmp_path),
                "--non-interactive",
            ],
        )
        assert result.exit_code == 0, result.output
        nexus_yml = tmp_path / "nexus.yml"
        assert nexus_yml.exists()
        data = json.loads(result.output)
        assert data["status"] == "created"
        assert "slug" in data
        assert "profile" in data

    def test_slug_option_overrides_detection(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(
            main,
            [
                "--format",
                "json",
                "init",
                "--project-path",
                str(tmp_path),
                "--non-interactive",
                "--slug",
                "custom-slug",
            ],
        )
        assert result.exit_code == 0
        content = (tmp_path / "nexus.yml").read_text()
        assert "slug: custom-slug" in content
        data = json.loads(result.output)
        assert data["slug"] == "custom-slug"

    def test_profile_option_pins_profile(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(
            main,
            [
                "--format",
                "json",
                "init",
                "--project-path",
                str(tmp_path),
                "--non-interactive",
                "--profile",
                "laravel-api",
            ],
        )
        assert result.exit_code == 0
        content = (tmp_path / "nexus.yml").read_text()
        assert "profile: laravel-api" in content

    def test_embedder_never_written_to_nexus_yml(self, runner: CliRunner, tmp_path: Path) -> None:
        """The embedder lives in the global config.yml, not the project file."""
        runner.invoke(
            main,
            [
                "--format",
                "json",
                "init",
                "--project-path",
                str(tmp_path),
                "--non-interactive",
                "--slug",
                "my-project",
                "--embedder",
                "ollama",
            ],
        )
        content = (tmp_path / "nexus.yml").read_text()
        assert "embedder" not in content
        assert "provider:" not in content

    def test_embedder_written_to_global_config(self, runner: CliRunner, tmp_path: Path) -> None:
        """init seeds the chosen embedder into <storage_root>/config.yml,
        which is where the index pipeline actually reads it from."""
        runner.invoke(
            main,
            [
                "--format",
                "json",
                "init",
                "--project-path",
                str(tmp_path),
                "--non-interactive",
                "--embedder",
                "ollama",
            ],
        )
        config = (tmp_path / ".nexus" / "config.yml").read_text()
        assert "provider: ollama" in config
        assert "model: nomic-embed-text" in config

    def test_schema_version_in_output(self, runner: CliRunner, tmp_path: Path) -> None:
        runner.invoke(
            main,
            ["--format", "json", "init", "--project-path", str(tmp_path), "--non-interactive"],
        )
        content = (tmp_path / "nexus.yml").read_text()
        assert "schema_version" in content

    def test_yes_flag_implies_non_interactive(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(
            main,
            [
                "--yes",
                "--format",
                "json",
                "init",
                "--project-path",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 0
        assert (tmp_path / "nexus.yml").exists()

    def test_no_overwrite_skips_existing_file(self, runner: CliRunner, tmp_path: Path) -> None:
        existing = tmp_path / "nexus.yml"
        existing.write_text("original")

        result = runner.invoke(
            main,
            [
                "--format",
                "json",
                "init",
                "--project-path",
                str(tmp_path),
                "--non-interactive",
                "--no-overwrite",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "skipped"
        assert existing.read_text() == "original"

    def test_overwrite_replaces_existing_file(self, runner: CliRunner, tmp_path: Path) -> None:
        existing = tmp_path / "nexus.yml"
        existing.write_text("original")

        runner.invoke(
            main,
            [
                "--format",
                "json",
                "init",
                "--project-path",
                str(tmp_path),
                "--non-interactive",
                "--slug",
                "new-slug",
            ],
        )
        content = existing.read_text()
        assert "new-slug" in content
        assert "original" not in content


# ---------------------------------------------------------------------------
# nexus init - interactive (scripted input)
# ---------------------------------------------------------------------------


class TestInitInteractive:
    def test_interactive_flow_uses_prompted_slug(self, runner: CliRunner, tmp_path: Path) -> None:
        """Scripted: accept default slug, accept default profile, accept default embedder."""
        result = runner.invoke(
            main,
            [
                "--format",
                "json",
                "init",
                "--project-path",
                str(tmp_path),
            ],
            # Press Enter for each prompt to accept defaults
            input="\n\n\n",
        )
        assert result.exit_code == 0, result.output
        assert (tmp_path / "nexus.yml").exists()

    def test_interactive_slug_override(self, runner: CliRunner, tmp_path: Path) -> None:
        """Scripted: type a custom slug, accept defaults for the rest."""
        result = runner.invoke(
            main,
            [
                "--format",
                "json",
                "init",
                "--project-path",
                str(tmp_path),
            ],
            input="my-custom-slug\n\n\n",
        )
        assert result.exit_code == 0, result.output
        content = (tmp_path / "nexus.yml").read_text()
        assert "slug: my-custom-slug" in content


# ---------------------------------------------------------------------------
# nexus init - help text
# ---------------------------------------------------------------------------


class TestInitHelp:
    def test_init_appears_in_root_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "init" in result.output

    def test_init_help_lists_key_options(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["init", "--help"])
        assert result.exit_code == 0
        assert "--non-interactive" in result.output
        assert "--slug" in result.output
        assert "--profile" in result.output
        assert "--embedder" in result.output
