"""Shared YAML loading utilities and exceptions for config files.

The two config loaders (global + project profile) share a surprising
amount of behaviour: read a file, parse as YAML, check the schema
version, validate with Pydantic. Centralising that flow here keeps the
individual model files focused on their shape.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeVar

import yaml
from pydantic import BaseModel, ValidationError

if TYPE_CHECKING:
    from pathlib import Path


T = TypeVar("T", bound=BaseModel)


class ConfigError(Exception):
    """Base class for config-loader failures."""


class ConfigNotFoundError(ConfigError):
    """The config file does not exist."""


class ConfigParseError(ConfigError):
    """The file exists but isn't valid YAML or doesn't match the schema."""


class ConfigVersionError(ConfigError):
    """The file's schema_version major is not understood by this build."""


def load_yaml_document(path: Path) -> dict[str, Any]:
    """Read and parse a YAML document into a dict.

    Raises:
        ConfigNotFoundError: ``path`` does not exist.
        ConfigParseError: the file is not valid YAML or the root is not
            a mapping.
    """
    if not path.is_file():
        raise ConfigNotFoundError(f"Config file not found: {path}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigParseError(f"Invalid YAML in {path}: {e}") from e

    if raw is None:
        return {}

    if not isinstance(raw, dict):
        raise ConfigParseError(
            f"Config file must be a YAML mapping at the top level: {path}",
        )

    return raw


def check_schema_major(
    raw: dict[str, Any],
    *,
    expected_major: int,
    source: Path,
) -> None:
    """Validate the ``schema_version`` field against an expected major.

    Missing ``schema_version`` is treated as ``<expected_major>.0`` —
    user config files should not be required to spell out a version
    string in the common case.
    """
    version = raw.get("schema_version")
    if version is None:
        raw["schema_version"] = f"{expected_major}.0"
        return

    if not isinstance(version, (int, float, str)):
        raise ConfigParseError(
            f"schema_version must be a string or number in {source}",
        )

    version_str = str(version)
    try:
        major_str, _ = version_str.split(".", 1) if "." in version_str else (version_str, "0")
        major = int(major_str)
    except ValueError as e:
        raise ConfigParseError(
            f"schema_version {version_str!r} is malformed in {source}",
        ) from e

    if major != expected_major:
        raise ConfigVersionError(
            f"{source} declares schema_version {version_str} but this Nexus "
            f"build expects major {expected_major}. Upgrade or downgrade to "
            f"a matching release.",
        )


def validate_model(
    model_cls: type[T],
    raw: dict[str, Any],
    *,
    source: Path,
) -> T:
    """Validate ``raw`` against a Pydantic model, wrapping errors."""
    try:
        return model_cls.model_validate(raw)
    except ValidationError as e:
        raise ConfigParseError(f"Invalid config in {source}: {e}") from e
