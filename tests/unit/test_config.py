"""Tests for nexus.config."""

from __future__ import annotations

from pathlib import Path

import pytest
from nexus.config import (
    ConfigError,
    ConfigVersionError,
    EmbedderConfig,
    GlobalConfig,
    load_global_config,
    load_project_profile,
)
from nexus.config.loader import ConfigNotFoundError, ConfigParseError


class TestGlobalConfigDefaults:
    def test_defaults_construct_cleanly(self) -> None:
        cfg = GlobalConfig.defaults()

        assert cfg.embedder.provider == "sentence_transformers"
        assert cfg.cost.confirm_above_usd == 0.50
        assert cfg.schema_version.startswith("1.")

    def test_missing_file_returns_defaults(self, tmp_path: Path) -> None:
        cfg = load_global_config(tmp_path / "nope.yml")

        assert cfg == GlobalConfig.defaults()


class TestGlobalConfigRoundTrip:
    def test_custom_embedder_provider(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yml"
        path.write_text(
            "schema_version: '1.0'\n"
            "embedder:\n"
            "  provider: voyage\n"
            "  model: voyage-code-3\n"
            "  api_key_env: VOYAGE_API_KEY\n",
        )

        cfg = load_global_config(path)

        assert cfg.embedder.provider == "voyage"
        assert cfg.embedder.model == "voyage-code-3"
        assert cfg.embedder.api_key_env == "VOYAGE_API_KEY"

    def test_cost_threshold_override(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yml"
        path.write_text("cost:\n  confirm_above_usd: 5.0\n")

        cfg = load_global_config(path)
        assert cfg.cost.confirm_above_usd == 5.0


class TestGlobalConfigErrors:
    def test_invalid_yaml(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yml"
        path.write_text("::: not yaml :::")

        with pytest.raises(ConfigParseError):
            load_global_config(path)

    def test_version_mismatch(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yml"
        path.write_text("schema_version: '2.0'\n")

        with pytest.raises(ConfigVersionError):
            load_global_config(path)

    def test_unknown_field_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yml"
        path.write_text("embedder:\n  provider: ollama\n  mystery: 42\n")

        with pytest.raises(ConfigParseError):
            load_global_config(path)


class TestProjectProfile:
    def test_minimal_profile(self, tmp_path: Path) -> None:
        path = tmp_path / "nexus.yml"
        path.write_text("project:\n  slug: my-crm\n")

        profile = load_project_profile(path)

        assert profile.project.slug == "my-crm"
        assert profile.conventions.custom_bases == {}
        assert profile.conventions.custom_suffixes == {}
        assert profile.indexing.include_vendor is False

    def test_full_profile(self, tmp_path: Path) -> None:
        path = tmp_path / "nexus.yml"
        path.write_text(
            "schema_version: '1.0'\n"
            "project:\n"
            "  slug: my-crm\n"
            "  name: 'My CRM'\n"
            "  description: Primary CRM application.\n"
            "profile: laravel-ddd-cqrs\n"
            "conventions:\n"
            "  custom_bases:\n"
            "    App\\Actions\\BaseAction: action\n"
            "  custom_suffixes:\n"
            "    Handler: command_handler\n"
            "  modules:\n"
            "    pattern: 'app/Modules/{module}/**'\n"
            "    layers: [Domain, Application, Infrastructure]\n"
            "indexing:\n"
            "  include_vendor: false\n"
            "  include_tests: true\n"
            "  exclude_paths:\n"
            "    - app/Legacy/**\n"
            "  include_vendor_packages:\n"
            "    - spatie/laravel-permission\n",
        )

        profile = load_project_profile(path)

        assert profile.profile == "laravel-ddd-cqrs"
        assert profile.conventions.custom_bases == {
            "App\\Actions\\BaseAction": "action",
        }
        assert profile.conventions.custom_suffixes == {"Handler": "command_handler"}
        assert profile.conventions.modules is not None
        assert profile.conventions.modules.layers == [
            "Domain",
            "Application",
            "Infrastructure",
        ]
        assert profile.indexing.include_tests is True
        assert "app/Legacy/**" in profile.indexing.exclude_paths

    def test_missing_project_slug_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "nexus.yml"
        path.write_text("project: {}\n")

        with pytest.raises(ConfigParseError):
            load_project_profile(path)

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigNotFoundError):
            load_project_profile(tmp_path / "absent.yml")


class TestConfigErrorHierarchy:
    def test_all_errors_inherit_from_config_error(self) -> None:
        assert issubclass(ConfigNotFoundError, ConfigError)
        assert issubclass(ConfigParseError, ConfigError)
        assert issubclass(ConfigVersionError, ConfigError)


class TestEmbedderConfigFrozen:
    def test_cannot_mutate(self) -> None:
        cfg = EmbedderConfig()

        with pytest.raises(ValueError, match="frozen"):
            cfg.provider = "other"  # type: ignore[misc]
